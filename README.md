# LiveKit Meet Lab

A local development stack combining conferencing, voice agent, and admin UI in a single Next.js app.

## Services

| Service | Port | Role |
|---------|------|------|
| `transport-server` | 7880 | LiveKit media server |
| `redis` | 6379 | Message bus between transport-server and egress |
| `egress` | — | LiveKit egress — composite room recording |
| `agent-runner` | 7860 | FastAPI — spawns Pipecat voice bots |
| `meet` | 3000 | Next.js 16 — conferencing, voice agent UI, Concierge admin |

## Quick Start

**Prerequisites:** Docker Desktop, OpenAI API key.

```bash
cd stacks/r3-livekit-meet-lab
cp agent-runner/.env.runner.example agent-runner/.env.runner   # set OPENAI_API_KEY
cp meet/.env.local.example meet/.env.local
make start
```

Open:
- `http://localhost:3000` — voice agent
- `http://localhost:3000/desk` — Concierge admin
- `http://localhost:3000/rooms/<name>` — Meet conferencing
- `http://localhost:7860/health` — agent-runner health check

## Commands

```bash
make start                          # build + start all services
make stop                           # stop and remove volumes
make logs SERVICE=meet              # tail service logs (agent-runner | meet | transport-server | egress | redis)
make test                           # unit + integration tests
make test-bot-longevity             # long-running bot drop timing test
make setup-livekit-cloud \
  LIVEKIT_CLOUD_URL=wss://... \
  LIVEKIT_API_KEY=... \
  LIVEKIT_API_SECRET=...            # switch to LiveKit Cloud
make revert-livekit-local           # revert to local LiveKit
make test-livekit-tooling           # test the cloud-switch script
```

Bot longevity test knobs (all optional):

| Variable | Default | Meaning |
|----------|---------|---------|
| `BOT_LONGEVITY_MAX_SECONDS` | `1050` | Test fails if bot drops before this many seconds |
| `BOT_LONGEVITY_POLL_SECONDS` | `5` | Polling interval |
| `BOT_LONGEVITY_MESSAGE_SECONDS` | `10` | Interval between chat messages sent to bot |

## Environment

`LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` must match across both env files. Local defaults are `devkey` / `secret`.

| File | Key variables |
|------|--------------|
| `agent-runner/.env.runner` | `OPENAI_API_KEY`, `LIVEKIT_URL=ws://transport-server:7880` |
| `meet/.env.local` | `LIVEKIT_URL_PUBLIC`, `LIVEKIT_URL_INTERNAL`, `BOT_RUNNER_URL`, `STORAGE_BACKEND`, recording vars |

## Recording

Recording uses LiveKit Egress. In local dev the egress container writes MP4 files to `./recordings/` (bind-mounted, created automatically). In staging/production LiveKit Cloud's managed egress writes directly to S3.

### Local dev

Recording is enabled by default via `meet/.env.local`. Join any room at `http://localhost:3000/rooms/<name>`, click the **⚙ gear icon** in the control bar, open the **Recording** tab, and click **Start Recording**. A red dot appears for all participants while recording is active.

Recordings land in `recordings/` at the project root as `<timestamp>-<roomName>.mp4`.

### Staging / production (S3)

Set these environment variables on your deployment (Elastic Beanstalk, etc.):

| Variable | Value |
|----------|-------|
| `NEXT_PUBLIC_SHOW_SETTINGS_MENU` | `true` |
| `NEXT_PUBLIC_LK_RECORD_ENDPOINT` | `/api/record` |
| `STORAGE_BACKEND` | `s3` |
| `S3_BUCKET` | `meetlab-data` |
| `S3_REGION` | `us-east-1` |
| `S3_KEY_ID` | IAM access key |
| `S3_KEY_SECRET` | IAM secret key |

The IAM key needs `s3:PutObject` on `arn:aws:s3:::meetlab-data/*`. LiveKit Cloud egress writes the file; the meet server only initiates the request.

Configure the LiveKit Cloud webhook in your project dashboard to point at:
```
https://<your-domain>/api/concierge/webhooks/livekit
```

## Remote demos with ngrok (optional)

For external access, first point the stack at a public LiveKit endpoint (LiveKit Cloud recommended):

```bash
make setup-livekit-cloud \
  LIVEKIT_CLOUD_URL=wss://<project>.livekit.cloud \
  LIVEKIT_API_KEY=<key> \
  LIVEKIT_API_SECRET=<secret>
make start
```

Verify the public URL is returned:

```bash
docker compose -f .devcontainer/docker-compose.yml exec -T meet \
  curl -sS "http://localhost:3000/api/connection-details?roomName=smoke&participantName=smoke"
```

Then tunnel port 3000:

```bash
ngrok http 3000
```

Share links of the form `https://<ngrok-domain>/rooms/<roomName>`.

Revert when done:

```bash
make revert-livekit-local && make start
```

## Known constraints

- All concierge state is in-memory — a `meet` restart clears all room claims and bot assignments.
- `livekit-server:latest` and `livekit/egress:latest` are unpinned; pin before any production deployment.
- LiveKit runs in `--dev` mode (`devkey`/`secret`, no security checks).
- Token TTL is 15 minutes with no refresh path.
- The `recordings/` directory is excluded from git; files persist across `make start/stop` but are deleted by `make down` (removes volumes and bind-mounts are unaffected — files remain on disk).
