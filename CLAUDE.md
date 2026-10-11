# MarkETH

A mobile-first prediction market for ETH Zurich students. Users trade YES/NO shares with free tokens against an LMSR market maker run by VIS; a winning share pays 1 token, and tokens buy vouchers for products at the bar. No real money is involved anywhere. The app runs behind the VIScon portal, which signs every visitor in and tells the API who is calling.

`docs/SPEC.md` is the source of truth for behaviour. Cite it as "SPEC §N" in docstrings and commit messages. Its Appendix A records the decisions behind the build; read it before changing behaviour.

## Map

| Path | What lives there |
| --- | --- |
| `docs/SPEC.md` | The engineering spec (§1 to §12) and Appendix A (build decisions) |
| `docs/SERVER_INFRASTRUCTURE.md` | The VM, the portal and the firewall in front of it, and how to deploy |
| `.env`, `.example.env` | Every tunable of the system. Identical keys; `.env` is gitignored |
| `compose.yaml`, `compose.dev.yaml` | The whole stack. `compose.yaml` alone is what is deployed; the overlay, which `make` merges in, adds hot reload, published ports and the development identity |
| `infra/nginx/` | Reverse proxy template, and in `infra/nginx/dev/` the stand-in for the portal's sign-in |
| `infra/deploy/` | What the deployed stack sets differently, and the backup, check and smoke scripts |
| `.github/workflows/` | `ci.yml`, the checks of every update of main, and `deploy.yml`, which has the VM deploy itself. Both run on the team's runner, which is the VM, and hold no secret |
| `backend/` | FastAPI API, the outbox relay and the scripts. Rules in `backend/CLAUDE.md` |
| `backend/mediabox/` | The media box: the supervisor in front of ffmpeg that turns what a broadcaster sends into the live stream people watch. An image of its own, the service `media` of `compose.yaml`; standard library only, and nothing of `backend/app` |
| `frontend/` | Next.js app. Rules in `frontend/CLAUDE.md`, design system in `frontend/DESIGN.md` |
| `Makefile` | The commands below |

## Commands

Run from the repository root. `make` alone lists them.

| Command | Does |
| --- | --- |
| `make up` / `make down` / `make logs` | Start, stop, tail the stack (app at http://localhost:8080, who is calling at http://localhost:8080/__dev/identity) |
| `make migrate` / `make seed` | Take the database to the schema of this code and check it / load demo data |
| `make revision NAME="..."` | Write the file of a new schema revision, after the models are edited |
| `make test` | Backend and frontend tests (`make test-backend`, `make test-frontend`, `make test-scylla`) |
| `make test-media` | The image test of the media box: builds its image and runs it as the stack does (needs Docker). No part of `make test` |
| `make e2e` | The browser suite of `frontend/e2e/`, in Chrome against the stack that is up and seeded |
| `make lint` / `make fmt` | Check / fix style, types and architecture rules |
| `make api-client` | Export `backend/openapi.json` and regenerate the frontend's API types |
| `make psql` / `make redis-cli` / `make cqlsh` | Shells into the data stores |
| `make reset` | Stop the stack and delete its volumes |
| `make deploy` / `make deploy-check` | Deploy to the VM / check the stack that runs there |
| `make deploy-status` / `make deploy-logs` | Containers, memory and disk on the VM / tail its logs |
| `make deploy-smoke` / `make deploy-wipe` / `make deploy-rollback` | Walk an empty database on the VM / dump and delete its databases / go back to the organizers' template |

`make deploy` runs `deploy-backup`, `deploy-sync`, `deploy-env`, `deploy-build`, `deploy-pull`, `deploy-migrate`, `deploy-stop-template`, `deploy-up` and `deploy-check` in that order; `docs/SERVER_INFRASTRUCTURE.md` §14 walks through them. The Deploy workflow runs the same targets on the VM itself (`make deploy DEPLOY_LOCAL=1`).

## Golden rules

These protect SPEC §1: no credit is created or destroyed outside an audited ledger entry.

1. **Money is integer micro-units.** 1 CR = 1,000,000 µCR, stored as BIGINT. Never a float, never in the database, never in a computation. Names ending in `_micro` are integers; names ending in `_cr` are decimals at the API or `.env` boundary.
2. **Every credit movement is a journal entry** whose postings sum to zero, written by `LedgerService` and nothing else. Journal entries and postings are append-only.
3. **Postgres decides.** Redis and ScyllaDB make things faster or prettier; they never decide a balance, a price, or whether a trade is allowed. If either is down, trades still commit.
4. **Lock order: market row, then user account.** Every transaction that touches a market locks the market row first.
5. **Rounding favours the market maker.** Costs round up, proceeds round down, shares bought round down.
6. **The frontend never computes a price, balance or limit.** It formats what the API returns.
7. **Every tunable is in `.env`.** No defaults in code. Adding one means editing `.env`, `.example.env` and `Settings` together.
8. **Async I/O only.** No blocking database, Redis or ScyllaDB call on the event loop.
9. **Vouchers and codes are never sold.** Do not add any payment or purchase flow (SPEC §2).

Two things follow from where the app runs (SPEC §3):

- **A credit is a "token" on screen** and a credit (`*_cr`, µCR) in the code, the API and the database.
- **The portal is the sign-in.** The API believes the two request headers in which the portal names the caller, and reads them in one place, `authenticate_request`. Do not add a sign-in or a development bypass to the backend: on a laptop the proxy names the caller (`infra/nginx/dev/`).

The live stream of a market and the live chat beside it (SPEC §6, §9, A.12) keep to all of these, and to three things of their own:

- **Only HTTP reaches the app.** The portal carries HTTP alone, without WebSockets, and the firewall lets in nothing beside it and SSH. So video goes up as requests of about a second and comes down as files a browser downloads (HLS).
- **The media box makes video and decides nothing.** Who may send and who may watch is the API's answer. The box is isolated (no network, no secret, a read-only root); if it is down, trades still commit and a live stream does not start.
- **Nothing of a stream or a chat is kept.** No picture and no sound reaches a disk, and the chat of a live stream is deleted when the stream ends. No credit moves in either.

## Where does this go?

| I need to... | Put it in |
| --- | --- |
| Add or change an endpoint | `backend/app/api/routes/`, with its schemas in `backend/app/schemas/`; then `make api-client` |
| Add a business rule | A class in `backend/app/services/` |
| Add a query | The matching class in `backend/app/repositories/` |
| Add a table or column | `backend/app/models/`, then `make revision NAME="..."` and the steps in the file it writes under `backend/migrations/versions/`. A database is changed by revisions alone and never wiped for its schema; `0001_baseline.py` is a record and is not edited |
| Change pricing maths | `backend/app/pricing/lmsr.py` only |
| Add a setting | `.env`, `.example.env`, `backend/app/core/config.py` |
| Add a product or change what one takes | `VOUCHER_PRODUCTS` in `.env` and `.example.env` |
| Change the proxy | `infra/nginx/conf.d/default.conf.template` |
| Change what the deployed stack sets differently | `infra/deploy/env.production` |
| Add a screen | `frontend/DESIGN.md` first, then a thin page in `frontend/src/app/`, logic in `frontend/src/features/` |
| Change a colour, font or radius | `frontend/DESIGN.md` and the tokens in `frontend/src/app/globals.css` |
| Change what a live stream or its chat may do | `backend/app/services/broadcasts.py` and `chat.py`; the screens are `frontend/src/features/broadcast`, `broadcast-studio` and `chat` |
| Change how video is made | `backend/mediabox/`, and the `BROADCAST_*` and `MEDIA_*` settings; then `make test-media` |

## Working agreements

- Finish with `make lint` and `make test` green.
- A behaviour change updates the test that covers it and the section of `docs/SPEC.md` that states it; a rule that is added gets a test.
- Describe what is: code, comments and documents are written in the present tense.
- Do not commit or push unless asked.
