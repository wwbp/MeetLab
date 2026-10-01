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

.PHONY: up down start stop logs migrate migration test test-unit test-integration test-bot-longevity test-multi-speaker test-multi-speaker-audio test-session-lifecycle setup-livekit-cloud revert-livekit-local test-livekit-tooling test-infra scan scan-agent-runner scan-meet benchmark benchmark-audio benchmark-audio-long benchmark-audio-paused benchmark-full benchmark-exp2 benchmark-report simulate soak soak-sanity bench-stt-concurrency bench-idle-room

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
	@# Tests that call the live runner start real bot containers in rooms nobody joins;
	@# a bot alone never leaves yet (design iteration 4, should_leave). Clean them up.
	-docker rm -f $$(docker ps -aq --filter label=meetlab.session) 2>/dev/null
	$(COMPOSE) exec -T meet pnpm test
	$(COMPOSE) exec -T meet pnpm lint

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

test-multi-speaker:
	$(COMPOSE) up -d transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env RUN_MULTI_SPEAKER=1 \
		uv run python -m unittest -v tests.test_multi_speaker_e2e

test-multi-speaker-audio:
	$(COMPOSE) up -d transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env RUN_MULTI_SPEAKER=1 RUN_MULTI_SPEAKER_AUDIO=1 \
		uv run python -m unittest -v tests.test_multi_speaker_e2e.TestMultiSpeakerE2E.test_07_audio_two_speakers

# Session-end / DB-consistency: cancellation-safe terminal write, stale-conversation
# reconciler, and the end-to-end all-users-leave path. See Part E / docs.
test-session-lifecycle:
	$(COMPOSE) up -d transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env RUN_SESSION_LIFECYCLE_TEST=1 \
		uv run python -m unittest -v tests.test_session_lifecycle

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

benchmark-audio-long: benchmark-audio

# Paused-speech fixture: one question delivered in clauses with PAUSE_MS gaps, so
# the turn arrives fragmented like real meeting speech. The standard fixtures are
# single clean phrases and can never reproduce the 5s turn-commit stall (RC1).
# See docs/pilot-postmortem-2026-08.md.
PAUSE_MS ?= 400
benchmark-audio-paused:
	$(COMPOSE) up -d agent-runner
	$(COMPOSE) exec -T agent-runner \
		env PAUSE_MS=$(PAUSE_MS) \
		uv run python tests/generate_paused_speech_audio.py

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
		BENCHMARK_CONFIGS="$(BENCHMARK_CONFIGS)" \
		BENCHMARK_WAV="$(BENCHMARK_WAV)" \
		BENCHMARK_PARALLEL=$(BENCHMARK_PARALLEL) \
		uv run python tests/run_benchmark_matrix.py

# Meeting simulation: reproduce STT/VAD failure modes locally and read the
# diagnostics. SCENARIO=noise|noise-bed|overlap|inaudible|echo (default noise).
# Knobs: SNR_DB, NOISE=pink|white|hum|hf, DURATION, SPEAKERS, STT_MODEL, ENDPOINTING_MS.
# See docs/meeting-simulations.md.
SCENARIO ?= noise
simulate:
	$(COMPOSE) up -d --wait transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env SCENARIO=$(SCENARIO) \
		SNR_DB=$(SNR_DB) NOISE=$(NOISE) DURATION=$(DURATION) SPEAKERS=$(SPEAKERS) \
		STT_MODEL=$(STT_MODEL) ENDPOINTING_MS=$(ENDPOINTING_MS) \
		uv run python tests/simulate_meeting.py

# Multi-room soak / load test: ROOMS rooms x USERS_PER_ROOM users x 1 bot conversing
# for DURATION_MIN minutes, all concurrent. Reports aggregate latency, backlog, and a
# DB-consistency verdict (every session must end terminal). Locally the bot transcribes
# with in-process whisper-base (STT_MODEL_OVERRIDE), so no STT sidecar is needed.
# Point AGENT_RUNNER_URL/LIVEKIT_URL at prod for real latency numbers against the NIM.
# See docs/meeting-simulations.md.
# MOCK=1 swaps in zero-cost synthetic TTS (no paid TTS calls — the dominant soak cost),
# while the soak still exercises real STT-under-load, the bot-speaking window, and
# session teardown. `make soak` defaults to MOCK=1; set MOCK=0 for real TTS.
# The LLM is always pinned to the cheapest model (gpt-5.4-nano) by the harness, not
# mocked. STT is never mocked: locally it's in-process whisper-base (free, no download
# after first run); prod uses the self-hosted Parakeet NIM.
ROOMS ?= 10
USERS_PER_ROOM ?= 2
DURATION_MIN ?= 20
SOAK_MODE ?= stress
soak: MOCK ?= 1
soak:
	BOT_TOKEN_TTL_MINUTES=30 BOT_MOCK_TTS=$(MOCK) \
		$(COMPOSE) up -d --wait transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env SOAK_MODE=$(SOAK_MODE) ROOMS=$(ROOMS) USERS_PER_ROOM=$(USERS_PER_ROOM) DURATION_MIN=$(DURATION_MIN) \
		STT_MODEL=$(STT_MODEL) ENDPOINTING_MS=$(ENDPOINTING_MS) \
		uv run python tests/soak_meeting.py

# Sanity check FIRST: a small, strict run — every room's bot must reply and every
# session must finalize. Defaults to REAL (cheap) models so it validates the real
# pipeline; cost is pennies at this size. Run before scaling up to the full `make soak`.
# Override as you scale: make soak-sanity ROOMS=4 DURATION_SANITY=5 MOCK=1
soak-sanity: MOCK ?= 0
soak-sanity:
	BOT_TOKEN_TTL_MINUTES=30 BOT_MOCK_TTS=$(MOCK) \
		$(COMPOSE) up -d --wait transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env SOAK_MODE=sanity ROOMS=$(or $(ROOMS_SANITY),2) USERS_PER_ROOM=2 \
		DURATION_MIN=$(or $(DURATION_SANITY),2) \
		STT_MODEL=$(STT_MODEL) ENDPOINTING_MS=$(ENDPOINTING_MS) \
		uv run python tests/soak_meeting.py

# Direct STT-server concurrency benchmark: fire N concurrent transcriptions straight at
# the STT server (no LiveKit/bot) and measure latency vs concurrency. Point STT_URL at a
# reachable NIM (run from inside the VPC) to confirm it scales. The old-sidecar baseline
# is recorded in docs/gpu-stt-deployment.md. See that doc for the before/after table.
# Knobs: STT_URL (required), STT_MODEL, STT_LANGUAGE, CONCURRENCIES, REQUESTS_PER.
bench-stt-concurrency:
	$(COMPOSE) up -d --wait agent-runner
	$(COMPOSE) exec -T agent-runner \
		env STT_URL="$(STT_URL)" \
		STT_MODEL="$(STT_MODEL)" CONCURRENCIES="$(CONCURRENCIES)" REQUESTS_PER="$(REQUESTS_PER)" \
		uv run python tests/bench_stt_concurrency.py

# Idle-room longevity: start a bot in a room, let NOBODY join, and measure how long the
# room + bot stay up (expected ceiling ≈ BOT_TOKEN_TTL_MINUTES) and whether the session
# finalizes cleanly at teardown. Free (no human → no STT/LLM/TTS). Knobs: MAX_SECONDS,
# POLL_SECONDS, BOT_TOKEN_TTL_MINUTES.
bench-idle-room:
	BOT_TOKEN_TTL_MINUTES=$(or $(BOT_TOKEN_TTL_MINUTES),15) \
		$(COMPOSE) up -d --wait transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env MAX_SECONDS="$(MAX_SECONDS)" POLL_SECONDS="$(POLL_SECONDS)" \
		uv run python tests/bench_idle_room.py

benchmark-exp2:
	$(COMPOSE) up -d transport-server agent-runner
	$(COMPOSE) exec -T agent-runner uv run alembic upgrade head
	$(COMPOSE) exec -T agent-runner \
		env BENCHMARK_SAMPLES=$(BENCHMARK_SAMPLES) \
		BENCHMARK_TIMEOUT=$(BENCHMARK_TIMEOUT) \
		BENCHMARK_WAV="$(BENCHMARK_WAV)" \
		BENCHMARK_CONFIGS="nova-3-general / gpt-5.4-nano / elevenlabs [sentence],nova-3-general / gpt-5.4-nano / elevenlabs [sentence][ep=100]" \
		uv run python tests/run_benchmark_matrix.py

benchmark-report:
	$(COMPOSE) up -d agent-runner
	$(COMPOSE) exec -T agent-runner uv run python tests/run_benchmark_matrix.py report
