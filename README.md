# MarkETH

A mobile-first prediction market for ETH Zurich students.

Users trade YES/NO shares on questions about student life with free tokens. VIS is the counterparty of every trade, as an LMSR market maker. A winning share pays 1 token, and tokens buy vouchers for products at the bar: a coffee, a drink, a snack. No real money is involved anywhere.

## How it works

- **Tokens are free.** A user gets them on sign-up, every month, and from codes that VIS hands out. Tokens, codes and vouchers are never sold.
- **Markets are binary.** Each market is a question that resolves YES or NO. The price of a share is the market's current estimate of the probability.
- **Every token is accounted for.** Each movement is an entry in an append-only, double-entry ledger, so no token is created or destroyed outside an audited entry.
- **The risk of VIS is bounded** per market, per user and in aggregate.
- **The VIScon portal is the sign-in.** The app runs behind it and has no sign-in of its own.

## Stack

Nginx in front, a Next.js app and a FastAPI API behind it, and three data stores: Postgres is the only source of truth, Redis makes things faster, ScyllaDB holds the price history. If Redis or ScyllaDB is down, trades still commit.

| Path | What lives there |
| --- | --- |
| `backend/` | The FastAPI API, the outbox relay and the scripts |
| `frontend/` | The Next.js app |
| `infra/` | The reverse proxy and the deployment scripts |
| `docs/` | The spec and the server documentation |

## Getting started

Docker with Compose and `make` are needed.

```sh
cp .example.env .env 
make up        # start the stack
make migrate   # create the schema
make seed      # load demo data
```

The app is at http://localhost:8080. `make` alone lists every command; `make lint` and `make test` check a change.

## Documentation

| Document | Covers |
| --- | --- |
| [`docs/SPEC.md`](docs/SPEC.md) | What the system does, and the decisions behind it. The source of truth for behaviour |
| [`docs/SERVER_INFRASTRUCTURE.md`](docs/SERVER_INFRASTRUCTURE.md) | The VM, the portal in front of it, and how to deploy |
| [`frontend/DESIGN.md`](frontend/DESIGN.md) | The design system |
| [`CLAUDE.md`](CLAUDE.md) | The rules of the codebase and where things go |
