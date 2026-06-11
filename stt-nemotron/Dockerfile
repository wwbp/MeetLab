# CPU build of Shadowfita/parakeet-tdt-0.6b-v2-fastapi — NVIDIA Parakeet-TDT-0.6B-v2
# (NeMo) behind a FastAPI /transcribe endpoint.
#
# Upstream is GPL-3.0: the code is cloned at build time and lives ONLY inside this
# image (network-API service), it is not vendored into the MeetLab codebase.
# Upstream's own Dockerfile is the GPU variant (CUDA torch); this file swaps in CPU
# torch so the identical code/API runs on dev laptops (incl. arm64). On a GPU host,
# build upstream's Dockerfile instead — the bot only sees the same HTTP API.
#
# Experiment log: docs/experiment-6-gpu-stt.md
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ARG UPSTREAM=https://github.com/Shadowfita/parakeet-tdt-0.6b-v2-fastapi.git
ARG COMMIT=31c5652b62d0
RUN git clone --depth 50 "$UPSTREAM" . && git checkout "$COMMIT" \
    && cp .env.example .env

# Default PyPI ships CPU-only torch wheels for linux/arm64 and the /whl/cpu index
# covers x86 — install explicitly from the CPU index so x86 hosts don't pull CUDA.
RUN pip install --no-cache-dir torch==2.7.0 torchaudio==2.7.0 \
        --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir "nemo_toolkit[asr]" \
    && pip install --no-cache-dir -r requirements.txt

# Model weights (~2.4GB, nvidia/parakeet-tdt-0.6b-v2) download on first start into
# HF_HOME — mount a volume there so restarts don't re-download.
ENV DEVICE=cpu \
    MODEL_PRECISION=fp32 \
    HF_HOME=/models \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8000
CMD ["uvicorn", "parakeet_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
