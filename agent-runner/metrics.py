from opentelemetry import metrics as _api

_meter = _api.get_meter("meetlab.agent-runner")

LATENCY_BOUNDARIES = [
    50, 100, 150, 200, 250, 300, 350, 400, 450, 500,
    600, 700, 800, 900, 1000, 1250, 1500, 2000, 3000, 5000,
]

stt_latency = _meter.create_histogram(
    "meetlab.stt_latency_ms",
    unit="ms",
    description="STT latency: last audio frame → transcript committed (endpointing + transcription + network)",
)
e2e_latency = _meter.create_histogram(
    "meetlab.e2e_latency_ms",
    unit="ms",
    description="Post-STT E2E latency: transcript committed → first TTS audio chunk",
)
llm_ttft = _meter.create_histogram(
    "meetlab.llm_ttft_ms",
    unit="ms",
    description="LLM time-to-first-token",
)
sentence_agg = _meter.create_histogram(
    "meetlab.sentence_agg_ms",
    unit="ms",
    description="Sentence aggregation duration before TTS",
)
tts_ttfb = _meter.create_histogram(
    "meetlab.tts_ttfb_ms",
    unit="ms",
    description="TTS time-to-first-byte",
)
utterances_total = _meter.create_counter(
    "meetlab.utterances_total",
    description="Total utterances processed",
)

# ── Phase 1 diagnostics: latency-spike root-cause instrumentation ────────────
# These exist to discriminate between the candidate causes of occasional
# extreme STT latency: VAD held open by continuous noise, output-queue backlog,
# STT provider stalls, and bot self-echo. See docs and bot.py spike logging.

stt_queue_depth = _meter.create_histogram(
    "meetlab.stt_queue_depth",
    description="MultiSpeakerSTT output-queue depth sampled at each dequeue (backlog detector)",
)
stt_spikes_total = _meter.create_counter(
    "meetlab.stt_spikes_total",
    description="Count of utterances whose stt_ms exceeded the spike threshold",
)
phantom_segments_total = _meter.create_counter(
    "meetlab.phantom_segments_total",
    description="User turns committed with empty content (VAD/STT fired but no transcript)",
)
self_echo_suspected_total = _meter.create_counter(
    "meetlab.self_echo_suspected_total",
    description="User transcripts that closely match recent bot TTS output (likely speaker re-capture)",
)

# ── Interruption handling: the bot should yield, not talk over users ─────────
bot_interruptions_total = _meter.create_counter(
    "meetlab.bot_interruptions_total",
    description="Times a user started speaking while the bot was still speaking (talk-over events)",
)
bot_talkover_ms = _meter.create_histogram(
    "meetlab.bot_talkover_ms",
    unit="ms",
    description="How long the bot kept speaking after a user interrupted (lower = yields faster)",
)

participants_refused_total = _meter.create_counter(
    "meetlab.participants_refused_total",
    description="Participants refused a recognition stream because the room hit its cap",
)
