"""RTVI between the bot and the room's browsers (design plan iteration 10, F13).

Pipecat turns RTVI on by default; RTVI assumes one client, and LiveKit sends every message to
the whole room. So the room hears only what everyone may hear (the user's choice, 2026-10-05):
the bot is ready, and who is speaking. Not the LLM's tokens, transcripts, metrics or errors.

Typed chat is RTVI's send-text. Pipecat's own handling appends it to the LLM context without
the sender, so here it becomes the sender's turn instead (on_chat), stored and labelled like
speech. Anything that isn't RTVI (LiveKit's legacy chat packet, junk) is ignored quietly.

ponytail: overrides two of RTVIProcessor's private methods (1.12); tests/test_rtvi.py fails if a
Pipecat upgrade renames them.
"""
from loguru import logger
from pipecat.processors.frameworks.rtvi import models as RTVI
from pipecat.processors.frameworks.rtvi.observer import RTVIObserverParams
from pipecat.processors.frameworks.rtvi.processor import RTVIProcessor

OBSERVER_PARAMS = RTVIObserverParams(
    bot_output_enabled=False, bot_llm_enabled=False, bot_tts_enabled=False, user_llm_enabled=False,
    user_mute_enabled=False, user_transcription_enabled=False, metrics_enabled=False,
    bot_speaking_enabled=True, user_speaking_enabled=True,
)


class RoomRTVIProcessor(RTVIProcessor):
    def __init__(self, on_chat, **kwargs):
        super().__init__(**kwargs)
        self._on_chat = on_chat  # async (participant_id, text)

    async def _handle_transport_message(self, frame):
        message = frame.message
        if message.get("label") != RTVI.MESSAGE_LABEL:
            return  # not RTVI: LiveKit's own chat packet, or junk
        if message.get("type") == "send-text":
            data = message.get("data")
            text = data.get("content") if isinstance(data, dict) else None
            if isinstance(text, str) and text.strip():
                await self._on_chat(getattr(frame, "participant_id", None), text.strip())
            return
        await super()._handle_transport_message(frame)

    async def _send_error_frame(self, frame):
        logger.debug(f"RTVI: error kept from the room's browsers: {frame.error}")
