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

# Latency benchmarks (always BENCHMARK_SAMPLES=10 minimum — see docs/performance-tests.md)
make benchmark BENCHMARK_SAMPLES=10                    # default config
make benchmark-full BENCHMARK_SAMPLES=10 BENCHMARK_CONFIGS="<label>,<label>"  # specific configs
make benchmark-report                                  # re-print table from stored results
```

Latency docs: `docs/performance-tests.md` (how to run/read, team-facing),
`docs/latency-experiments.md` (experiment log with metric definitions and history).
Local tracing: Jaeger at `http://localhost:16686` (`ENABLE_TRACING=true` in `.env.runner`).
Note: agent-runner has no hot reload — `docker compose restart agent-runner` after editing
`bot.py`/`multi_speaker_stt.py`/`runner.py`.

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
2. `agent-runner /start` → records a `running` session, then starts one bot per meeting: an ECS task on staging (`BOT_DISPATCHER=ecs`) or a Docker container locally (`BOT_DISPATCHER=docker`); there is no in-process mode
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

- `runner.py` — FastAPI app; `POST /start` validates input, records the session, and dispatches the bot (`dispatch.py`); `bot_task.py` is the bot's own process: it reads the session row, mints its JWT, runs `bot()`, and heartbeats
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
- `lib/concierge/event-log.ts` — durable event log: admin actions, LiveKit webhooks and route failures are written to agent-runner's `events` table (not the old in-memory ring), where the runner and bots also mirror every WARNING+ log. Writes never throw and are deferred with `after()`; reads throw so the console can't render "no events" when the log is down. Console view at `/events`. See `docs/event-log.md`
- `middleware.ts` — console auth is a path allow-list, so a new console page or admin API is PUBLIC until added there; `middleware.test.ts` enumerates `app/(shell)` and asserts coverage. `/api/record/*` is knowingly public (participant's browser calls it) and still needs room-scoped auth
- `app/(shell)/config/page.tsx` — the console's Bot Config form is a hand-written field list, not generated from the runner's schema, and the two services' tests cannot see each other (each container mounts only its own directory). A new `bot_config` column therefore has to be added in **three** places or it is invisible: `agent-runner/db/models.py` + a migration, `agent-runner/runner.py` (`/config` GET response and PUT validation), and this form
- `lib/session-limit.ts` — advisory session time cap (`bot_config.session_limit_minutes`, 0 = unlimited); all logic is pure and unit-tested, `lib/SessionTimer.tsx` is a 1s tick over it. No stored deadline and no server timer: the countdown is derived from the earliest non-bot `joinedAt` LiveKit reports, so late joiners share one clock and an emptied room resets it. `/api/connection-details` hands the limit to the browser and degrades to unlimited if agent-runner is unreachable. See `docs/session-limits.md`
- `lib/study.ts` / `lib/completion-code.ts` — paid-study support. Pre-join requires a Prolific ID (prefilled from `?PROLIFIC_PID`, editable, validated client-side), which travels as LiveKit participant metadata and lands in `speakers.meta.prolific_id`. On leaving, the participant sees a completion code — `HMAC(LIVEKIT_API_SECRET, room:prolific_id)`, derived not stored — to paste into the survey. The bot announces the end of a capped session with `bot_config.closing_message`. See `docs/study-support.md`

## Browser & device support

The app requires WebRTC, `navigator.mediaDevices.getUserMedia`, and WebSockets. The binding constraint is `Cross-Origin-Embedder-Policy: credentialless` (set globally in `next.config.js`), which is required for `SharedArrayBuffer` — used by E2EE and the Krisp noise filter.

### Supported browsers

| Browser | Minimum version | Notes |
|---------|----------------|-------|
| Chrome / Chromium | 96 (Nov 2021) | First version with COEP `credentialless` |
| Edge | 96 (Nov 2021) | Same engine as Chrome |
| Firefox | 119 (Oct 2023) | First version with COEP `credentialless` |
| Safari (macOS) | 17 (Sep 2023) | First version with COEP `credentialless` |
| Safari (iOS) | 17 (Sep 2023) | All iOS browsers use WebKit; iOS 17 required |
| Chrome for Android | 96+ | Follows desktop Chrome |
| Samsung Internet | 24+ | Partial; not actively tested |

### Not supported

- Internet Explorer (any version) — no WebRTC
- Firefox < 119, Chrome < 96, Safari < 17
- iOS < 17 (all iOS browsers use WKWebView, constrained to OS WebKit version)
- Opera Mini — no WebRTC
- UC Browser — no WebRTC

### Feature detection

`meet/lib/browser-support.ts` exports `getBrowserSupport()`, `isCoreSupported()`, and `isEnhancedSupported()`. The root layout renders `UnsupportedBrowserGate` (client-only), which blocks the UI with a full-screen message when core APIs are absent. Unit tests are in `meet/lib/browser-support.test.ts`.

### Device notes

- Camera and microphone permissions are required for video/audio
- Minimum 4 CPU cores recommended for concurrent encode/decode; `isLowPowerDevice()` in `client-utils.ts` flags `hardwareConcurrency < 6`
- Responsive layout but optimised for landscape (tablet/desktop); voice agent UI is mobile-friendly
- No native mobile app; all mobile access is via browser

## Known constraints

- All concierge state is in-memory: a restart of `meet` resets all room claims, locks, and event history. Sessions in flight are stranded.
- `livekit-server:latest` is unpinned — pin to a specific version before any production use.
- The LiveKit server runs with `--dev` which uses `devkey`/`secret` and disables security checks.
- Bot identity detection (`isBotParticipant` in `bots/route.ts`) uses `identity.startsWith('bot_')` — any participant with that prefix is treated as a bot.
- Token TTL is 15 minutes with no refresh path; sessions longer than that will silently drop.
- `web-client/` directory is retained as historical reference but the service is removed from docker-compose; all functionality has been consolidated into `meet/`.
