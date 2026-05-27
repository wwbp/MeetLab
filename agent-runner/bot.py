import asyncio
import json
import os
import sys
import time
import uuid as _uuid_mod
from datetime import datetime, timezone

from loguru import logger
from PIL import Image

from livekit import rtc

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    InterruptionFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
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
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService, OpenAIRealtimeSTTSettings
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import load_config, require
from db.config_loader import load_bot_config
from db.engine import AsyncSessionLocal
from db.models import Conversation, Speaker, Utterance
from runner_types import LiveKitRunnerArguments

_STT_DELAY_VALUES = frozenset({"minimal", "low", "medium", "high", "xhigh"})


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


def _build_stt(bot_config, openai_api_key: str, deepgram_api_key: str | None):
    """Instantiate the STT service based on stt_model prefix.

    Models starting with 'gpt-' use OpenAI Realtime STT; everything else
    (nova-*, enhanced-*, etc.) uses Deepgram.
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


async def bot(runner_args: LiveKitRunnerArguments):
    logger.info(f"Bot starting - joining room: {runner_args.room_name}")

    # sid → identity lookup: populated at on_participant_connected
    _sid_to_identity: dict[str, str] = {}
    # last utterance ids for reply_to chaining
    _last_user_utt_id: list[str | None] = [None]
    _last_bot_utt_id: list[str | None] = [None]
    # latency from UserBotLatencyObserver, written into next assistant utterance
    _pending_latency_ms: list[float | None] = [None]

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

    bot_config = await load_bot_config(runner_args.room_name)
    logger.info(
        f"Loaded bot config for room '{runner_args.room_name}': "
        f"model={bot_config.llm_model} voice={bot_config.tts_voice} vad={bot_config.vad_stop_secs}s"
    )

    # "server": OpenAI server-side VAD (turn_detection=None, no local VAD needed)
    # "local": Pipecat SileroVAD commits audio (turn_detection=False)
    server_vad = bot_config.stt_vad_mode == "server"
    stt = _build_stt(bot_config, openai_api_key, env_config.deepgram_api_key)
    logger.info(
        f"STT: model={bot_config.stt_model} vad_mode={bot_config.stt_vad_mode} delay={bot_config.stt_delay}"
    )
    llm = OpenAILLMService(api_key=openai_api_key, model=bot_config.llm_model)
    if bot_config.tts_provider == "openai":
        from pipecat.services.openai.tts import OpenAITTSService
        tts = OpenAITTSService(api_key=openai_api_key, voice=bot_config.tts_voice or "alloy")
    else:
        tts = ElevenLabsTTSService(
            api_key=elevenlabs_api_key,
            settings=ElevenLabsTTSService.Settings(voice=bot_config.tts_voice),
        )
    logger.info(f"TTS: provider={bot_config.tts_provider} voice={bot_config.tts_voice}")

    context = LLMContext([{"role": "system", "content": bot_config.system_prompt}])
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=(
                SileroVADAnalyzer(params=VADParams(stop_secs=bot_config.vad_stop_secs))
                if not server_vad
                else None
            ),
        ),
    )

    latency_observer = UserBotLatencyObserver()

    # Per-turn timing dict — populated by frame interceptors and turn hooks.
    # Keys set during a turn: "stt_done", "llm_first", "tts_first".
    # Cleared in on_assistant_turn_stopped after writing to Utterance.meta.
    _turn_timing: dict[str, float] = {}

    class _LLMFirstTimer(FrameProcessor):
        """Records when the first LLM text token is emitted (true TTFT)."""

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMTextFrame):
                _turn_timing.setdefault("llm_first", time.monotonic())
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
            stt,
            context_aggregator.user(),
            llm,
            _LLMFirstTimer(),
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
        observers=[latency_observer],
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
        # Anchor for LLM TTFT: moment the transcript is committed to the LLM context.
        _turn_timing["stt_done"] = time.monotonic()
        _turn_timing.pop("llm_first", None)
        _turn_timing.pop("tts_first", None)
        if not message.content:
            return
        sid = next(iter(_sid_to_identity), None)
        identity = _sid_to_identity.get(sid, sid) if sid else None
        if not identity:
            logger.warning("on_user_turn_stopped: no known participant identity, skipping utterance")
            return
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
        ts = _iso_to_unix(message.timestamp)
        utt_id = _new_id()
        meta: dict = {}
        if _pending_latency_ms[0] is not None:
            meta["latency_ms"] = _pending_latency_ms[0]
            _pending_latency_ms[0] = None
        t = _turn_timing.copy()
        _turn_timing.clear()
        timing: dict = {}
        if "stt_done" in t and "llm_first" in t:
            timing["llm_ttft_ms"] = round((t["llm_first"] - t["stt_done"]) * 1000, 1)
        if "llm_first" in t and "tts_first" in t:
            timing["tts_first_ms"] = round((t["tts_first"] - t["llm_first"]) * 1000, 1)
        if timing:
            meta["timing"] = timing
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

    @latency_observer.event_handler("on_latency_measured")
    async def on_latency_measured(observer, latency_secs: float):
        _pending_latency_ms[0] = round(latency_secs * 1000, 1)

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
        logger.info(f"Participant connected: {identity} (sid={participant_id})")
        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    pg_insert(Speaker)
                    .values(id=identity, meta={"role": "participant"})
                    .on_conflict_do_nothing(index_elements=["id"])
                )

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport, participant_id: str):
        identity = _sid_to_identity.pop(participant_id, participant_id)
        logger.info(f"Participant disconnected: {identity}")
        if not _sid_to_identity:
            logger.info("No participants remain — cancelling pipeline")
            await task.cancel()

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant_id):
        logger.info(f"First participant joined: {participant_id}")
        await asyncio.sleep(1)
        await task.queue_frame(TTSSpeakFrame(bot_config.greeting))

    @transport.event_handler("on_data_received")
    async def on_data_received(transport, data, participant_id):
        logger.info(f"Received data from participant {participant_id}: {data}")
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
    except Exception as e:
        logger.error(f"Bot pipeline error in room {runner_args.room_name}: {e}")
    finally:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == runner_args.session_id)
                    .values(ended_at=datetime.now(timezone.utc), status=status)
                )

    logger.info(f"Bot session {runner_args.session_id} ended ({status})")


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
