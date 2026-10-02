"""Resolving which participant a finished turn belongs to.

The 2026-08-19 production load test found utterances being dropped at concurrency:
155 at three concurrent rooms, 215 at five, 335 at eight, against zero at one.
Each dropped turn is missing from the transcript and from per-turn timing — the
bot still answers (the reply is driven by the pipeline, not by this path), so
nothing looks wrong during the meeting and the loss is silent.

Cause: identity was learned *once*, in the participant-connected callback, via

    p = _find_participant(room.remote_participants, participant_id)
    if not p:
        return                      # identity never recorded, never retried

Pipecat (then 1.4) passed the SID, and the SDK's roster is keyed by identity and populated
asynchronously — so under load the lookup frequently ran before the roster caught
up. In that window the callback returned and nothing ever revisited it. Production
logs bear it out: 8 and 13 bots started against 1 and 2 identities recorded.

The fix is to stop treating it as a one-shot. `resolve_speaker_identity` consults
the live roster whenever the cache misses, so a lost connect event costs nothing
beyond one dictionary scan, and the result is cached for later turns.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bot import resolve_speaker_identity


class FakeParticipant:
    def __init__(self, sid, identity, name=""):
        self.sid = sid
        self.identity = identity
        self.name = name


def roster(*participants):
    """Mimic room.remote_participants: keyed by identity, not SID."""
    return {p.identity: p for p in participants}


class TestCacheHit(unittest.TestCase):
    def test_a_known_sid_resolves_from_cache(self):
        cache = {"PA_1": "alice__ab12"}
        got, learned = resolve_speaker_identity("PA_1", cache, roster())
        self.assertEqual(got, "alice__ab12")
        self.assertEqual(learned, {}, "nothing new to record")


class TestTheRaceThisFixes(unittest.TestCase):
    """A connect event that lost the race must not cost the transcript. Pipecat >= 1.8
    passes the participant's identity, which is also the roster's key."""

    def test_a_cache_miss_falls_back_to_the_live_roster(self):
        cache = {}
        p = FakeParticipant("PA_1", "alice__ab12", "Alice")
        got, learned = resolve_speaker_identity("alice__ab12", cache, roster(p))
        self.assertEqual(got, "alice__ab12")

    def test_what_it_learns_is_reported_for_caching(self):
        """Later turns in the same session must not repeat the scan."""
        p = FakeParticipant("PA_1", "alice__ab12", "Alice")
        _, learned = resolve_speaker_identity("alice__ab12", {}, roster(p))
        self.assertEqual(learned, {"alice__ab12": ("alice__ab12", "Alice")})

    def test_a_participant_with_no_name_falls_back_to_the_identity_stem(self):
        """Display names strip the __randomPostfix added for uniqueness."""
        p = FakeParticipant("PA_1", "alice__ab12", "")
        _, learned = resolve_speaker_identity("alice__ab12", {}, roster(p))
        self.assertEqual(learned, {"alice__ab12": ("alice__ab12", "alice")})


class TestFallbacks(unittest.TestCase):
    def test_an_unknown_sid_falls_back_to_a_known_participant(self):
        """Pre-existing behaviour: better to attribute to the only speaker present
        than to discard the turn. Kept so single-participant rooms are unaffected."""
        cache = {"PA_1": "alice__ab12"}
        got, _ = resolve_speaker_identity("PA_UNKNOWN", cache, roster())
        self.assertEqual(got, "alice__ab12")

    def test_no_sid_at_all_still_uses_a_known_participant(self):
        cache = {"PA_1": "alice__ab12"}
        got, _ = resolve_speaker_identity(None, cache, roster())
        self.assertEqual(got, "alice__ab12")

    def test_an_empty_room_with_an_empty_cache_resolves_to_nothing(self):
        """The only case where dropping the utterance is correct."""
        got, learned = resolve_speaker_identity("PA_1", {}, roster())
        self.assertIsNone(got)
        self.assertEqual(learned, {})

    def test_the_roster_beats_an_unrelated_cached_participant(self):
        """With two speakers, guessing the wrong one corrupts the transcript —
        so a roster hit must win over the first-known-participant fallback."""
        cache = {"alice__ab12": "alice__ab12"}
        p = FakeParticipant("PA_2", "bob__cd34", "Bob")
        got, _ = resolve_speaker_identity("bob__cd34", cache, roster(p))
        self.assertEqual(got, "bob__cd34")


if __name__ == "__main__":
    unittest.main()
