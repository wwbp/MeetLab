"""Typed chat from the browser (chat.py, diagnosis F13).

LiveKit's chat sends a legacy data packet {id, timestamp: <ms>, message}. The bot took
any packet as chat: valid JSON that isn't an object crashed the handler, an empty one
became an empty turn, and the numeric timestamp failed the ISO parse, so stored chat
turns had no time.
"""
import json
import unittest
from datetime import datetime, timezone

from chat import chat_message

MS = 1_790_000_000_000  # 2026-09-21T13:33:20Z


def packet(**fields) -> bytes:
    return json.dumps({"id": "m1", "timestamp": MS, "message": "hello", **fields}).encode()


class ChatMessageTests(unittest.TestCase):
    def test_a_chat_packet_gives_its_text_and_an_iso_time(self):
        self.assertEqual(chat_message(packet(message="  hello  ")),
                         ("hello", datetime.fromtimestamp(MS / 1000, timezone.utc).isoformat()))

    def test_anything_else_is_ignored(self):
        for data in (b"not json", b"\xff\xfe", b"5", b"[]", packet(message=""), packet(message="   "),
                     packet(message=7), json.dumps({"timestamp": MS}).encode()):
            self.assertIsNone(chat_message(data), data)

    def test_a_missing_or_odd_timestamp_still_gives_the_text(self):
        text, when = chat_message(packet(timestamp="soon"))
        self.assertEqual(text, "hello")
        self.assertTrue(datetime.fromisoformat(when).tzinfo)


if __name__ == "__main__":
    unittest.main()
