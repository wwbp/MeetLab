import asyncio
import json
import os
import sys
import uuid as _uuid_mod
from datetime import datetime, timezone

from loguru import logger
from PIL import Image

from livekit import rtc

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    InterruptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
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
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import load_config, require
from db.config_loader import load_bot_config
from db.engine import AsyncSessionLocal
from db.models import Conversation, Speaker, Utterance
from runner_types import LiveKitRunnerArguments

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

    stt = OpenAISTTService(api_key=openai_api_key)
    llm = OpenAILLMService(api_key=openai_api_key, model=bot_config.llm_model)
    tts = ElevenLabsTTSService(
        api_key=elevenlabs_api_key,
        settings=ElevenLabsTTSService.Settings(voice=bot_config.tts_voice),
    )

    context = LLMContext([{"role": "system", "content": bot_config.system_prompt}])
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(stop_secs=bot_config.vad_stop_secs)
            ),
        ),
    )

    latency_observer = UserBotLatencyObserver()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
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
    )

    # --- transcript hooks ---

    @context_aggregator.user().event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        sid = next(iter(_sid_to_identity), None)
        identity = _sid_to_identity.get(sid, sid) if sid else "unknown"
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
        p = room.remote_participants.get(participant_id)
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
