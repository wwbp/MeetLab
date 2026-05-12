# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack overview

Four Docker services orchestrated via `.devcontainer/docker-compose.yml`:

| Service | Dir | Port | Role |
|---------|-----|------|------|
| `transport-server` | — | 7880-7882 | LiveKit media server (`--dev` mode) |
| `agent-runner` | `agent-runner/` | 7860 | FastAPI service that spawns Pipecat bots |
| `meet` | `meet/` | 3000 | Next.js 16 — conferencing UI + voice agent UI + Concierge admin API |
| `bastion` | — | — | Ubuntu dev container for VS Code attach |

All `make` targets delegate to `docker compose -f .devcontainer/docker-compose.yml`.

## Commands

```bash
# Stack lifecycle
make start           # build + start all services detached
make stop            # stop and remove volumes
make logs SERVICE=agent-runner   # tail logs for one service

# Tests (require running stack)
make test            # unit + integration
make test-unit       # agent-runner Python unittest + meet lint
make test-integration # meet concierge API + load tests
make test-bot-longevity BOT_LONGEVITY_MAX_SECONDS=1050   # long-running bot drop test

# Run a single Python test file inside the container
docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
  uv run python -m unittest tests.test_runner_start -v

# Run meet tests against a running stack
docker compose -f .devcontainer/docker-compose.yml exec -T meet \
  pnpm test:api     # concierge integration tests
docker compose -f .devcontainer/docker-compose.yml exec -T meet \
  pnpm test:load    # load tests

# LiveKit cloud switching
make setup-livekit-cloud LIVEKIT_CLOUD_URL=wss://... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
make revert-livekit-local
make test-livekit-tooling   # tests the switch script itself
```

## Environment setup

Two env files must exist before `make start`:
```bash
cp agent-runner/.env.runner.example agent-runner/.env.runner   # set OPENAI_API_KEY
cp meet/.env.local.example meet/.env.local
```

`LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` must be identical across both files. The local dev defaults are `devkey` / `secret`.

Key per-service variables:
- `agent-runner`: `OPENAI_API_KEY`, `LIVEKIT_URL=ws://transport-server:7880`
- `meet`: `LIVEKIT_URL_PUBLIC` (browser-facing), `LIVEKIT_URL_INTERNAL` (server-side, `ws://transport-server:7880` in Docker), `LIVEKIT_URL` (fallback), `BOT_RUNNER_URL=http://agent-runner:7860/`

## Architecture: request flow

**Voice agent UI (`meet/app/agent`):**
1. Browser → `POST /api/agent-connection` → creates room + participant token, calls `agent-runner /start` via `BOT_RUNNER_URL`
2. `agent-runner /start` → mints bot JWT, spawns `bot()` as a FastAPI `BackgroundTask`
3. `bot()` (Pipecat pipeline) joins the LiveKit room; STT → LLM → TTS runs until room ends
4. Browser connects to LiveKit directly using the returned token

**Desk admin UI (`meet/app/desk`):**
- `ConciergeConsole` component talks to the `/api/concierge/**` routes
- Room lifecycle (create, delete, metadata) via `RoomServiceClient` (LiveKit server SDK)
- Bot lifecycle guarded by three in-memory stores: `bot-start-lock-store` (mutex), `bot-room-claim-store` (one bot per room), `bot-requests-store` (request history)
- LiveKit webhooks (`POST /api/concierge/webhooks/livekit`) reconcile the in-memory claim store when bots leave

**Meet conference UI (`meet/app/rooms/[roomName]`):**
- Standard LiveKit Meet flow: landing → pre-join → `VideoConference` component
- `GET /api/connection-details` issues tokens for human participants (no bot runner involvement)
- COOP (`same-origin`) + COEP (`credentialless`) headers in `next.config.js` required for `SharedArrayBuffer` (E2EE, Krisp)

## agent-runner internals

- `runner.py` — FastAPI app; `POST /start` validates input, creates a LiveKit JWT for the bot, calls `background_tasks.add_task(bot, runner_args)`
- `bot.py` — Pipecat pipeline: `LiveKitTransport` → `OpenAISTTService` → `LLMContextAggregatorPair` → `OpenAILLMService` → `OpenAITTSService` → `LiveKitTransport`
- `config.py` uses `python-dotenv` to load `.env.runner` then `.env.runner.local` (override)
- Package manager: `uv`; run scripts with `uv run python ...`

## meet internals

- Next.js 16.2.4 App Router; all routes under `meet/app/`
- `lib/concierge/` — all in-memory state (no database); stores are plain `globalThis`-keyed Maps, reset on process restart
- `lib/concierge/livekit-admin.ts` — wraps `RoomServiceClient`; handles Docker hostname translation (`localhost` ↔ `transport-server`) and `ws://`↔`http://` URL conversion
- `lib/config/server.ts` — server-side env; `lib/config/client.ts` — client-side env (only `NEXT_PUBLIC_*` vars)
- Webhook verification uses LiveKit's `WebhookReceiver` with SHA-256 body hash in the `Authorization` header
- `components/agent/` — voice agent UI; `components/desk/` — concierge admin UI; `components/ui/` — shared primitives
- `app/agent/layout.tsx` and `app/desk/layout.tsx` are nested layouts (no `<html>`/`<body>`); they import `agent-globals.css` for Tailwind v4 theming
- Tailwind v4 configured via `@tailwindcss/postcss`; theme scoped to agent/desk via nested layout CSS imports
- `output: 'standalone'` in `next.config.js` — `meet/Dockerfile` is the single file for dev and prod; stages: `deps → dev → builder → runner`; docker-compose builds the `dev` target (alpine, hot-reload, source bind-mounted); production targets `runner` (non-root `nextjs` user, standalone output)
- Integration tests use Node's built-in test runner (`node --test`); unit tests use Vitest (`pnpm test`)

## Known constraints

- All concierge state is in-memory: a restart of `meet` resets all room claims, locks, and event history. Sessions in flight are stranded.
- `livekit-server:latest` is unpinned — pin to a specific version before any production use.
- The LiveKit server runs with `--dev` which uses `devkey`/`secret` and disables security checks.
- Bot identity detection (`isBotParticipant` in `bots/route.ts`) uses `identity.startsWith('bot_')` — any participant with that prefix is treated as a bot.
- Token TTL is 15 minutes with no refresh path; sessions longer than that will silently drop.
- `web-client/` directory is retained as historical reference but the service is removed from docker-compose; all functionality has been consolidated into `meet/`.
