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

.PHONY: test-config-parity up down start stop logs migrate migration test test-unit test-integration test-infra test-stt-cpu test-image test-dead-code scan scan-agent-runner scan-meet sim-attribution

up:
	$(COMPOSE) up --build -d

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
	$(COMPOSE) exec -T agent-runner alembic upgrade head

migration:
	$(COMPOSE) exec -T agent-runner alembic revision --autogenerate -m "$(MSG)"

logs:
	$(COMPOSE) logs -f --tail=$(LOG_TAIL) $(SERVICE)

test: test-unit test-integration

test-unit:
	$(COMPOSE) up -d transport-server agent-runner meet
	$(COMPOSE) exec -T agent-runner alembic upgrade head
	$(COMPOSE) exec -T agent-runner python -m unittest discover -s tests -p "test_*.py" -v
	@# Tests that call the live runner start real bot containers in rooms nobody joins;
	@# a bot alone never leaves yet (design iteration 4, should_leave). Clean them up.
	-docker rm -f $$(docker ps -aq --filter label=meetlab.session) 2>/dev/null
	$(COMPOSE) exec -T meet pnpm test
	$(COMPOSE) exec -T meet pnpm lint
	$(COMPOSE) exec -T meet pnpm knip # no unused files, exports or dependencies (knip)

# Every Bot Config field is in the database, the runner's API and the console form. The
# services' own tests can't see each other, so this runs on the host from the repo root.
test-config-parity:
	python3 agent-runner/tests/config_parity_check.py

# No dead code or undeclared dependencies in agent-runner, by the standard tools: vulture
# (unused code; framework-signature false positives in vulture_whitelist.py) and deptry
# (pyproject.toml against imports). Runs on the host, no Docker.
test-dead-code:
	cd agent-runner && uvx vulture . vulture_whitelist.py --exclude ".venv,alembic" --min-confidence 80
	cd agent-runner && uv run --python 3.12 --with deptry deptry .

# The bot/runner image (agent-runner): within budget, and every runtime entry point imports inside
# it (a cut that removes something used fails here). Budget: the files in the image (du), the same
# on any Docker. Before the deep cut (2026-10-06): 1,879 MB (978 MB compressed in ECR): ffmpeg, a
# second uv sync, local Whisper; after: 779 MB (262 MB compressed).
IMAGE_BUDGET_MB ?= 850
test-image:
	docker build --platform linux/amd64 -q -t meetlab-agent-runner:budget agent-runner >/dev/null
	@mb=$$(docker run --rm --platform linux/amd64 meetlab-agent-runner:budget du -sxm / | cut -f1); \
		echo "agent-runner image: $$mb MB of files (budget $(IMAGE_BUDGET_MB) MB)"; [ $$mb -le $(IMAGE_BUDGET_MB) ]
	docker run --rm --platform linux/amd64 -e DATABASE_URL=postgresql+asyncpg://x:x@localhost/x \
		-e LIVEKIT_URL=ws://localhost:7880 -e LIVEKIT_API_KEY=x -e LIVEKIT_API_SECRET=x \
		meetlab-agent-runner:budget python -c \
		"import runner, bot, bot_task, migration_check, soundfile; import tests.load_run; print('entry points import')"

# Speech-to-text on CPU (stt-cpu/): the server against recorded fixture clips. Downloads the
# model once (~650 MB) into the Hugging Face cache; no AWS, no Docker.
test-stt-cpu:
	cd stt-cpu && uv run --no-project --with "onnx-asr[cpu,hub]==0.12.*" --with soundfile --with fastapi \
		--with httpx --with python-multipart python -m unittest test_app -v

# Offline: fmt, validate and `terraform test` (mock provider) for every infra/v2 stack.
# No AWS credentials needed, so it runs in CI and before any plan.
test-infra:
	@for d in infra/v2/*/; do \
		echo "== $$d"; \
		terraform -chdir=$$d fmt -check -recursive && \
		terraform -chdir=$$d init -backend=false -input=false >/dev/null && \
		terraform -chdir=$$d validate -no-color && \
		terraform -chdir=$$d test -no-color || exit 1; \
	done

test-integration:
	$(COMPOSE) up -d transport-server agent-runner meet
	$(COMPOSE) exec -T meet pnpm test:api
	$(COMPOSE) exec -T meet pnpm test:load

scan: scan-agent-runner scan-meet

scan-agent-runner:
	$(COMPOSE) up -d agent-runner
	cd agent-runner && uv export --no-dev --format requirements-txt --no-hashes | uvx pip-audit -r /dev/stdin

scan-meet:
	$(COMPOSE) up -d meet
	$(COMPOSE) exec -T meet pnpm audit

# Three people talking over each other: is each one's speech stored and labelled as
# theirs (diagnosis F11)? Real speech recognition; SPEAKERS=a is the one-speaker control.
sim-attribution:
	$(COMPOSE) up -d --wait transport-server agent-runner
	$(COMPOSE) exec -T -e SPEAKERS=$(or $(SPEAKERS),abc) agent-runner python tests/sim_attribution.py
