import asyncio
import json
import os
import re
import sys
import time
import uuid as _uuid_mod
from dataclasses import replace
from datetime import datetime, timezone

from loguru import logger
from PIL import Image

from livekit import rtc

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    UserAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TextAggregationMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService, OpenAIRealtimeSTTSettings
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

import audio_tracks
from config import load_config, require
from db.config_loader import load_bot_config
from db.engine import AsyncSessionLocal
from db.models import Conversation, MediaFile, Speaker, Utterance
from interruption import InterruptionTracker
from multi_speaker_stt import MultiSpeakerSTT, SpeakerLabelInjector
from runner_types import LiveKitRunnerArguments

_STT_DELAY_VALUES = frozenset({"minimal", "low", "medium", "high", "xhigh"})

# ── Phase 1 diagnostics tunables ─────────────────────────────────────────────
# stt_ms above this is logged + counted as a spike so we can capture the
# conditions (queue depth, model, content) when extreme latency occurs.
_STT_SPIKE_THRESHOLD_MS = float(os.getenv("STT_SPIKE_THRESHOLD_MS", "2000"))
# Word-overlap above this between a user transcript and recent bot TTS flags a
# likely speaker re-capture (browser AEC failure). Heuristic, not exact.
_SELF_ECHO_SIMILARITY = float(os.getenv("STT_SELF_ECHO_SIMILARITY", "0.65"))
# How many recent bot utterances to compare incoming user transcripts against.
_RECENT_BOT_TEXT_WINDOW = 5

_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize_words(text: str) -> list[str]:
    """Lowercase word tokens, stripping punctuation and any 'Name:' speaker prefix."""
    # SpeakerLabelInjector prepends "DisplayName: " to user transcripts; drop the
    # prefix so the name itself doesn't dilute the bot-echo comparison. Only treat
    # it as a prefix when the part before ": " is short (a name, ≤3 words), so a
    # colon deeper in real content (e.g. "...a strange ratio: forty two") is kept.
    if text and ": " in text:
        head, body = text.split(": ", 1)
        if len(head.split()) <= 3:
            text = body
    return _WORD_RE.findall((text or "").lower())


def _text_similarity(a: str, b: str) -> float:
    """Word-level Jaccard similarity in [0, 1]; 0 if either side is empty.

    Cheap self-echo heuristic: a user transcript that overlaps heavily with
    recent bot TTS text is likely the bot's own voice re-captured by a speaker.
    """
    wa, wb = set(_normalize_words(a)), set(_normalize_words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


class _OpenAIRealtimeSTT(OpenAIRealtimeSTTService):
    """OpenAIRealtimeSTTService extended with transcription delay support.

    Pipecat 1.2.1 does not expose the `delay` field from OpenAI's realtime
    transcription API. This subclass injects it into the session.update
    payload when provided, so callers can tune latency vs accuracy tradeoffs.
    """

    def __init__(self, *, transcription_delay: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self._transcription_delay = transcription_delay

    async def _send_session_update(self):
        if not self._transcription_delay:
            return await super()._send_session_update()

        # Replicate parent payload and inject delay into transcription dict.
        # Only gpt-realtime-whisper supports this field.
        from pipecat.services.openai.stt import OPENAI_SAMPLE_RATE
        from pipecat.utils.language import Language

        settings: OpenAIRealtimeSTTSettings = self._settings
        transcription: dict = {"model": settings.model, "delay": self._transcription_delay}

        language_code = self._language_to_code(settings.language) if settings.language else None
        if language_code:
            transcription["language"] = language_code
        if settings.prompt:
            transcription["prompt"] = settings.prompt

        input_audio: dict = {
            "format": {"type": "audio/pcm", "rate": OPENAI_SAMPLE_RATE},
            "transcription": transcription,
        }

        if self._turn_detection is False:
            input_audio["turn_detection"] = None
        elif self._turn_detection is not None:
            input_audio["turn_detection"] = self._turn_detection

        if settings.noise_reduction:
            input_audio["noise_reduction"] = {"type": settings.noise_reduction}

        await self._ws_send({
            "type": "session.update",
            "session": {"type": "transcription", "audio": {"input": input_audio}},
        })


def _build_vad_processor(bot_config):
    """Per-participant Silero VADProcessor for segmented STT chains.

    stop_secs mirrors Deepgram's endpointing_ms so stt_ms numbers stay
    comparable across providers.
    """
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.processors.audio.vad_processor import VADProcessor

    endpointing_ms = getattr(bot_config, "stt_endpointing_ms", 200) or 200
    return VADProcessor(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=endpointing_ms / 1000))
    )


def _build_whisper_chain(bot_config):
    """Local Whisper STT chain: (VADProcessor, WhisperSTTService) head/tail pair.

    WhisperSTTService is a SegmentedSTTService — it transcribes only the audio
    between VADUserStarted/StoppedSpeakingFrames and emits nothing without them.
    MultiSpeakerSTT routes raw per-participant audio straight into each STT
    (the transport's VAD never sees that path), so the chain carries its own
    VADProcessor.

    Note: WhisperSTTService.__init__ loads the model eagerly (downloads on first
    use) — the first participant's chain creation blocks until the model is warm.
    """
    from pipecat.services.whisper.stt import WhisperSTTService

    model_name = bot_config.stt_model[len("whisper-"):]
    vad = _build_vad_processor(bot_config)
    stt = WhisperSTTService(settings=WhisperSTTService.Settings(model=model_name))
    vad.link(stt)
    return (vad, stt)


def _build_parakeet_chain(bot_config):
    """Parakeet NIM chain: (VADProcessor, NemotronHTTPSTTService).

    Same segmented shape as the whisper chain; the tail POSTs each segment to the
    Parakeet NIM at NEMOTRON_STT_URL. See docs/gpu-stt-deployment.md.
    """
    from nemotron_stt import NemotronHTTPSTTService, nemotron_stt_url

    vad = _build_vad_processor(bot_config)
    stt = NemotronHTTPSTTService(base_url=nemotron_stt_url(), model=bot_config.stt_model)
    vad.link(stt)
    return (vad, stt)


def _apply_stt_model_override(bot_config):
    """Force the bot's STT model regardless of the DB config, when STT_MODEL_OVERRIDE is set.

    Local dev has no GPU to run the Parakeet NIM (the prod default), so the local stack sets
    STT_MODEL_OVERRIDE=whisper-base to transcribe in-process instead of depending on a sidecar.
    Prod leaves it unset and uses the DB config (NIM). This is applied only to the running bot,
    not to the /config store/API — those still reflect what's actually persisted.
    """
    override = (os.environ.get("STT_MODEL_OVERRIDE") or "").strip()
    if override and override != bot_config.stt_model:
        logger.info(f"STT_MODEL_OVERRIDE active: {bot_config.stt_model} → {override}")
        return replace(bot_config, stt_model=override)
    return bot_config


def _build_stt(bot_config, openai_api_key: str, deepgram_api_key: str | None):
    """Instantiate the STT service based on stt_model prefix.

    Models starting with 'gpt-' use OpenAI Realtime STT; 'whisper-' returns a
    local (VADProcessor, WhisperSTTService) chain — see _build_whisper_chain;
    everything else (nova-*, etc.) uses Deepgram.
    """
    if bot_config.stt_model.startswith("gpt-"):
        return _OpenAIRealtimeSTT(
            api_key=openai_api_key,
            turn_detection=_turn_detection_for_vad_mode(bot_config.stt_vad_mode),
            transcription_delay=bot_config.stt_delay,
            settings=_OpenAIRealtimeSTT.Settings(
                model=bot_config.stt_model,
                noise_reduction="near_field",
            ),
        )
    if bot_config.stt_model.startswith("whisper-"):
        return _build_whisper_chain(bot_config)
    if bot_config.stt_model.startswith("parakeet-"):
        return _build_parakeet_chain(bot_config)
    # Deepgram path — disable server endpointing so local Silero VAD drives commits
    if not deepgram_api_key:
        raise ValueError("DEEPGRAM_API_KEY is required for Deepgram STT models")
    return DeepgramSTTService(
        api_key=deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model=bot_config.stt_model,
            endpointing=False,
        ),
    )


_AVATAR_PATH = os.path.join(os.path.dirname(__file__), "avatar.png")
_avatar_tasks: set = set()


async def _publish_avatar(room: rtc.Room) -> None:
    img = Image.open(_AVATAR_PATH).convert("RGBA")
    w, h = img.size
    frame = rtc.VideoFrame(w, h, rtc.VideoBufferType.RGBA, img.tobytes())
    source = rtc.VideoSource(w, h)

    track = rtc.LocalVideoTrack.create_video_track("avatar", source)
    await room.local_participant.publish_track(track)
    logger.info("Avatar video track published")

    async def _frame_loop():
        try:
            while True:
                source.capture_frame(frame)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_frame_loop())
    _avatar_tasks.add(task)
    task.add_done_callback(_avatar_tasks.discard)


logger.remove()
logger.add(sys.stderr, level="DEBUG")


async def _finalize_conversation(session_id: str, status: str) -> None:
    """Write a conversation's terminal status + ended_at, resilient to cancellation.

    Conversation.status would otherwise stay stuck on 'running' if the bot task is
    cancelled mid-write — all participants leaving fires on_participant_disconnected
    → task.cancel(), and a plain `await` inside the shutdown path can be interrupted
    before the UPDATE commits. The write runs inside asyncio.shield so a cancellation
    of the surrounding bot() coroutine cannot abort the commit; if our await is the
    one cancelled, we still wait for the shielded write to finish before re-raising.
    """
    async def _write() -> None:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == session_id)
                    .values(ended_at=datetime.now(timezone.utc), status=status)
                )

    write_task = asyncio.ensure_future(_write())
    try:
        await asyncio.shield(write_task)
    except asyncio.CancelledError:
        await write_task  # shielded write is still running — let it commit
        raise
    except Exception as e:
        logger.error(f"Failed to finalize conversation {session_id}: {e}")


async def bot(runner_args: LiveKitRunnerArguments):
    logger.info(f"Bot starting - joining room: {runner_args.room_name}")

    # sid → identity lookup: populated at on_participant_connected / on_participant_disconnected
    _sid_to_identity: dict[str, str] = {}
    # sid → clean display name (LiveKit token name field, strips __randomPostfix)
    _sid_to_name: dict[str, str] = {}
    # SID of the participant who triggered the current user turn.
    # Written by _SpeakerTracker (TranscriptionFrame.user_id, reliable for data-channel path)
    # and on_active_speaker_changed (best-effort for mixed-audio STT path).
    _current_speaker_sid: list[str | None] = [None]
    # Raw SID of the last participant who sent a data-channel message.
    # Set in on_data_received before queueing frames so on_user_turn_stopped
    # can fall back to it when _sid_to_identity is not yet populated.
    _last_data_sender: list[str | None] = [None]
    # last utterance ids for reply_to chaining
    _last_user_utt_id: list[str | None] = [None]
    _last_bot_utt_id: list[str | None] = [None]

    transport = LiveKitTransport(
        url=runner_args.url,
        token=runner_args.token,
        room_name=runner_args.room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            video_in_enabled=False,
        ),
    )

    env_config = load_config()
    openai_api_key = require(env_config.openai_api_key, "OPENAI_API_KEY")
    elevenlabs_api_key = require(env_config.elevenlabs_api_key, "ELEVENLABS_API_KEY")

    bot_config = _apply_stt_model_override(await load_bot_config(runner_args.room_name))
    logger.info(
        f"Loaded bot config for room '{runner_args.room_name}': "
        f"model={bot_config.llm_model} voice={bot_config.tts_voice} vad={bot_config.vad_stop_secs}s"
    )

    # Per-participant STT: each participant gets a dedicated STT instance so
    # TranscriptionFrame.user_id is always the correct LiveKit participant SID.
    # The factory is called once per participant join — OpenAI Realtime STT uses
    # server-side VAD (turn_detection=None) so each instance handles its own
    # turn boundaries and emits UserStarted/StoppedSpeakingFrames independently.
    def _stt_factory():
        return _build_stt_for_multi_speaker(bot_config, openai_api_key, env_config.deepgram_api_key)

    # Interruption tracking: the bot should yield, not talk over users. The tracker
    # is fed bot-speaking frames (via _InterruptionObserver) and REAL user speech
    # onset from the per-participant VAD (via MultiSpeakerSTT's on_speech_onset).
    interruptions = InterruptionTracker(labels={"stt_model": bot_config.stt_model})

    def _on_speech_onset(sid: str) -> None:
        interruptions.user_onset(time.monotonic(), sid)

    multi_stt = MultiSpeakerSTT(_stt_factory, on_speech_onset=_on_speech_onset)
    logger.info(
        f"STT: model={bot_config.stt_model} mode=per-participant delay={bot_config.stt_delay}"
    )

    # Per-speaker audio capture. The sink buffers each participant's PCM and writes
    # one WAV per speaker on flush; it stays disabled until recording is requested
    # (POST /recordings/start or auto_record). Registered by room name so the
    # in-process recording endpoint can enable it. The recorder tap is inserted
    # before multi_stt (which consumes UserAudioRawFrame) and forwards frames on.
    audio_sink = build_audio_track_sink(
        runner_args.room_name, runner_args.session_id, _sid_to_identity
    )
    audio_tracks.register_sink(runner_args.room_name, audio_sink)
    audio_recorder = audio_tracks.PerSpeakerAudioRecorder(audio_sink)
    # Mock TTS (BOT_MOCK_TTS) swaps in zero-cost synthetic silence for load/soak
    # testing — no paid TTS calls. OFF by default; never enable in production. The LLM
    # is pinned to the cheapest model by the soak harness rather than mocked.
    _mock_tts = os.getenv("BOT_MOCK_TTS", "").lower() in ("1", "true", "yes")

    llm = OpenAILLMService(api_key=openai_api_key, model=bot_config.llm_model)
    _tts_mode = (
        TextAggregationMode.TOKEN
        if bot_config.tts_aggregation_mode == "token"
        else TextAggregationMode.SENTENCE
    )
    if _mock_tts:
        from mock_services import MockTTSService
        logger.warning("BOT_MOCK_TTS enabled — synthetic silence, no TTS API calls")
        tts = MockTTSService(text_aggregation_mode=_tts_mode)
    elif bot_config.tts_provider == "openai":
        from pipecat.services.openai.tts import OpenAITTSService
        tts = OpenAITTSService(
            api_key=openai_api_key,
            voice=bot_config.tts_voice or "alloy",
            text_aggregation_mode=_tts_mode,
        )
    else:
        tts = ElevenLabsTTSService(
            api_key=elevenlabs_api_key,
            settings=ElevenLabsTTSService.Settings(voice=bot_config.tts_voice),
            text_aggregation_mode=_tts_mode,
        )
    logger.info(
        f"TTS: provider={bot_config.tts_provider} voice={bot_config.tts_voice}"
        f" aggregation={bot_config.tts_aggregation_mode}"
    )

    context = LLMContext([{"role": "system", "content": bot_config.system_prompt}])
    # VAD is handled per-participant inside each dedicated STT instance, so the
    # context aggregator does not need its own VAD analyzer.
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=None),
    )

    # Per-turn timing — stt_done/tts_first anchors for E2E (stt_done → tts_first).
    # Per-stage breakdown (llm_ttft_ms, sentence_agg_ms, tts_ttfb_ms) comes from
    # Pipecat's MetricsFrame via _MetricsObserver and is stored in _metrics_data.
    _turn_timing: dict[str, float] = {}
    _metrics_data: dict[str, float] = {}
    # Monotonic timestamp of the last UserAudioRawFrame received per participant SID.
    # Used to compute real STT latency: last audio frame → transcript committed.
    # Covers endpointing silence wait + transcription + network roundtrip.
    _last_audio_times: dict[str, float] = {}
    # Recent bot TTS texts, for the self-echo heuristic in on_user_turn_stopped.
    _recent_bot_texts: list[str] = []

    class _MetricsObserver(BaseObserver):
        """Intercepts Pipecat MetricsFrame to capture per-stage TTFB values.

        Writes to _metrics_data (shared closure dict) so on_assistant_turn_stopped
        can persist them to Utterance.meta and observe Prometheus histograms.
        Prometheus observations are also made immediately here so they aren't lost
        if on_assistant_turn_stopped fires before all MetricsFrames arrive.
        """

        def __init__(self):
            super().__init__()
            self._seen: set = set()

        async def on_push_frame(self, data: FramePushed):
            frame = data.frame
            if not isinstance(frame, MetricsFrame) or frame.id in self._seen:
                return
            self._seen.add(frame.id)
            for m in frame.data:
                try:
                    self._handle(m)
                except Exception:
                    pass

        def _handle(self, m):
            import metrics as _prom
            if isinstance(m, TTFBMetricsData):
                val_ms = m.value * 1000
                p = m.processor.lower()
                if "llm" in p or "openai" in p:
                    _metrics_data["llm_ttft_ms"] = val_ms
                    _prom.llm_ttft.record(val_ms, {"llm_model": bot_config.llm_model})
                elif "elevenlabs" in p or "tts" in p:
                    _metrics_data["tts_ttfb_ms"] = val_ms
                    _prom.tts_ttfb.record(val_ms, {"tts_provider": bot_config.tts_provider})
            elif isinstance(m, TextAggregationMetricsData):
                val_ms = m.value * 1000
                _metrics_data["sentence_agg_ms"] = val_ms
                _prom.sentence_agg.record(val_ms, {"tts_provider": bot_config.tts_provider})

    class _InterruptionObserver(BaseObserver):
        """Feeds the bot's TTS speaking window to the InterruptionTracker.

        Pairs with MultiSpeakerSTT's on_speech_onset (real user speech start) so the
        tracker can tell when a user spoke while the bot was still talking.
        """

        def __init__(self):
            super().__init__()
            self._seen: set = set()

        async def on_push_frame(self, data: FramePushed):
            frame = data.frame
            if frame.id in self._seen:
                return
            if isinstance(frame, BotStartedSpeakingFrame):
                self._seen.add(frame.id)
                interruptions.bot_started(time.monotonic())
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._seen.add(frame.id)
                interruptions.bot_stopped(time.monotonic())

    class _AudioTimestampRecorder(FrameProcessor):
        """Records the monotonic time of each participant's last audio frame.

        Must sit before multi_stt in the pipeline — multi_stt consumes
        UserAudioRawFrame and does not re-emit it downstream.
        The timestamp is later read by _SpeakerTracker to compute stt_ms.
        """

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if direction == FrameDirection.DOWNSTREAM and isinstance(frame, UserAudioRawFrame):
                if frame.user_id:
                    _last_audio_times[frame.user_id] = time.monotonic()
            await self.push_frame(frame, direction)

    class _SpeakerTracker(FrameProcessor):
        """Records the SID of whoever sent the most recent TranscriptionFrame.

        Sits between the STT service and the context aggregator so it fires on
        every transcription, regardless of turn order or when participants joined.

        Data-channel path: TranscriptionFrame.user_id is the sender's SID (set
        explicitly in on_data_received) — perfectly accurate.

        Audio (mixed-STT) path: STT services don't populate user_id on frames
        they emit, so this tracker has no effect there. Attribution for the audio
        path is handled by on_active_speaker_changed instead.
        """

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame):
                if frame.user_id:
                    _current_speaker_sid[0] = frame.user_id
                    identity = _sid_to_identity.get(frame.user_id, frame.user_id)
                    logger.debug("SpeakerTracker: {} → {}", identity, frame.text[:60])
                    # Latch the last-audio timestamp for this speaker so stt_ms
                    # measures: last audio frame → transcript committed.
                    last_ts = _last_audio_times.get(frame.user_id)
                    if last_ts is not None:
                        _turn_timing["last_audio_ts"] = last_ts
            await self.push_frame(frame, direction)

    class _TTSFirstTimer(FrameProcessor):
        """Records when TTS produces its first audio chunk of each turn."""

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TTSAudioRawFrame):
                _turn_timing.setdefault("tts_first", time.monotonic())
            await self.push_frame(frame, direction)

    pipeline = Pipeline(
        [
            transport.input(),
            _AudioTimestampRecorder(),
            audio_recorder,
            multi_stt,
            _SpeakerTracker(),
            SpeakerLabelInjector(_sid_to_identity, _sid_to_name),
            context_aggregator.user(),
            llm,
            tts,
            _TTSFirstTimer(),
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[MetricsLogObserver(), _MetricsObserver(), _InterruptionObserver()],
        enable_tracing=env_config.enable_tracing,
        enable_turn_tracking=env_config.enable_tracing,
        conversation_id=runner_args.session_id,
        additional_span_attributes={
            "room.name": runner_args.room_name,
            "bot.identity": runner_args.bot_identity,
            "llm.model": bot_config.llm_model,
            "tts.voice": bot_config.tts_voice,
        },
    )

    # --- transcript hooks ---

    @context_aggregator.user().event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        # Anchor for E2E: moment the transcript is committed to the LLM context.
        # Per-stage breakdown (llm_ttft, sentence_agg, tts_ttfb) comes from MetricsFrame.
        stt_done_time = time.monotonic()
        _turn_timing["stt_done"] = stt_done_time
        _turn_timing.pop("tts_first", None)
        _metrics_data.clear()
        # Real STT latency: last audio packet → transcript committed.
        # Covers endpointing silence wait + transcription + network roundtrip.
        last_audio_ts = _turn_timing.pop("last_audio_ts", None)
        if last_audio_ts is not None:
            _turn_timing["stt_ms"] = (stt_done_time - last_audio_ts) * 1000
        # Spike diagnostics: capture the conditions when stt_ms goes extreme so we
        # can attribute it (queue backlog vs held-open VAD vs provider stall).
        stt_ms = _turn_timing.get("stt_ms")
        # Record queue depth on every turn so the overlap scenario can see backlog
        # even when no individual turn crosses the spike threshold.
        _turn_timing["diag_queue_depth"] = multi_stt.qsize()
        if stt_ms is not None and stt_ms > _STT_SPIKE_THRESHOLD_MS:
            _turn_timing["diag_stt_spike"] = True
            _ep = getattr(bot_config, "stt_endpointing_ms", 200)
            try:
                import metrics as _prom
                _prom.stt_spikes_total.add(
                    1, {"stt_model": bot_config.stt_model, "endpointing_ms": str(_ep)}
                )
            except Exception:
                pass
            logger.warning(
                "STT latency spike: stt_ms={:.0f} model={} endpointing_ms={} "
                "queue_depth={} content={!r}",
                stt_ms, bot_config.stt_model, _ep, multi_stt.qsize(),
                (message.content or "")[:80],
            )
        if not message.content:
            # VAD/STT fired but produced no transcript — these are dropped from the
            # stt_ms histogram, so count them separately to explain percentile skew.
            try:
                import metrics as _prom
                _prom.phantom_segments_total.add(1, {"stt_model": bot_config.stt_model})
            except Exception:
                pass
            logger.debug("Phantom segment: user turn committed with empty content")
            return
        # Self-echo heuristic: does this user transcript echo recent bot TTS?
        for _bot_text in _recent_bot_texts:
            sim = _text_similarity(message.content, _bot_text)
            if sim >= _SELF_ECHO_SIMILARITY:
                _turn_timing["diag_self_echo"] = True
                try:
                    import metrics as _prom
                    _prom.self_echo_suspected_total.add(1, {"stt_model": bot_config.stt_model})
                except Exception:
                    pass
                logger.warning(
                    "Possible bot self-echo (sim={:.2f}): user={!r} ~ bot={!r}",
                    sim, message.content[:80], _bot_text[:80],
                )
                break
        # Resolution order for speaker identity:
        #   1. SID stored by _SpeakerTracker from TranscriptionFrame.user_id
        #   2. Raw SID stored directly from on_data_received (_last_data_sender)
        #   3. First known participant (audio-mixed-STT path, no per-frame user_id)
        speaker_sid = _current_speaker_sid[0] or _last_data_sender[0]
        if speaker_sid and speaker_sid in _sid_to_identity:
            identity = _sid_to_identity[speaker_sid]
        else:
            sid = next(iter(_sid_to_identity), None)
            identity = _sid_to_identity.get(sid, sid) if sid else None
        if not identity:
            logger.warning("on_user_turn_stopped: no known participant identity, skipping utterance")
            return
        # Log full LLM context so we can see what the model receives across speakers.
        # Visible via: make logs SERVICE=agent-runner
        logger.debug(
            "LLM context snapshot — {} messages, last speaker={}\n{}",
            len(context.messages),
            identity,
            "\n".join(
                "  [{}] {}".format(m["role"], str(m.get("content") or "")[:120])
                for m in context.messages
            ),
        )

        ts = _iso_to_unix(message.timestamp)
        utt_id = _new_id()
        async with AsyncSessionLocal() as db:
            async with db.begin():
                db.add(
                    Utterance(
                        id=utt_id,
                        speaker_id=identity,
                        conv_id=runner_args.session_id,
                        reply_to=_last_bot_utt_id[0],
                        ts=ts,
                        text=message.content,
                    )
                )
                await _set_root_utterance_if_needed(db, runner_args.session_id, utt_id)
        _last_user_utt_id[0] = utt_id

    @context_aggregator.assistant().event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        # Remember recent bot speech so on_user_turn_stopped can flag self-echo.
        if message.content:
            _recent_bot_texts.append(message.content)
            if len(_recent_bot_texts) > _RECENT_BOT_TEXT_WINDOW:
                del _recent_bot_texts[0]
        ts = _iso_to_unix(message.timestamp)
        utt_id = _new_id()
        meta: dict = {}
        t = _turn_timing.copy()
        _turn_timing.clear()
        m = _metrics_data.copy()
        # Don't clear _metrics_data here — MetricsFrames for TTS may still be in flight.
        # _MetricsObserver already observed Prometheus; we just read for DB storage.
        timing: dict = {}
        # stt_ms from _turn_timing (real measurement: last audio → transcript committed)
        if "stt_ms" in t:
            timing["stt_ms"] = round(t["stt_ms"], 1)
        for key in ("llm_ttft_ms", "sentence_agg_ms", "tts_ttfb_ms"):
            if key in m:
                timing[key] = round(m[key], 1)
        # Post-STT E2E: stt_done → tts_first (transcript committed → first TTS audio chunk).
        if "stt_done" in t and "tts_first" in t:
            meta["latency_ms"] = round((t["tts_first"] - t["stt_done"]) * 1000, 1)
        # Total user-perceived latency: last audio → first TTS audio chunk.
        if "stt_ms" in timing and meta.get("latency_ms") is not None:
            meta["total_latency_ms"] = round(timing["stt_ms"] + meta["latency_ms"], 1)
        if timing:
            meta["timing"] = timing
        # Diagnostics surfaced to the DB so the simulation harness (and prod
        # debugging) can see spikes/echo/backlog without scraping logs/Prometheus.
        diag: dict = {}
        if t.get("diag_stt_spike"):
            diag["stt_spike"] = True
        if t.get("diag_self_echo"):
            diag["self_echo"] = True
        if "diag_queue_depth" in t:
            diag["queue_depth"] = t["diag_queue_depth"]
        if diag:
            meta["diag"] = diag
        # E2E latency + utterance counter (per-stage metrics observed by _MetricsObserver)
        try:
            import metrics as _prom
            _ep = str(getattr(bot_config, "stt_endpointing_ms", 200))
            if meta.get("latency_ms"):
                _prom.e2e_latency.record(meta["latency_ms"], {"stt_model": bot_config.stt_model, "endpointing_ms": _ep})
            if timing.get("stt_ms"):
                _prom.stt_latency.record(timing["stt_ms"], {"stt_model": bot_config.stt_model, "endpointing_ms": _ep})
            _prom.utterances_total.add(1, {"stt_model": bot_config.stt_model})
        except Exception:
            pass
        async with AsyncSessionLocal() as db:
            async with db.begin():
                db.add(
                    Utterance(
                        id=utt_id,
                        speaker_id=runner_args.bot_identity,
                        conv_id=runner_args.session_id,
                        reply_to=_last_user_utt_id[0],
                        ts=ts,
                        text=message.content,
                        meta=meta,
                    )
                )
                await _set_root_utterance_if_needed(db, runner_args.session_id, utt_id)
        _last_bot_utt_id[0] = utt_id

    # --- transport hooks ---

    @transport.event_handler("on_connected")
    async def on_connected(transport):
        try:
            await _publish_avatar(transport._client.room)
        except Exception as e:
            logger.warning(f"Avatar publish failed (non-fatal): {e}")

    @transport.event_handler("on_participant_connected")
    async def on_participant_connected(transport, participant_id: str):
        room = transport._client.room
        # remote_participants is keyed by identity, not SID — find by SID
        p = _find_participant_by_sid(room.remote_participants, participant_id)
        if not p:
            return
        identity = p.identity
        _sid_to_identity[participant_id] = identity
        _sid_to_name[participant_id] = p.name or identity.split("__")[0]
        logger.info(f"Participant connected: {identity} (sid={participant_id})")
        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    pg_insert(Speaker)
                    .values(id=identity, meta={
                        "role": "participant",
                        "display_name": _sid_to_name[participant_id],
                    })
                    .on_conflict_do_nothing(index_elements=["id"])
                )

    @transport.event_handler("on_active_speaker_changed")
    async def on_active_speaker_changed(transport, participant_id: str):
        """Update current speaker from LiveKit's active-speaker signal.

        This is the best-effort attribution path for the mixed-audio STT case,
        where TranscriptionFrame.user_id is not set by the STT service.
        Only updates the cell when the SID is already known (i.e. the participant
        has already been seen via on_participant_connected).
        """
        if participant_id and participant_id in _sid_to_identity:
            _current_speaker_sid[0] = participant_id
            logger.debug(
                "ActiveSpeaker: {} (sid={})",
                _sid_to_identity[participant_id],
                participant_id,
            )

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport, participant_id: str):
        # Flush this speaker's buffered audio to a WAV now that they've left —
        # BEFORE popping the identity map, so the track resolves to the right speaker.
        try:
            await audio_sink.flush(participant_id)
        except Exception as e:
            logger.warning(f"audio_track flush on disconnect failed for {participant_id}: {e}")
        identity = _sid_to_identity.pop(participant_id, participant_id)
        _sid_to_name.pop(participant_id, None)
        # Clear current speaker if this participant just left.
        if _current_speaker_sid[0] == participant_id:
            _current_speaker_sid[0] = None
        await multi_stt.remove_participant(participant_id)
        logger.info(f"Participant disconnected: {identity}")
        if not _sid_to_identity:
            logger.info("No participants remain — cancelling pipeline")
            await task.cancel()

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant_id):
        logger.info(f"First participant joined: {participant_id}")
        # Auto-start recording (composite mp4 + per-speaker WAV) when configured.
        # Best-effort: a recording failure must never block the greeting or the call.
        if bot_config.auto_record:
            try:
                from runner import start_recording_for_room
                status, payload = await start_recording_for_room(runner_args.room_name)
                logger.info(f"auto_record: start_recording_for_room → {status} {payload}")
            except Exception as e:
                logger.warning(f"auto_record failed for {runner_args.room_name}: {e}")
        await asyncio.sleep(1)
        await task.queue_frame(TTSSpeakFrame(bot_config.greeting))

    @transport.event_handler("on_data_received")
    async def on_data_received(transport, data, participant_id):
        logger.info(f"Received data from participant {participant_id}: {data}")
        # Guard against race with on_participant_connected: if this sender isn't
        # in our SID→identity map yet, look them up directly from the room now.
        if participant_id not in _sid_to_identity:
            room = transport._client.room
            p = _find_participant_by_sid(room.remote_participants, participant_id)
            if p:
                _sid_to_identity[participant_id] = p.identity
                _sid_to_name[participant_id] = p.name or p.identity.split("__")[0]
                logger.info(f"on_data_received: self-registered {p.identity} (sid={participant_id})")
                async with AsyncSessionLocal() as db:
                    async with db.begin():
                        await db.execute(
                            pg_insert(Speaker)
                            .values(id=p.identity, meta={
                                "role": "participant",
                                "display_name": _sid_to_name[participant_id],
                            })
                            .on_conflict_do_nothing(index_elements=["id"])
                        )
        _last_data_sender[0] = participant_id
        try:
            json_data = json.loads(data)
        except Exception:
            logger.warning(f"on_data_received: invalid JSON from {participant_id}")
            return
        timestamp = json_data.get("timestamp", 0)
        await task.queue_frames(
            [
                InterruptionFrame(),
                UserStartedSpeakingFrame(),
                TranscriptionFrame(
                    user_id=participant_id,
                    timestamp=timestamp,
                    text=json_data.get("message", ""),
                ),
                UserStoppedSpeakingFrame(),
            ],
        )

    runner = PipelineRunner()
    status = "error"
    try:
        await runner.run(task)
        status = "completed"
    except asyncio.CancelledError:
        # Graceful end: all participants left (on_participant_disconnected →
        # task.cancel()) or the runner's background task was cancelled. Record a
        # clean completion, then re-raise so the cancellation isn't swallowed.
        status = "completed"
        raise
    except Exception as e:
        logger.error(f"Bot pipeline error in room {runner_args.room_name}: {e}")
    finally:
        # Flush any audio still buffered (speakers who never fired a disconnect, or
        # the final drain). Shielded so end-of-call cancellation can't abort a write
        # mid-flight; then deregister the sink from the in-process registry.
        try:
            await asyncio.shield(asyncio.ensure_future(audio_sink.flush_all()))
        except Exception as e:
            logger.warning(f"audio_track flush_all on shutdown failed: {e}")
        finally:
            audio_tracks.unregister_sink(runner_args.room_name)
        await _finalize_conversation(runner_args.session_id, status)
        _isum = interruptions.summary()
        logger.info(
            f"Bot session {runner_args.session_id} ended ({status}) — "
            f"interruptions={_isum['interruptions']} "
            f"talkover_ms(max={_isum['talkover_ms_max']:.0f} avg={_isum['talkover_ms_avg']:.0f})"
        )


def _build_stt_for_multi_speaker(bot_config, openai_api_key: str, deepgram_api_key: str | None):
    """Build an STT instance for a single participant in per-participant mode.

    OpenAI Realtime STT: turn_detection=None (server VAD) so each instance drives
    its own turn boundaries and emits UserStarted/StoppedSpeakingFrames.

    Deepgram: endpointing driven by bot_config.stt_endpointing_ms (default 200ms,
    reducible to 100ms for lower latency). _FrameCollector wraps final transcripts
    in VAD frame sandwiches for the context aggregator.

    Local Whisper: each participant gets a (VADProcessor, WhisperSTTService)
    chain — see _build_whisper_chain. _FrameCollector wraps TranscriptionFrames
    in VAD sandwiches (needs_vad_wrap=True) the same way it does for Deepgram,
    and drops the chain's raw VAD frames so they don't double-fire the observer.
    """
    if bot_config.stt_model.startswith("gpt-"):
        return _OpenAIRealtimeSTT(
            api_key=openai_api_key,
            turn_detection=None,  # server VAD per-participant instance
            transcription_delay=bot_config.stt_delay,
            settings=_OpenAIRealtimeSTT.Settings(
                model=bot_config.stt_model,
                noise_reduction="near_field",
            ),
        )
    if bot_config.stt_model.startswith("whisper-"):
        return _build_whisper_chain(bot_config)
    if bot_config.stt_model.startswith("parakeet-"):
        return _build_parakeet_chain(bot_config)
    if not deepgram_api_key:
        raise ValueError("DEEPGRAM_API_KEY is required for Deepgram STT models")
    endpointing_ms = getattr(bot_config, "stt_endpointing_ms", 200)
    return DeepgramSTTService(
        api_key=deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model=bot_config.stt_model,
            endpointing=endpointing_ms,
        ),
    )


def build_audio_track_sink(room_name: str, session_id: str, sid_to_identity: dict) -> audio_tracks.AudioTrackSink:
    """Assemble the per-speaker WAV sink for a session.

    Wires audio_tracks.build_track_flush to this conversation: speakers resolve
    through the live _sid_to_identity map, and each finished track is written to
    storage and recorded as an available MediaFile(type="audio_track").
    """
    async def _persist(fields: dict) -> None:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                db.add(MediaFile(id=_new_id(), conv_id=session_id, **fields))

    on_flush = audio_tracks.build_track_flush(
        room_name=room_name,
        resolve_speaker=lambda sid: sid_to_identity.get(sid),
        persist=_persist,
    )
    return audio_tracks.AudioTrackSink(on_flush)


def _find_participant_by_sid(remote_participants: dict, sid: str):
    """Find a participant by SID in a dict keyed by identity.

    The LiveKit SDK keys room.remote_participants by *identity*, not SID.
    Pipecat callbacks pass the participant's SID, so a direct .get() always
    returns None — we must search by value.
    """
    return next((p for p in remote_participants.values() if p.sid == sid), None)


def _turn_detection_for_vad_mode(vad_mode: str):
    """Return turn_detection=False (local Silero VAD) — the only supported mode."""
    return False


def _new_id() -> str:
    return str(_uuid_mod.uuid4())


def _iso_to_unix(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return None


async def _set_root_utterance_if_needed(db, conv_id: str, utt_id: str) -> None:
    result = await db.execute(
        select(Conversation.root_utterance_id).where(Conversation.id == conv_id)
    )
    if result.scalar_one_or_none() is None:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(root_utterance_id=utt_id)
        )
