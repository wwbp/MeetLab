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
