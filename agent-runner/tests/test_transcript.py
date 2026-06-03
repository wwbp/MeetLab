"""Unit tests for transcript.py formatting logic.

Avoids any DB or filesystem I/O — tests only the pure formatter.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from transcript import _display_name, _fmt_ts, format_transcript


class TestDisplayName(unittest.TestCase):
    def test_uses_display_name_from_meta(self):
        self.assertEqual(_display_name("John__abc1", {"display_name": "John"}), "John")

    def test_strips_postfix_when_no_meta_key(self):
        self.assertEqual(_display_name("Alice__xyz9", {}), "Alice")

    def test_no_postfix_identity_unchanged(self):
        self.assertEqual(_display_name("bot_runner", {}), "bot_runner")

    def test_empty_meta_falls_back(self):
        self.assertEqual(_display_name("Bob__aa11", {"role": "participant"}), "Bob")


class _FakeConv:
    def __init__(self, room="test-room", started_at=None, ended_at=None):
        self.room_name = room
        self.started_at = started_at or datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        self.ended_at = ended_at


class _FakeSpeaker:
    def __init__(self, meta=None):
        self.meta = meta or {}


class _FakeUtt:
    def __init__(self, speaker_id, text, ts=None, speaker_meta=None):
        self.speaker_id = speaker_id
        self.text = text
        self.ts = ts
        self.speaker = _FakeSpeaker(speaker_meta)


class TestFmtTs(unittest.TestCase):
    def setUp(self):
        self.session_start = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)

    def test_formats_relative_seconds(self):
        utt_time = datetime(2026, 6, 1, 10, 1, 5, tzinfo=timezone.utc).timestamp()
        result = _fmt_ts(utt_time, self.session_start)
        self.assertEqual(result, "00:01:05")

    def test_handles_none(self):
        self.assertEqual(_fmt_ts(None, self.session_start), "??:??:??")

    def test_hours(self):
        utt_time = datetime(2026, 6, 1, 11, 30, 45, tzinfo=timezone.utc).timestamp()
        result = _fmt_ts(utt_time, self.session_start)
        self.assertEqual(result, "01:30:45")


class TestFormatTranscript(unittest.TestCase):
    def test_contains_room_name(self):
        conv = _FakeConv(room="my-room")
        md = format_transcript(conv, [])
        self.assertIn("my-room", md)

    def test_contains_date(self):
        conv = _FakeConv()
        md = format_transcript(conv, [])
        self.assertIn("2026-06-01", md)

    def test_empty_session_note(self):
        conv = _FakeConv()
        md = format_transcript(conv, [])
        self.assertIn("No utterances", md)

    def test_utterances_present(self):
        conv = _FakeConv()
        utts = [
            _FakeUtt("Alice__abc1", "hello there", ts=datetime(2026, 6, 1, 10, 0, 5, tzinfo=timezone.utc).timestamp()),
            _FakeUtt("bot_runner", "hi!", ts=datetime(2026, 6, 1, 10, 0, 10, tzinfo=timezone.utc).timestamp(), speaker_meta={"role": "bot"}),
        ]
        md = format_transcript(conv, utts)
        self.assertIn("Alice", md)
        self.assertIn("hello there", md)
        self.assertIn("hi!", md)
        self.assertIn("*(bot)*", md)

    def test_uses_clean_display_name(self):
        conv = _FakeConv()
        utts = [_FakeUtt("John__xyz9", "question", speaker_meta={"display_name": "John"})]
        md = format_transcript(conv, utts)
        self.assertIn("John", md)
        self.assertNotIn("John__xyz9", md)

    def test_duration_shown_when_ended(self):
        start = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 6, 1, 10, 5, 30, tzinfo=timezone.utc)
        conv = _FakeConv(started_at=start, ended_at=end)
        md = format_transcript(conv, [])
        self.assertIn("5m 30s", md)

    def test_timestamps_relative_to_session_start(self):
        conv = _FakeConv()
        ts = datetime(2026, 6, 1, 10, 0, 7, tzinfo=timezone.utc).timestamp()
        utts = [_FakeUtt("Alice__a", "hi", ts=ts)]
        md = format_transcript(conv, utts)
        self.assertIn("00:00:07", md)

    def test_turn_count_in_header(self):
        conv = _FakeConv()
        utts = [_FakeUtt("A__x", "a"), _FakeUtt("B__y", "b")]
        md = format_transcript(conv, utts)
        self.assertIn("2", md)


if __name__ == "__main__":
    unittest.main()
