"""Pipecat STT service for the self-hosted Parakeet-TDT NIM (NVIDIA Speech NIM).

NemotronHTTPSTTService is a SegmentedSTTService: the in-chain VADProcessor cuts speech
segments, SegmentedSTTService wraps each one as WAV bytes, and run_stt POSTs them to the
NIM's OpenAI-compatible endpoint (POST /v1/audio/transcriptions, Triton-backed, concurrent).

Wired by _build_stt / _build_stt_for_multi_speaker in bot.py for stt_model values with the
"parakeet-" prefix, reusing the (VADProcessor, tail) chain mechanism built for local whisper.
The NIM's private endpoint is set via NEMOTRON_STT_URL. Experiment log: docs/latency-experiments.md
(Experiments 6–7); deployment: docs/gpu-stt-deployment.md.
"""
import os
from typing import AsyncGenerator

import aiohttp
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601


def nemotron_stt_url() -> str:
    """Base URL of the Parakeet NIM (e.g. http://10.0.5.21:9000). Set on the agent-runner
    env (NEMOTRON_STT_URL). No default — parakeet-* models require a reachable NIM."""
    return os.environ.get("NEMOTRON_STT_URL", "").rstrip("/")


class NemotronHTTPSTTService(SegmentedSTTService):
    """Transcribes VAD-cut speech segments via the Parakeet NIM (OpenAI-compatible API)."""

    def __init__(self, *, base_url: str, model: str, request_timeout_s: float = 30.0, **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/")
        self._request_timeout_s = request_timeout_s
        # model name's single source of truth is _settings.model (AIService)
        self._settings.model = model
        self._sync_model_name_to_metrics()
        # The NIM advertises its own served model id + requires a language field — both
        # differ from our internal stt_model. Env-overridable; defaults match the deployed
        # Parakeet NIM (multilingual offline profile).
        self._served_model = os.environ.get("NEMOTRON_STT_MODEL", "parakeet-tdt-0.6b-multi-asr-offline")
        self._language = os.environ.get("NEMOTRON_STT_LANGUAGE", "multi")

    @property
    def model_name(self) -> str:
        return self._settings.model

    async def _transcribe(self, wav_bytes: bytes) -> str:
        """POST a WAV segment to the NIM's /v1/audio/transcriptions and return the text.

        The NIM validates both the served model id and a language code (it 400/404/500s
        without them) and returns an OpenAI-style {"text": ...} body.
        """
        form = aiohttp.FormData()
        form.add_field("file", wav_bytes, filename="segment.wav", content_type="audio/wav")
        form.add_field("model", self._served_model)
        form.add_field("language", self._language)
        timeout = aiohttp.ClientTimeout(total=self._request_timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.base_url}/v1/audio/transcriptions", data=form) as resp:
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
