# Voice Pipeline Latency Experiments

Step-by-step log of latency optimization experiments. Each experiment records the hypothesis,
the code change, the benchmark commands, and the measured result.

Key metric (since Experiment 4): **Total latency P50** (`total_latency_ms` = `stt_ms` + post-STT
E2E) — the true user-perceived gap from the last audio packet of the user's speech to the first
TTS audio chunk from the bot.

Stage metrics stored in `Utterance.meta["timing"]`:

| Metric | Measures | Source |
|--------|----------|--------|
| `stt_ms` | last user audio frame → transcript committed (endpointing wait + transcription + network) | direct, `_AudioTimestampRecorder` + `on_user_turn_stopped` |
| `llm_ttft_ms` | transcript committed → first LLM token | Pipecat MetricsFrame |
| `sentence_agg_ms` | LLM token buffering until sentence boundary | Pipecat MetricsFrame |
| `tts_ttfb_ms` | sentence sent to TTS → first audio chunk | Pipecat MetricsFrame |
| `latency_ms` (post-STT E2E) | transcript committed → first TTS audio chunk | monotonic timers |
| `total_latency_ms` | `stt_ms` + `latency_ms` | derived sum |

> **History:** before Experiment 4, `stt_ms` was a *derived residual* (`e2e − llm − tts`) and the
> headline metric was post-STT E2E only. Results recorded before 2026-06-10 use the old
> definitions — do not compare their `stt_ms`/`e2e_ms` directly against newer runs.

Benchmark data lives in `agent-runner/tests/fixtures/benchmark_results.json`.
Run commands are in `Makefile`; always use `BENCHMARK_SAMPLES=10` for meaningful percentiles
(the `_pct()` function with 3 samples returns the minimum, not the median).

---

## Baseline — May 27, 2026

Full 40-config sweep, 3 samples each. Raw data: `agent-runner/tests/fixtures/benchmark_results_archive_20260527_103106.json`.

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

Local CPU benchmark, 2026-06-07, short audio ("What is the capital of France?", 1.7s).
5 samples each (run #78 and #80 in benchmark_results.json). Full stage breakdown now
available via Pipecat MetricsFrame (TTFBMetricsData + TextAggregationMetricsData).

| Stage | ep=200 P50 | ep=100 P50 | Δ |
|-------|-----------|-----------|---|
| LLM TTFB | 220ms | 201ms | −19ms |
| Sentence agg | 45ms | 46ms | +1ms |
| TTS TTFB | 167ms | 149ms | −18ms |
| **E2E (post-transcript)** | **433ms** | **442ms** | +9ms |
| True E2E (est., +endpointing) | ~633ms | ~542ms | **−91ms** |

**Why post-transcript E2E numbers are nearly identical (433ms vs 442ms):**
Our E2E metric starts at `stt_done` (transcript committed to LLM context). The Deepgram
endpointing wait (200ms or 100ms) happens *before* `stt_done` fires — not captured here.
The ~100ms saving from ep=100 is real for the user (VAD-stop → first bot audio), but
invisible to this benchmark. True measurement would require recording the `VADUserStoppedSpeakingFrame`
timestamp and subtracting it from the first bot audio frame.

**Stage breakdown confirms:** LLM dominates (220ms), TTS TTFB is second (167ms), sentence
aggregation is small (45ms). Total post-transcript latency is ~430ms; adding ~200ms Deepgram
endpointing puts true user-perceived E2E at ~630ms.

**Next steps:**
1. Qualitative eval — 10-minute live session with `stt_endpointing_ms=100`, count false
   terminations. If <1 per 10 turns, 100ms is safe to ship as default.
2. AWS GPU benchmark — same `make benchmark-exp2` against deployed stack for true
   comparison at lower overall latency baseline.

### Addendum 2026-06-10 — direct measurement REFUTES the −91ms estimate

Experiment 4's direct `stt_ms` (last audio frame → transcript committed, which *includes*
the endpointing wait) shows **no difference** between ep=200 and ep=100:

| | ep=200 | ep=100 |
|---|---|---|
| `stt_ms` P50 (10 samples) | 395ms | 391ms |
| `stt_ms` P95 | 418ms | 567ms |

If the endpointing wait were the dominant fixed cost, ep=100 should cut ~100ms here. It
doesn't. The "True E2E (est.)" row in the table above — which simply *assumed* the saving —
is wrong. Hypotheses for why (unverified): Deepgram may enforce a minimum endpointing
window; the final-transcript flush may be gated on something other than the endpointing
timer; or the benchmark WAV's trailing silence interacts with VAD timing. Until one of
these is confirmed, **treat `stt_endpointing_ms` as a no-op for latency** and leave it
at 200ms (lower values still raise the false-termination risk for no measured gain).

---

## Production observability (current, 2026-06-11)

Metrics are pushed via OTLP (no Prometheus scraping — the old scrape setup was removed
in PR #35). Prod agent-runner env: `ENABLE_TRACING=true`,
`OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-us-east-3.grafana.net/otlp`,
`OTEL_SERVICE_NAME=meetlan-otel` (typo is live in labels — don't "fix" casually).

Grafana Cloud stack: org `sabhay`, web UI https://sabhay.grafana.net, Prometheus read
endpoint `prometheus-prod-66-prod-us-east-3.grafana.net` (instance 3238458). Query from
the CLI with `scripts/grafana-prom.sh` (needs a `metrics:read` access-policy token in
`~/.config/meetlab/grafana-read-token`; the token in `.env.runner` is OTLP write-only).

Metric names get a `_milliseconds` unit suffix in storage:
`meetlab_stt_latency_ms_milliseconds_bucket`, `meetlab_e2e_latency_ms_milliseconds_bucket`,
`meetlab_llm_ttft_ms_milliseconds_bucket`, `meetlab_tts_ttfb_ms_milliseconds_bucket`,
`meetlab_utterances_total`. Useful labels: `stt_model`, `endpointing_ms`.

```bash
# stt_ms P50 by model over the last 30 minutes
scripts/grafana-prom.sh 'histogram_quantile(0.5,
  sum by (stt_model, le) (rate(meetlab_stt_latency_ms_milliseconds_bucket[30m])))'
```

Pending ops (from the prod setup work, 2026-06-10): build the team dashboard + P95
alert in Grafana; remove stale `GRAFANA_PROM_*` env vars from the EB environment
(triggers a rolling restart — quiet window); move the OTLP write token out of the
`.env.runner` comment block.

---

## Experiment 3 — Jaeger profiling baseline

**Status:** ✅ Complete (2026-06-10)

**Goal:** Use the existing OTLP→Jaeger traces to see per-service TTFB breakdowns that the
`Utterance.meta` timing cannot show (e.g., time inside the Pipecat STT service vs. network).

**Setup used:** `ENABLE_TRACING=true` + `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`
in `agent-runner/.env.runner`; spans inspected via `http://localhost:16686` (UI) and
`http://localhost:16686/api/traces?service=meetlab-agent-runner` (API) during a
10-sample benchmark run.

**What the traces actually contain** (20 traces, 2026-06-10 benchmark):

| Span | n | duration P50 | `metrics.ttfb` P50 |
|------|---|-------------|--------------------|
| `conversation` (root) | 20 | 9,078ms | — |
| `turn` | 40 | 3,423ms | — |
| `llm` | 20 | 442ms | **381ms** |
| `tts` | 40 | 175ms | **160ms** |

**Finding 1 — there are NO `stt` spans.** Pipecat's turn tracing only instruments services
that live inside the `Pipeline` object. Our per-participant STT instances are created
dynamically inside `MultiSpeakerSTT` (outside the pipeline), so the tracer never sees them.
The custom `stt_ms` metric from Experiment 4 is therefore the *only* STT latency visibility
we have — Jaeger cannot answer "is STT network- or queue-dominated" with the current
architecture.

**Finding 2 — Jaeger cross-validates the benchmark numbers.** Trace-level `llm` ttfb P50
(381ms) and `tts` ttfb P50 (160ms) match the same run's MetricsFrame-based benchmark
values (llm_ttft 398ms, tts_ttfb 157ms) within noise. The two measurement paths agree,
so we can trust either.

**Use Jaeger for:** per-turn flame views during live debugging (`turn` span shows the full
sequence), token counts on `llm` spans, spotting outlier turns visually.
**Don't use it for:** STT latency (absent), aggregate percentiles (use the benchmark or
Grafana histograms instead).

---

## Experiment 4 — Direct STT measurement + total user-perceived latency

**Status:** ✅ Complete (2026-06-10)

**Problem:** `stt_ms` was a derived residual (`e2e − llm_ttft − tts_first`) computed from
metrics with *different clock anchors*, so it absorbed every timing race in the pipeline —
the baseline's "STT ≈ 694ms" was an artifact, not a measurement. And the headline E2E
started only *after* the transcript was committed, hiding the entire STT cost from the
number we optimized.

**Change** (`bot.py`):
- `_AudioTimestampRecorder` (new `FrameProcessor` before `multi_stt`) records the monotonic
  time of each participant's last `UserAudioRawFrame`.
- `on_user_turn_stopped` computes `stt_ms = transcript-committed − last-audio-frame`,
  covering endpointing wait + transcription + network round-trip.
- `total_latency_ms = stt_ms + post-STT E2E` is the new headline metric; benchmark reports
  sort by it.
- New OTLP histogram `meetlab.stt_latency_ms` (labels: `stt_model`, `endpointing_ms`)
  alongside the renamed-in-description `meetlab.e2e_latency_ms` (now explicitly "post-STT").

**Result** (nova-3-general / gpt-5.4-nano / elevenlabs [sentence], 10 samples, 2026-06-10):

| Stage | P50 | P95 |
|-------|-----|-----|
| STT (direct) | **395ms** | 418ms |
| LLM TTFB | 398ms | 850ms |
| Sentence agg | 46ms | 54ms |
| TTS TTFB | 157ms | 171ms |
| Post-STT E2E | 623ms | 1,044ms |
| **Total (user-perceived)** | **1,010ms** | 1,491ms |

**Conclusions:**
- Real STT cost is ~395ms — a stable, tight distribution (P95 within 25ms of P50). The old
  derived ~694ms overstated it by ~75%.
- STT and LLM are now co-equal latency drivers (~400ms each); TTS is a minor cost (~160ms).
- The user hears the bot ~1.0s after they stop speaking (local Docker, cloud STT/LLM/TTS).
- See Experiment 2 addendum: the direct measurement also revealed that `stt_endpointing_ms`
  has no measurable effect.

---

## Experiment 5 — Local faster-whisper STT (whisper-turbo)

**Status:** ✅ Complete (2026-06-10) — **rejected for CPU; code path kept for future GPU hosts**

**Hypothesis:** A local `WhisperSTTService` (faster-whisper large-v3-turbo) eliminates the
cloud round-trip portion of `stt_ms`. On CPU the transcription itself may eat the savings;
this experiment measures where the break-even sits. (GPU would change the picture entirely
— the Docker container currently has no GPU access.)

**Change** (`bot.py`, `multi_speaker_stt.py`):
- `whisper-*` STT models build a per-participant chain `VADProcessor(SileroVADAnalyzer) →
  WhisperSTTService` (`_build_whisper_chain`). Whisper is a `SegmentedSTTService` — it only
  transcribes the audio between VAD start/stop events, and per-participant audio routed by
  `MultiSpeakerSTT` never passes the transport's VAD, so each chain carries its own.
- `MultiSpeakerSTT._ensure_stt` accepts `(head, tail)` chain factories; `_FrameCollector`
  drops the chain's raw VAD frames so they don't double-fire the latency observer
  (the synthetic VAD sandwich around the finalized transcript remains the single source
  of VAD events for the main pipeline).
- VAD `stop_secs` mirrors `stt_endpointing_ms` (default 200ms) for comparable `stt_ms`.
- Unit tests: `tests/test_bot.py::TestBuildSttWhisperChain`,
  `tests/test_multi_speaker_stt.py::TestMultiSpeakerSTTChainFactory` (TDD;
  `WhisperSTTService._load` patched out — the constructor eagerly downloads ~1.6GB).

**Results (2026-06-10, Docker on Apple Silicon, CPU only):**

End-to-end sample through the real pipeline (whisper-turbo / gpt-5.4-nano / elevenlabs):

| Stage | whisper-turbo (1 sample) | nova-3 Deepgram (P50, n=10) |
|-------|--------------------------|------------------------------|
| STT | **9,684ms** | 395ms |
| LLM TTFT | 1,088ms¹ | 398ms |
| TTS TTFB | 170ms | 157ms |
| **Total** | **10,994ms** | 1,010ms |

¹ LLM inflated by CPU contention from Whisper inference in the same container.

Synthetic CPU timings (warm model, 2s of audio — `faster_whisper.WhisperModel.transcribe`):

| Model | compute | transcribe 2s audio |
|-------|---------|---------------------|
| turbo (809M) | float32 (auto fallback — no efficient fp16 on this CPU) | 16.4s |
| turbo | int8 | ~9s |
| small | default | 3.25s |
| base (39M) | default | 2.85s |

**Verdict: rejected on CPU.** Even the smallest model is slower than realtime; turbo costs
~25× Deepgram's full STT round-trip. Two operational hazards on top of latency:

1. **Memory:** each per-participant chain loads its own ~3.2GB float32 model. Two
   concurrent (or even back-to-back, overlapping-teardown) whisper bots OOM-killed the
   agent-runner in the 7.75GiB Docker VM — this is why `whisper-turbo` is commented out
   of the default benchmark matrix (`run_benchmark_matrix.py`).
2. **Eager load:** `WhisperSTTService.__init__` downloads (~1.6GB, first time) and loads
   (~4s) the model synchronously inside the event loop, on the first audio frame from a
   new participant.

**What was validated and kept:** the `VADProcessor → WhisperSTTService` per-participant
chain works correctly end-to-end (live transcript "What is the capital of France?" through
the full pipeline, correct speaker attribution, VAD sandwich timing intact). The code path
and its unit tests stay; revisit on a GPU host (`--gpus all` + `device="cuda"`), where
turbo transcribes 2s of audio in ~100-200ms and would plausibly beat Deepgram's 395ms.

**Config:** `/config` accepts `whisper-turbo`, `whisper-base`, `whisper-small`
(`runner.py` allowlist + admin UI choices).

---

## Experiment 6 — Self-hosted Parakeet STT (CPU + prod GPU) vs Deepgram

**Status:** ✅ Stages 0–3 complete (soak harness 2026-06-30) — **works, beats Deepgram
modestly in prod; promoted to the default (+ep=100) on 2026-06-30. Accuracy study (Stage 4)
still open.**

**Hypothesis:** a self-hosted streaming-class STT can beat Deepgram nova-3's measured
`stt_ms` floor (~375–395ms P50), which Experiment 2 proved un-tunable on their side.

**Research basis (Stage 0):** Pipecat's official [stt-benchmark](https://github.com/pipecat-ai/stt-benchmark)
(1000 samples, TTFS = our stt_ms) ranks NVIDIA Nemotron/Parakeet first at 221ms median /
1.90% semantic WER, ahead of Deepgram nova-3 (247ms / 1.71%). Parakeet-TDT-0.6b-v2:
Open ASR Leaderboard ~6.3% avg WER, RTFx ≈ 3300. Ruled out by cited evidence:
streaming-whisper wrappers (LocalAgreement policy floor ≈ 1.9s even with infinite
compute), Kyutai STT (fixed 500ms lookahead). ⚠ Research sweep's adversarial
verification was rate-limited; numbers quoted from primary sources directly.

**Setup:**
- `stt-nemotron` sidecar: [Shadowfita/parakeet-tdt-0.6b-v2-fastapi](https://github.com/Shadowfita/parakeet-tdt-0.6b-v2-fastapi)
  @ `31c5652` (GPL-3.0, cloned at image build — never vendored). Local compose service
  = our CPU/arm64 Dockerfile (`stt-nemotron/Dockerfile`); prod = upstream's CUDA image
  on EC2 g4dn.xlarge T4 (`meetlab-stt-gpu`, private 10.0.5.115 in the vivaprox VPC,
  SG opens :8000 to the EB security group only). `NEMOTRON_STT_URL` on the EB env.
- Bot side: `parakeet-*` stt_model prefix → (VADProcessor → `NemotronHTTPSTTService`)
  per-participant chain (same mechanism as Experiment 5's whisper chain).
  `should_chunk=false` per request — segments are already VAD-cut, and upstream's
  chunking path crashes (their issue #16). `stt_endpointing_ms` drives Silero
  `stop_secs` — genuinely effective, unlike Deepgram's knob.
- Model download inside containers stalls (same as Exp 5) — host-download the 2.4GB
  `.nemo` and install into the HF cache volume (blob name = sha256).

**Results — local (laptop CPU, 10 samples, same-run control):**

| | stt_ms P50/P95 | total P50/P95 |
|---|---|---|
| parakeet sidecar (CPU!) | **134 / 200ms** | **813 / 976ms** |
| nova-3 control | 387 / 406ms | 951 / 1575ms |

**Results — raw T4 (curl on-box):** 1.7s clip **96ms**, 8s clip **115ms**, word-perfect.
Concurrency (serialized decoding, GPU at 15% util / 1.5GB VRAM): 4-way p50 290ms,
8-way 475ms, 16-way 842ms — ceiling is the server, not the hardware.

**Results — production (10 sessions/row, via Grafana `stt_model` split, same hour):**

| Prod stt_ms | P50 | P95 |
|---|---|---|
| Deepgram nova-3 | 375ms | 488ms |
| Parakeet T4, ep=200 | 358ms | 486ms |
| **Parakeet T4, ep=100** | **334ms** | 483ms |

Transcripts word-perfect (punctuated) in every run, both providers.

**Analysis:** prod win is ~40ms P50 (~11%), far less than local — because in the real
WebRTC path both stacks are **endpointing + delivery dominated**: halving the VAD wait
(200→100ms) bought only 24ms, and P95 ≈ 485ms is identical everywhere (jitter
buffer/packet cadence, not recognition). The local 134ms benefited from the stt_ms
anchor overlapping the streamed audio tail. What self-hosting durably buys: a latency
knob we own (ep<100, SmartTurn-style early commit now possible), no per-minute STT
bill, audio stays in-house, and the P95 problem becomes ours to attack instead of
vendor-opaque.

**Decisions (2026-06-11):** default stays nova-3. Per-room flip switch shipped
(console dropdown + `/config`). T4 runs only during demo/test windows (~$0.53/hr;
$380/mo if 24/7 — likely above Deepgram per-minute at research volume). Demo rooms:
`demo-fast-ears` (parakeet ep=100) vs `demo-classic-ears` (nova-3).

**Decisions (2026-06-30):** **default promoted to `parakeet-tdt-0.6b-v2` + `ep=100`**
(`BotConfig` defaults + migration `c7f1a2b3d4e5`; console UI; nova-3 stays a one-click
opt-out). Implies the sidecar is now always-on — standing T4 cost decision, not per-demo.
Two soak prerequisites shipped alongside: bot token TTL is now env-configurable
(`BOT_TOKEN_TTL_MINUTES`, default 15) so 20-min+ sessions don't drop at expiry, and the
session-end "status stuck on `running`" bug is fixed (cancellation-safe terminal write +
a stale-conversation reconciler in agent-runner). See `make test-session-lifecycle`.

**Stage 3 — multi-bot soak (✅ harness + local capacity run 2026-06-30):** `make soak`
drives N rooms × 2 users × 1 bot, all concurrent, reporting aggregate latency, backlog,
early-drop detection, and a DB-consistency verdict. See `docs/meeting-simulations.md`.

**Local capacity (8 GB dev host, CPU sidecar, mock TTS + nano LLM so STT is isolated):**

| Rooms | Streams | Result |
|---|---|---|
| 2 | 4 | Clean — stt_ms P50 158ms, no spikes |
| 4 | 8 | Survives — P50 155ms, P95 5.2s, 6 spikes, 1 room quiet-early |
| 6 | 12 | Survives — P50 166ms, 12 spikes, 1 room starved to 5.2s median |
| 10 | 20 | **OOM** — sidecar killed (exit 137), STT dead, 0 bot turns |

Takeaways: the single CPU sidecar's memory ceiling is between 6 and 10 rooms on 8 GB;
**usable** quality holds only to ~4 rooms (serialized decoding starves some rooms as
concurrency climbs — the P95 tail and per-room 5s medians are the ceiling showing). The
**10-room target needs the GPU/prod sidecar** (or a concurrency-capped / multi-replica /
NIM deployment). DB consistency held through the OOM: all stranded sessions finalized (bot
finalize + reconciler). Hardening shipped: `restart: unless-stopped` on the sidecar;
`FINALIZE_WAIT_SECS` so the soak verdict waits for late finalization. _Prod-T4 10-room run: TBD._

**Addendum — `parakeet-unified-en-0.6b` (offline, 2026-06-30):** NVIDIA's
[unified offline+streaming English model](https://huggingface.co/nvidia/parakeet-unified-en-0.6b)
(April 2026; RNN-T cache-aware FastConformer, selectable streaming latency 2080→160ms) is now
a selectable STT (console dropdown + `/config` + valid set). It shares the `parakeet-` prefix so
it routes through the **existing offline sidecar chain** (VAD-cut → POST WAV → transcript) with no
bot-side wiring change. Caveats: (1) the bot does **not** send the model name to the sidecar — the
served model is whatever the `stt-nemotron` container loaded, so deploying this model is a
server-side choice (the bot-side id selects the sidecar + tags metrics); (2) **offline mode does not
deliver the ~160ms streaming latency** — that needs a true streaming STT service (continuous chunks +
cache-aware streaming endpoint) replacing the VAD-segment design, tracked as the streaming follow-up.

**Open (Stage 4):** formal WER on a standard set (jiwer; candidate sets:
LibriSpeech test-other / AMI); long-prompt false-termination test at ep=100; ep=50 probe;
if prod soak latency under ~20-way concurrency is unacceptable, evaluate NVIDIA NIM / a
continuous-batching server / multiple sidecar replicas; other OSS models (Canary,
sherpa-onnx streaming zipformer) if the team wants a second candidate.

---

## Future ideas (not yet planned)

- **SmartTurn analyzer**: `LocalSmartTurnAnalyzerV3` can trigger LLM before the full VAD
  silence timeout when turn confidence is high. Complex to wire into per-participant path.
- **LLM streaming to TTS without sentence boundary**: already handled by aggregation mode,
  but could also investigate `split_secs` parameter if available in ElevenLabs service.
- **Why is `stt_endpointing_ms` a no-op?** (from Experiment 2 addendum) — capture Deepgram
  websocket timing to see when the final transcript actually arrives relative to last audio.
