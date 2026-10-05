"""RTVI between the bot and the room's browsers (rtvi.py; design plan iteration 10, F13).

Pipecat turns RTVI on by default and, until 2026-10-05, our bot broadcast all of it to every
participant: the LLM's tokens before they were spoken, everyone's transcripts, metrics and
pipeline errors. The user's choice (option A): bot ready and who is speaking, nothing else.
Chat is RTVI's send-text, kept as the typing person's turn."""
import asyncio
import os
import sys
import unittest
from unittest import mock
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipecat.frames.frames import ErrorFrame  # noqa: E402
from pipecat.transports.livekit.transport import LiveKitInputTransportMessageFrame  # noqa: E402

from rtvi import OBSERVER_PARAMS, RoomRTVIProcessor  # noqa: E402


def packet(message, sender="alice"):
    return LiveKitInputTransportMessageFrame(message=message, participant_id=sender)


def send_text(content):
    return {"label": "rtvi-ai", "type": "send-text", "id": "m1", "data": {"content": content}}


class TestChat(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.on_chat = AsyncMock()
        self.rtvi = RoomRTVIProcessor(on_chat=self.on_chat)
        self.rtvi._message_queue = asyncio.Queue()  # made at StartFrame in a running pipeline

    async def asyncTearDown(self):
        await self.rtvi.cleanup()

    async def test_typed_chat_is_the_senders_turn(self):
        await self.rtvi._handle_transport_message(packet(send_text("  hello bot ")))
        self.on_chat.assert_awaited_once_with("alice", "hello bot")
        self.assertTrue(self.rtvi._message_queue.empty())  # not also RTVI's own (unattributed) path

    async def test_anything_else_is_ignored_quietly(self):
        # LiveKit's legacy chat packet, junk, an empty or malformed send-text: no turn, no warning.
        with mock.patch("rtvi.logger") as log, mock.patch("pipecat.processors.frameworks.rtvi.processor.logger") as pc_log:
            for m in ({"id": "x", "timestamp": 1, "message": "hi"}, {"label": "rtvi-ai", "type": "send-text"},
                      send_text("   "), send_text(5), {"label": "rtvi-ai", "type": "send-text", "data": "hi"}):
                await self.rtvi._handle_transport_message(packet(m))
        self.on_chat.assert_not_awaited()
        log.warning.assert_not_called()
        pc_log.warning.assert_not_called()

    async def test_other_rtvi_messages_reach_pipecat(self):
        await self.rtvi._handle_transport_message(packet(
            {"label": "rtvi-ai", "type": "client-ready", "id": "c1", "data": {"version": "2.1.0", "about": {"library": "meetlab"}}}))
        self.assertEqual(self.rtvi._message_queue.qsize(), 1)  # client-ready: Pipecat answers bot-ready


class TestPrivacy(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_errors_stay_in_our_logs(self):
        rtvi = RoomRTVIProcessor(on_chat=AsyncMock())
        rtvi.push_transport_message = AsyncMock()
        await rtvi._send_error_frame(ErrorFrame(error="LLM 400: context too long"))
        rtvi.push_transport_message.assert_not_awaited()
        await rtvi.cleanup()

    def test_the_room_hears_only_bot_ready_and_who_is_speaking(self):
        on = {k for k, v in vars(OBSERVER_PARAMS).items() if k.endswith("_enabled") and v}
        self.assertEqual(on, {"bot_speaking_enabled", "user_speaking_enabled"})


if __name__ == "__main__":
    unittest.main()
