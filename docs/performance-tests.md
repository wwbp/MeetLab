# Performance Tests — How We Measure Voice Latency

## What we measure

When you talk to the voice agent, there is a moment of silence between *you finishing
your sentence* and *the bot starting to speak*. That silence is what we measure.
We call it **total latency**. Around one second feels natural; beyond two seconds
feels broken.

That silence is not one thing — it is four steps that happen one after another:

```
 You stop          Bot knows what      Bot decides         Bot starts
 speaking    ──►   you said      ──►   what to say   ──►   speaking
            STT                 LLM                 TTS
        (speech-to-text)   (language model)   (text-to-speech)
```

| Step | Plain-language meaning | Metric name | Typical share |
|------|------------------------|-------------|---------------|
| STT | Turning your speech into written text. Includes the deliberate short wait that confirms you are actually done talking (not just pausing), plus the transcription itself. | `stt_ms` | ~400ms |
| LLM | The AI reading the text and producing the first words of its reply. | `llm_ttft_ms` | ~400ms |
| Sentence assembly | Collecting the AI's words until there is a full sentence to speak. | `sentence_agg_ms` | ~50ms |
| TTS | Turning that sentence into the first audible sound. | `tts_ttfb_ms` | ~160ms |
| **Total** | You stop talking → bot starts talking. | `total_latency_ms` | **~1,000ms** |

## How to read the numbers

Every test speaks the same recorded question to the bot 10 times and measures each run.
We report:

- **P50** (median) — the typical experience. Half the runs were faster than this.
- **P95** — the bad-day experience. Only 1 run in 20 was slower than this.
- **Mean** — the average. We show it, but P50 is the honest "what users feel" number.

Two rules of thumb:

1. **Never trust a run with fewer than 10 samples.** With 3 samples our percentile
   math returns the *fastest* run as the "median", which flatters the result.
2. **Only compare runs from the same date/setup.** Cloud AI services (OpenAI, Deepgram,
   ElevenLabs) are slower at busy hours; a Tuesday-afternoon run and a Sunday-morning
   run can differ by 200ms for reasons that have nothing to do with our code.

## Current best result (2026-06-10, local Docker)

Configuration: Deepgram `nova-3-general` (STT) + OpenAI `gpt-5.4-nano` (LLM) +
ElevenLabs (TTS), 10 samples.

| Stage | P50 | P95 |
|-------|-----|-----|
| STT | 395ms | 418ms |
| LLM first token | 398ms | 850ms |
| Sentence assembly | 46ms | 54ms |
| TTS first sound | 157ms | 171ms |
| **Total** | **1,010ms** | 1,491ms |

What this says: STT and the LLM each cost ~0.4s and together are ~80% of the total.
TTS is cheap. If we want to get under a second reliably, those two are the targets.

Things we have already ruled out (details in the experiment log): running *Whisper*
speech recognition on CPU — ~25× slower than the vendor (Experiment 5) — and
shortening Deepgram's end-of-speech wait setting, which measurably changed nothing
(Experiment 2 addendum).

The full experiment history (what we tried, what worked, what didn't) lives in
[latency-experiments.md](latency-experiments.md).

## Self-hosted speech recognition (Parakeet) — available since 2026-06-11

We can run speech recognition on our own server instead of sending audio to Deepgram.
It uses NVIDIA's open-source **Parakeet-TDT 0.6B v2** model, served by a small web
service (`stt-nemotron`) that runs on CPU on dev laptops and on a GPU server in AWS.

**Measured in production** (10 live conversations per row, same prompt, same hour):

| STT configuration | Typical (P50) | Slow cases (P95) | Transcripts |
|---|---|---|---|
| Deepgram nova-3 (default) | 375ms | 488ms | word-perfect |
| Parakeet on our GPU, 200ms pause | 358ms | 486ms | word-perfect |
| Parakeet on our GPU, 100ms pause | **334ms** | 483ms | word-perfect |

Key facts:

- The model itself transcribes in under 0.1s on the GPU; most of the remaining time
  is the deliberate "are you done talking?" pause plus network travel. Unlike
  Deepgram's, **our pause length is tunable** (`stt_endpointing_ms`).
- The P95 numbers are nearly identical everywhere — those slow cases come from
  audio delivery over the internet, not from either recognizer.
- One GPU server (AWS g4dn.xlarge, ~$0.53/hour) handled 8 simultaneous
  conversations at 15% load. It is not free: ~$380/month if left on 24/7, so it
  runs only when needed. Deepgram remains the default.

**How to use it (new team members start here):**

1. *Switch any room*: Console (`/desk` on the meet host) → Config → pick
   `parakeet-tdt-0.6b-v2 (self-hosted GPU)` as the STT model for that room's scope.
   Or via API: `PUT /config {"scope": "<room>", "stt_model": "parakeet-tdt-0.6b-v2"}`.
2. *Locally*: `make start` brings up the `stt-nemotron` container automatically
   (first start downloads a 2.4GB model). Benchmark it:
   `make benchmark-full BENCHMARK_SAMPLES=10 BENCHMARK_CONFIGS="parakeet-tdt-0.6b-v2 / gpt-5.4-nano / elevenlabs [sentence]"`
3. *In production*: the GPU server must be running (EC2 `meetlab-stt-gpu`,
   private address set via `NEMOTRON_STT_URL` on the agent-runner environment).
   If a parakeet room's bot joins but never responds, that server is the first
   thing to check.

Full detail, decisions, and dead ends: [experiment-6-gpu-stt.md](experiment-6-gpu-stt.md).

## Running the tests

All commands run from the repository root with the Docker stack running (`make start`).

```bash
# Quick check — 10 samples against the current default configuration
make benchmark BENCHMARK_SAMPLES=10

# Compare specific configurations (comma-separated labels)
make benchmark-full BENCHMARK_SAMPLES=10 \
  BENCHMARK_CONFIGS="nova-3-general / gpt-5.4-nano / elevenlabs [sentence]"

# Full sweep of every STT × LLM × TTS combination (slow — ~1h, costs API credits)
make benchmark-full BENCHMARK_SAMPLES=10

# Re-print the comparison table from all stored results (no new runs)
make benchmark-report
```

Results accumulate in `agent-runner/tests/fixtures/benchmark_results.json` — every
run is kept, so the report can always compare today against history.

## Watching latency live

Two dashboards, one for each environment:

| Where | Tool | URL | What it shows |
|-------|------|-----|---------------|
| Local dev | Jaeger | http://localhost:16686 → service `meetlab-agent-runner` | A timeline ("trace") of every single conversation turn: how long the LLM and TTS steps took, token counts. Good for inspecting one slow turn in detail. |
| Production | Grafana Cloud | (team Grafana, `meetlab-prod` stack) | Graphs over time of the same metrics across all real sessions: `meetlab.stt_latency_ms`, `meetlab.e2e_latency_ms`, `meetlab.llm_ttft_ms`, `meetlab.tts_ttfb_ms`, plus utterances per minute. Good for spotting trends and regressions. |

Caveat worth knowing: the Jaeger timeline does **not** show the STT step (a measurement
blind spot in the tracing library for our multi-speaker setup — see Experiment 3 in the
experiment log). The STT number always comes from our own instrumentation, which both
the benchmark and Grafana use.

## When to run what

| Situation | Do this |
|-----------|---------|
| Changed anything in the bot pipeline | `make benchmark BENCHMARK_SAMPLES=10` before and after; compare P50s |
| Considering a new STT/LLM/TTS model | `make benchmark-full` with the new config label |
| "The bot feels slow today" | Check Grafana (prod) or Jaeger (local) before assuming the code changed |
| Writing up results | Record date, config label, sample count, P50 and P95 in `docs/latency-experiments.md` |
