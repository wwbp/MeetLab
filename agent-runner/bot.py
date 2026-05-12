import asyncio
import json
import os
import sys
from loguru import logger
from PIL import Image

from livekit import rtc

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    InterruptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

from config import load_config, require
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

    transport = LiveKitTransport(
        url=runner_args.url,
        token=runner_args.token,
        room_name=runner_args.room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            video_in_enabled=False,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
        ),
    )

    config = load_config()
    openai_api_key = require(config.openai_api_key, "OPENAI_API_KEY")
    stt = OpenAISTTService(api_key=openai_api_key)
    llm = OpenAILLMService(api_key=openai_api_key)
    tts = OpenAITTSService(api_key=openai_api_key)

    messages = [
        {
            "role": "system",
            "content": "You are a helpful LLM in a WebRTC call. "
            "Your goal is to demonstrate your capabilities in a succinct way. "
            "Your output will be converted to audio so don't include special characters in your answers. "
            "Respond to what the user said in a creative and helpful way.",
        },
    ]

    context = LLMContext(messages)
    context_aggregator = LLMContextAggregatorPair(context)

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
    )

    @transport.event_handler("on_connected")
    async def on_connected(transport):
        await _publish_avatar(transport._client.room)

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant_id):
        logger.info(f"First participant joined: {participant_id}")
        await asyncio.sleep(1)
        await task.queue_frame(
            TTSSpeakFrame(
                "Hello there! How are you doing today? Would you like to talk about the weather?"
            )
        )

    @transport.event_handler("on_data_received")
    async def on_data_received(transport, data, participant_id):
        logger.info(f"Received data from participant {participant_id}: {data}")
        json_data = json.loads(data)
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
    await runner.run(task)

    logger.info(f"Bot session ended for room: {runner_args.room_name}")
