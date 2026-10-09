# MarkETH developer commands, the ones listed in CLAUDE.md. Run them from the repository
# root; `make` alone prints this list.
#
# The stack runs in Docker (compose.yaml plus compose.override.yaml, both fed by .env).
# Tests, linters and the API client generator run on the host through uv and npm; the
# integration tests start their own databases with Testcontainers.
#
# Kept to what GNU Make 3.81 understands, the version macOS ships.

.DEFAULT_GOAL := help

COMPOSE := docker compose
BACKEND := cd backend &&
# uv keeps backend/.venv in step with uv.lock by itself; npm needs the nudge. This
# reinstalls the frontend's dependencies whenever package-lock.json is no longer the
# one they were installed from (the same stamp frontend/Dockerfile uses).
FRONTEND := cd frontend && { cmp -s package-lock.json node_modules/.installed-lock \
	|| { npm ci && cp package-lock.json node_modules/.installed-lock; }; } &&

.PHONY: help up down logs migrate seed test test-backend test-frontend test-scylla \
        lint fmt api-client psql redis-cli cqlsh reset

help: ## List these commands
	@awk 'BEGIN { FS = ":.*## " } /^[a-z-]+:.*## / { printf "  make %-14s %s\n", $$1, $$2 }' Makefile

# --- Stack --------------------------------------------------------------------

up: ## Build and start the stack in the background
	$(COMPOSE) up --build --detach
	@echo "App:  http://localhost:$$($(COMPOSE) port nginx 80 | sed 's/.*://')"
	@echo "Mail: http://localhost:$$($(COMPOSE) port mailpit 8025 | sed 's/.*://')"

down: ## Stop the stack; the database volumes stay
	$(COMPOSE) down --remove-orphans

logs: ## Tail the logs of every service
	$(COMPOSE) logs --follow --tail=100

migrate: ## Apply the Alembic migrations (as the owner role)
	$(COMPOSE) run --rm migrate

seed: ## Load demo data (the stack must be up)
	$(COMPOSE) exec api python -m scripts.seed

reset: ## Stop the stack and delete its volumes
	$(COMPOSE) down --volumes --remove-orphans

# --- Tests --------------------------------------------------------------------

test: test-backend test-frontend ## Backend and frontend tests

test-backend: ## Unit and integration tests (needs Docker for Testcontainers)
	$(BACKEND) uv run pytest

test-scylla: ## The slow ScyllaDB tests
	$(BACKEND) uv run pytest -m scylla

test-frontend: ## Frontend tests
	$(FRONTEND) npm test

# --- Code quality -------------------------------------------------------------

lint: ## Check style, types and architecture rules
	$(BACKEND) uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports
	$(FRONTEND) npm run lint && npm run typecheck

fmt: ## Fix what can be fixed automatically
	$(BACKEND) uv run ruff check --fix-only . && uv run ruff format .
	$(FRONTEND) npm run lint -- --fix

api-client: ## Export backend/openapi.json and regenerate the frontend's API types
	$(BACKEND) uv run python -m scripts.export_openapi
	$(FRONTEND) npm run generate:api

# --- Shells into the data stores (the stack must be up) -------------------------

psql: ## Postgres shell as the owner role
	$(COMPOSE) exec postgres sh -c 'exec psql --username "$$POSTGRES_USER" --dbname "$$POSTGRES_DB"'

redis-cli: ## Redis shell on the database the app uses
	$(COMPOSE) exec redis sh -c 'exec redis-cli -n "$$REDIS_DB"'

cqlsh: ## ScyllaDB shell
	$(COMPOSE) exec scylla cqlsh
