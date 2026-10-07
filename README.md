# MeetLab

Browser meetings with a voice assistant, recorded for research: participants join a room from a
link, a bot listens to each person separately and answers, and every session is stored (turns per
speaker, per-speaker audio, video) for the study team.

## Learn the repository

The documentation site starts at [docs/index.md](docs/index.md) (`make docs` to browse it). The
architecture, its sizes and measured capacity are in
[docs/v2-infrastructure.md](docs/v2-infrastructure.md); decisions and costs in
[infra/v2/LEDGER.md](infra/v2/LEDGER.md).

## Quick start

**Prerequisites:** Docker Desktop and an OpenAI API key.

```bash
cp agent-runner/.env.runner.example agent-runner/.env.runner   # add OPENAI_API_KEY
cp meet/.env.local.example meet/.env.local                     # set CONSOLE_PASSWORD + BOT_RUNNER_SECRET
make start
```

Open `http://localhost:3000` and sign in with `CONSOLE_PASSWORD`. The local stack mirrors
production: the same images, and speech-to-text on the same CPU Parakeet server.

| URL | What it is |
|-----|-----------|
| `http://localhost:3000` | The console (login required) |
| `http://localhost:3000/rooms/<name>` | A meeting room |
| `http://localhost:7860/health` | agent-runner health |

## The console

- **Rooms**: create rooms, start and stop bots, record video, prepare for a study.
- **Meetings**: past sessions with transcripts, per-speaker audio and recordings.
- **Events**: the durable event log (errors and admin actions).
- **Bot settings**: the bot's prompt, greeting, models, voice and timing, globally or per room.
- **Start links**: one link per participant for a study.
- **Database ↗**: SQLAdmin over Postgres (console login required).

## Commands

```bash
make start / make stop        # the local stack
make test                     # unit + integration tests against it
make test-infra               # Terraform tests, offline
make docs                     # the documentation site
```

The full list, and how a meeting runs through the code, is in [CLAUDE.md](CLAUDE.md). Deploys go
through pull requests into `v2` (GitHub Actions); load tests run in AWS
([docs/load-testing.md](docs/load-testing.md)).

## Recording

Video recording uses LiveKit egress: locally the `egress` container writes to `recordings/`;
production records through LiveKit Cloud into S3. Per-speaker audio is written by the bot itself
and survives a failed video recording. With auto-record on, a session that can't get a recorder
within 10 minutes is marked as having a failed recording, so the console shows it.

## Participant cap

One room admits `MAX_PARTICIPANT_WORKERS` speech-recognition streams (default `6`). Past that, a
**new** participant is refused: their audio is not transcribed, and the bot cannot hear them. The
refusal is deliberate (a silent overload once dropped replies from 261 to 23) and is logged once
per participant as a WARNING in the event log. Someone already in the meeting keeps their stream;
a participant leaving frees the slot. Override it with the `MAX_PARTICIPANT_WORKERS` env var on
agent-runner.

## Known constraints

- A participant whose identity starts with `bot_` is treated as a bot.
- Meet keeps some display state in memory (track-subscription signals); a restart resets those
  views, not sessions.
