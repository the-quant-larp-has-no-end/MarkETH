# MarkETH developer commands, the ones listed in CLAUDE.md. Run them from the repository
# root; `make` alone prints this list.
#
# The stack runs in Docker (compose.yaml plus compose.override.yaml, both fed by .env).
# Tests, linters and the API client generator run on the host through uv and npm; the
# integration tests start their own databases with Testcontainers. The browser suite
# (`make e2e`) starts nothing: it drives Chrome against the stack that is up.
#
# The deploy-* targets act on the VM named in .env (DEPLOY_SSH_HOST, DEPLOY_PATH) over
# SSH, or with DEPLOY_LOCAL=1 on the machine make runs on, which is how the VM deploys
# itself (.github/workflows/deploy.yml); docs/SERVER_INFRASTRUCTURE.md §14 walks through
# them.
#
# Kept to what GNU Make 3.81 understands, the version macOS ships.

.DEFAULT_GOAL := help

COMPOSE := docker compose
BACKEND := cd backend &&
# uv keeps backend/.venv in step with uv.lock by itself; npm needs the nudge. This
# reinstalls the frontend's dependencies whenever package-lock.json differs from the
# one they were installed from (the same stamp frontend/Dockerfile uses).
FRONTEND := cd frontend && { cmp -s package-lock.json node_modules/.installed-lock \
	|| { npm ci && cp package-lock.json node_modules/.installed-lock; }; } &&

.PHONY: help up down logs migrate seed test test-backend test-frontend test-scylla e2e \
        lint fmt api-client psql redis-cli cqlsh reset \
        deploy deploy-guard deploy-backup deploy-sync deploy-env deploy-build deploy-pull \
        deploy-migrate deploy-stop-template deploy-up deploy-check deploy-smoke deploy-wipe \
        deploy-rollback deploy-status deploy-logs

help: ## List these commands
	@awk 'BEGIN { FS = ":.*## " } /^[a-z0-9-]+:.*## / { printf "  make %-21s %s\n", $$1, $$2 }' Makefile

# --- Stack --------------------------------------------------------------------

up: ## Build and start the stack in the background
	$(COMPOSE) up --build --detach
	@port=$$($(COMPOSE) port nginx 80 | sed 's/.*://') \
		&& echo "App:      http://localhost:$$port" \
		&& echo "Identity: http://localhost:$$port/__dev/identity"

down: ## Stop the stack; the database volumes stay
	$(COMPOSE) down --remove-orphans

logs: ## Tail the logs of every service
	$(COMPOSE) logs --follow --tail=100

migrate: ## Build the schema as the owner role and check that it is the one of this code
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

# No part of `make test`: it needs the stack. Where that stack is, and how the suite
# names a person to it, is the runner's to read from its environment (E2E_BASE_URL,
# E2E_IDENTITY; frontend/e2e/support/runner.ts lists what it reads).
e2e: ## The browser suite, in Chrome against the stack that is up
	$(FRONTEND) npm run e2e

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

# --- Deployment (SPEC A.10, docs/SERVER_INFRASTRUCTURE.md §14) -----------------
#
# The VM runs compose.yaml alone, from a mirror of this tree under its home directory.
# Images are built there (the laptop has another CPU architecture), and its .env is
# rendered there, so no secret ever leaves it.
#
# DEPLOY_LOCAL=1 on the make command line is for a make that runs on the VM itself, as
# the deploy workflow's does on the team's runner: every command below then runs in a
# shell of this machine, started in the home directory as an SSH session is, and the
# tree is mirrored from this directory into the one beside it. Nothing connects anywhere.

deploy_setting = $(shell sed -n 's/^$(1)=//p' .env)
DEPLOY_SSH_HOST = $(call deploy_setting,DEPLOY_SSH_HOST)
DEPLOY_PATH = $(call deploy_setting,DEPLOY_PATH)
# The Compose project, which prefixes the names of its images and volumes.
STACK = $(shell sed -n 's/^name: *//p' compose.yaml)
DATABASE_VOLUME = $(STACK)_postgres-data
# What only the VM knows (two passwords and the bootstrap admins): a file beside the tree.
VM_OWN_ENV = $$HOME/$(DEPLOY_PATH).env
# The organizers' template, the other project that publishes the port the portal
# forwards to. It is stopped and started, its files are never touched.
TEMPLATE_PATH := template
# The local form changes directory inside `env`, so that a script a target hands over on
# standard input is still read from this tree.
ON_VM = $(if $(DEPLOY_LOCAL),env --chdir="$$HOME" sh -c,ssh -o BatchMode=yes $(DEPLOY_SSH_HOST))
# Where the tree is mirrored to, as rsync is told, and the VM as the targets name it.
MIRROR = $(if $(DEPLOY_LOCAL),"$$HOME/$(DEPLOY_PATH)/",--rsh='ssh -o BatchMode=yes' '$(DEPLOY_SSH_HOST):$(DEPLOY_PATH)/')
VM = $(if $(DEPLOY_LOCAL),this machine,$(DEPLOY_SSH_HOST))
VM_COMPOSE = cd $(DEPLOY_PATH) && docker compose -f compose.yaml
# ADMIN_EMAILS=... given to make names the bootstrap admins on the VM; left out, the VM
# keeps the ones it has. Every other admin and every barista is appointed in the admin
# panel of the running app.
BOOTSTRAP_ADMINS = $(if $(findstring command line,$(origin ADMIN_EMAILS)),--admins "$(ADMIN_EMAILS)")

# The sync deletes whatever the VM's directory holds beyond this tree, so the directory
# must be one of its own: a plain name, not the home directory, not the template.
deploy-guard:
	@case "$(DEPLOY_LOCAL)$(DEPLOY_SSH_HOST)" in \
		"") echo "Set DEPLOY_SSH_HOST in .env (see .example.env)."; exit 1;; \
	esac
	@case "$(DEPLOY_PATH)" in \
		""|$(TEMPLATE_PATH)|[!A-Za-z0-9]*|*[!A-Za-z0-9._-]*) \
			echo "DEPLOY_PATH in .env must be a plain directory name (letters, digits, '.', '_', '-'," \
				"starting with a letter or digit) other than '$(TEMPLATE_PATH)'. It is '$(DEPLOY_PATH)'."; \
			exit 1;; \
	esac

deploy: deploy-guard ## Back up, mirror the tree to the VM, build, migrate, switch over, check
	@$(MAKE) --no-print-directory deploy-backup
	@$(MAKE) --no-print-directory deploy-sync
	@$(MAKE) --no-print-directory deploy-env
	@$(MAKE) --no-print-directory deploy-build
	@$(MAKE) --no-print-directory deploy-pull
	@$(MAKE) --no-print-directory deploy-migrate
	@$(MAKE) --no-print-directory deploy-stop-template
	@$(MAKE) --no-print-directory deploy-up
	@$(MAKE) --no-print-directory deploy-check

deploy-backup: deploy-guard ## Dump the VM's database beside the tree, if there is one
	@echo "==> $(VM): dump the database into ~/$(DEPLOY_PATH)-backups, if a database exists"
	$(ON_VM) 'sh -s -- $(DEPLOY_PATH) $(DATABASE_VOLUME)' < infra/deploy/backup.sh

deploy-sync: deploy-guard ## Make ~/DEPLOY_PATH on the VM a mirror of this tree
	@echo "==> $(VM): make ~/$(DEPLOY_PATH) a mirror of this tree; whatever else is in it is deleted"
	rsync --archive --compress --delete --exclude-from=infra/deploy/rsync-exclude ./ $(MIRROR)
	$(ON_VM) 'rm -rf $(DEPLOY_PATH)/compose.override.yaml $(DEPLOY_PATH)/infra/nginx/dev'

deploy-env: deploy-guard ## Render the VM's .env there: [ADMIN_EMAILS=<id>,<id>] names the bootstrap admins
	@echo "==> $(VM): render ~/$(DEPLOY_PATH)/.env from .example.env, infra/deploy/env.production and ~/$(DEPLOY_PATH).env"
	$(ON_VM) 'cd $(DEPLOY_PATH)/backend && python3 -B -m scripts.production_env \
		--example ../.example.env --production ../infra/deploy/env.production \
		--own "$(VM_OWN_ENV)" --output ../.env --postgres-volume $(DATABASE_VOLUME) $(BOOTSTRAP_ADMINS)'
	$(ON_VM) '$(VM_COMPOSE) config --quiet'

deploy-build: deploy-guard ## Build the images on the VM; the ones in use stay tagged "previous"
	@echo "==> $(VM): tag the images in use as 'previous', then build the images of this tree"
	$(ON_VM) '$(VM_COMPOSE) config --images | grep "^$(STACK)-" | while read -r image; do \
		if docker image inspect "$$image" > /dev/null 2>&1; then docker tag "$$image" "$${image%%:*}:previous"; fi; \
		done'
	$(ON_VM) '$(VM_COMPOSE) build'

deploy-pull: deploy-guard ## Pull the store and proxy images on the VM
	@echo "==> $(VM): pull the images this stack does not build"
	$(ON_VM) '$(VM_COMPOSE) pull --ignore-buildable'

deploy-migrate: deploy-guard ## Build or verify the schema on the VM with the image just built
	@echo "==> $(VM): run the migrate image once; a failure stops the deployment here, with whatever served before still serving"
	$(ON_VM) '$(VM_COMPOSE) run --rm migrate'

deploy-stop-template: deploy-guard ## Stop the organizers' template on the VM; its files stay
	@echo "==> $(VM): docker compose down of ~/$(TEMPLATE_PATH), which frees the published port; its files and images stay"
	$(ON_VM) 'if [ -f $(TEMPLATE_PATH)/docker-compose.yml ]; then cd $(TEMPLATE_PATH) && docker compose down; \
		else echo "No template project in ~/$(TEMPLATE_PATH)."; fi'

deploy-up: deploy-guard ## Start the stack on the VM and load the proxy configuration
	@echo "==> $(VM): start the stack and wait until it is healthy, then recreate nginx so it reads the proxy files of this tree"
	$(ON_VM) '$(VM_COMPOSE) up --detach --wait --no-build --remove-orphans'
	$(ON_VM) '$(VM_COMPOSE) up --detach --wait --no-build --no-deps --force-recreate nginx'

# A machine answers its own address whatever its firewall says, so with DEPLOY_LOCAL the
# probe from outside is left to another machine: the deploy workflow's second job.
deploy-check: deploy-guard ## Check the stack on the VM, and from here that its port is closed
	@echo "==> $(VM): check the running stack (infra/deploy/check.sh); no data is changed"
	$(ON_VM) 'cd $(DEPLOY_PATH) && sh infra/deploy/check.sh'
	@if [ -n "$(DEPLOY_LOCAL)" ]; then \
		echo "==> not probed from here: this is the VM. Its public address must not answer on the published port from another machine"; \
	else \
		echo "==> from this machine: the VM's public address must not answer on the published port"; \
		endpoint=$$($(ON_VM) 'cd $(DEPLOY_PATH) && sh infra/deploy/check.sh public-endpoint') \
			&& sh infra/deploy/check.sh outside $$endpoint; \
	fi

deploy-smoke: deploy-guard ## Walk an empty database on the VM from sign-up to a redeemed voucher: CONFIRM=smoke
	@test "$(CONFIRM)" = "smoke" || { \
		echo "This registers made-up people and the first bootstrap admin, makes one of the made-up people a barista and writes a market, a trade, a voucher and a code into the VM's database."; \
		echo "Run it on an empty database and wipe it afterwards: make deploy-smoke CONFIRM=smoke"; exit 1; }
	@echo "==> $(VM): walk the stack through infra/deploy/smoke.sh; this writes test data"
	$(ON_VM) 'cd $(DEPLOY_PATH) && sh infra/deploy/smoke.sh'

deploy-wipe: deploy-guard ## Dump, then delete every database on the VM: CONFIRM=wipe
	@test "$(CONFIRM)" = "wipe" || { \
		echo "This stops the stack on the VM and deletes every user, balance, market, trade and voucher."; \
		echo "A dump is taken first. Run: make deploy-wipe CONFIRM=wipe"; exit 1; }
	@$(MAKE) --no-print-directory deploy-backup
	@echo "==> $(VM): stop the stack and delete its volumes"
	$(ON_VM) '$(VM_COMPOSE) down --volumes --remove-orphans'
	@echo "The VM serves nothing now. Start it empty with: make deploy"

deploy-rollback: deploy-guard ## Stop MarkETH on the VM (its data stays) and start the template again
	@echo "==> $(VM): stop the MarkETH containers (volumes stay), then start ~/$(TEMPLATE_PATH)"
	$(ON_VM) 'if [ -f $(DEPLOY_PATH)/compose.yaml ]; then $(VM_COMPOSE) stop; fi'
	$(ON_VM) 'cd $(TEMPLATE_PATH) && docker compose up --detach'

deploy-status: deploy-guard ## Containers, memory and disk on the VM
	@echo "==> $(VM): containers, memory and disk"
	$(ON_VM) '$(VM_COMPOSE) ps --all; docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}"; free -m; df -h /'

deploy-logs: deploy-guard ## Tail the logs of the stack on the VM
	@echo "==> $(VM): follow the logs of every service (Ctrl-C to leave)"
	$(ON_VM) '$(VM_COMPOSE) logs --follow --tail=100'
