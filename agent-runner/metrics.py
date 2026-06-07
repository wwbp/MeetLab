"""Prometheus metrics for agent-runner.

Histograms are observed from bot.py (background task context) and exposed via
GET /metrics in runner.py. Both share the same process so the default registry
is sufficient — no multiprocess mode needed.

Grafana scrape config (prometheus.yml):
  scrape_configs:
    - job_name: meetlab-agent-runner
      static_configs:
        - targets: ['<host>:7860']
      metrics_path: /metrics
"""

from prometheus_client import Histogram, Counter, REGISTRY  # noqa: F401

# Bucket boundaries chosen to span the expected 200–2000ms range with fine
# resolution near the sub-800ms target and coarser resolution above 1s.
_LATENCY_BUCKETS = (
    100, 150, 200, 250, 300, 350, 400, 450, 500, 600,
    700, 800, 900, 1000, 1250, 1500, 2000, 3000, 5000,
)

e2e_latency = Histogram(
    "meetlab_e2e_latency_ms",
    "End-to-end latency: transcript committed → first TTS audio chunk (ms)",
    ["stt_model", "endpointing_ms"],
    buckets=_LATENCY_BUCKETS,
)

llm_ttft = Histogram(
    "meetlab_llm_ttft_ms",
    "LLM time-to-first-token: transcript committed → first LLM token (ms)",
    ["llm_model"],
    buckets=_LATENCY_BUCKETS,
)

tts_first_chunk = Histogram(
    "meetlab_tts_first_chunk_ms",
    "TTS time-to-first-audio: first LLM token → first audio frame (ms)",
    ["tts_provider"],
    buckets=_LATENCY_BUCKETS,
)

utterances_total = Counter(
    "meetlab_utterances_total",
    "Total bot utterances written to DB",
    ["stt_model"],
)
