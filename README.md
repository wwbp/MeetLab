# MeetLab

A self-hosted conferencing and voice agent stack. One `make start` brings up a LiveKit media server, a FastAPI bot runner, and a Next.js app covering the conference UI, voice agent UI, and operator console — all in Docker.

## Services

| Service | Port | Role |
|---------|------|------|
| `transport-server` | 7880 | LiveKit media server (`--dev` mode) |
| `agent-runner` | 7860 | FastAPI — spawns Pipecat voice bots, stores transcripts |
| `meet` | 3000 | Next.js 16 — operator console, conference UI, voice agent UI |
| `egress` | — | LiveKit egress — composite room recording |
| `redis` | 6379 | Message bus for egress |

## Quick start

**Prerequisites:** Docker Desktop, OpenAI API key.

```bash
cp agent-runner/.env.runner.example agent-runner/.env.runner   # add OPENAI_API_KEY
cp meet/.env.local.example meet/.env.local                     # set CONSOLE_PASSWORD + BOT_RUNNER_SECRET
make start
```

Open `http://localhost:3000` and sign in with the password you set in `CONSOLE_PASSWORD`.

| URL | What it is |
|-----|-----------|
| `http://localhost:3000` | Operator console (login required) |
| `http://localhost:3000/agent` | Voice agent demo UI |
| `http://localhost:3000/rooms/<name>` | Conference room |
| `http://localhost:7860/health` | agent-runner health check |

## Operator console

After signing in at `/` you have three tabs:

- **Rooms & Bots** — create/delete rooms, start/stop bots, view event history
- **Bot Config** — edit system prompt, greeting, LLM model, TTS voice, VAD threshold; scoped globally or per room
- **DB Admin** — SQLAdmin over Postgres; view and edit speakers, conversations, utterances, events

## Environment

Both env files share `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` — the values must match. Local dev defaults are `devkey` / `secret`.

`BOT_RUNNER_SECRET` is a shared bearer token between `meet` and `agent-runner`. Set the same value in both files. Without it all agent-runner endpoints are open.

| File | Key variables |
|------|--------------|
| `agent-runner/.env.runner` | `OPENAI_API_KEY`, `LIVEKIT_URL`, `BOT_RUNNER_SECRET` |
| `meet/.env.local` | `LIVEKIT_URL_PUBLIC`, `LIVEKIT_URL_INTERNAL`, `BOT_RUNNER_URL`, `CONSOLE_PASSWORD`, `BOT_RUNNER_SECRET` |

## Commands

```bash
make start                    # build + start all services, run migrations
make stop                     # stop and remove volumes
make logs SERVICE=meet        # tail logs (meet | agent-runner | transport-server | egress | redis)

make test                     # unit + integration
make test-unit                # Python unittest (agent-runner) + Vitest (meet)
make test-integration         # concierge API + load tests (requires running stack)
make test-bot-longevity       # long-running bot drop timing test

make scan                     # pip-audit + pnpm audit for known CVEs

make migration MSG="add index" # generate Alembic migration
make migrate                  # apply pending migrations
```

### Bot longevity test knobs

| Variable | Default | Meaning |
|----------|---------|---------|
| `BOT_LONGEVITY_MAX_SECONDS` | `1050` | Test fails if bot drops before this many seconds |
| `BOT_LONGEVITY_POLL_SECONDS` | `5` | Polling interval |
| `BOT_LONGEVITY_MESSAGE_SECONDS` | `10` | Interval between chat messages sent to the bot |

## Recording

In local dev the egress container writes MP4 files to `./recordings/` at the project root. In production, LiveKit Cloud's managed egress writes directly to S3.

### Local dev

Recording is enabled by default. Join any room, click the **⚙ gear icon** in the control bar, open the **Recording** tab, and click **Start Recording**.

### Production (S3)

Set these on your deployment:

| Variable | Value |
|----------|-------|
| `STORAGE_BACKEND` | `s3` |
| `S3_BUCKET` | your bucket name |
| `S3_REGION` | e.g. `us-east-1` |
| `S3_KEY_ID` | IAM access key (`s3:PutObject` on the bucket) |
| `S3_KEY_SECRET` | IAM secret key |

Point the LiveKit Cloud webhook at `https://<your-domain>/api/concierge/webhooks/livekit`.

## Remote demos with ngrok

Switch to LiveKit Cloud first (local transport-server isn't reachable externally):

```bash
make setup-livekit-cloud \
  LIVEKIT_CLOUD_URL=wss://<project>.livekit.cloud \
  LIVEKIT_API_KEY=<key> \
  LIVEKIT_API_SECRET=<secret>
make start
ngrok http 3000
```

Share links of the form `https://<ngrok-domain>/rooms/<roomName>`. Revert with:

```bash
make revert-livekit-local && make start
```

## Known constraints

- All room/bot state in `meet` is in-memory — a restart clears everything. Sessions in flight are stranded.
- `livekit-server:latest` and `livekit/egress:latest` are unpinned — pin before production.
- LiveKit runs in `--dev` mode; `devkey`/`secret` defaults and no JWT validation.
- Bot token TTL is 15 minutes with no refresh path — long sessions will silently drop.
