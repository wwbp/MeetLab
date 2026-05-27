COMPOSE = docker compose -f .devcontainer/docker-compose.yml
LOG_TAIL ?= 200
SERVICE ?=
BOT_LONGEVITY_MAX_SECONDS ?= 1050
BOT_LONGEVITY_POLL_SECONDS ?= 5
BOT_LONGEVITY_MESSAGE_SECONDS ?= 10
BENCHMARK_SAMPLES ?= 10
BENCHMARK_TIMEOUT ?= 30

MSG ?= migration

.PHONY: up down start stop logs migrate migration test test-unit test-integration test-bot-longevity setup-livekit-cloud revert-livekit-local test-livekit-tooling scan scan-agent-runner scan-meet benchmark benchmark-audio benchmark-full benchmark-report

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down -v

start: up migrate

stop: down

migrate:
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head

migration:
	$(COMPOSE) exec -T agent-runner uv run alembic revision --autogenerate -m "$(MSG)"

logs:
	$(COMPOSE) logs -f --tail=$(LOG_TAIL) $(SERVICE)

test: test-unit test-integration

test-unit:
	$(COMPOSE) up -d transport-server agent-runner meet
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner uv run python -m unittest discover -s tests -p "test_*.py" -v
	$(COMPOSE) exec -T meet pnpm test

test-integration:
	$(COMPOSE) up -d transport-server agent-runner meet
	$(COMPOSE) exec -T meet pnpm test:api
	$(COMPOSE) exec -T meet pnpm test:load

test-bot-longevity:
	$(COMPOSE) up -d transport-server agent-runner meet
	$(COMPOSE) exec -T agent-runner \
		env \
		RUN_BOT_LONGEVITY_TEST=1 \
		BOT_LONGEVITY_MAX_SECONDS=$(BOT_LONGEVITY_MAX_SECONDS) \
		BOT_LONGEVITY_POLL_SECONDS=$(BOT_LONGEVITY_POLL_SECONDS) \
		BOT_LONGEVITY_MESSAGE_SECONDS=$(BOT_LONGEVITY_MESSAGE_SECONDS) \
		uv run python -m unittest -v tests.test_bot_longevity_minimal

scan: scan-agent-runner scan-meet

scan-agent-runner:
	$(COMPOSE) up -d agent-runner
	$(COMPOSE) exec -T agent-runner uv run pip-audit

scan-meet:
	$(COMPOSE) up -d meet
	$(COMPOSE) exec -T meet pnpm audit

setup-livekit-cloud:
	./scripts/setup_livekit_cloud.sh setup \
		--url "$(LIVEKIT_CLOUD_URL)" \
		--api-key "$(LIVEKIT_API_KEY)" \
		--api-secret "$(LIVEKIT_API_SECRET)"

revert-livekit-local:
	./scripts/setup_livekit_cloud.sh revert

test-livekit-tooling:
	./scripts/test_setup_livekit_cloud.sh

benchmark-audio:
	$(COMPOSE) up -d agent-runner
	$(COMPOSE) exec -T agent-runner uv run python tests/generate_benchmark_audio.py

benchmark:
	$(COMPOSE) up -d transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env RUN_BENCHMARK=1 \
		BENCHMARK_SAMPLES=$(BENCHMARK_SAMPLES) \
		BENCHMARK_TIMEOUT=$(BENCHMARK_TIMEOUT) \
		uv run python -m unittest -v tests.test_benchmark

benchmark-full:
	$(COMPOSE) up -d transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env BENCHMARK_SAMPLES=$(BENCHMARK_SAMPLES) \
		BENCHMARK_TIMEOUT=$(BENCHMARK_TIMEOUT) \
		uv run python tests/run_benchmark_matrix.py

benchmark-report:
	$(COMPOSE) up -d agent-runner
	$(COMPOSE) exec -T agent-runner uv run python tests/run_benchmark_matrix.py report
