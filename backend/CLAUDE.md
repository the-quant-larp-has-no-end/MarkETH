# Backend

FastAPI API (`app.main:create_app`) and the outbox relay (`python -m app.relay`). Python 3.14, managed with uv. Run every command from `backend/`.

```
uv run pytest                      # unit + integration (needs Docker for Testcontainers)
uv run pytest -m scylla            # the slow ScyllaDB tests
uv run ruff check . && uv run ruff format .
uv run mypy                        # strict
uv run lint-imports                # the layer rules below, enforced
uv run alembic upgrade head        # as the owner role
uv run uvicorn app.main:create_app --factory --reload
```

## Layers

Imports only point downwards. `lint-imports` fails the build otherwise.

| Layer | Contains | Rules |
| --- | --- | --- |
| `app/api` | Routes, dependencies, error handlers, middleware | One function per endpoint. Parse input, call one service method, return its result. No SQL, no business rules. |
| `app/container.py` | `Container`: builds every singleton once | The only place that wires classes together. Shared by the API, the relay, scripts and tests. |
| `app/services` | Business logic classes | Own the transaction. Never import FastAPI or Starlette. Raise `AppError` subclasses. |
| `app/repositories` | Data-access classes and `UnitOfWork` | Queries only. Never commit, never raise `AppError`. |
| `app/integrations` | Redis, ScyllaDB and mail adapters | Each class states what happens when its backend is down. |
| `app/models` | SQLAlchemy ORM models | Tables, constraints, indexes. No behaviour. |
| `app/db` | `Base`, `Database` (async engine) | |
| `app/core` | Settings, enums, errors, units, clock, security | Imports nothing else from `app`. |
| `app/schemas` | Pydantic request and response models | The API contract. Imports only `app.core`. Used by routes and services. |
| `app/pricing/lmsr.py` | LMSR maths | Pure functions, no I/O, imports only `app.core` (SPEC §4). |

Schemas double as the data-transfer objects services return. Repositories and models never see them.

## Classes and wiring

- Services, repositories and adapters are classes with constructor-injected dependencies. No module-level state, no globals, no service locators.
- `Container.build(settings, ...)` constructs everything. A route receives a service through an `Annotated[..., Depends(...)]` alias defined in `app/api/deps.py`.
- `Clock` and `Mailer` are injected so tests can freeze time and capture mail. Never call `datetime.now()` in a service; use `self._clock.now()`.

## Transactions

`UnitOfWork` is one session and one transaction. A service method that changes state looks like this:

```python
async with self._uow_factory() as uow:  # BEGIN (READ COMMITTED)
    market = await uow.markets.get_for_update(market_id)  # market row first
    account = await uow.accounts.get_user_account(user.id, for_update=True)
    ...
    await self._ledger.post(uow, ...)  # every credit movement
# COMMIT on normal exit, ROLLBACK on exception
# after the block: best-effort side effects only (cache delete, publish)
```

- One `async with` per business operation. Do not nest them, except deliberately for a separate transaction (the circuit breaker in SPEC §5).
- Lock order is market row, then user account, then anything else. Lock several accounts in ascending `id` order (`accounts.lock_many`, `accounts.lock_user_accounts`).
- Raising inside the block rolls everything back, including an idempotency key that was just claimed.
- Nothing after the block may fail the request: wrap Redis calls so they degrade.
- Do not catch a lost database connection. Let it leave the block: the API answers it with 503 for every route (`app/api/errors.py`, SPEC §9).
- The ORM is used without lazy loading. Query what you need explicitly; there are no relationship attributes to traverse.

## Money

- `*_micro` is an `int` of µCR (or µshares). `*_cr` is a `Decimal` of CR. Convert only with `app.core.units` (`to_micro`, `to_cr`, `format_cr`, `ceil_micro`, `floor_micro`).
- Credits move only through `LedgerService.post(...)`. It inserts the journal entry and its postings; the database adds each posting to `accounts.balance_micro`. A positive posting increases the account's balance. Appendix A.8 of the spec lists the postings of every entry kind. Never assign `balance_micro`: the app role cannot write it.
- The database backs this up (Appendix A.7): a deferred constraint trigger rejects an entry that does not sum to zero, a balance is the sum of its postings, a committed entry takes no further postings, user, market and coffee-ticket accounts have a `>= 0` check, and the app role cannot UPDATE or DELETE ledger rows.
- Timestamps are timezone-aware. A naive `datetime` is refused when it is bound to a statement.

## Errors

Raise a subclass of `app.core.errors.AppError`. The handler in `app/api/errors.py` renders `{code, message, details}`. To add one, add an `ErrorCode` member and a subclass. Never raise `HTTPException` from a service. The keys of `details` are part of the contract: they are listed per code in the docstring of `ErrorResponse` (`app/schemas/common.py`), amounts in there are `to_cr(...)` values named `*_cr`, and a `LimitExceededError` carries `details["limit"]`, a `Limit` member.

## Settings

`app.core.config.Settings` mirrors `.env` section by section and has no defaults. Read values through the injected `Settings`, never through `os.environ`. `tests/unit/test_settings.py` fails if `.example.env`, `Settings` and the infra-only list drift apart.

## Async

Postgres through asyncpg (app, Alembic, tests, scripts). Redis through `redis.asyncio`. ScyllaDB through `ScyllaClient`, which bridges the driver's futures to asyncio and connects in a worker thread. SMTP through aiosmtplib. Nothing may block the event loop.

## Tests

- `tests/unit` needs nothing external. `tests/integration` uses real Postgres and Redis through Testcontainers and is marked `integration`. ScyllaDB tests are marked `scylla` and are deselected by default.
- Fixtures in `tests/conftest.py`: `settings`, `container`, `client` (httpx against the ASGI app), `clock` (frozen, advance it explicitly), `mailer` (captures mail), `as_user` (act as any user without a session), `flush_redis`, and `factory` (`tests/support/factories.py`), which creates users with a balance, sessions (`factory.session(user)` returns the cookie token), markets and positions directly in the database.
- A dependency that is down: mark a test `redis_down` or `postgres_down`, or ask for `scylla_proxy` in a test marked `scylla`, and the same fixtures give the application running through that outage. The conftest docstring has the details.
- Expected prices, statuses and stakes come from `MarketView` and `app.pricing.lmsr`, not from arithmetic repeated in the test.
- Every bullet of SPEC §12 has a test. Name tests after the rule they prove.
- Property tests use Hypothesis.

## Recipes

**Add an endpoint.** (1) Request and response models in `app/schemas/<area>.py`. (2) A method on the service in `app/services/`. (3) Any new query on the repository. (4) The route in `app/api/routes/<area>.py`, with the right guard (`CurrentUser`, `AdminUser`, `BaristaUser`, or none for public). (5) A test. (6) `make api-client` so the frontend types follow.

**Add a table or column.** (1) Edit the model in `app/models/`. (2) `uv run alembic revision --autogenerate -m "..."` against a migrated database, then read the generated file. (3) If the app role should not write it, add the REVOKE. (4) A repository method and a test.

**Change a CHECK constraint or the members of an enum.** Autogenerate does not compare CHECK constraints, and an enum column is a VARCHAR with one: it writes nothing, and `alembic check` stays green. (1) Edit the model or the enum. (2) Write the revision by hand: `op.drop_constraint("ck_<table>_<name>", "<table>", type_="check")`, then `op.create_check_constraint(...)` with the new expression, and the reverse in `downgrade`. `migrations/versions/0002_ledger_guards.py` is an example. (3) `test_check_and_index_definitions_match_the_models` fails until the migration and the models agree.

**Add a setting.** (1) Add it to `.env` and `.example.env` with a comment and a unit. (2) Add a field with `validation_alias` to the matching section in `app/core/config.py`. (3) Use it through `Settings`.

**Add a way credits can move.** (1) A `JournalKind` member. (2) A hand-written migration that re-creates `ck_journal_entries_kind` with the new member (see the recipe above); without it the database rejects the new kind. (3) A row in Appendix A.8. (4) A service method that calls `LedgerService.post` inside its transaction with a deterministic `idempotency_key`. (5) A test asserting all accounts still sum to zero.
