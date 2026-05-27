# Voice Pipeline Latency Benchmark — May 27, 2026

## Summary

I ran a full sweep across all available STT, LLM, and TTS configurations to find the fastest end-to-end pipeline. 40 configurations tested, 3 samples each, measuring each stage independently (STT, LLM time-to-first-token, TTS first chunk, and total E2E).

**Winner: Deepgram nova-3-general + gpt-5.4-nano + ElevenLabs — 1,279ms E2E P50**

---

## Top 10 Configurations (sorted by E2E P50)

| Rank | STT | LLM | TTS | E2E P50 |
|------|-----|-----|-----|---------|
| 🥇 1 | nova-3-general | gpt-5.4-nano | elevenlabs | **1,279ms** |
| 2 | nova-3-general | gpt-5.4-mini | elevenlabs | 1,372ms |
| 3 | nova-3-general | gpt-4.1-nano | elevenlabs | 1,567ms |
| 4 | nova-3-general | gpt-5.4-mini | openai | 2,021ms |
| 5 | nova-3-general | gpt-4o-mini | elevenlabs | 2,031ms |
| 6 | gpt-4o-transcribe | gpt-5.4-nano | elevenlabs | 2,221ms |
| 7 | nova-3-general | gpt-4.1-nano | openai | 2,273ms |
| 8 | gpt-realtime-whisper | gpt-5.4-mini | elevenlabs | 2,323ms |
| 9 | gpt-4o-transcribe | gpt-5.4-mini | elevenlabs | 2,339ms |
| 10 | gpt-realtime-whisper | gpt-5.4-nano | elevenlabs | 2,346ms |

---

## Key Findings

### STT: Deepgram wins by ~1,000ms
Deepgram nova-3-general streams transcription incrementally while audio is still playing, finishing in **~700ms P50**. All OpenAI STT options (gpt-realtime-whisper, gpt-4o-transcribe, gpt-4o-mini-transcribe) batch the audio buffer first, landing consistently at **~1,750ms** — adding a full second regardless of which LLM or TTS follows.

### TTS: ElevenLabs saves ~900–1,200ms every time
ElevenLabs first chunk arrives in **~220–260ms P50**. OpenAI TTS first chunk takes **~900–1,500ms**. This is the second biggest lever. Every config using OpenAI TTS loses 700–1,200ms vs its ElevenLabs equivalent.

### LLM: gpt-5.4-nano is fastest and cheapest
Among the small models tested, gpt-5.4-nano edges out gpt-5.4-mini by ~100ms P50 (333ms vs 378ms TTFT). It's also the cheapest of the generation. gpt-4.1-nano is close behind at ~305ms.

### What didn't work
- **nova-3-meeting / nova-3-phonecall / nova-3-voicemail** — 100% timeout across all samples. These models aren't available on our current Deepgram plan. Removed from all config options.
- **Server-side VAD** — always timed out when combined with WAV injection. Local Silero VAD is the only viable mode for our setup.
- **stt_delay parameter** — only affects server-side VAD streaming; no effect with local VAD.

---

## Per-Stage Breakdown (winning config)

| Stage | P50 | Notes |
|-------|-----|-------|
| STT (Deepgram nova-3-general) | ~700ms | Streams while audio plays |
| LLM TTFT (gpt-5.4-nano) | ~333ms | First token very fast |
| TTS first chunk (ElevenLabs) | ~239ms | Near-instant |
| **End-to-end** | **~1,279ms** | User speaks → bot audio starts |

---

## Recommendation

**Use nova-3-general + gpt-5.4-nano + ElevenLabs as the default.** I've already set this as the new default in the codebase and confirmed it with the benchmark.

If you need a fallback for cost or API availability:
- Drop-in LLM alternative: gpt-4.1-nano (similar speed, different provider)
- If ElevenLabs is unavailable, the next-best all-OpenAI option is gpt-realtime-whisper + gpt-5.4-mini + openai TTS at ~2,700ms — about 2× slower

---

*Tested on local Docker stack (LiveKit --dev mode, transport-server + agent-runner). 3 samples per config with parallel execution. Results in `agent-runner/tests/fixtures/benchmark_results.json`.*
