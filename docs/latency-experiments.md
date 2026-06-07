# Voice Pipeline Latency Experiments

Step-by-step log of latency optimization experiments. Each experiment records the hypothesis,
the code change, the benchmark commands, and the measured result.

Key metric: **E2E P50** (`stt_done → first TTS audio chunk`), measured inline in
`on_assistant_turn_stopped` from existing monotonic timers.
Supporting metrics stored in `Utterance.meta["timing"]`: `llm_ttft_ms`, `tts_first_ms`.
Derived: `stt_ms = e2e_ms - llm_ttft_ms - tts_first_ms` (≈ 0ms with current E2E definition,
since E2E is post-transcript only — does NOT include Deepgram endpointing or STT round-trip).

> **E2E definition note:** `stt_done` is set when the transcript is committed to the LLM context
> (inside `on_user_turn_stopped`). So E2E = LLM TTFT + TTS TTFB. True end-to-end from user
> silence would need to add Deepgram endpointing (~200ms) + STT round-trip (~500ms) on top.

Benchmark data lives in `agent-runner/tests/fixtures/benchmark_results.json`.
Run commands are in `Makefile`; always use `BENCHMARK_SAMPLES=10` for meaningful percentiles
(the `_pct()` function with 3 samples returns the minimum, not the median).

---

## Baseline — May 27, 2026

Full 40-config sweep, 3 samples each. See full report at `docs/benchmark-report-2026-05-27.md`.

**Best config:** `nova-3-general + gpt-5.4-nano + elevenlabs`

| Stage | P50 | Raw values (3 samples) |
|-------|-----|------------------------|
| STT (derived) | 694ms | 693.6, 706.5, 2617.3 |
| LLM TTFT | 333ms | 333.4, 571.6, 1015.2 |
| TTS first chunk | 239ms | 238.9, 320.6, 1016.2 |
| **E2E** | **1,279ms** | 1278.8, 1585.8, 4648.7 |

> **⚠ Only 3 samples.** With the current `_pct()` formula, P50 of 3 values is the minimum,
> not the median. True medians: STT≈707ms, LLM≈572ms, TTS≈321ms, E2E≈1586ms.
> Re-run with 10 samples for trustworthy numbers before drawing conclusions.

**What `stt_ms` actually measures:** time from `UserStoppedSpeakingFrame` (from `_FrameCollector`'s
synthetic VAD wrap) to `on_user_turn_stopped` firing. For the Deepgram path this includes the
Deepgram `endpointing=200ms` silence wait + cloud round-trip + async queue hops. It is NOT a
direct measure of transcription quality — Deepgram streams tokens while audio plays.

**Why OpenAI Realtime Whisper is ~1,750ms STT:** it batches the full audio buffer before
transcribing (Pipecat's `OpenAIRealtimeSTTService` uses server-side VAD that waits for the
entire utterance). Deepgram streams so the transcript is nearly complete by the time silence
is detected. Stick with Deepgram for latency.

**Why OpenAI TTS is ~900–1,500ms:** OpenAI's streaming TTS has high TTFB. ElevenLabs
streams audio within ~240ms. Keep ElevenLabs.

---

## Experiment 1 — TTS TextAggregationMode: SENTENCE vs TOKEN

**Status:** ✅ Complete

**Hypothesis:** Pipecat's `ElevenLabsTTSService` defaults to `TextAggregationMode.SENTENCE`,
buffering LLM tokens until a sentence boundary (`.`, `?`, `!`) before sending to ElevenLabs.
Switching to `TextAggregationMode.TOKEN` streams each token directly, reducing `tts_first_ms`
by removing the sentence-boundary wait. Expected saving: 50–200ms on `tts_first_ms`.

**Tradeoff:** TOKEN mode sends very short text chunks to ElevenLabs. Prosody (naturalness)
may degrade because ElevenLabs has less context for intonation. Listen carefully during the
benchmark and rate prosody subjectively alongside latency numbers.

**Code change:** `BotConfig.tts_aggregation_mode` column added (default `"sentence"`).
Exposed via `GET/PUT /config`. Benchmark matrix tests both variants side-by-side.

**Schema:** `tts_aggregation_mode`: `"sentence"` | `"token"`

### Step 1 — Establish reliable 10-sample baseline

```bash
make benchmark-full \
  BENCHMARK_SAMPLES=10 \
  BENCHMARK_CONFIGS="nova-3-general / gpt-5.4-nano / elevenlabs [sentence]"
```

> Run this FIRST before comparing to TOKEN mode results. Records under label
> `nova-3-general / gpt-5.4-nano / elevenlabs [sentence]` in benchmark_results.json.

### Step 2 — Run TOKEN-mode variant

```bash
make benchmark-full \
  BENCHMARK_SAMPLES=10 \
  BENCHMARK_CONFIGS="nova-3-general / gpt-5.4-nano / elevenlabs [token]"
```

### Step 3 — Compare

```bash
make benchmark-report
```

Look for delta in `tts_first_ms` mean/P50. Also listen to a live bot session using TOKEN
mode and rate prosody (1–5 scale) vs SENTENCE mode.

### Results

Three benchmark runs with increasing audio complexity (all using `nova-3-general + gpt-5.4-nano + elevenlabs`, 10 samples each, 2026-06-07).

#### Run A — Short audio: "What is the capital of France?" (1.7s, ~1-sentence bot reply)
Benchmark runs 60–61. The bot's reply is ~1 short sentence (~8 tokens). SENTENCE mode sends it
in one TTS call; TOKEN mode sends ~8 separate micro-calls.

| Variant | LLM P50 | TTS first P50 | E2E P50 |
|---------|---------|---------------|---------|
| SENTENCE | 365ms | 232ms | 576ms |
| TOKEN | 322ms | **221ms** | **549ms** |
| Δ | −43ms | −11ms | −27ms |

#### Run B — Medium audio: "Briefly explain how DNS works, 2-3 sentences" (8s, ~3-4 sentence bot reply)
Benchmark runs 64–65. The bot generates ~80-120 tokens across several sentences.

| Variant | LLM P50 | TTS first P50 | E2E P50 | TTS successes |
|---------|---------|---------------|---------|---------------|
| SENTENCE | 292ms | **150ms** | **446ms** | 6/10 |
| TOKEN | 270ms | 348ms | 603ms | 3/10 |
| Δ | −22ms | **+198ms** | **+157ms** | |

#### Run C — Long audio: Internet explanation (11s, ~560-token bot reply)
Benchmark run 62–63. TOKEN mode hit the 45s timeout on 10/10 samples.

| Variant | LLM P50 | TTS first P50 | E2E P50 | TTS successes |
|---------|---------|---------------|---------|---------------|
| SENTENCE | 311ms | 150ms | 470ms | 6/10 |
| TOKEN | 297ms | timeout | timeout | 0/10 |

#### Interpretation

**TOKEN mode with ElevenLabs is worse for responses longer than ~1 sentence.** The failure
mode: in TOKEN mode, each LLM token creates its own ElevenLabs `context_id` (separate
synthesis call). ElevenLabs is designed for complete phrases, not sub-word fragments —
it generates better audio quality and lower latency per synthesis unit when receiving
a full sentence than when receiving 8 individual "words" or tokens. For a 3-sentence
answer (~80 tokens), TOKEN mode runs 80 micro-synthesis calls on the same WebSocket,
producing the first audio chunk ~200ms LATER than SENTENCE mode.

For the short "capital of France" response, the first sentence is only ~8 tokens, so
the SENTENCE mode boundary is hit quickly anyway. This is why the improvement looks
marginal (−11ms) — SENTENCE mode barely buffers anything for a 1-sentence response.

**Recommendation: keep `TextAggregationMode.SENTENCE` (the default)**. TOKEN mode would
only help for a TTS backend with true streaming accumulation (e.g. a local TTS server that
accepts partial tokens and flushes chunks on its own schedule). ElevenLabs is not such a backend.

Prosody evaluation no longer needed — TOKEN mode is eliminated on latency grounds.

**What the experiment revealed about the architecture:**
- `tts_first_ms` is measured from first LLM token to first `TTSAudioRawFrame` (generation latency, not playback)
- `on_assistant_turn_stopped` fires when the LLM finishes generating, which is BEFORE TTS
  has generated audio in TOKEN mode (for long responses). 7/10 samples for TOKEN mode
  recorded LLM TTFT but no TTS timing — the turn ends before the first audio chunk arrives.
- The TTS serialization queue should hold `LLMFullResponseEndFrame` until audio contexts drain,
  but in TOKEN mode with many empty/minimal-audio contexts, this protection sometimes fails.

---

## Experiment 2 — Deepgram endpointing: 200ms → 100ms

**Status:** 📋 Ready to run

**Hypothesis:** `DeepgramSTTService` is configured with `endpointing=200` (200ms server-side
silence detection — Deepgram holds the transcript until 200ms of silence is confirmed).
This wait is the dominant fixed cost after the user stops speaking (endpointing latency is
independent of LLM or TTS). Reducing to 100ms shaves ~100ms from the true E2E
(VAD-stop → first bot audio). Expected: E2E P50 drops from ~700ms → ~600ms.

**Risk:** Shorter endpointing raises the false-termination rate — mid-sentence pauses (e.g.
"I want to ask about... DNS") can trigger a premature transcript commit. This degrades
conversation quality even if latency numbers improve. **Qualitative evaluation required.**

**What changed:** `stt_endpointing_ms` is now a `BotConfig` field (default `200`).
No code deploy needed to switch — use the API or admin UI at `/console/admin`.

```bash
# Switch to 100ms on the running stack (no restart needed)
curl -s -X PUT http://localhost:7860/config \
  -H "Content-Type: application/json" \
  -d '{"scope":"global","stt_endpointing_ms":100}'

# Revert
curl -s -X PUT http://localhost:7860/config \
  -H "Content-Type: application/json" \
  -d '{"scope":"global","stt_endpointing_ms":200}'
```

### Step 1 — CPU local benchmark (baseline confirmation + 100ms comparison)

```bash
# Short audio — confirms 200ms baseline matches Experiment 1 numbers
make benchmark-exp2 BENCHMARK_SAMPLES=10

# Long audio — DNS question is the harder test for false endpointing
make benchmark-exp2 BENCHMARK_SAMPLES=10 \
  BENCHMARK_WAV=tests/fixtures/benchmark_prompt_long.wav
```

Both runs together: `benchmark-exp2` always compares `ep=200` and `ep=100` side-by-side
in one command. Results go to `tests/fixtures/benchmark_results.json` with labels
`nova-3-general / gpt-5.4-nano / elevenlabs [sentence]` and
`nova-3-general / gpt-5.4-nano / elevenlabs [sentence][ep=100]`.

### Step 2 — GPU / AWS benchmark

Same commands, but run against the deployed AWS stack:

```bash
AGENT_RUNNER_URL=https://agent-runner.your-domain.com \
LIVEKIT_URL=wss://your-livekit.livekit.cloud \
make benchmark-exp2 BENCHMARK_SAMPLES=10
```

AWS colocation should also compress LLM+TTS times. Run the GPU benchmark AFTER confirming
ep=100 is safe on local CPU first.

### Step 3 — Qualitative evaluation

Alongside the numbers, do a 10-minute live session with `stt_endpointing_ms=100` and count:
- False terminations (bot interrupts user mid-sentence)
- Correct terminations that felt faster
- Rating: acceptable / marginal / rejected

If the false-termination rate is >1 per 10 turns, the 100ms saving is not worth it.
Consider 150ms as a middle ground if 100ms feels too aggressive.

### Step 4 — Prometheus / Grafana check

After each run, verify latency metrics appear in Grafana:

```bash
# Check metrics endpoint
curl http://localhost:7860/metrics | grep meetlab_e2e_latency
```

The `meetlab_e2e_latency_ms_bucket` histogram should accumulate new observations
within 60 seconds of a completed utterance.

### Results

_To be filled in after running._

| Variant | LLM P50 | TTS first P50 | E2E P50 (post-transcript) | True E2E P50 (incl. endpointing) | False-term rate |
|---------|---------|---------------|--------------------------|----------------------------------|-----------------|
| ep=200 (baseline) | | | | ~700ms | — |
| ep=100 | | | | ~600ms (expected) | TBD |

---

## Production Grafana setup

### Metrics endpoint

`GET /metrics` on the agent-runner (port 7860) returns Prometheus-format text.
Metrics emitted per utterance:

| Metric | Labels | What it measures |
|---|---|---|
| `meetlab_e2e_latency_ms` | `stt_model`, `endpointing_ms` | transcript commit → first TTS audio (ms) |
| `meetlab_llm_ttft_ms` | `llm_model` | transcript commit → first LLM token (ms) |
| `meetlab_tts_first_chunk_ms` | `tts_provider` | first LLM token → first audio frame (ms) |
| `meetlab_utterances_total` | `stt_model` | total bot utterances (counter) |

Verify locally:
```bash
curl http://localhost:7860/metrics | grep meetlab_
```

### Prometheus scrape config

Add to your `prometheus.yml` (or Grafana Cloud agent config):
```yaml
scrape_configs:
  - job_name: meetlab-agent-runner
    scrape_interval: 15s
    static_configs:
      - targets: ['<agent-runner-host>:7860']
    metrics_path: /metrics
```

On AWS, `<agent-runner-host>` is the internal hostname or ECS task IP. If the agent-runner
is behind a load balancer, scrape the task directly (not the ALB) to avoid duplicate counting.

### Grafana dashboard queries (PromQL)

```promql
# E2E P50 by endpointing variant (Experiment 2)
histogram_quantile(0.50,
  rate(meetlab_e2e_latency_ms_bucket[5m])
)

# E2E P50 comparison: ep=200 vs ep=100
histogram_quantile(0.50,
  rate(meetlab_e2e_latency_ms_bucket{endpointing_ms="100"}[5m])
)

# LLM TTFT P50/P95 over time
histogram_quantile(0.95,
  rate(meetlab_llm_ttft_ms_bucket[5m])
)

# Utterances per minute (throughput)
rate(meetlab_utterances_total[1m]) * 60
```

---

## Experiment 3 — Jaeger profiling baseline

**Status:** 📋 Planned

**Goal:** Use the existing OTLP→Jaeger traces to see per-service TTFB breakdowns that the
`Utterance.meta` timing cannot show (e.g., time inside the Pipecat STT service vs. network).

**Setup:**
1. Confirm Jaeger is in docker-compose (check `.devcontainer/docker-compose.yml`)
2. Set `ENABLE_TRACING=1` in `agent-runner/.env.runner.local`
3. Run a live conversation
4. Open `http://localhost:16686` → find `meetlab-agent-runner` service
5. Inspect `turn → stt_DeepgramSTTService` span's `metrics.ttfb` vs pipeline overhead

**What Pipecat traces emit per turn:**
```
conversation (root)
└── turn
    ├── stt_DeepgramSTTService   → metrics.ttfb (time from first audio to final transcript)
    ├── llm_OpenAILLMService     → metrics.ttfb (time to first token), input_tokens, output_tokens
    └── tts_ElevenLabsTTSService → metrics.ttfb (time to first audio chunk), character_count
```

This will reveal whether `stt_ms ≈ 700ms` is network-dominated or queue-dominated.

---

## Future ideas (not yet planned)

- **Local faster-whisper STT** (`WhisperSTTService`, distil-large-v3 int8): only viable with
  GPU (`--gpus all` in docker-compose). On CPU it would be 500–800ms vs Deepgram's 200ms
  endpointing wait. Check: does the container have GPU access?
- **SmartTurn analyzer**: `LocalSmartTurnAnalyzerV3` can trigger LLM before the full VAD
  silence timeout when turn confidence is high. Complex to wire into per-participant path.
- **LLM streaming to TTS without sentence boundary**: already handled by aggregation mode,
  but could also investigate `split_secs` parameter if available in ElevenLabs service.
