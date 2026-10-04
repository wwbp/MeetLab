"""Closing the session, and identifying who was in it.

Two study-support pieces, both pure so they test without a room or a clock.

`closing_due` decides when the bot should announce that time is up. The session
limit already exists as bot_config.session_limit_minutes and is already loaded
into the bot's config — it was simply never read there. Until now the countdown
lived only in the browser (lib/SessionTimer.tsx), so the bot had no idea a study
session had a length.

`prolific_id` is the participant identifier a paid study is matched and paid on.
It arrives as a URL parameter from Prolific, which goes missing often enough
(bookmarks, refreshes, param-stripping extensions) that the field has to be
editable — so it also has to be validated, because a mistyped 24-character hex
string is an unpayable session discovered weeks later.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from study_support import closing_due, prolific_id, speaker_meta


class TestClosingDue(unittest.TestCase):
    def test_not_due_before_the_limit(self):
        self.assertFalse(closing_due(joined_at=0.0, now=299.0, limit_minutes=5))

    def test_due_once_the_limit_passes(self):
        self.assertTrue(closing_due(joined_at=0.0, now=300.0, limit_minutes=5))

    def test_zero_means_unlimited(self):
        """0 is the existing convention for no cap, so a study without a limit
        must never hear a closing message."""
        self.assertFalse(closing_due(joined_at=0.0, now=99_999.0, limit_minutes=0))

    def test_a_negative_limit_is_treated_as_unlimited(self):
        """Config is operator-editable; a nonsense value should not make the bot
        announce the end one second into every session."""
        self.assertFalse(closing_due(joined_at=0.0, now=500.0, limit_minutes=-5))

    def test_nobody_joined_yet_is_never_due(self):
        """joined_at is None until the first participant arrives. Without this
        the timer starts at process boot and fires into an empty room."""
        self.assertFalse(closing_due(joined_at=None, now=10_000.0, limit_minutes=5))

    def test_it_is_measured_from_the_join_not_from_zero(self):
        self.assertFalse(closing_due(joined_at=1_000.0, now=1_200.0, limit_minutes=5))
        self.assertTrue(closing_due(joined_at=1_000.0, now=1_300.0, limit_minutes=5))


class TestProlificId(unittest.TestCase):
    def test_a_valid_id_is_accepted(self):
        self.assertEqual(prolific_id("5f2a91b3c4d5e6f708192a3b"), "5f2a91b3c4d5e6f708192a3b")

    def test_surrounding_whitespace_is_forgiven(self):
        """People paste from Prolific's dashboard and pick up spaces."""
        self.assertEqual(prolific_id("  5f2a91b3c4d5e6f708192a3b \n"), "5f2a91b3c4d5e6f708192a3b")

    def test_case_is_normalised(self):
        """Prolific renders lowercase hex; a pasted uppercase copy is the same
        person and must not become a second row."""
        self.assertEqual(prolific_id("5F2A91B3C4D5E6F708192A3B"), "5f2a91b3c4d5e6f708192a3b")

    def test_wrong_length_is_rejected(self):
        self.assertIsNone(prolific_id("5f2a91b3"))
        self.assertIsNone(prolific_id("5f2a91b3c4d5e6f708192a3b00"))

    def test_non_hex_is_rejected(self):
        """Catching a typo while the participant can still fix it is the whole
        point; catching it at payment time is not."""
        self.assertIsNone(prolific_id("zzzz91b3c4d5e6f708192a3b"))

    def test_empty_and_missing_are_rejected(self):
        self.assertIsNone(prolific_id(""))
        self.assertIsNone(prolific_id(None))
        self.assertIsNone(prolific_id("   "))


if __name__ == "__main__":
    unittest.main()


class TestSpeakerMeta(unittest.TestCase):
    """What a person's speaker record holds, built one way for everyone: whether they joined
    before the bot or after (people already in the room were never registered at join, so
    their Prolific ID was never stored, 2026-10-04)."""

    def test_name_and_prolific_id(self):
        self.assertEqual(speaker_meta("Ana", "5f2a91b3c4d5e6f708192a3b"),
                         {"role": "participant", "display_name": "Ana", "prolific_id": "5f2a91b3c4d5e6f708192a3b"})

    def test_an_invalid_id_is_kept_so_the_session_can_still_be_matched_by_hand(self):
        self.assertEqual(speaker_meta("Ana", "not-an-id")["prolific_id_invalid"], "not-an-id")

    def test_no_metadata_no_id(self):
        self.assertEqual(speaker_meta("Ana", ""), {"role": "participant", "display_name": "Ana"})
        self.assertEqual(speaker_meta("Ana", None), {"role": "participant", "display_name": "Ana"})
