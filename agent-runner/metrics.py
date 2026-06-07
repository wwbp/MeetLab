"""Prometheus metrics for agent-runner.

Histograms are observed from bot.py via _MetricsObserver (Pipecat MetricsFrame)
and exposed via GET /metrics in runner.py.

Stage breakdown per turn (all in ms):
  llm_ttft       — Pipecat LLM TTFB: API request sent → first token received
  sentence_agg   — Pipecat TextAggregation: first LLM token → first sentence sent to TTS
  tts_ttfb       — Pipecat TTS TTFB: text sent to ElevenLabs API → first audio chunk
  e2e_latency    — inline: transcript committed → first TTS audio chunk (llm+agg+tts combined)

Grafana scrape config (prometheus.yml):
  scrape_configs:
    - job_name: meetlab-agent-runner
      static_configs:
        - targets: ['<host>:7860']
      metrics_path: /metrics
"""

from prometheus_client import Counter, Histogram  # noqa: F401

# Bucket boundaries span 50–3000ms with fine resolution near the 100–500ms target range.
_LATENCY_BUCKETS = (
    50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 600,
    700, 800, 900, 1000, 1250, 1500, 2000, 3000, 5000,
)

e2e_latency = Histogram(
    "meetlab_e2e_latency_ms",
    "Post-transcript E2E: transcript committed → first TTS audio chunk (ms)",
    ["stt_model", "endpointing_ms"],
    buckets=_LATENCY_BUCKETS,
)

llm_ttft = Histogram(
    "meetlab_llm_ttft_ms",
    "LLM TTFB: API request sent → first token received (ms)",
    ["llm_model"],
    buckets=_LATENCY_BUCKETS,
)

sentence_agg = Histogram(
    "meetlab_sentence_agg_ms",
    "Sentence aggregation: first LLM token → first sentence boundary sent to TTS (ms)",
    ["tts_provider"],
    buckets=_LATENCY_BUCKETS,
)

tts_ttfb = Histogram(
    "meetlab_tts_ttfb_ms",
    "TTS TTFB: text sent to ElevenLabs API → first audio chunk (ms)",
    ["tts_provider"],
    buckets=_LATENCY_BUCKETS,
)

utterances_total = Counter(
    "meetlab_utterances_total",
    "Total bot utterances written to DB",
    ["stt_model"],
)
