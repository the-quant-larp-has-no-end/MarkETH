# MarkETH

A mobile-first prediction market for ETH Zurich students. Users trade YES/NO shares with free vouchers against an LMSR market maker run by VIS; a winning share pays 1 voucher, redeemable for a coffee. No real money is involved anywhere.

`docs/SPEC.md` is the source of truth for behaviour. Cite it as "SPEC §N" in docstrings and commit messages. Its Appendix A records the decisions taken while building; read it before changing behaviour.

## Map

| Path | What lives there |
| --- | --- |
| `docs/SPEC.md` | The engineering spec (§1 to §12) and Appendix A (build decisions) |
| `.env`, `.example.env` | Every tunable of the system. Identical keys; `.env` is gitignored |
| `compose.yaml`, `compose.override.yaml` | The whole stack; the override adds hot reload and published ports |
| `infra/nginx/` | Reverse proxy template |
| `backend/` | FastAPI API and the outbox relay. Rules in `backend/CLAUDE.md` |
| `frontend/` | Next.js app. Rules in `frontend/CLAUDE.md`, design system in `frontend/DESIGN.md` |
| `Makefile` | The commands below |

## Commands

Run from the repository root.

| Command | Does |
| --- | --- |
| `make up` / `make down` / `make logs` | Start, stop, tail the stack (app at http://localhost:8080, mail at http://localhost:8025) |
| `make migrate` / `make seed` | Apply migrations / load demo data |
| `make test` | Backend and frontend tests (`make test-backend`, `make test-frontend`, `make test-scylla`) |
| `make lint` / `make fmt` | Check / fix style, types and architecture rules |
| `make api-client` | Export `backend/openapi.json` and regenerate the frontend's API types |
| `make psql` / `make redis-cli` / `make cqlsh` | Shells into the data stores |
| `make reset` | Stop the stack and delete its volumes |

## Golden rules

These protect SPEC §1: no credit is created or destroyed outside an audited ledger entry.

1. **Money is integer micro-units.** 1 CR = 1,000,000 µCR, stored as BIGINT. Never a float, never in the database, never in a computation. Names ending in `_micro` are integers; names ending in `_cr` are decimals at the API or `.env` boundary.
2. **Every credit movement is a journal entry** whose postings sum to zero, written by `LedgerService` and nothing else. Journal entries and postings are append-only.
3. **Postgres decides.** Redis and ScyllaDB make things faster or prettier; they never decide a balance, a price, or whether a trade is allowed. If either is down, trades still commit.
4. **Lock order: market row, then user account.** Every transaction that touches a market locks the market row first.
5. **Rounding favours the market maker.** Costs round up, proceeds round down, shares bought round down.
6. **The frontend never computes a price, balance or limit.** It formats what the API returns.
7. **Every tunable is in `.env`.** No defaults in code. Adding one means editing `.env`, `.example.env` and `Settings` together.
8. **Async I/O only.** No blocking database, Redis, Scylla or SMTP call on the event loop.
9. **Vouchers and codes are never sold.** Do not add any payment or purchase flow (SPEC §2).

## Where does this go?

| I need to... | Put it in |
| --- | --- |
| Add or change an endpoint | `backend/app/api/routes/`, with its schemas in `backend/app/schemas/` |
| Add a business rule | A class in `backend/app/services/` |
| Add a query | The matching class in `backend/app/repositories/` |
| Add a table or column | `backend/app/models/` plus an Alembic migration |
| Change pricing maths | `backend/app/pricing/lmsr.py` only |
| Add a setting | `.env`, `.example.env`, `backend/app/core/config.py` |
| Add a screen | A thin page in `frontend/src/app/`, logic in `frontend/src/features/` |
| Change a colour, font or radius | `frontend/DESIGN.md` and the tokens in `frontend/src/app/globals.css` |

## Working agreements

- Finish with `make lint` and `make test` green.
- A behaviour change updates the test that covers it; a new rule gets a test.
- Do not commit or push unless asked.
