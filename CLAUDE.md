# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What runs where

MeetLab v2: browser meetings with a voice bot, recorded for research. Production is
the Terraform stack `infra/stack` (`env = prod`; `staging` remains a profile, unused), sizes in `profiles.tf`,
one production at meet.wwbp.org, no staging (since 2026-10-14). A merge builds images (`build.yml`); only a `v2.*` release tag deploys, after an approval (`release.yml`); CI's own permissions are
`infra/bootstrap`, applied by a person. Decisions, costs and measurements: `infra/LEDGER.md`.
`main` is v2 (since 2026-10-14). v1 (Elastic Beanstalk, removed) is the tag `v1.0.0` and the archive branch `v1`.

The local Docker stack (`.devcontainer/docker-compose.yml`) mirrors production so errors show up
early: same images, same speech-to-text path.

| Service | Dir | Role |
|---|---|---|
| `meet` | `services/web` | Next.js 16: participant rooms, the console (Rooms, Meetings, Events, Bot settings, Start links, Database) |
| `agent-runner` | `services/api`, `services/bot`, `packages/core` | FastAPI on 7860: sessions in Postgres, starts one bot per meeting, recordings, Bot settings API, SQLAdmin at `/api/db` |
| `stt-cpu` | `services/stt` | The bot's speech-to-text, one server shared by every bot: Parakeet int8 on CPU, `/v1/audio/transcriptions` (production's default; the GPU NIM is for studies past the measured switch point) |
| `transport-server` | — | LiveKit (production uses LiveKit Cloud; staging self-hosts) |
| `egress`, `redis` | — | local video recording |
| `postgres` | — | the database |
| `docker-socket-proxy` | — | lets the runner start bot containers locally (`BOT_DISPATCHER=docker`); ECS tasks in AWS |
| `bastion` | — | dev container for VS Code attach |

## Commands

```bash
make start / make stop          # local stack up (build + migrate) / down
make logs SERVICE=agent-runner

make test                       # unit + integration against the local stack (ci.yml)
make test-static                # static analysis: dead code, image budget, docs, meet lint/tsc/knip
make test-unit                  # runner unittest + meet vitest
make test-infra                 # terraform fmt/validate/test, offline (mock provider)
make test-stt-cpu               # the CPU speech server against recorded clips
make test-config-parity         # a Bot settings field exists in DB, API and form
make sim-attribution            # overlapping speakers each heard as themselves

# one test file, inside the stack, from its package's directory (each package has a `tests`
# package; the image's PYTHONPATH finds the code):
docker compose -f .devcontainer/docker-compose.yml exec -T -w /app/services/api agent-runner \
  python -m unittest tests.test_runner_start -v
```

Test levels, types and pass criteria: `docs/testing.md`. Load tests run in AWS: the "Load test" workflow (`docs/load-testing.md`). Live tests run after
every release, on production (`infra/live-tests.sh`). The runner has no hot reload:
`docker compose restart agent-runner` after editing it.

## Environment

```bash
cp .env.example .env   # OPENAI_API_KEY, ...
cp services/web/.env.local.example services/web/.env.local
```
`LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` must match across both (local: `devkey` / `secret`).
In AWS, secrets live in SSM under `/meetlab-v2/<env>/`, never in Terraform state.

## How a meeting runs

1. The console (or a start link) asks meet; meet calls the runner's `POST /start` (`BOT_RUNNER_URL`).
2. The runner records a `running` session (one per room, enforced in Postgres; a repeat is 409) and
   dispatches the bot (`dispatch.py`): an ECS task in AWS, a container locally.
3. `bot_task.py` reads its session row, mints its LiveKit token, runs `bot()` and heartbeats.
   `bot.py`: LiveKit transport → per-speaker STT (`multi_speaker_stt.py`) → LLM → TTS → LiveKit.
4. The runner's background loop (`reconcile_tick`, every 10 s) fails silent sessions, rejoins a
   dead bot with context (`rejoin.py`), closes sessions whose room is gone, and syncs recordings.

## Layout

| Dir | Python package | What it is |
|---|---|---|
| `services/web` | — | Next.js: rooms and the console (below) |
| `services/api` | `meetlab_api` | `runner.py` (API, background loop), `dispatch.py`, `capacity.py` / `capacity_model.py` (Prepare for study; how much to run for N rooms), `transcript.py` |
| `services/bot` | `meetlab_bot` | `bot.py` (pipeline), `bot_task.py` (a bot's process), `multi_speaker_stt.py`, `smart_turn.py`, `interruption.py`, … |
| `packages/core` | `meetlab_core` | shared by both: `config.py`, `db/` (models, engine) and `migrations/` (Alembic, `packages/core/alembic.ini`), `sessions.py`, `heartbeat.py`, `rejoin.py`, `storage.py` (S3 via task roles), `event_log.py` |
| `services/stt` | — | the speech-to-text server (FastAPI + Parakeet ONNX), its own image |
| `tools/loadtest` | `meetlab_loadtest` | the load-test harness (runs in AWS as the load generator), the conversation library, quality scores |
| `tests/integration`, `tests/system` | — | tests across services; live tests (`acceptance.py`), permission contract, parity checks |

- **Boundaries** (`.importlinter`, checked by `make test-static`): the API and the bot never import
  each other; `meetlab_core` imports neither; services never import `meetlab_loadtest`.
- One `pyproject.toml` / `uv.lock` at the root (package manager `uv`). Image:
  `services/api/Dockerfile`, built from the repo root; the venv is `/venv` (outside `/app`, so the
  dev mount of the repo doesn't hide it), the code is found through `PYTHONPATH`; bots start as
  `python -m meetlab_bot.bot_task` under an init (PID 1).
- `config.py` loads the root `.env`, then `.env.local`.

## web (`services/web`)

- Next.js App Router. The console lives in `app/(shell)` and renders `components/desk/*` and
  `components/console/*`; participants use `app/rooms/[roomName]` and `/api/connection-details`.
- `middleware.ts`: console auth is an allow-list of paths, so a new console page or admin API is
  PUBLIC until listed there; `middleware.test.ts` checks every `(shell)` page is covered.
- Bot settings (`app/(shell)/config/page.tsx`) is a hand-written form: a new `bot_config` column goes
  in three places (`packages/core/meetlab_core/db/models.py` + a migration, `services/api/meetlab_api/runner.py` GET/PUT, the form);
  `make test-config-parity` checks all three. Model names: `lib/model-choices.ts`.
- Session limits: `lib/session-limit.ts` (`docs/session-limits.md`). Study flow: `lib/study.ts`,
  `lib/completion-code.ts` (`docs/study-support.md`). Event log: `lib/concierge/event-log.ts`
  (`docs/event-log.md`).
- COOP/COEP headers in `next.config.js` are required for `SharedArrayBuffer` (E2EE, Krisp), which
  sets the browser floor: Chrome/Edge 96, Firefox 119, Safari 17 (`lib/browser-support.ts`).

## Known constraints

- Bot identity is `identity.startsWith('bot_')`.
- Meet keeps some display state in memory (track-subscription signals); a restart resets views,
  not sessions.
