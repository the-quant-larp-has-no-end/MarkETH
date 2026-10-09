# MarkETH — Hackathon Spec v2

Oct 9, 2026 · @Doyun

## 1. Overview

MarkETH is a mobile-first prediction market for ETH Zurich students, with VIS as the LMSR market maker. Users trade YES/NO shares using free vouchers; a winning share pays 1 voucher, which VIS redeems for a coffee.

Objectives, in priority order:

1. Correct pricing and accounting: no credit is created or destroyed outside an audited ledger entry.
2. Bounded VIS risk: per market, per user and in aggregate.
3. A smooth trading flow on a phone.
4. The smallest architecture that achieves 1 to 3.

Non-goals: buying vouchers with money or any other real payment, native apps, ETH SSO, multi-outcome markets, order books, limit orders and short selling.

## 2. Vouchers and voucher codes

There is one unit everywhere: 1 voucher = 1 coffee-equivalent = 1 credit (CR). The trading balance is the voucher balance, stored as BIGINT micro-units (1 CR = 1,000,000 µCR).

Credits enter only through these three sources, each booked as a ledger entry from `vis:issuance` to the user's balance:

| Source | Amount | Idempotency |
| --- | --- | --- |
| Signup grant | 5 CR when email verification succeeds | Unique `voucher_grants(user_id, 'SIGNUP')` |
| Monthly grant | 1 CR per calendar month (Europe/Zurich), from the month after signup | Unique `voucher_grants(user_id, 'YYYY-MM')` |
| Voucher code | Set by VIS per batch, 1 to 5 CR (default 1) | Code row locked; `redeemed_by` set once |

Monthly grants are created lazily on the user's next authenticated request with `INSERT … ON CONFLICT DO NOTHING`, so no cron job is needed. An admin endpoint can also grant all owed months at once.

### Voucher codes

VIS gives vouchers away by handing out codes, for example at the first-semester apéro, as quiz prizes or as a reward for good market proposals. Codes are never sold: a sold voucher would count as a paid stake under Swiss gambling law.

Creating codes (admin):

1. Create a batch with a label, the number of codes, the value per code, an expiry date and an optional rule of one redemption per user per batch.
2. The server generates each code as 12 characters of Crockford Base32 from `secrets` (60 bits), shown as `XXXX-XXXX-XXXX`. Only the SHA-256 hash is stored.
3. The plain codes are returned exactly once, as a CSV and a printable QR sheet. They cannot be shown again.
4. The batch's total value counts against `MAX_CODE_ISSUANCE_PER_MONTH` and the outstanding-obligation metric.

Redeeming a code (user):

1. The user types the code or scans its QR on the Wallet screen. The server normalizes it: uppercase, dashes removed, O read as 0, I and L read as 1.
2. In one transaction: `SELECT … FROM voucher_codes WHERE code_hash = $1 FOR UPDATE`. Reject if the code is not ACTIVE, has expired, was revoked, or the user hit the batch limit.
3. Set status REDEEMED with `redeemed_by` and `redeemed_at`, book `vis:issuance → user`, commit.
4. Unknown, used, expired and revoked codes all get the same generic error, so codes cannot be probed.
5. Rate limit via Redis: 5 attempts per user per 10 minutes and 20 per IP per hour. If Redis is down, redemption fails closed with 503.

Admins can revoke an unredeemed code or a whole batch. Codes already redeemed are not affected.

### Coffee tickets

A user turns 1 CR into a single-use coffee ticket (QR plus 8-character code): the user is debited and `vis:redemption_pending` credited. A barista scans it and marks it REDEEMED, which moves the credit to `vis:redeemed`; a second scan is rejected. Unused tickets expire after 30 days and are refunded by a compensating entry. Only whole vouchers become tickets; fractional balances stay tradeable.

## 3. Authentication and eligibility

Login is a passwordless magic link sent to an allowlisted ETH address; an account becomes ACTIVE only after that link is confirmed.

- **Allowlist:** `ALLOWED_EMAIL_DOMAINS=student.ethz.ch,ethz.ch` by default, exact match, configured server-side.
- **Normalization:** trim, NFKC, lowercase; reject `+` tags and quoted local parts. `x@student.ethz.ch` and `x@ethz.ch` map to one canonical identity `x@ethz.ch` with a unique index, so the 5-voucher signup grant cannot be claimed twice.
- **Token:** 32 random bytes, stored as a SHA-256 hash, valid 15 minutes, single use, consumed in the same transaction that creates the session.
- **Confirm step:** the link opens a page with a "Confirm sign-in" button that POSTs the token. A GET never consumes it, because mail scanners open links automatically.
- **No account probing:** "request link" always answers 202, whether or not the address exists.
- **Rate limits (Redis):** 3 links per email per 15 minutes, 20 per IP per hour, 10 verify attempts per IP per minute. Login endpoints fail closed if Redis is down.
- **Session:** opaque ID in an HttpOnly, Secure, SameSite=Lax cookie. The row lives in Postgres and is cached in Redis; it expires after 30 days, or 7 days idle. State-changing requests check the Origin header.
- **Roles:** user; admin (markets and voucher codes); barista (coffee tickets only). Admins are bootstrapped via `ADMIN_EMAILS`.
- **Mail:** local development sends to Mailpit. Do one real SMTP test before the demo.

## 4. LMSR market maker and stake limit

Each user may have at most 1 voucher at stake per market. Prices come from a binary LMSR with a default b of 10, so a full 1 CR bet moves the price from 50% to about 55%.

### Stake limit

- A user's net stake in a market = the sum of their buy costs minus the sum of their sell proceeds in that market, floored at 0.
- A buy is rejected with `LIMIT_EXCEEDED` if the net stake after the trade would exceed `MAX_STAKE_PER_USER_MARKET` (default 1 CR = 1,000,000 µCR).
- Selling is always allowed and lowers the net stake, so a user can sell and buy again up to 1 CR net.
- The trade sheet offers 0.25, 0.5 and 1 CR and shows how much stake is left in this market.

### Formulas

All functions live in one pure module, `pricing/lmsr.py`, with no I/O. Markets start at q\_yes = q\_no = 0, i.e. 50/50, with no hidden liability.

Cost, with log-sum-exp shifted by its maximum so no exponential overflows:

```latex
C(q) = b \cdot \ln\left(e^{q_{yes}/b} + e^{q_{no}/b}\right)
```

Price, computed as a complement so the two always sum to exactly 1:

```latex
p_{yes} = \frac{1}{1 + e^{(q_{no} - q_{yes})/b}}, \qquad p_{no} = 1 - p_{yes}
```

Shares x bought for a budget c, where p is the current price of the outcome bought:

```latex
x = b \left[ \ln\left(\operatorname{expm1}(c/b) + p\right) - \ln p \right]
```

Proceeds from selling s shares, the exact reverse transition:

```latex
\text{proceeds} = -b \cdot \operatorname{log1p}\left(p \cdot \operatorname{expm1}(-s/b)\right)
```

Worst-case market-maker loss per market:

```latex
L_{max} = b \ln 2
```

Users cannot sell more than they hold; short positions are rejected.

### Choosing b

The hard rule: b = budget / ln 2, rounded down to micro-units, with budget ≤ 1,000 CR. With stakes capped at 1 CR, small b values are the useful ones:

| Tier | b | Loss bound b·ln 2 | Shares for 1 CR at 50% | Price after |
| --- | --- | --- | --- | --- |
| Tight | 5 | 3.47 CR | 1.83 | 59.1% |
| Default | 10 | 6.93 CR | 1.91 | 54.8% |
| Medium | 20 | 13.86 CR | 1.95 | 52.4% |
| Maximum allowed | 1,442.695 | 1,000 CR | 2.00 | 50.03% |

### Precision and rounding

- Credits and shares are BIGINT micro-units; no floats are stored.
- Math uses `decimal.Decimal` with precision 50 and converts to integers at the edges.
- Rounding always favours the market maker: costs round up, proceeds round down, and shares bought for a budget round down. The cost is then recomputed from the rounded shares, rounded up and asserted ≤ c.
- Minimum trade: 0.01 CR or 0.01 shares. Displayed prices are rounded to 0.1 percentage points.

## 5. Risk controls

At the default b, VIS can lose at most 6.93 CR per market, so 100 open markets risk at most 693 CR instead of the 100,000 CR the 1,000-per-market ceiling would allow.

Per market: the budget is at most 1,000 CR and b is derived from it. Both are immutable while the market is OPEN. Changing them requires HALT, an admin migration, a check that b·ln 2 ≤ budget and an audit-log entry. Existing positions are never repriced.

| Control | Default | Checked at |
| --- | --- | --- |
| `MAX_STAKE_PER_USER_MARKET` | 1 CR | Every buy |
| `MAX_OPEN_MARKETS` | 100 | Approve |
| `MAX_MARKET_BUDGET` | 1,000 CR | Approve |
| `MAX_AGGREGATE_LOSS_BUDGET` (sum of b·ln 2 over OPEN and HALTED markets) | 700 CR | Approve |
| `MAX_CODE_ISSUANCE_PER_MONTH` | 200 CR | Code batch creation |
| `MAX_ORDERS_PER_MINUTE` | 10 per user | Every trade (Redis) |
| `MAX_MARKET_LIFETIME` | 90 days | Approve |
| Minimum trade | 0.01 CR | Every trade |

**Outstanding obligations** = all user balances + pending coffee tickets + for each open market max(q\_yes, q\_no) − that market's account balance. This is the most coffee VIS could owe right now. It is shown on the admin risk page, with an alert above a configurable threshold.

**Circuit breaker:** after every trade, inside the transaction, check that the ledger nets to zero, positions sum to the market's q, and no user balance is negative. On failure: roll back, set the market to HALTED in a separate transaction, and write an `alerts` row shown to admins.

## 6. Market lifecycle

A market is proposed by a user, opened by an admin, closes at its deadline and is resolved exactly once, either to an outcome or to VOID.

&#91;embedded content: market states · 7 states, 8 transitions\]

Admins can halt an open market and resume it; a halted market is closed before it is resolved or voided.

- **Proposal (any active user):** question; what counts as YES and NO; an objective resolution criterion; a named source or procedure; a trading close time; a resolution deadline at most 14 days after close; a requested tier. Limit: 3 proposals per user per day.
- **Approval (admin):** may edit the wording, picks the tier (which sets budget and b) and passes the aggregate caps. Approving opens the market in one atomic step.
- **Close:** trading stops at `close_at`, checked on every trade, so no cron job is needed. Admins can HALT and RESUME at any time.
- **Resolve (admin, from CLOSED; a halted market is closed first):** in one transaction, lock the market and insert a `resolutions` row; its unique `market_id` makes a second resolution impossible. Pay 1 CR per winning share from the market account to holders, book the remainder to `vis:mm_pnl`, set status RESOLVED.
- **VOID:** each user gets back their net stake in that market (at most 1 CR). Positions are zeroed and the difference is booked to `vis:mm_pnl`. The rule is shown on the market page before anyone trades.
- **Content rules:** no questions about named people's private matters, identifiable people's grades or outcomes a trader can decide alone. No party-political or religious questions, as VSETH requires Fachvereine to stay neutral there. Proposers and resolvers may not trade their own market.

## 7. Data model (PostgreSQL, Alembic)

Postgres holds every balance, position and ledger entry; all amounts are BIGINT micro-units.

| Table | Key columns |
| --- | --- |
| `users` | id, canonical\_email (unique), email, role, status, created\_at |
| `login_tokens` | id, email, token\_hash (unique), expires\_at, consumed\_at |
| `sessions` | id, user\_id, expires\_at, last\_seen\_at |
| `markets` | id (UUID), question, yes\_def, no\_def, resolution\_criteria, resolution\_source, status, budget\_micro, b\_micro, q\_yes\_micro, q\_no\_micro, version, close\_at, resolve\_by, created\_by, approved\_by |
| `positions` | (user\_id, market\_id, outcome) PK, shares\_micro, cost\_micro, proceeds\_micro |
| `trades` | id, market\_id, user\_id, side, outcome, shares\_micro, amount\_micro, p\_before, p\_after, market\_version, created\_at |
| `idempotency_keys` | (user\_id, key) PK, request\_hash, response\_json, created\_at |
| `accounts` | id, kind, owner\_id, market\_id, balance\_micro; CHECK balance ≥ 0 for user accounts |
| `journal_entries` | id, kind, ref\_type, ref\_id, idempotency\_key (unique), created\_at |
| `postings` | entry\_id, account\_id, amount\_micro; each entry sums to 0 (deferred constraint trigger) |
| `voucher_grants` | (user\_id, period) unique, entry\_id; period = 'SIGNUP' or 'YYYY-MM' |
| `voucher_code_batches` | id, label, value\_micro, count, expires\_at, one\_per\_user, created\_by, revoked\_at |
| `voucher_codes` | id, batch\_id, code\_hash (unique), status, redeemed\_by, redeemed\_at, entry\_id |
| `coffee_tickets` | id, user\_id, code\_hash (unique), status, created\_at, redeemed\_at, redeemed\_by |
| `resolutions` | market\_id (unique), outcome, evidence, resolved\_by, created\_at |
| `outbox` | id (BIGSERIAL), topic, payload JSONB, created\_at, published\_at |
| `audit_log` | id, actor\_id, action, target, before, after, created\_at |
| `alerts` | id, severity, message, context, created\_at, acked\_by |

A user's net stake in a market is `cost_micro − proceeds_micro` summed over both outcomes. `journal_entries` and `postings` are append-only: the app role has no UPDATE or DELETE on them.

System accounts: `vis:issuance` (source of all granted and coded vouchers, may go negative), `vis:mm_pnl`, `vis:redemption_pending`, `vis:redeemed`, and `market:{id}` (may go down to −budget).

## 8. Trade transaction

Every trade is one Postgres transaction that locks the market row first, so concurrent trades on a market run one after another on fresh quantities.

`POST /api/markets/{id}/trades` with header `Idempotency-Key: <uuid>` and body `{side, outcome, amount_cr | shares, max_cost_cr | min_proceeds_cr, quote_version}`:

1. Authenticate, validate with Pydantic (strict decimals, at most 6 places) and apply the Redis rate limit.
2. `BEGIN` (READ COMMITTED). Insert the idempotency key. Same key and same request hash → return the stored response; different hash → 422.
3. `SELECT … FROM markets WHERE id = $1 FOR UPDATE`, then lock the user's account. Always in this order (market, then user) to avoid deadlocks.
4. Check status OPEN and `now < close_at`. Compute cost or proceeds from the locked q with `pricing/lmsr.py`.
5. Slippage guard: cost above `max_cost` or proceeds below `min_proceeds` → `409 QUOTE_STALE` with a fresh quote.
6. For buys, check the 1 CR stake limit, the user's balance and the market's limits.
7. Write the journal entry and postings; upsert the position; insert the trade; update `q_yes`, `q_no` and `version + 1`; insert an `outbox` row `quote.updated`.
8. Check the invariants, store the response on the idempotency row, `COMMIT`.
9. After commit, best effort: delete the market's Redis cache key and publish the new quote on the Redis channel `market:{id}`.

A row lock is simpler than SERIALIZABLE here, because every trade on a market depends on that one row anyway, and it needs no retry loop.

## 9. Infrastructure

The stack: Nginx in front, Next.js and FastAPI behind it, Postgres as the only source of truth, Redis for speed and limits, ScyllaDB for price history. One `docker compose up` starts nginx, web, api, relay, postgres, redis, scylla and mailpit.

&#91;embedded content: service architecture · 7 services\]

If Redis or ScyllaDB fails, trades still commit in Postgres; only speed, limits and charts degrade.

### Nginx

- `/` → `web:3000`, `/api/` → `api:8000`. One origin for both, so cookies need no CORS.
- `/api/markets/*/stream` (server-sent events) with `proxy_buffering off` and a long read timeout.
- An outer `limit_req` zone per IP as a second layer behind the app's Redis limits; `client_max_body_size 64k`; gzip; security headers (HSTS when on TLS, `X-Content-Type-Options`, `Referrer-Policy`, a basic CSP).

### Redis

- **Rate limits:** fixed windows with `INCR` + `EXPIRE` for login, code redemption and trades.
- **Cache:** market list and market summaries with a 5-second TTL, deleted after every committed trade.
- **Sessions:** a read-through cache in front of the Postgres `sessions` table.
- **Live quotes:** after commit the API publishes to `market:{id}`; the SSE endpoint subscribes and pushes the new price to open phones.
- Redis never decides a balance, a price or whether a trade is allowed.

### ScyllaDB

Single node: `scylladb/scylla` with `--smp 1 --memory 750M --overprovisioned 1 --developer-mode 1`, a `cqlsh` healthcheck, keyspace `SimpleStrategy` with replication factor 1. The API starts and trades without it.

```sql
CREATE TABLE quotes_by_market_day (
  market_id uuid, day date, ts timestamp, version bigint,
  p_yes_micro bigint, q_yes_micro bigint, q_no_micro bigint, trade_id uuid,
  PRIMARY KEY ((market_id, day), ts, version)
) WITH CLUSTERING ORDER BY (ts DESC, version DESC)
  AND compaction = {'class':'TimeWindowCompactionStrategy','compaction_window_unit':'DAYS','compaction_window_size':1};
```

- The relay is its own process using `scylla-driver`. In a loop it takes up to 100 unpublished `outbox` rows with `FOR UPDATE SKIP LOCKED`, writes them to Scylla, sets `published_at` and commits. If Scylla is down nothing is marked, and the rows are retried later.
- Writes are upserts keyed by `version`, so delivering a row twice is harmless.
- `GET /api/markets/{id}/history` reads Scylla and falls back to the Postgres `trades` table when Scylla is down or behind.

### When a dependency fails

| Down | Trading | Effect |
| --- | --- | --- |
| ScyllaDB | Works | Charts fall back to Postgres; the outbox backs up and drains once Scylla is back |
| Redis | Works | Login and code redemption fail closed (503); trade rate limit falls back to in-process; cache bypassed; live updates fall back to 10-second polling |
| Postgres | Stops | Everything returns 503; nothing financial is decided anywhere else |

## 10. API

FastAPI with Pydantic models; the OpenAPI schema generates the TypeScript client. All paths sit under `/api`.

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/auth/request-link` | `{email}`, always 202 |
| POST | `/auth/verify` | `{token}`, sets the session cookie, books the signup grant once |
| POST | `/auth/logout` |  |
| GET | `/me` | Profile, balance, next monthly grant date |
| GET | `/markets?status=` | List with p\_yes and volume |
| GET | `/markets/{id}` | Details, rules, deadline, price, own position and stake left |
| GET | `/markets/{id}/quote` | Query `side, outcome, amount_cr or shares`; returns cost, shares, average price, p\_after, quote\_version |
| POST | `/markets/{id}/trades` | Idempotent trade (section 8) |
| GET | `/markets/{id}/history` | Chart data, Scylla with Postgres fallback |
| GET | `/markets/{id}/stream` | SSE quote updates via Redis pub/sub |
| GET | `/me/positions`, `/me/trades`, `/me/ledger` |  |
| POST | `/markets` | Proposal |
| POST | `/vouchers/redeem-code` | `{code}`; credits the code's value |
| POST | `/coffee-tickets` | Turns 1 CR into a coffee ticket |
| POST | `/admin/markets/{id}/approve`, `reject`, `halt`, `resume`, `close`, `resolve`, `void` | Audit-logged |
| POST | `/admin/code-batches` | Creates a batch; returns plain codes once |
| POST | `/admin/code-batches/{id}/revoke` | Revokes unredeemed codes |
| GET | `/admin/risk` | Open markets, summed loss budgets, outstanding obligations, alerts |
| POST | `/barista/coffee-tickets/{code}/redeem` |  |

Errors share one shape, `{code, message, details}`, with codes `INSUFFICIENT_BALANCE`, `QUOTE_STALE`, `MARKET_NOT_OPEN`, `LIMIT_EXCEEDED`, `TRADE_TOO_SMALL`, `CODE_INVALID`, `RATE_LIMITED`.

## 11. Frontend

Next.js with TypeScript, mobile-first; the frontend only shows numbers the server returns and never computes a price or balance itself.

| Screen | Contents |
| --- | --- |
| Login | Email field → "check your inbox" → Confirm button |
| Markets | Cards with question, YES %, close date |
| Market detail | Price, live chart, rules, deadline, own position, stake left (of 1 CR) |
| Trade sheet | Bottom sheet: YES/NO toggle, 0.25 / 0.5 / 1 CR buttons, live quote (300 ms debounce) showing shares, average price and new probability, Confirm |
| Portfolio | Open positions, value at current price, resolved results |
| Wallet | Balance, ledger, "Redeem code" (typed or camera QR), "Get a coffee" ticket QR |
| Propose market | Form with all required fields |
| Admin | Approve and resolve markets, create and revoke code batches, risk panel |
| Barista | Camera scanner for coffee tickets |

Tap targets are at least 44 px. After `QUOTE_STALE` the sheet shows the new quote and asks to confirm again. The Confirm button disables itself and reuses one Idempotency-Key until a response arrives.

## 12. Tests and acceptance criteria

The demo is ready when the eight acceptance criteria below pass on a phone and the test suite is green.

### Tests (Hypothesis for property tests, Testcontainers for real Postgres)

- **Pricing:** p\_yes + p\_no = 1; start at 50/50; buying YES strictly raises p\_yes; cost equals the difference in C; shares bought for c never cost more than c; buy then sell never yields a profit; random sequences never push the market maker's loss above b·ln 2; |q|/b = 50 does not overflow.
- **Stake limit:** a buy that would take net stake above 1 CR is rejected; sell then buy again up to 1 CR net is allowed.
- **Accounting:** every journal entry sums to 0; all accounts sum to 0; market q equals the sum of positions; settlement pays exactly the winning shares; VOID refunds equal net stakes.
- **Concurrency:** 50 parallel trades on one market end consistent; one Idempotency-Key sent 10 times gives one trade.
- **Voucher codes:** the same code redeemed by 2 users in parallel succeeds once; expired, revoked and used codes fail with the same error; the one-per-user batch rule holds; the 6th attempt within 10 minutes is rate-limited; a batch above the monthly issuance cap is refused.
- **Grants:** the signup grant books once; a repeated monthly grant inserts nothing; the student./bare-domain alias does not create a second account.
- **Lifecycle and auth:** trading while HALTED or CLOSED fails; a second resolution fails; non-allowlisted domains, reused and expired tokens are rejected; non-admins get 403 on admin routes.
- **Failures:** with Scylla stopped trades succeed and the chart catches up after restart; with Redis stopped trades succeed and code redemption returns 503.

### Acceptance criteria

- [ ] A new `@student.ethz.ch` user logs in through a magic link in under a minute and sees 5 CR.
- [ ] Redeeming a VIS code raises the balance by the code's value; redeeming it again fails.
- [ ] Buying YES for 1 CR confirms at the quoted cost and moves the price from 50% to about 55%.
- [ ] A further buy in the same market is rejected with the stake limit; selling half returns the exact reverse cost, rounded down.
- [ ] A double tap on Confirm creates exactly one trade.
- [ ] Resolving pays winners exactly their winning shares; a second resolution is rejected.
- [ ] Stopping Scylla mid-demo leaves trading working; the chart backfills after restart.
- [ ] `/admin/risk` shows open markets, summed loss budgets against the cap and outstanding obligations.

## 13. Demo script (about 4 minutes)

The demo shows free vouchers coming in, a bet moving the price, VIS's risk staying bounded and trading surviving a database outage.

1. On a phone, log in with a student address; the magic link arrives in Mailpit on the projector. Balance: 5 CR.
2. Hand a judge a printed code card. They scan it in the Wallet: 6 CR.
3. Open "Will the Polyterrasse Mensa serve pizza on Friday?" (b = 10). Buy YES for 1 CR: the price moves from 50% to about 55%. Try to buy more: the stake limit message appears.
4. A second phone buys NO; the first phone's price and chart update live.
5. Run `docker stop scylla`, trade again, show it succeeds; restart it and the chart catches up.
6. Admin view: risk panel (6.93 CR worst case per market, aggregate within the cap), then resolve YES and show the payouts in the ledger.
7. Wallet: "Get a coffee" shows a ticket QR; the barista view redeems it, and a second scan is rejected.
8. Closing line: free to play, 1 voucher per bet, every credit accounted for.

## 14. 42-hour plan

The must-haves take about 96 of roughly 120 productive person-hours for a team of 4, which leaves room for a few should-haves before the feature freeze at hour 30.

Roles: **A** pricing and ledger, **B** API, auth and codes, **C** frontend, **D** infrastructure, tests and demo.

| Item | Priority | Owner | Hours |
| --- | --- | --- | --- |
| Compose (nginx, web, api, relay, postgres, redis, scylla, mailpit), Alembic baseline, CI | Must | D | 5 |
| `lmsr.py` with Decimal, closed forms, rounding, property tests | Must | A | 7 |
| Ledger, zero-sum trigger, signup and monthly grants | Must | A | 6 |
| Trade transaction with locks, idempotency, stake limit, outbox; concurrency test | Must | A | 8 |
| Magic-link auth, sessions, Redis rate limits | Must | B | 7 |
| Market proposal, approve, halt, close, resolve, VOID, audit log | Must | B | 7 |
| Voucher code batches, redeem, revoke, tests | Must | B | 5 |
| Quote, trade and me endpoints; OpenAPI → TS client | Must | B | 3 |
| Login, markets, detail, trade sheet, portfolio | Must | C | 16 |
| Wallet with code redemption; admin code batches | Must | C | 4 |
| Admin page: approve, resolve, risk panel | Must | C | 3 |
| Outbox relay → Scylla, history with Postgres fallback | Must | D | 6 |
| Redis cache, pub/sub and SSE stream | Must | D | 4 |
| Nginx routes incl. SSE, limits, headers | Must | D | 2 |
| Seed data, README, three demo rehearsals | Must | D | 5 |
| Integration and bug-fix buffer | Must | All | 8 |
| Coffee tickets and barista scanner | Should | B, C | 4 |
| Printable QR sheet for code batches | Should | C | 2 |
| Stop-loss auto-halt | Should | A | 2 |
| Leaderboard | Should | C | 2 |

&#91;embedded content: 42-hour timeline · 6 phases, 2 gates\]

The hour-12 checkpoint catches integration problems early; after hour 30 only bug fixes and small should-haves go in.

Out of scope: buying vouchers and any payment, password login, ETH SSO, multi-outcome markets, Scylla rollups, TTLs and multi-node setups.

## 15. Open decisions and legal note

Every voucher is free, which keeps MarkETH a free game. This is not legal advice: VIS should have it confirmed before a public launch.

**Legal note.** Gespa treats prediction markets as betting, and Swiss law only allows licensed bets on sports. The Federal Office of Justice states that free games without any stake fall outside the Gambling Act. That only holds while vouchers and codes are never sold, never tied to a paid membership and never given in exchange for a purchase.

Open decisions for the VIS board:

- [ ] Is the free-game reading confirmed by a lawyer or Gespa?
- [ ] Value of `MAX_AGGREGATE_LOSS_BUDGET` (default 700 CR) and the launch value of `MAX_OPEN_MARKETS`.
- [ ] Value of `MAX_CODE_ISSUANCE_PER_MONTH` (default 200 CR) and who may create code batches.
- [ ] Does a user get the monthly voucher for the month they sign up in? Do vouchers expire?
- [ ] Students only, or staff and PhD students too? `ethz.ch` also covers employees.
- [ ] Who pays for realized losses and coffees, and what does one coffee cost VIS?
- [ ] Hosting (ETH or external) and a data-protection notice under the revDSG.
- [ ] Who resolves markets, and is there an appeal?
