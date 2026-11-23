"""Pipecat STT service for the self-hosted Parakeet-TDT NIM (NVIDIA Speech NIM).

NemotronHTTPSTTService is a SegmentedSTTService: the in-chain VADProcessor cuts speech
segments, SegmentedSTTService wraps each one as WAV bytes, and run_stt POSTs them to the
NIM's OpenAI-compatible endpoint (POST /v1/audio/transcriptions, Triton-backed, concurrent).

Transcription starts early: the VAD closes a segment only after its silence wait
(stt_endpointing_ms, 450), so the service sends what it has a moment into the pause
(speech_paused) and, when the segment closes with nothing more said, uses that answer
instead of asking again. Speaking again throws the early answer away.

Wired by _build_stt / _build_stt_for_multi_speaker in bot.py for stt_model values with the
"parakeet-" prefix, as the tail of a (VADProcessor, tail) chain.
The NIM's private endpoint is set via NEMOTRON_STT_URL. Experiment log: v1.0.0:docs/latency-experiments.md
(Experiments 6–7); deployment: v1.0.0:docs/gpu-stt-deployment.md.
"""
import asyncio
import os
from typing import AsyncGenerator

import aiohttp
from loguru import logger
from pipecat.audio.utils import pcm_to_wav
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame, VADUserStoppedSpeakingFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601


def nemotron_stt_url() -> str:
    """Base URL of the Parakeet NIM (e.g. http://10.0.5.21:9000). Set on the agent-runner
    env (NEMOTRON_STT_URL). No default — parakeet-* models require a reachable NIM."""
    return os.environ.get("NEMOTRON_STT_URL", "").rstrip("/")


class NemotronHTTPSTTService(SegmentedSTTService):
    """Transcribes VAD-cut speech segments via the Parakeet NIM (OpenAI-compatible API)."""

    def __init__(self, *, base_url: str, model: str, request_timeout_s: float = 30.0,
                 speculate_after_secs: float = 0.15, **kwargs):
        super().__init__(**kwargs)
        # How long a pause must last before we transcribe early. Shorter saves more of the
        # silence wait; longer sends fewer requests for breaths inside a sentence.
        # ponytail: one extra request per pause this long; raise it if the speech server gets busy.
        self._speculate_after_secs = speculate_after_secs
        self._early: asyncio.Task | None = None       # for the pause in progress
        self._early_sent = False                      # it has taken its audio
        self._closed: list[asyncio.Task | None] = []  # one per closed segment, in order
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

    def speech_paused(self) -> None:
        """The speaker went silent: start transcribing what we have."""
        self.speech_resumed()
        self._early_sent = False
        self._early = asyncio.create_task(self._speculate())
        self._early.add_done_callback(lambda t: t.cancelled() or t.exception())

    def speech_resumed(self) -> None:
        """They kept talking: the early answer is for half a sentence."""
        if self._early is not None:
            self._early.cancel()
            self._early = None

    def segment_closed(self) -> None:
        """The VAD closed the segment: its early answer (or none) waits for run_stt."""
        if not self._early_sent:  # the buffer is about to be cleared: too late to send
            self.speech_resumed()
        self._closed.append(self._early)
        self._early = None

    async def _speculate(self) -> tuple[bytes, str]:
        if self._speculate_after_secs:
            await asyncio.sleep(self._speculate_after_secs)
        pcm = bytes(self._audio_buffer)
        self._early_sent = True
        wav = pcm_to_wav(pcm + self._trailing_silence(), self.sample_rate)
        return pcm, await self._transcribe(wav)

    async def _early_text(self, wav: bytes) -> str | None:
        """The early answer for this segment, if it was made from this segment's audio."""
        task = self._closed.pop(0) if self._closed else None
        if task is None:
            return None
        try:
            pcm, text = await task
        except Exception as e:
            logger.warning(f"early transcription failed, transcribing the segment: {e}")
            return None
        return text if wav[44:].startswith(pcm) else None  # 44: the WAV header

    async def process_frame(self, frame, direction):
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self.segment_closed()
        await super().process_frame(frame, direction)

    async def cancel(self, frame):
        for task in [self._early, *self._closed]:
            if task is not None:
                task.cancel()
        await super().cancel(frame)

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Transcribe one WAV-encoded speech segment (SegmentedSTTService contract)."""
        try:
            await self.start_processing_metrics()
            await self.start_ttfb_metrics()
            text = await self._early_text(audio)
            if text is None:
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
