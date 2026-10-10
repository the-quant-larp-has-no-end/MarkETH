# Backend

FastAPI API (`app.main:create_app`) and the outbox relay (`python -m app.relay`). Python 3.14, managed with uv. Run every command from `backend/`.

```
uv run pytest                      # unit + integration (needs Docker for Testcontainers)
uv run pytest --numprocesses 4     # the same in four processes, as `make test-backend` runs it
uv run pytest -m scylla            # the slow ScyllaDB tests
uv run ruff check . && uv run ruff format .
uv run mypy                        # strict
uv run lint-imports                # the layer rules below, enforced
uv run alembic upgrade head        # as the owner role: builds the schema in an empty database
uv run python -m scripts.check_schema    # the database was built from the models of this code
uv run python -m scripts.export_openapi  # writes openapi.json; `make api-client` also regenerates the frontend types
uv run uvicorn app.main:create_app --factory --reload
```

## Layers

Imports only point downwards. `lint-imports` fails the build otherwise.

| Layer | Contains | Rules |
| --- | --- | --- |
| `app/main.py`, `app/relay` | The FastAPI application and the relay process | The two entry points. Each builds one `Container` and runs on it. |
| `app/api` | Routes, dependencies, error handlers, middleware | One function per endpoint. Parse input, call one service method, return its result. No SQL, no business rules. |
| `app/container.py` | `Container`: builds every singleton once | The only place that wires classes together. Shared by the API, the relay, scripts and tests. |
| `app/services` | Business logic classes | Own the transaction. Never import FastAPI or Starlette. Raise `AppError` subclasses. |
| `app/repositories` | Data-access classes and `UnitOfWork` | Queries only. Never commit, never raise `AppError`. |
| `app/integrations` | Redis and ScyllaDB adapters | Each class states what happens when its backend is down. |
| `app/models` | SQLAlchemy ORM models, the trigger and privilege SQL, the schema fingerprint | Tables, constraints, indexes. No behaviour. |
| `app/db` | `Base`, `Database` (async engine) | |
| `app/core` | Settings, enums, errors, units, clock, identity values, the degree catalogue | Imports nothing else from `app`. |
| `app/schemas` | Pydantic request and response models | The API contract. Imports only `app.core`. Used by routes and services. |
| `app/pricing/lmsr.py` | LMSR maths | Pure functions, no I/O, imports only `app.core` (SPEC §4). |

Schemas double as the data-transfer objects services return. Repositories and models never see them. `scripts/` holds what runs beside the application: `seed`, `export_openapi`, `check_schema` and `production_env`, which renders the deployed `.env` with the standard library alone.

## Classes and wiring

- Services, repositories and adapters are classes with constructor-injected dependencies. No module-level state, no globals, no service locators.
- `Container.build(settings, ...)` constructs everything. A route receives a service through an `Annotated[..., Depends(...)]` alias defined in `app/api/deps.py`.
- `Clock` is injected so tests can freeze time. Never call `datetime.now()` in a service; use `self._clock.now()`.

## Who is calling

The API has no sign-in of its own (SPEC §3). The portal names the caller in two request headers, and `authenticate_request` in `app/api/deps.py` is the one place that reads them; `IdentityService` turns them into a `Caller`. There is no development bypass: on a laptop the proxy sets the headers.

- A route says who may call it by its guard: `Identity` (whoever the portal named, with or without a profile), `CurrentUser` (a profile whose account is ACTIVE), `BaristaUser` (role BARISTA or ADMIN), `AdminUser`, or none for public. A route in a module of `member_router` is guarded by where it is included (`app/api/router.py`).
- The role of a caller is `users.role`, except that an address of `ADMIN_EMAILS` is ADMIN whatever its row says. Ask `EmailPolicy.effective_role`, never the column alone.
- A service that only an admin may use checks `actor.is_admin` itself too: the rule holds for whoever calls it.

## Transactions

`UnitOfWork` is one transaction on one connection. A service method that changes state looks like this:

```python
async with self._uow_factory() as uow:  # BEGIN (READ COMMITTED)
    market = await uow.markets.get_for_update(market_id)  # market row first
    now = self._clock.now()  # read once the first lock is granted
    account = await uow.accounts.get_user_account(user.id, for_update=True)
    ...
    await self._ledger.post(uow, ...)  # every credit movement
    answer = ...  # built here, from what this transaction wrote
# COMMIT on normal exit, ROLLBACK on exception
# after the block: best-effort side effects only (cache delete, publish)
```

- One `async with` per business operation. Do not nest them, except deliberately for a separate transaction (the circuit breaker in SPEC §5).
- Lock order is market row, then user account, then anything else. Lock several accounts in ascending `id` order (`accounts.lock_many`, `accounts.lock_user_accounts`).
- A check-then-insert rule that no row lock covers takes `uow.advisory_lock(name)`: the caps of an approval, the monthly cap of a code batch, the choice of a username.
- Raising inside the block rolls everything back, including an idempotency key that was just claimed.
- Nothing after the block may fail the request: build the answer of a write inside the block, and wrap Redis calls so they degrade.
- Do not catch a lost database connection. Let it leave the block: the API answers it with 503 for every route (`app/api/errors.py`, SPEC §9). A lock that is not granted in time takes the same road.
- The ORM is used without lazy loading. Query what you need explicitly; there are no relationship attributes to traverse.

## Money

- `*_micro` is an `int` of µCR (or µshares). `*_cr` is a `Decimal` of CR. Convert only with `app.core.units` (`to_micro`, `to_cr`, `format_cr`, `ceil_micro`, `floor_micro`).
- Credits move only through `LedgerService.post(...)`. It inserts the journal entry and its postings; the database adds each posting to `accounts.balance_micro`. A positive posting increases the account's balance. Appendix A.8 of the spec lists the postings of every entry kind. Never assign `balance_micro`: the app role cannot write it.
- The database backs this up (Appendix A.7): a deferred constraint trigger rejects an entry that does not sum to zero, a balance is the sum of its postings, a committed entry takes no further postings, user, market and voucher accounts have a `>= 0` check, and the app role cannot UPDATE or DELETE ledger rows.
- Timestamps are timezone-aware. A naive `datetime` is refused when it is bound to a statement.

## Errors

Raise a subclass of `app.core.errors.AppError`. The handler in `app/api/errors.py` renders `{code, message, details}`. To add one, add an `ErrorCode` member and a subclass. Never raise `HTTPException` from a service. The keys of `details` are part of the contract: they are listed per code in the docstring of `ErrorResponse` (`app/schemas/common.py`), amounts in there are `to_cr(...)` values named `*_cr`, and a `LimitExceededError` carries `details["limit"]`, a `Limit` member.

A `message` is a sentence for the person in front of the screen, and the frontend shows it as sent. That holds for a 422 too. A rule a service checks raises `ValidationFailedError` with its sentence and `details["field"]`. What a request model refuses is worded by the table in `app/api/validation.py` (`SENTENCES`), never by the framework; a field whose rule needs words of its own gets them with `in_its_own_words(...)` or `said(...)` from `app/schemas/common.py`.

## Settings

`app.core.config.Settings` mirrors `.env` section by section and has no defaults. Read values through the injected `Settings`, never through `os.environ`. A variable that only Compose, nginx or the Makefile reads is listed in `INFRA_ONLY_VARIABLES`. `tests/unit/test_settings.py` fails if `.example.env`, `Settings` and that list drift apart.

## Async

Postgres through asyncpg (app, Alembic, tests, scripts). Redis through `redis.asyncio`. ScyllaDB through `ScyllaClient`, which bridges the driver's futures to asyncio and connects in a worker thread. Nothing may block the event loop.

## Tests

- `tests/unit` needs nothing external. `tests/integration` uses real Postgres and Redis through Testcontainers and is marked `integration`. ScyllaDB tests are marked `scylla` and are deselected by default.
- Fixtures in `tests/conftest.py`: `settings` (`.example.env` with two bootstrap admins, pointing at the containers; `base_settings` is the same without any service behind it), `container`, `app`, `client` (httpx against the ASGI app, Origin header set, nobody calling), `clock` (frozen, advance it explicitly), `as_user` (act as any user without the portal's headers), `portal_headers` (the two headers the portal would send, for a request that goes through the real seam), `flush_redis`, `uow_factory`, and `factory` (`tests/support/factories.py`), which writes users with a balance, markets and positions directly and keeps the books straight: `factory.user()`, `factory.admin()` (a bootstrap admin), `factory.barista()` (a row with that role), `factory.market()`, `factory.position()`.
- A test of anything that moves credits ends with `await assert_books_balance(container.uow_factory)` (`tests/support/books.py`).
- A dependency that is down: mark a test `redis_down` or `postgres_down`, or ask for `scylla_proxy` in a test marked `scylla`, and the same fixtures give the application running through that outage. The conftest docstring has the details.
- `tests/support` has the rest: `economy` (the numbers of the test settings), `history` (`MemoryHistory`, a ScyllaDB kept in memory, and helpers that queue chart points), `schema` (what the schema would be if a model were edited), `database` (`scratch_database`), `network` (a backend that hangs or is cut off).
- Expected prices, statuses and stakes come from `MarketView` and `app.pricing.lmsr`, not from arithmetic repeated in the test.
- Every bullet of SPEC §12 has a test. Name tests after the rule they prove.
- Property tests use Hypothesis.

## Recipes

**Add an endpoint.** (1) Request and response models in `app/schemas/<area>.py`. (2) A method on the service in `app/services/`. (3) Any query it needs on the repository. (4) The route in `app/api/routes/<area>.py`, with the right guard (`Identity`, `CurrentUser`, `BaristaUser`, `AdminUser`, or none for public). (5) A test, and the operation in the sets and counts that `tests/integration/test_api_contract.py`, `tests/integration/test_errors.py` and `tests/integration/test_identity.py` pin. (6) Its row in SPEC §10 or A.2. (7) `make api-client` so `openapi.json` and the frontend types follow.

**Add a table or column.** (1) Edit the model in `app/models/`. (2) If the app role should not write it, say so in `app/models/privileges.py`. (3) `make revision NAME="..."` from the repository root: it writes the next file of `migrations/versions/` with the fingerprint of the models as they are now. Write its `upgrade` and `downgrade` with `op`; a new table is followed by `grant_app_role()`, and a column that takes no NULL needs a `server_default` or a step that fills the rows that are there. `0001_baseline.py` is the first deployed schema and is never edited. (4) A repository method and a test. `tests/integration/test_schema.py` compares a database taken through the revisions with one built from the models, and `tests/unit/test_fingerprint.py` fails while the last revision does not carry the fingerprint of the models. (5) `make migrate` takes the local database there; the deployed one follows at the next deploy, with its rows.

**Change a CHECK constraint or the members of an enum.** An enum column is a VARCHAR with a CHECK, and `alembic check` compares neither. (1) Edit the model or the enum. (2) `make revision NAME="..."`, and in it drop the constraint and create it again with the new expression (`op.drop_constraint`, `op.create_check_constraint`). The fingerprint sees the change (`tests/unit/test_fingerprint.py`) and the comparison of `tests/integration/test_schema.py` reads the expression from the database.

**Add a setting.** (1) Add it to `.env` and `.example.env` with a comment and a unit. (2) Add a field with `validation_alias` to the matching section in `app/core/config.py`, or the name to `INFRA_ONLY_VARIABLES` if no Python code reads it. (3) Use it through `Settings`. (4) Name it in `docs/SPEC.md` with its value.

**Add a way credits can move.** (1) A `JournalKind` member, which changes `ck_journal_entries_kind` and with it the schema (see the recipe above). (2) Its `*_key` function in `app/services/ledger.py` and its line in `LEDGER_DESCRIPTIONS` (`app/services/account.py`). (3) A row in Appendix A.8. (4) A service method that calls `LedgerService.post` inside its transaction with that deterministic `idempotency_key`. (5) A test that ends with `assert_books_balance`.
