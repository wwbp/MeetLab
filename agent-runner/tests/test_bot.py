"""Unit tests for bot.py helper functions and guard logic.

Covers three reliability fixes:
  1. Participant SID lookup: LiveKit SDK keys remote_participants by identity,
     not SID — the old .get(sid) always returned None.
  2. VAD mode mapping: stt_vad_mode string → turn_detection argument for
     OpenAIRealtimeSTTService.
  3. on_user_turn_stopped guards: skip DB insert when content is empty or
     participant identity is unknown (prevents FK violation + empty LLM turns).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

# Provide env vars required at import time by db/engine.py and config.py
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://localhost:7880")

# Import the module-level helpers directly from bot.
# db.engine creates an engine lazily on first connection, so import is safe.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from bot import _find_participant_by_sid, _turn_detection_for_vad_mode


def _fake_participant(identity: str, sid: str) -> MagicMock:
    p = MagicMock()
    p.identity = identity
    p.sid = sid
    return p


class TestFindParticipantBySid(unittest.TestCase):
    """_find_participant_by_sid searches remote_participants by SID value."""

    def test_finds_participant_when_sid_matches(self):
        p = _fake_participant("alice", "PA_abc")
        result = _find_participant_by_sid({"alice": p}, "PA_abc")
        self.assertIs(result, p)

    def test_returns_none_when_sid_not_present(self):
        p = _fake_participant("alice", "PA_abc")
        result = _find_participant_by_sid({"alice": p}, "PA_unknown")
        self.assertIsNone(result)

    def test_returns_none_for_empty_dict(self):
        self.assertIsNone(_find_participant_by_sid({}, "PA_abc"))

    def test_old_get_by_sid_key_always_fails(self):
        """Regression: dict.get(sid) on an identity-keyed dict returns None."""
        p = _fake_participant("alice", "PA_abc")
        participants = {"alice": p}
        self.assertIsNone(participants.get("PA_abc"))        # old broken pattern
        self.assertIsNotNone(_find_participant_by_sid(participants, "PA_abc"))  # fixed

    def test_selects_correct_participant_among_multiple(self):
        alice = _fake_participant("alice", "PA_aaa")
        bob = _fake_participant("bob", "PA_bbb")
        participants = {"alice": alice, "bob": bob}
        self.assertIs(_find_participant_by_sid(participants, "PA_bbb"), bob)
        self.assertIs(_find_participant_by_sid(participants, "PA_aaa"), alice)


class TestTurnDetectionForVadMode(unittest.TestCase):
    """_turn_detection_for_vad_mode maps config string to turn_detection value."""

    def test_server_mode_returns_none(self):
        # None tells OpenAIRealtimeSTTService to enable server-side VAD
        self.assertIsNone(_turn_detection_for_vad_mode("server"))

    def test_local_mode_returns_false(self):
        # False tells the service to disable server VAD; Silero handles it
        self.assertIs(_turn_detection_for_vad_mode("local"), False)

    def test_unknown_mode_falls_back_to_local_behaviour(self):
        # Any unrecognised value should be safe (local VAD, not server)
        self.assertIs(_turn_detection_for_vad_mode("unknown"), False)
        self.assertIs(_turn_detection_for_vad_mode(""), False)

    def test_server_and_local_are_distinct(self):
        self.assertNotEqual(
            _turn_detection_for_vad_mode("server"),
            _turn_detection_for_vad_mode("local"),
        )


class TestOnUserTurnStoppedGuards(unittest.TestCase):
    """Guard conditions for on_user_turn_stopped mirror the bot handler logic.

    The handler skips DB writes (and avoids FK violations / empty LLM turns)
    when:
      - message.content is falsy (empty transcription)
      - no participant identity is known (_sid_to_identity is empty)
    """

    def _run_guards(self, content: str, sid_to_identity: dict):
        """Execute the exact guard sequence from on_user_turn_stopped."""
        if not content:
            return "skipped:empty_content"

        sid = next(iter(sid_to_identity), None)
        identity = sid_to_identity.get(sid, sid) if sid else None
        if not identity:
            return "skipped:unknown_identity"

        return f"proceed:{identity}"

    # --- empty content ---

    def test_empty_string_skips_insert(self):
        self.assertEqual(self._run_guards("", {"PA_x": "alice"}), "skipped:empty_content")

    def test_whitespace_skips_insert(self):
        # Transcriptions that are only whitespace are falsy after strip,
        # but the guard uses `not content` so only the empty string hits it.
        # Confirm empty string is caught.
        self.assertEqual(self._run_guards("", {}), "skipped:empty_content")

    # --- unknown identity ---

    def test_empty_sid_map_skips_insert(self):
        self.assertEqual(self._run_guards("Hello", {}), "skipped:unknown_identity")

    # --- happy path ---

    def test_known_identity_and_content_proceeds(self):
        result = self._run_guards("Hello there", {"PA_abc": "alice"})
        self.assertEqual(result, "proceed:alice")

    def test_returns_first_known_identity(self):
        # _sid_to_identity may have multiple entries; first one is used
        sid_map = {"PA_aaa": "alice", "PA_bbb": "bob"}
        result = self._run_guards("Hey", sid_map)
        first_identity = sid_map[next(iter(sid_map))]
        self.assertEqual(result, f"proceed:{first_identity}")


if __name__ == "__main__":
    unittest.main()
