# MeetLab

A self-hosted conferencing and voice agent stack. One `make start` brings up a LiveKit media server, a FastAPI bot runner, and a Next.js app covering the conference UI, voice agent UI, and operator console — all in Docker.

## Learn the repository

Start with the [architecture guide](docs/architecture.md) for system diagrams,
runtime flows, infrastructure, data, and design choices. With
[uv](https://docs.astral.sh/uv/getting-started/installation/) installed, run
`make docs` and open <http://127.0.0.1:8000> for the searchable documentation site.
It supports Mermaid, SVG/PNG figures, and LaTeX math; see
[writing diagrams](docs/writing-diagrams.md). `make docs-build` checks the site
and writes static HTML to `site/`.

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

## Participant cap

One room admits `MAX_PARTICIPANT_WORKERS` speech-recognition streams (default
`6`). Past that, a **new** participant is refused: their audio is not
transcribed, and the bot cannot hear them.

Refusal is deliberate. On 2026-08-19 an eight-room ramp accepted everyone,
exhausted recognition throughput and quietly went from 261 replies to 23 while
the processor sat at 77% — nothing said no. A refusal is visible; silent
degradation is not.

- The cap applies to **admission only**. Someone already in the meeting keeps
  their stream at the ceiling — throttling a healthy conversation to protect
  capacity breaks the thing being protected.
- A participant leaving **frees the slot**, even if their teardown raises.
- Each refusal logs once per participant (WARNING → the event log at `/events`)
  and increments `meetlab.participants_refused_total`.

Override with the `MAX_PARTICIPANT_WORKERS` env var on `agent-runner`. It needs
no deploy, so turning a room's ceiling down is the fastest lever during an
incident. The default of 6 was sized from the 19 Aug ramp on a **t3.medium**;
agent-runner has since moved to c6i.xlarge, so it is probably conservative —
raise it behind a measurement, not a guess.

## Known gaps and failure modes

Things that are broken, misleading, or will bite you. Kept here rather than in a
commit message because each one has already cost someone an afternoon.

### Late joiners can be silently dropped

A participant who joins **mid-session** sometimes has their data-channel message
never reach the bot at all — `on_data_received` never fires, no utterance is
written, and no reply comes. Reproduced in isolation roughly **one run in two**,
on `main` as well as on feature branches. The token grants `can_publish_data`, so
it is not a permissions problem.

`test_06_late_join` covers this path and is intermittently red as a result. Its
polls are deliberately left at pre-greeting counts so it is no stricter than it
has always been — see the comment in the test. **Unfixed.** It matters for any
session where people arrive at different times.

### The local bind mounts serve stale code — this will waste your afternoon

`agent-runner` and `meet` are both bind-mounted, but modifications to existing
files do **not** reliably propagate into the containers. New files do; edits do
not. A container will happily run a weeks-old copy of a file you just saved, and
your tests will pass — or fail — against code that is not on your disk.

Both have burned us in a single session:

- `agent-runner` ran an **Aug 19** copy of a file edited that morning, so two
  test runs reported results for code that no longer existed.
- `meet` failed one concierge integration test (`bot remove rejects identity
  mismatch`, returning a 404 page). Nothing was wrong with the code. After a
  restart the suite was 15/15.

**Restart the container after editing its source, before believing any result:**

```bash
docker compose -f .devcontainer/docker-compose.yml restart agent-runner
docker compose -f .devcontainer/docker-compose.yml restart meet     # slower to be ready
```

If a result surprises you, verify the container is running your code:

```bash
# these must match
md5 -q agent-runner/multi_speaker_stt.py
docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
  md5sum /app/multi_speaker_stt.py
```

Note the trap: `docker compose exec -T agent-runner wc -l < file` redirects on the
**host**, so it silently reads the host file and always appears to match. Pass
the path as an argument instead of redirecting.

The rule of thumb: **a surprising local test result is a stale container until
proven otherwise.** Restart and re-run before debugging the code.

### `make benchmark` reports OK while measuring nothing

Locally every stage comes back `None`/`0ms` and the run still exits `OK`:

```
LLM TTFT    10    0ms    0ms    0ms    0ms
```

Verified against a clean `main`, so it is not a regression from any current
branch — but the `BENCHMARK_SAMPLES=10` minimum in `docs/performance-tests.md` is
currently measuring nothing on local dev. Do not read local benchmark output as
evidence. **Unfixed.**

### The bot greets first, and utterance counts must allow for it

`on_first_participant_joined` speaks `bot_config.greeting`, which is stored as a
bot utterance. Consequences that have already produced false test results:

- The conversation's root is the **greeting**, so the first *user* utterance has
  `reply_to` set, not `None`.
- The greeting is the one bot utterance with `reply_to=None`.
- It is canned `TTSSpeakFrame` text that never goes through the LLM, so its
  `llm_ttft_ms` is a meaningless `0.0`. Assert timing on generated replies only.
- Any "N turns → 2N utterances" arithmetic is off by one. In the e2e suite
  `_poll_utterances` adds the greeting centrally; `plus_greeting=False` opts out
  for callers waiting on the greeting itself.

Four assertions in `test_multi_speaker_e2e.py` predated the greeting and were
failing on `main` for this reason alone, while the off-by-one poll counts let
other tests pass without ever waiting for the bot's reply.

### The worker-architecture rewrite is landed but dormant

`participant_pool.py`, `participant_router.py`, `responder_context.py` and
`listener_factory.py` are tested and **unwired** — nothing calls them. Only
`participant_workers.py` is live, via the participant cap above. Wiring the rest
means transplanting `bot.py` (~1349 lines) onto Pipecat's worker/bus model and
re-homing the interruption path from PR #72/#73, which ~1563 lines of tests
currently pin to `MultiSpeakerSTT`. That is a separate PR with its own 1/3/5/8
ramp — do not treat these files as load-bearing.

## Known constraints

- All room/bot state in `meet` is in-memory — a restart clears everything. Sessions in flight are stranded.
- `livekit-server:latest` and `livekit/egress:latest` are unpinned — pin before production.
- LiveKit runs in `--dev` mode; `devkey`/`secret` defaults and no JWT validation.
- Bot token TTL is 15 minutes with no refresh path — long sessions will silently drop.
