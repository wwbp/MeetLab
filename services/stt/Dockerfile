# Speech-to-text on CPU (app.py). The model is downloaded at build time into the image's
# Hugging Face cache and the server runs offline, so a new machine starts in seconds.
FROM python:3.12-slim
RUN pip install --no-cache-dir "onnx-asr[cpu,hub]==0.12.*" soundfile fastapi "uvicorn[standard]" python-multipart \
    && apt-get update && apt-get install -y --no-install-recommends libsndfile1 && rm -rf /var/lib/apt/lists/*
ENV HF_HOME=/models
RUN python -c "import onnx_asr; onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v2', quantization='int8', providers=['CPUExecutionProvider'])"
ENV HF_HUB_OFFLINE=1
WORKDIR /app
COPY app.py .
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s CMD python -c "import urllib.request,json,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://localhost:8000/health'))['ready'] else 1)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
