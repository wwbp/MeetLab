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

Things we have already ruled out (details in the experiment log): running speech
recognition locally instead of in the cloud — ~25× slower on our current hardware
(Experiment 5) — and shortening the end-of-speech wait setting, which measurably
changed nothing (Experiment 2 addendum).

The full experiment history (what we tried, what worked, what didn't) lives in
[latency-experiments.md](latency-experiments.md).

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
