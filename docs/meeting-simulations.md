# Meeting simulations

A local harness that streams **synthetic audio** into a real LiveKit room and
reports the STT/VAD diagnostics. The point: reproduce the failure modes behind
high STT latency — and confirm fixes / tune the VAD — **before** a live meeting,
because we only get one shot at the real thing.

It builds on the same machinery as the latency benchmark: a programmatic LiveKit
participant publishes an audio track into the `--dev` transport-server, the bot
joins and runs the normal pipeline, and we read the results back from the
database. The new part is **procedurally generated audio** (noise, overlapping
speakers, pure tones, and the bot's own voice replayed back).

## TL;DR

```bash
make simulate SCENARIO=noise SNR_DB=5        # speech + background noise
make simulate SCENARIO=noise-bed             # continuous noise, no speech
make simulate SCENARIO=overlap SPEAKERS=3    # 3 people talking at once
make simulate SCENARIO=inaudible             # infrasonic + near-ultrasonic tones
make simulate SCENARIO=echo                  # bot hears its own voice
```

Watch the bot side in another terminal while a sim runs:

```bash
make logs SERVICE=agent-runner
```

## Scenarios

| `SCENARIO` | What it streams | What it tests |
|---|---|---|
| `noise` | The speech fixture mixed with background noise at a chosen signal-to-noise ratio | Does real background noise inflate STT latency or block the bot from responding? |
| `noise-bed` | Continuous noise, **no speech** | Does noise hold the voice detector "open" so it never finishes a turn? Do empty (phantom) turns appear? |
| `overlap` | Several participants streaming speech **at the same time** | Stress the per-speaker STT and the internal output queue (backlog → latency spikes) |
| `inaudible` | A ~12 Hz (infrasonic) tone and a ~11 kHz (near-ultrasonic) tone, played together | Does sound humans can barely hear reach the voice detector, or is it filtered out first? |
| `echo` | Records the bot's own spoken audio, then replays it back as if a person said it | The speakerphone problem: the bot hearing itself and replying to itself |

## Knobs

All optional; sensible defaults shown.

| Variable | Default | Meaning |
|---|---|---|
| `SCENARIO` | `noise` | Which scenario (above) |
| `SNR_DB` | `5` | Signal-to-noise ratio in dB. **Lower = noisier.** Try `10` (mild), `0`, `-5` (severe) |
| `NOISE` | `pink` | Noise type: `pink` (HVAC-like), `white`, `hum` (120 Hz), `hf` (7.5 kHz) |
| `DURATION` | `12` | Seconds of audio for `noise-bed` / `inaudible` |
| `SPEAKERS` | `3` | Number of concurrent talkers for `overlap` |
| `STT_MODEL` | (bot default — `parakeet-tdt-0.6b-v2`) | Override STT, e.g. `nova-3-general`, `gpt-4o-transcribe` |
| `ENDPOINTING_MS` | (bot default) | VAD end-of-turn silence: `50`, `100`, or `200` |

Example — find the noise level where STT starts failing:

```bash
for snr in 15 10 5 0 -5; do make simulate SCENARIO=noise SNR_DB=$snr; done
```

## Reading the output

Each run prints one row per bot turn:

```
 #    stt_ms  total_ms  qdepth  spike  echo  text
 1         -         -       -      -     -  Hello!                         ← greeting (no preceding speech)
 2     360.3    2111.0       0      -     -  The capital of France is Paris.
```

- **stt_ms** — speech-to-text latency (last audio in → transcript ready). This is
  the number we care about; healthy is a few hundred ms.
- **total_ms** — full user-perceived latency (speech → bot's first audio back).
- **qdepth** — internal output-queue depth at that turn. Non-zero/rising under
  `overlap` means backlog.
- **spike** — `YES` if `stt_ms` crossed the spike threshold (default 2000 ms).
- **echo** — `YES` if this turn looks like the bot reacting to its own voice.

**No rows at all** is itself a result: for `noise-bed` and `inaudible` it's
expected (there's no real speech). For `noise` it means the noise *prevented* the
bot from understanding — the failure we're hunting. In that case check
`make logs SERVICE=agent-runner` for `Phantom segment` and `STT latency spike`
lines.

## How the diagnostics get there

The bot writes a small `diag` block onto each turn it stores
(`stt_spike`, `self_echo`, `queue_depth`), alongside the existing latency
`timing`. The harness reads those straight from the database, so you don't need
a metrics stack running locally. The same fields power the Prometheus counters
(`meetlab.stt_spikes_total`, `meetlab.phantom_segments_total`,
`meetlab.self_echo_suspected_total`, `meetlab.stt_queue_depth`) in production.

## Soak / load test (`make soak`)

The single-room scenarios above reproduce *failure modes*. The soak reproduces *scale*:
**N rooms, each with M users and one bot, all holding a conversation at once** for D
minutes. It's the pre-event load check — the goal config is **10 rooms × 2 users × 1 bot
for 20 minutes**.

```bash
make soak                                     # 10 rooms, 2 users, 20 min (defaults)
make soak ROOMS=4 USERS_PER_ROOM=2 DURATION_MIN=5   # quick smoke
make soak STT_MODEL=nova-3-general            # compare against Deepgram
```

Each user loops the speech fixture with randomized inter-turn gaps (`MIN_GAP`/`MAX_GAP`),
the two users in a room staggered so turns mostly alternate. All rooms run concurrently.
`make soak` raises the bot token TTL to 30 min so sessions outlive the 15-min default.

**Knobs:** `ROOMS=10`, `USERS_PER_ROOM=2`, `DURATION_MIN=20`, `STT_MODEL`, `ENDPOINTING_MS`,
`MIN_GAP=3`, `MAX_GAP=8`, `SETTLE_SECS=4`, `STAGGER=4`.

**Reading the report:** aggregate `stt_ms`/`total_ms` P50/P95 across every turn in every room,
max queue depth, spike/self-echo counts, a per-room line, and a **verdict**. The verdict
hard-fails on two things only: any session left stuck on `running` (the hanging-status bug —
see below) and any session that produced zero bot turns. Latency is *informational*, because:

> **The local CPU sidecar is expected to saturate.** 10 rooms × 2 speakers = up to 20
> concurrent per-participant STT streams hitting one `stt-nemotron` container, which decodes
> serially. Rising `qdepth` and high `stt_ms` locally is the *intended* stress result, not a
> regression. For real latency-under-load numbers, point `AGENT_RUNNER_URL` / `LIVEKIT_URL` /
> `NEMOTRON_STT_URL` at the deployed stack (T4 GPU sidecar) and run the same harness.

**Session-end / DB consistency.** The soak doubles as a teardown check: when the 20 min are up
it disconnects every user (the all-users-leave path) and asserts each `Conversation` reached a
terminal status with `ended_at`. For the focused, deterministic version of these checks —
cancellation-safe finalize, the stale-conversation reconciler, and the all-users-leave path —
run `make test-session-lifecycle`.

## Notes & limits

- The bot has **no hot reload** — after editing `bot.py`, restart it
  (`docker compose -f .devcontainer/docker-compose.yml restart agent-runner`)
  before simulating, or the diagnostics won't reflect your change.
- Audio is generated at 24 kHz mono to match the checked-in speech fixtures
  (`tests/fixtures/benchmark_prompt.wav`). Regenerate with `make benchmark-audio`.
- The audio generators are pure functions with unit tests
  (`tests/test_audio_scenarios.py`) — no network needed to test them.
- `echo` captures whatever the bot is currently saying (usually its greeting).
  Raise `CAPTURE_SECS` if it captures silence.
