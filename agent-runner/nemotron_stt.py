"""Pipecat STT service for the stt-nemotron sidecar (NVIDIA Parakeet-TDT, Experiment 6).

NemotronHTTPSTTService is a SegmentedSTTService: the in-chain VADProcessor cuts
speech segments, SegmentedSTTService wraps each one as WAV bytes, and run_stt POSTs
them to the sidecar's /transcribe endpoint (Shadowfita/parakeet-tdt-0.6b-v2-fastapi,
running CPU locally and GPU in cloud — same HTTP API either way).

Wired by _build_stt / _build_stt_for_multi_speaker in bot.py for stt_model values
with the "parakeet-" prefix, reusing the (VADProcessor, tail) chain mechanism built
for local whisper. Experiment log: docs/experiment-6-gpu-stt.md
"""
import os
from typing import AsyncGenerator

import aiohttp
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601

DEFAULT_NEMOTRON_STT_URL = "http://stt-nemotron:8000"


def nemotron_stt_url() -> str:
    return os.environ.get("NEMOTRON_STT_URL", DEFAULT_NEMOTRON_STT_URL).rstrip("/")


def nemotron_stt_api() -> str:
    """Which server API the sidecar speaks:

    'shadowfita' (default) — the community FastAPI wrapper's POST /transcribe.
    'openai'               — NVIDIA NIM / Riva OpenAI-compatible POST /v1/audio/transcriptions.

    Flip NEMOTRON_STT_API=openai (and point NEMOTRON_STT_URL at the NIM) to migrate to the
    concurrent Triton-backed server without any per-room config change.
    """
    return os.environ.get("NEMOTRON_STT_API", "shadowfita").strip().lower()


class NemotronHTTPSTTService(SegmentedSTTService):
    """Transcribes VAD-cut speech segments via the stt-nemotron sidecar."""

    def __init__(self, *, base_url: str, model: str, request_timeout_s: float = 30.0,
                 api: str = "shadowfita", **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/")
        self._request_timeout_s = request_timeout_s
        self._api = api.strip().lower()
        # model name's single source of truth is _settings.model (AIService)
        self._settings.model = model
        self._sync_model_name_to_metrics()

    @property
    def model_name(self) -> str:
        return self._settings.model

    async def _transcribe(self, wav_bytes: bytes) -> str:
        """POST a WAV segment to the STT server and return the transcript text.

        Both server flavors return an OpenAI-style {"text": ...} body; only the endpoint
        and form fields differ.
        """
        form = aiohttp.FormData()
        form.add_field("file", wav_bytes, filename="segment.wav", content_type="audio/wav")
        if self._api == "openai":
            # NVIDIA NIM / Riva OpenAI-compatible transcription endpoint.
            form.add_field("model", self._settings.model)
            path = "/v1/audio/transcriptions"
        else:
            # Segments are already VAD-cut single utterances — server-side chunking is
            # redundant, and upstream's chunking path crashes (Shadowfita issue #16).
            form.add_field("should_chunk", "false")
            path = "/transcribe"
        timeout = aiohttp.ClientTimeout(total=self._request_timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.base_url}{path}", data=form) as resp:
                resp.raise_for_status()
                payload = await resp.json()
        return (payload.get("text") or "").strip()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Transcribe one WAV-encoded speech segment (SegmentedSTTService contract)."""
        try:
            await self.start_processing_metrics()
            await self.start_ttfb_metrics()
            text = await self._transcribe(audio)
            await self.stop_ttfb_metrics()
            await self.stop_processing_metrics()
            if text:
                yield TranscriptionFrame(
                    text=text,
                    user_id=self._user_id,
                    timestamp=time_now_iso8601(),
                    result=None,
                )
        except Exception as e:
            logger.error(f"NemotronHTTPSTTService error: {e}")
            yield ErrorFrame(f"Nemotron STT error: {e}")
