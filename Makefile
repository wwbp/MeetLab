COMPOSE = docker compose -f .devcontainer/docker-compose.yml
LOG_TAIL ?= 200
SERVICE ?=
BOT_LONGEVITY_MAX_SECONDS ?= 1050
BOT_LONGEVITY_POLL_SECONDS ?= 5
BOT_LONGEVITY_MESSAGE_SECONDS ?= 10
BENCHMARK_SAMPLES ?= 10
BENCHMARK_TIMEOUT ?= 25
BENCHMARK_CONFIGS ?=
BENCHMARK_WAV ?=
# Rooms run concurrently per config. Lower it on a memory-constrained Docker VM:
# concurrent bots contend for RAM and the resulting numbers are unattributable.
BENCHMARK_PARALLEL ?= 3

MSG ?= migration
ALEMBIC = alembic -c packages/core/alembic.ini
# Each package's tests run in their own process (each has a `tests` package); then the
# cross-service integration and system tests from the repo root.
UNIT_SUITES = packages/core services/api services/bot tools/loadtest

.PHONY: test-static test-config-parity up down start stop logs migrate migration test test-unit test-integration test-infra test-stt-cpu test-image test-dead-code scan scan-agent-runner scan-meet sim-attribution

up:
	$(COMPOSE) build bot tests
	$(COMPOSE) up --build -d --renew-anon-volumes

down:
	@# Bot containers (BOT_DISPATCHER=docker) are not part of the compose project.
	-docker rm -f $$(docker ps -aq --filter label=meetlab.session) 2>/dev/null
	$(COMPOSE) down -v

start: up migrate

stop: down

# Documentation uses an isolated uv environment, independently of Docker.
.PHONY: docs docs-build
docs:
	uv run --no-project --with-requirements requirements-docs.txt mkdocs serve

docs-build:
	uv run --no-project --with-requirements requirements-docs.txt mkdocs build --strict

migrate:
	$(COMPOSE) exec -T agent-runner $(ALEMBIC) upgrade head

migration:
	$(COMPOSE) exec -T agent-runner $(ALEMBIC) revision --autogenerate -m "$(MSG)"

logs:
	$(COMPOSE) logs -f --tail=$(LOG_TAIL) $(SERVICE)

test: test-unit test-integration

test-unit:
	$(COMPOSE) up -d transport-server agent-runner meet
	$(COMPOSE) exec -T agent-runner $(ALEMBIC) upgrade head
	$(COMPOSE) run --rm --no-deps -T tests sh -ec 'for d in $(UNIT_SUITES); do echo "== $$d"; (cd $$d && python -m unittest discover -s tests -t . -p "test_*.py"); done; \
	  echo "== tests"; python -m unittest discover -s tests -t . -p "test_*.py"'
	@# Tests that call the live runner start real bot containers in rooms nobody joins;
	@# a bot alone never leaves yet (design iteration 4, should_leave). Clean them up.
	-docker rm -f $$(docker ps -aq --filter label=meetlab.session) 2>/dev/null
	@$(COMPOSE) exec -T meet sh -c 'cmp -s pnpm-lock.yaml node_modules/.pnpm/lock.yaml' \
	  || { echo "meet's node_modules is older than pnpm-lock.yaml: run make up"; exit 1; }
	$(COMPOSE) exec -T meet pnpm test

# Every Bot Config field is in the database, the runner's API and the console form. The
# services' own tests can't see each other, so this runs on the host from the repo root.
test-config-parity:
	python3 tests/system/config_parity_check.py

# No dead code or undeclared dependencies in the Python code, by the standard tools: vulture
# (unused code; framework-signature false positives in vulture_whitelist.py) and deptry
# (pyproject.toml against imports). Runs on the host, no Docker.
# Static testing (ISTQB, docs/testing.md): defects found without running the code. No stack:
# meet's checks run in its deps stage, node_modules seeded from the image (anonymous volume).
test-static: test-dead-code test-image docs-build
	python3 tests/system/capacity_doc_check.py
	@# The layout (services/, packages/, tools/) and the architecture: the API and the bot never
	@# import each other; core imports neither (.importlinter).
	python3 tests/system/layout_check.py
	PYTHONPATH=$(PYTHONPATH_ALL) uv run --with import-linter lint-imports
	docker build -q --target deps -t meetlab-meet:deps services/web >/dev/null
	docker run --rm -v $(CURDIR)/services/web:/app -v /app/node_modules -w /app meetlab-meet:deps \
		sh -c 'pnpm lint && pnpm exec tsc --noEmit && pnpm knip'
	@# Workflows (actionlint: syntax, expressions, untrusted input in scripts) and scripts
	@# (shellcheck); warnings and errors, not style notes.
	docker run --rm -e SHELLCHECK_OPTS=--severity=warning -v $(CURDIR):/repo -w /repo rhysd/actionlint:1.7.7 -no-color -oneline
	docker run --rm -v $(CURDIR):/mnt -w /mnt koalaman/shellcheck:v0.10.0 -S warning infra/*.sh

PY_DIRS = packages/core services/api services/bot tools/loadtest tests
PYTHONPATH_ALL = packages/core:services/api:services/bot:tools/loadtest
test-dead-code:
	uvx vulture $(PY_DIRS) vulture_whitelist.py --exclude ".venv,migrations" --min-confidence 80
	PYTHONPATH=$(PYTHONPATH_ALL) uv run --python 3.12 --all-extras --with deptry deptry packages/core services/api services/bot --extend-exclude ".*/tests/.*"

# The two Python images, each within budget, each one's entry points importing inside it, and
# the API's without Pipecat (the point of splitting them). Budget: the files in the image (du), the
# same on any Docker. One image before the split (2026-10-14): 767 MB.
API_IMAGE_BUDGET_MB ?= 400
BOT_IMAGE_BUDGET_MB ?= 850
IMAGE_TEST_ENV = -e DATABASE_URL=postgresql+asyncpg://x:x@localhost/x -e LIVEKIT_URL=ws://localhost:7880 \
	-e LIVEKIT_API_KEY=x -e LIVEKIT_API_SECRET=x
test-image:
	docker build --platform linux/amd64 -q -f services/api/Dockerfile -t meetlab-api:budget . >/dev/null
	docker build --platform linux/amd64 -q -f services/bot/Dockerfile -t meetlab-bot:budget . >/dev/null
	@mb=$$(docker run --rm --platform linux/amd64 meetlab-api:budget du -sxm / | cut -f1); \
		echo "api image: $$mb MB of files (budget $(API_IMAGE_BUDGET_MB) MB)"; [ $$mb -le $(API_IMAGE_BUDGET_MB) ]
	@mb=$$(docker run --rm --platform linux/amd64 meetlab-bot:budget du -sxm / | cut -f1); \
		echo "bot image: $$mb MB of files (budget $(BOT_IMAGE_BUDGET_MB) MB)"; [ $$mb -le $(BOT_IMAGE_BUDGET_MB) ]
	docker run --rm --platform linux/amd64 $(IMAGE_TEST_ENV) meetlab-api:budget python -c \
		"import meetlab_api.runner, alembic; print('api entry points import')"
	docker run --rm --platform linux/amd64 meetlab-api:budget python -c \
		"import importlib.util as u; assert u.find_spec('pipecat') is None, 'Pipecat in the API image'; print('api image has no Pipecat')"
	docker run --rm --platform linux/amd64 $(IMAGE_TEST_ENV) meetlab-bot:budget python -c \
		"import meetlab_bot.bot, meetlab_bot.bot_task, meetlab_loadtest.load_run, soundfile; print('bot entry points import')"

# Speech-to-text on CPU (services/stt/): the server against recorded fixture clips. Downloads the
# model once (~650 MB) into the Hugging Face cache; no AWS, no Docker.
test-stt-cpu:
	cd services/stt && uv run --no-project --with "onnx-asr[cpu,hub]==0.12.*" --with soundfile --with fastapi \
		--with httpx --with python-multipart python -m unittest test_app -v

# Offline: fmt, validate and `terraform test` (mock provider) for every infra stack.
# No AWS credentials needed, so it runs in CI and before any plan.
test-infra:
	@for d in infra/*/; do \
		echo "== $$d"; \
		terraform -chdir=$$d fmt -check -recursive && \
		terraform -chdir=$$d init -backend=false -input=false >/dev/null && \
		terraform -chdir=$$d validate -no-color && \
		terraform -chdir=$$d test -no-color || exit 1; \
	done
	@# Every ECS service waits for its tasks' secrets policy (a fresh stack: v2.0.0's meet).
	python3 infra/stack/tests/secrets_order.py
	@# One production, no staging: merges build, releases deploy (the workflows).
	python3 infra/stack/tests/pipeline_check.py

test-integration:
	$(COMPOSE) up -d transport-server agent-runner meet
	$(COMPOSE) exec -T meet pnpm test:api
	$(COMPOSE) exec -T meet pnpm test:load

scan: scan-agent-runner scan-meet

scan-agent-runner:
	uv export --no-dev --format requirements-txt --no-hashes | uvx pip-audit -r /dev/stdin

scan-meet:
	$(COMPOSE) up -d meet
	$(COMPOSE) exec -T meet pnpm audit

# Three people talking over each other: is each one's speech stored and labelled as
# theirs (diagnosis F11)? Real speech recognition; SPEAKERS=a is the one-speaker control.
sim-attribution:
	$(COMPOSE) up -d --wait transport-server agent-runner
	$(COMPOSE) run --rm --no-deps -T -e SPEAKERS=$(or $(SPEAKERS),abc) tests python -m meetlab_loadtest.sim_attribution
