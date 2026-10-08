# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What runs where

MeetLab v2: browser meetings with a voice bot, recorded for research. Production and staging are
the same Terraform stack (`infra/v2/stack`, `env = staging | prod`, sizes in `profiles.tf`),
deployed only by GitHub Actions: staging on merge (`infra-v2.yml`), production from a `v2.*` release tag after an approval (`release-v2.yml`); CI's own permissions are
`infra/v2/bootstrap`, applied by a person. Decisions, costs and measurements: `infra/v2/LEDGER.md`.
v1 (Elastic Beanstalk) is frozen on `main` and tagged `v1.0.0`; nothing of it lives on `v2`.

The local Docker stack (`.devcontainer/docker-compose.yml`) mirrors production so errors show up
early: same images, same speech-to-text path.

| Service | Dir | Role |
|---|---|---|
| `meet` | `meet/` | Next.js 16: participant rooms, the console (Rooms, Meetings, Events, Bot settings, Start links, Database) |
| `agent-runner` | `agent-runner/` | FastAPI on 7860: sessions in Postgres, starts one bot per meeting, recordings, Bot settings API, SQLAdmin at `/api/db` |
| `stt-cpu` | `stt-cpu/` | Speech-to-text: Parakeet int8 on CPU, `/v1/audio/transcriptions` (production's default; the GPU NIM is for studies past the measured switch point) |
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

# one runner test file, inside the stack (the venv is /venv, on PATH):
docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
  python -m unittest tests.test_runner_start -v
```

Test levels, types and pass criteria: `docs/testing.md`. Load tests run in AWS: the "Load test v2" workflow (`docs/load-testing.md`). Live tests run after
every staging deploy (`agent-runner/tests/acceptance_staging.py`). The runner has no hot reload:
`docker compose restart agent-runner` after editing it.

## Environment

```bash
cp agent-runner/.env.runner.example agent-runner/.env.runner   # OPENAI_API_KEY, ...
cp meet/.env.local.example meet/.env.local
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

## agent-runner

- `runner.py` (API, background loop), `bot.py` (pipeline), `bot_task.py` (a bot's process),
  `dispatch.py`, `sessions.py`, `heartbeat.py`, `rejoin.py`, `storage.py` (S3 via task roles),
  `migration_check.py` (v1 → v2 data copy check), `capacity.py` / `capacity_model.py` (Prepare for
  study; how much to run for N rooms).
- Image: two-stage `Dockerfile`; the venv is `/venv` (outside `/app`, so the dev mount doesn't hide
  it); bots start as `python -m bot_task` under an init (PID 1).
- Package manager `uv` (host); `config.py` loads `.env.runner` then `.env.runner.local`.

## meet

- Next.js App Router. The console lives in `app/(shell)` and renders `components/desk/*` and
  `components/console/*`; participants use `app/rooms/[roomName]` and `/api/connection-details`.
- `middleware.ts`: console auth is an allow-list of paths, so a new console page or admin API is
  PUBLIC until listed there; `middleware.test.ts` checks every `(shell)` page is covered.
- Bot settings (`app/(shell)/config/page.tsx`) is a hand-written form: a new `bot_config` column goes
  in three places (`db/models.py` + a migration, `runner.py` GET/PUT, the form);
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
