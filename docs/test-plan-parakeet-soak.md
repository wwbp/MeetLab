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
| Soak (local) | `make soak ROOMS=10 USERS_PER_ROOM=2 DURATION_MIN=20` | harness runs at scale; DB-consistency at scale; backlog signature | manual |
| Soak (prod T4) | same, env pointed at deployed stack | real latency under ~20-way concurrency | manual |

## Pass / fail criteria

**Hard fail (must be green before merge):**
- All `make test-unit` pass.
- All `make test-session-lifecycle` pass (A finalize-under-cancel, B reconciler, B2 grace, C all-users-leave).
- Soak verdict: **every session reaches a terminal status with `ended_at`** (no row stuck on `running`), and **no session produces zero bot turns**.

**Informational (recorded, not gating):**
- Soak `stt_ms` / `total_ms` P50/P95, max queue depth, spike / self-echo counts.
- Local CPU sidecar is **expected to saturate** under ~20 concurrent streams (rising `qdepth`,
  high `stt_ms`) — this is the intended stress result, not a regression. Real latency numbers
  come only from the prod-T4 run.

## Session-end / DB-consistency — the three teardown paths

The soak and `test-session-lifecycle` together cover what the user flagged ("status sometimes
stays hanging"):

| Path | Exercised by | Expected |
|---|---|---|
| All users leave | lifecycle C + end of every soak room | `task.cancel()` → shielded finalize commits `completed` + `ended_at` |
| Bot dies hard (OOM/kill, no finally) | lifecycle B (room-gone reconcile) | periodic reconciler marks it `ended` (min-age grace spares just-started sessions — lifecycle B2) |
| Normal completion | lifecycle A (finalize directly) | terminal status committed even if the caller is cancelled mid-write |

## Known gaps / open questions (for review)

- **No mid-run liveness check.** Drops are inferred post-hoc (terminal status + per-room turn
  counts), not detected live. A bot dropping at minute 12 shows up as a low turn count, not an
  explicit alert. Decide whether to add an in-run turn-rate watchdog.
- **Conversation realism.** Two users free-run their fixture loops with a random initial stagger,
  so turns overlap rather than strictly alternate. Overlap stresses multi-speaker STT (arguably
  realistic) — confirm that's the intent vs enforced turn-taking.
- **Offline `parakeet-unified-en-0.6b`.** Selectable + routed, but the sidecar must be deployed
  with that model (bot doesn't send the model name) and offline mode does NOT deliver the ~160ms
  streaming latency — streaming is a separate, planned integration.

## Pre-run checklist

- `stt-nemotron` sidecar up (default model needs it): `make soak`/`make simulate` bring it up.
- First sidecar start downloads ~2.4 GB — warm it before timing anything.
- `make benchmark-audio` if `tests/fixtures/benchmark_prompt.wav` is missing.
- Restart `agent-runner` after editing `bot.py`/`runner.py` (no hot reload).
