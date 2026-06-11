# Experiment 6 — GPU self-hosted STT vs Deepgram

**Goal:** beat Deepgram nova-3's measured `stt_ms` (last audio → final transcript,
**375ms P50 in prod / 395ms local**) with self-hosted GPU STT, *at equal-or-better
accuracy*. Each stage below has an expectation written before we run it and a
kill/proceed gate — if a gate fails we stop spending and write up why.

**Working agreement:** this doc is the single collaboration surface. Results get
filled into the tables as they land; decisions get a dated one-liner. Detailed
history still goes to `latency-experiments.md` at the end.

**The latency budget we're playing with:**

```
stt_ms  =  VAD silence wait (stop_secs, ours to tune: 100–200ms)
         + STT inference on the segment (the GPU question)
         + pipeline overhead (~20–50ms, measured locally)
Beat 375ms  ⇒  inference must fit in ~125–250ms depending on stop_secs.
```

---

## Stage 0 — Research (running, $0)

Deep-research sweep over faster-whisper variants, streaming whisper (whisper-streaming,
WhisperLive), streaming-native models (NVIDIA Parakeet-TDT/Canary, Kyutai, Moonshine,
sherpa-onnx), hosted whisper APIs (Groq) as reference, WER methodology, and
Pipecat/LiveKit community production notes. Authoritative + community sources, cited.

- **Expectation:** at least one candidate with *cited* evidence of ≤175ms short-utterance
  inference (or ≤250ms streaming finalize) on T4/L4-class GPU.
- **Gate:** no such candidate → **kill** (stay on Deepgram, attack LLM TTFT instead).
- **Output → fills:** candidate table below, chosen WER test set, GPU choice.

### Stage 0 results (2026-06-10)

**Best single source found: Pipecat's official STT benchmark**
([pipecat-ai/stt-benchmark](https://github.com/pipecat-ai/stt-benchmark), 1000 samples,
TTFS = user-stops-speaking → final transcript — exactly our `stt_ms`):

| Service | TTFS median | TTFS P95 | Semantic WER | Self-hostable? |
|---|---|---|---|---|
| **NVIDIA Nemotron 3.0 ASR (en)** | **221ms** | 238ms | 1.90% | **Yes** — Pipecat `NvidiaSTTService` points at a local Riva/NIM gRPC, no API key |
| Deepgram nova-3-general | 247ms | 298ms | 1.71% | No (our current vendor) |
| Soniox stt-rt-v4 | 249ms | 281ms | 1.25% | No (hosted) |
| AssemblyAI universal-streaming | 256ms | 362ms | 3.49% | No |
| Cartesia ink-2 | 299ms | 328ms | 1.47% | No |
| OpenAI gpt-4o-transcribe | 637ms | 965ms | 3.24% | No |

Supporting findings (extracted from primary sources; ⚠ adversarial verification was
rate-limited, so treat as primary-source-quoted but not independently re-checked):

- **Parakeet-TDT-0.6b-v3** (the model family behind Nemotron ASR): Open ASR Leaderboard
  avg WER 6.34% (formal scoring), LibriSpeech test-other 3.59%, RTFx ≈ 3300 → compute
  for a short utterance is single-digit ms; streaming via NeMo/Riva. Riva offers
  Two-Pass End-of-Utterance with recommended early-EOU ≈ 240ms (CTC variants).
- **Streaming-whisper wrappers are ruled out**: whisper-streaming/LocalAgreement-2
  paper reports 3.3s avg latency (≈1.9s even with infinite compute — policy floor);
  WhisperLiveKit/WhisperLive publish no end-of-speech latency numbers at all;
  WhisperLive defaults to a model per connection (VRAM footgun).
- **Kyutai STT ruled out**: 1B model has a fixed 500ms lookahead delay — floor alone
  exceeds the target.
- **Segmented faster-whisper** (our built path): distil-large-v3 ≈ 6.3× faster than
  large-v3 at ~1.3pt worse short-form WER (9.7% vs 8.4%). No citable short-utterance
  T4/L4 numbers found — Stage 1 measures this ourselves.
- **Our Deepgram 375ms vs Pipecat's 247ms for the same model** suggests ~130ms of
  recoverable config/measurement gap on our side regardless of self-hosting — their
  `docs/measuring-ttfs.md` methodology is worth replicating (free win to chase first).

**Gate: PASSED.** Nemotron/Parakeet has cited evidence of beating our 375ms *including*
endpointing (221ms median TTFS), at comparable semantic WER, with a first-party Pipecat
service class. Revised Stage 1–2 candidates:

1. **Parakeet/Nemotron via self-hosted Riva (NIM container) on L4 or T4** — primary
2. **faster-whisper large-v3-turbo + distil-large-v3, segmented** (already integrated) — fallback
3. **Deepgram config tuning per Pipecat's TTFS methodology** — zero-GPU control arm

## Stage 1 — Synthetic GPU timing (~$1–2, ~1–2h, one EC2 GPU box)

No pipeline, no LiveKit — just the candidate models on the GPU transcribing **our actual
benchmark WAVs** (1.7s and 8s prompts) plus a 20-clip conversational sample. Measures:
model load time, warm inference P50/P95 per clip length, VRAM, and 1→4 parallel inferences.

- **Expectation:** within ±50% of the research numbers. (If reality is 2× the citations,
  the citations were wrong — that's worth knowing before building integration.)
- **Gate:** best candidate warm inference P50 on the short clip:
  ≤175ms → proceed; 175–300ms → proceed *only if* Stage 3's stop_secs=100 looks safe;
  >300ms → **kill**.

| Model | load (s) | 1.7s clip P50 | 8s clip P50 | VRAM | 4-way parallel P50 |
|---|---|---|---|---|---|
| | | | | | |

## Stage 2 — In-pipeline single bot (~$2–4, ~half day)

Full agent-runner stack on the GPU box via docker compose (local transport-server, so
the whole loop is on-box; avoids the remote-result-collection gap). Existing benchmark,
10 samples per config: top-2 candidates × stop_secs {200, 150, 100} **+ nova-3 baseline
from the same box** for network parity.

- **Expectation:** `stt_ms ≈ stop_secs + Stage-1 inference + 20–50ms`. If it's >100ms
  above that, something in our chain is wrong (queueing, resample, VAD lag) — debug
  before judging the model.
- **Gate:** any config with `stt_ms` P50 < 375ms → proceed. None → **kill** unless a
  specific fixable overhead was identified.

| Config | stt_ms P50/P95 | total_latency P50/P95 | vs nova-3 same-run |
|---|---|---|---|
| **parakeet-tdt-0.6b-v2 (local sidecar, CPU!)** 2026-06-11, n=10 | **134 / 200ms** | **813 / 976ms** | **STT −253ms, total −138ms** |
| nova-3-general (control, same run) | 387 / 406ms | 951 / 1575ms | — |

**Local-CPU result (2026-06-11):** the Parakeet sidecar — on a laptop CPU, in Docker —
**beat Deepgram nova-3** in the full pipeline: STT P50 134ms vs 387ms, total 813ms vs
951ms, with word-perfect punctuated transcripts ("What is the capital of France?") on
all 10 samples. Parakeet's P95 spread is also far tighter (976ms vs 1575ms total).
Direct sidecar timing for the same clip: ~725ms warm via curl — the in-pipeline number
is lower because inference overlaps with the audio tail still streaming (the stt_ms
anchor is the last *audio packet*, identical for both providers, so the comparison is
fair within-run). Upstream bug worked around: `should_chunk=false` per request
(Shadowfita issue #16); our segments are pre-cut by VAD anyway.

Implication: if CPU already wins, GPU (~50× faster inference) mainly buys headroom for
concurrency and longer utterances, plus tighter tails — and removes the CPU-contention
risk under multi-bot load. The remaining open questions for AWS are concurrency and
quality-at-scale (Stages 3–4), not raw latency.

### Prod dark-launch results (2026-06-11, T4 sidecar in vivaprox VPC)

Setup: g4dn.xlarge (`i-0f045aa6c337b55ab`, private 10.0.5.115, SG allows :8000 from EB
only), upstream GPU image (fp16). Prod EB env got `NEMOTRON_STT_URL`; bench rooms
flipped per-room via /config; default stayed nova-3 throughout.

**Raw sidecar (curl on-box):** 1.7s utterance **96ms**, 8s utterance **115ms**, both
word-perfect. Concurrency (single T4, serialized decoding): 4-way p50 290ms,
8-way 475ms, 16-way 842ms — GPU at 15% util / 1.5GB VRAM, so the ceiling is the
server's serialization, not hardware.

**In prod pipeline (10 sessions each, via Grafana `stt_model` split):**

| stt_ms | nova-3 | parakeet T4 (ep=200) |
|---|---|---|
| P50 | 375ms | **358ms** |
| P95 | 488ms | 486ms |

Near-tie — and the decomposition says why: in the real WebRTC path both stacks are
**endpointing-dominated** (~200ms silence wait each; parakeet then pays ~100ms GPU+VPC
vs Deepgram's ~175ms finalize). The local 134ms number benefited from anchor overlap
with the audio tail; prod streaming cadence doesn't give that gift.

**The actual lever: our VAD wait is tunable, Deepgram's isn't** (Experiment 2 addendum
proved their knob is a no-op). Matrix now generates `[ep=100]` variants for
whisper-/parakeet- chains too.

**ep=100 prod result (10 sessions):** stt_ms **334ms P50 / 483ms P95** — transcripts
still word-perfect. Full prod ladder:

| Prod stt_ms | P50 | P95 |
|---|---|---|
| Deepgram nova-3 | 375ms | 488ms |
| Parakeet T4, ep=200 | 358ms | 486ms |
| **Parakeet T4, ep=100** | **334ms** | 483ms |

**Honest read:** the prod win is ~40ms P50 (~11%), not the ~100ms the decomposition
predicted — halving the VAD wait only bought 24ms, which means the residual lives in
WebRTC audio delivery (jitter buffer, packet cadence) and VAD detection mechanics that
neither provider can dodge. P95 is delivery-bound and identical everywhere.

**What GPU self-hosting actually buys, measured:** (a) ~40ms P50 today with a knob we
control and room to push (ep=50, SmartTurn-style early commit); (b) freedom from the
vendor latency floor and per-minute STT pricing; (c) word-perfect punctuated transcripts
on par with nova-3 (formal WER pass still open, Stage 3); (d) the P95 problem is now
OURS to fix (delivery/VAD) instead of opaque vendor behavior.

**Cost reality:** T4 24/7 ≈ $380/mo on-demand vs Deepgram per-minute at research-scale
usage — for current volume Deepgram is likely cheaper; the flip-switch (`stt_model`
per-room) means we can run either at any time. Decision recorded in the log below.

## Stage 3 — Quality at speed (~$2–4, ~half day)

"Beats Deepgram" must hold at equal accuracy, measured two ways:
1. **WER** on the research-chosen standard set (candidate vs Deepgram nova-3 API on the
   identical audio), using `jiwer` with standard normalization.
2. **Endpointing quality:** long-prompt fixture (the DNS question) + a 10-minute live
   session at the winning stop_secs — count false terminations (Experiment 2 rubric:
   reject if >1 per 10 turns).

- **Expectation:** whisper-large-v3-turbo-class WER within ~1 point of nova-3 (research
  will set the precise baseline numbers).
- **Gate:** WER gap >2 points absolute or false terminations >1/10 turns → drop that
  config (try larger model or longer stop_secs; if that breaks the latency gate → **kill**).

## Stage 4 — Concurrency + soak (~$3–5, ~half day)

The winning config under load on the same box: 3 concurrent bot sessions
(benchmark parallel=3 and/or `lk load-test` for synthetic participants), 30-minute soak,
cold-start timing. Verify: shared-model serving works (no per-participant model copies),
no VRAM creep, P95 ≤ 1.5× P50 under load.

- **Gate:** instability/OOM at 3 bots → the production story needs a dedicated STT
  serving layer (Riva/sidecar) — re-cost before declaring victory.

## Stage 5 — Decision + writeup ($0)

- Side-by-side: stt_ms, WER, false-termination rate, $/month at our usage (GPU 24/7
  ≈ $380/mo for T4 vs Deepgram per-minute at measured volume) + ops burden.
- Write Experiment 6 results into `latency-experiments.md`; update
  `performance-tests.md` "ruled out / adopted" line; production rollout sketch if GO.

---

## Implementation plan — minimal pieces, local-first (added 2026-06-10)

Build order is chosen so each piece is functional and testable on the laptop before
anything touches a GPU or production.

### Piece 1 — Deepgram TTFS control arm (no new code, $0)
Replicate Pipecat's `stt-benchmark` measuring methodology against our pipeline; tune
Deepgram streaming params (interim results / endpointing / utterance_end) toward their
measured 247ms. **Local test:** `make benchmark BENCHMARK_SAMPLES=10` per config.
**Done when:** we either close the 375→247ms gap (new bar for self-hosting) or can
explain precisely why our number differs (e.g., our commit anchor includes aggregator
hops theirs doesn't).

### Piece 2 — NvidiaSTTService wired into the bot (TDD, local, ~$0)
Add `nemotron-*`/`parakeet-*` prefix to `_build_stt_for_multi_speaker` →
Pipecat `NvidiaSTTService`; `/config` allowlist + admin choices; check
`_stt_emits_vad_frames` semantics for Riva (server-side endpointing → likely no
vad_wrap needed, verify). **Local test without any GPU:** point the service at
NVIDIA's hosted endpoint (build.nvidia.com API key, free tier) and run the standard
benchmark through the full local stack — proves pipeline correctness AND gives a
hosted-Nemotron latency datapoint to compare with Pipecat's 221ms. Unit tests mirror
the whisper-chain tests.
**Needs from abby:** a free NVIDIA build.nvidia.com API key (NGC account).

### Piece 3 — Self-hosted Riva NIM on GPU EC2 (the $10–20)
Same bot code as Piece 2 — only the server address changes. Launch g6.xlarge (L4;
NIM containers are validated on L4) with nvidia-container-toolkit, run the Parakeet
NIM, run the full stack on-box (compose), benchmark: Nemotron-local vs nova-3 from
the same box, then whisper variants (Stage 1b synthetic timing on the same instance
while it's up). Terminate instance same day.
**Needs from abby:** NGC key for pulling the NIM image; instance spend OK (budgeted).

### Deploy (only if Stage 3+4 gates pass)
- STT sidecar architecture: one GPU EC2 (not EB) running the NIM in a private
  security group; agent-runner reaches it via VPC address in an env var
  (`NVIDIA_STT_SERVER`). Bot falls back to Deepgram if the sidecar is unreachable
  (config-level, no code branching beyond the existing stt_model switch).
- Rollout: deploy with stt_model still nova-3 (dark), switch one room via per-room
  `/config` scope, watch `meetlab_stt_latency_ms{stt_model=...}` in Grafana
  side-by-side, then flip the global default. Rollback = flip the config back.

## Decisions log

- 2026-06-10: scope = broad sweep incl. non-whisper streaming models; quality bar =
  standard-set WER (not just spot-check); GPU budget ≈ $10–20 on-demand. (abby + claude)

## Status

- [x] Stage 0 research launched 2026-06-10
- [ ] Stage 0 gate decision
- [ ] Stage 1
- [ ] Stage 2
- [ ] Stage 3
- [ ] Stage 4
- [ ] Stage 5 decision
