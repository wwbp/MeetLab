# Fastest options per stage: a lookup

Which speech-to-text, LLM and voice options answer fastest, what our Bot Config offers today,
and what is worth adding. For studies that require a vendor (e.g. ElevenLabs or OpenAI) and for
studies that allow alternatives. Collected 2026-10-04; numbers move, so measure before changing
a default (`make benchmark-full BENCHMARK_SAMPLES=10`, or a load test on staging).

**Sources**, tagged on each number:

- **[SB]** Pipecat's [stt-benchmark](https://github.com/pipecat-ai/stt-benchmark): 1,000 samples, Sept 2026.
- **[PC]** Pipecat's built-in latency figures in its code (`services/stt_latency.py`).
- **[AA]** [Artificial Analysis](https://artificialanalysis.ai): independent, live pages.
- **[Coval]** Coval's time-to-first-audio benchmark (2026-09-08), read through a vendor's summary.
- **[V]** the vendor's own claim.
- **[Ours]** our measurements: [v1's latency experiments](https://github.com/wwbp/MeetLab/blob/v1.0.0/docs/latency-experiments.md), the
  [load test report](load-test-report-2026-10.md).

✓ = in our Bot Config today.

## Speech to text

Time from the end of speech to the final transcript (TTFS), and word error rate.

| Option | TTFS p50 / p99 | Word error rate | Notes |
|---|---|---|---|
| ✓ NVIDIA Parakeet (our own GPU) | 221 / 252 ms [SB] | 1.95% | English only; no per-minute fee |
| ✓ Deepgram nova-3 | 247 / 326 ms [SB] | 1.37% | streaming |
| Soniox stt-rt-v4 | 249 / 310 ms [SB] | 1.09% | streaming, multilingual, most accurate of the fast ones |
| AssemblyAI universal-streaming | 256 / 417 ms [SB] | 2.42% | |
| ElevenLabs scribe v2 realtime | 281 / 407 ms [SB] | 3.13% | for ElevenLabs-only studies |
| ✓ OpenAI gpt-4o-transcribe | 637 / 1,655 ms [SB] | 2.95% | **the slowest we offer** |

## LLM

Time to the first token (TTFT), and speed once it's going (tokens per second).

| Option | First token | Tokens/s | Notes |
|---|---|---|---|
| ✓ Qwen 2.5 7B, 8-bit (our own GPU) | 58–104 ms p95 [Ours] | — | no network hop; queues past ~84 rooms on one GPU |
| ✓ gpt-4.1-nano | 0.63 s [AA] | 149 | lowest OpenAI first token |
| ✓ gpt-5.4-nano, no reasoning | 0.69 s [AA]; 0.66–0.81 s p95 [Ours] | 169 | with reasoning on, the first token takes **5.7 s** [AA] |
| Groq (Llama 3.1 8B, gpt-oss-20b) | 0.8–0.9 s [AA] | 645–943 | first sentence done 3–5× sooner than OpenAI |
| Google Gemini Flash-Lite | ~0.32 s [AA, unverified] | — | |

## Voice

Time to first audio, and how each provider takes text, which decides the best **Start speaking**
setting.

| Option | First audio | Takes text | Best "Start speaking" | Notes |
|---|---|---|---|---|
| ✓ Kokoro (our own GPU) | 90–227 ms p95 [Ours] | per request | **clause** | queues under load: a 2nd GPU kept it at 176 ms at 72 rooms |
| ✓ ElevenLabs Flash v2.5 | 157 ms p50 [Ours]; 288 ms p50 [Coval] | streamed | sentence | token mode was slower for us; worth one re-test |
| Inworld TTS-2 | 75–111 ms [Coval] | streamed | token | fastest major provider found |
| Gradium | 214 / 346 ms [Coval] | streamed | token | |
| Cartesia Sonic-3.6 | 440 / 696 ms [Coval]; "sub-90 ms" [V] | streamed | token | vendor and independent numbers disagree: measure |
| ✓ OpenAI gpt-4o-mini-tts | 812 ms; 0.9–1.5 s [Ours] | per request | clause | **the slowest we offer** |

"Per request" voices get one request per chunk, so `token` would mean one request per word:
use `sentence` or `clause`. "Streamed" voices keep one connection per reply and can take words
as they come.

## Recommended low-latency choices

**A study that requires vendors:**
- **Speech to text:** Deepgram nova-3, or Soniox.
- **LLM:** gpt-4.1-nano or gpt-5.4-nano, with reasoning off.
- **Voice:** ElevenLabs Flash, Start speaking at `sentence`.
- **Avoid:** gpt-4o-transcribe and gpt-4o-mini-tts unless the study requires them; they're the slowest.

**A study that allows alternatives:** our own stack. Parakeet, Qwen and Kokoro at `clause`;
add a voice GPU before anything else when rooms go past ~80.

**Worth adding to Bot Config:**
1. **Inworld or Cartesia voice** (streamed, `token`): a fast cloud voice when ElevenLabs isn't
   required.
2. **Soniox speech to text**: about 0.4 s faster than gpt-4o-transcribe at p50 and 1.3 s at
   p99, and more accurate.
3. **Groq LLM**: same first-token time as OpenAI, but the first sentence is done sooner.

## Turn-taking, for reference

- **Our default:** a turn splits after 450 ms of silence (calibrated on pilot audio); the bot
  then waits 50 ms after the transcript before answering. That wait was 300 ms until B4
  (2026-10-05) measured that it only delays the reply: 50 ms was 0.24 s faster, splitting unchanged.
- **Pipecat's default:** 200 ms of silence plus 600 ms.
- **Smart turn:** Pipecat's Smart Turn v3 needs exactly 200 ms of silence. On our pilot audio it
  kept 73% of mid-thought pauses open, but 41% of real turn ends waited for its 3 s backstop.
