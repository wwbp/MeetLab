# Test plan — Parakeet default, multi-room soak, session-end consistency

Covers the changes on `feat/stt-parakeet-default-soak`:
1. STT default → `parakeet-tdt-0.6b-v2` @ `ep=100`
2. Multi-room soak harness (`make soak`)
3. Session-end / DB-consistency fix (cancellation-safe finalize + stale-conversation reconciler)
4. Configurable bot-token TTL (`BOT_TOKEN_TTL_MINUTES`)
5. `parakeet-unified-en-0.6b` selectable (offline)

## What each layer proves

| Layer | Command | Proves | Gating |
|---|---|---|---|
| Unit — defaults | `make test-unit` → `tests/test_defaults.py` | DB column defaults are parakeet + ep=100 | always |
| Unit — routing | `tests/test_bot.py::TestBuildSttParakeetChain` | `parakeet-*` (incl. `-unified-en-0.6b`) builds the sidecar chain | always |
| Unit — config API | `tests/test_runner_start.py` | `/config` accepts the new model ids, rejects junk | always |
| Lifecycle | `make test-session-lifecycle` | finalize survives cancellation; reconciler closes dead rooms; young sessions spared; all-users-leave → terminal | `RUN_SESSION_LIFECYCLE_TEST=1` |
| Token TTL | `make test-bot-longevity BOT_LONGEVITY_MAX_SECONDS=1260` | bot survives past 15 min with `BOT_TOKEN_TTL_MINUTES=30` | `RUN_BOT_LONGEVITY_TEST=1` |
| Unit — interruptions | `tests/test_interruption.py` + `tests/test_multi_speaker_stt.py` | talk-over detection logic + the real-onset callback wiring | always |
| Soak (sanity) | `make soak-sanity` | small STRICT run: every bot replies, every session finalizes — prove health before load | manual |
| Soak (local stress) | `make soak ROOMS=10 USERS_PER_ROOM=2 DURATION_MIN=20` | harness at scale; DB-consistency at scale; backlog + drop signature | manual |
| Soak (prod T4) | same, env pointed at deployed stack | real latency under ~20-way concurrency | manual |

## Pass / fail criteria

**Hard fail (must be green before merge):**
- All `make test-unit` pass.
- All `make test-session-lifecycle` pass (A finalize-under-cancel, B reconciler, B2 grace, C all-users-leave).
- `make soak-sanity` passes its STRICT verdict (every bot replies, every session finalizes).
- Any soak run: **every session reaches a terminal status with `ended_at`** (no row stuck on
  `running`) — enforced in every mode.

**Mode-dependent:**
- `sanity` mode: zero-bot-turns and early-drops are hard fails.
- `stress` mode: zero-turns / early-drops / high latency are **reported, not failed** — the local
  CPU sidecar is expected to saturate under ~20 concurrent streams. Real latency numbers come
  only from the prod-T4 run.

**Informational (recorded, not gating):**
- Soak `stt_ms` / `total_ms` P50/P95, max queue depth, spike / self-echo counts, per-room `quiet_s`.
- Bot interruptions: `meetlab.bot_interruptions_total` + `meetlab.bot_talkover_ms` (and the
  per-session log line). We want these low — the bot should yield, not talk over users.
- Machine-readable run log at `soak-results-<run>.json` for later tracing.

## Session-end / DB-consistency — the three teardown paths

The soak and `test-session-lifecycle` together cover what the user flagged ("status sometimes
stays hanging"):

| Path | Exercised by | Expected |
|---|---|---|
| All users leave | lifecycle C + end of every soak room | `task.cancel()` → shielded finalize commits `completed` + `ended_at` |
| Bot dies hard (OOM/kill, no finally) | lifecycle B (room-gone reconcile) | periodic reconciler marks it `ended` (min-age grace spares just-started sessions — lifecycle B2) |
| Normal completion | lifecycle A (finalize directly) | terminal status committed even if the caller is cancelled mid-write |

## Decisions applied

- **Graduated runs.** Sanity check first (`make soak-sanity`, strict), then scale to the full
  `make soak`. `SOAK_MODE` controls verdict strictness.
- **Drop detection + logging.** Per-room `quiet_s` + `DROP?` flag when the bot falls silent
  >`DROP_GAP_SECS` before the room ends; full run log written to `soak-results-<run>.json`.
- **Realistic overlap kept.** The two users overlap (not enforced turn-taking) — and we added
  interruption instrumentation so the bot talking over users is now measured, not assumed.

## Known gaps / open questions

- **Drop detection is gap-based, not live.** A bot that dies at minute 12 is flagged afterward by
  its `quiet_s`, not interrupted mid-run. Good enough for a post-run verdict; a live watchdog
  could come later.
- **Interruption metric covers VAD-based STT only** (default Parakeet, whisper, Deepgram). The
  OpenAI realtime path uses its own server-side interruption handling and isn't fed the metric.
- **Offline `parakeet-unified-en-0.6b`.** Selectable + routed, but the sidecar must be deployed
  with that model (bot doesn't send the model name) and offline mode does NOT deliver the ~160ms
  streaming latency — streaming is a separate, planned integration.

## Pre-run checklist

- `stt-nemotron` sidecar up (default model needs it): `make soak`/`make simulate` bring it up.
- First sidecar start downloads ~2.4 GB — warm it before timing anything.
- `make benchmark-audio` if `tests/fixtures/benchmark_prompt.wav` is missing.
- Restart `agent-runner` after editing `bot.py`/`runner.py` (no hot reload).
