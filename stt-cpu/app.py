"""Speech-to-text on CPU: Parakeet TDT 0.6B v2 (int8, ONNX) behind the endpoint the bots already
call for the GPU NIM (agent-runner/nemotron_stt.py: POST /v1/audio/transcriptions, a WAV segment
plus `model` and `language`, answered with {"text": ...}). No GPU, no NVIDIA licence; the model is
baked into the image, so it starts in seconds. Which server bots use is a profile setting
(infra/v2/stack/profiles.tf); the switch point to the GPU is measured (docs/load-testing.md).
"""
import io
import os
from contextlib import asynccontextmanager

import numpy as np
import onnx_asr
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

MODEL = os.environ.get("STT_MODEL", "nemo-parakeet-tdt-0.6b-v2")
RATE = 16000
_model = None


@asynccontextmanager
async def lifespan(_app):
    global _model
    _model = onnx_asr.load_model(MODEL, quantization="int8", providers=["CPUExecutionProvider"])  # the image's HF cache
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"ready": _model is not None}


@app.post("/v1/audio/transcriptions")
def transcribe(file: UploadFile = File(...), model: str = Form(""), language: str = Form("")):
    """Sync on purpose: FastAPI runs it on its thread pool, so segments from many rooms are
    transcribed in parallel (onnxruntime releases the GIL). `model`/`language` are the NIM's
    fields, accepted and ignored: this server has one model."""
    try:
        audio, rate = sf.read(io.BytesIO(file.file.read()), dtype="float32", always_2d=True)
    except Exception:
        raise HTTPException(400, "not a readable audio file")
    audio = audio.mean(axis=1)
    if rate != RATE:  # ponytail: linear resampling; the bots send 16 kHz, so this is rarely used
        audio = np.interp(np.arange(0, len(audio), rate / RATE), np.arange(len(audio)), audio).astype("float32")
    return {"text": _model.recognize(audio, sample_rate=RATE) if len(audio) else ""}
