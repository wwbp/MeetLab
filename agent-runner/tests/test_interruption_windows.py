"""The two windows that decide whether the bot can be interrupted at all.

Prod symptom (2026-08-19): interruption "feels slow or non-existent — bots keep
speaking". The enforcement path added in RC3 works when it fires: 24h of prod
logs show one onset, one matching "yielding to", and zero failures. It just
rarely gets the chance to fire, for two reasons this module pins.

**Gap 1 — the response window opened too late.** It was armed by
BotStartedSpeakingFrame, i.e. the first TTS *audio*. A user who starts talking
during LLM generation or TTS synthesis therefore hit `_response_open == False`,
got no interruption, and was then talked over for the whole response.

**Gap 2 — onset is edge-triggered.** VADUserStartedSpeakingFrame fires once, at
the moment speech starts. In the situation above the user is already mid-utterance
by the time the bot begins, so no second onset ever arrives and nothing can
rescue the turn. The bot needs to check, at the moment it starts speaking,
whether anyone is *currently* talking.

The staleness guard exists because gap 2's fix introduces a failure mode worse
than the bug: if a VAD stop is ever lost, a speaker would be considered "still
talking" forever and the bot would yield on every response — silence, permanently.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from interruption import InterruptionTracker


def tracker():
    return InterruptionTracker(record=False)


class TestResponseWindowOpensAtGeneration(unittest.TestCase):
    """The window must open when the bot commits to answering, not when audio lands."""

    def test_onset_during_llm_generation_yields(self):
        t = tracker()
        t.llm_started(0.0)
        self.assertTrue(
            t.user_onset(0.3, "sidA"),
            "user spoke while the LLM was generating — the bot should yield "
            "before it ever opens its mouth",
        )

    def test_onset_with_no_response_in_flight_is_ordinary_turn_taking(self):
        t = tracker()
        self.assertFalse(t.user_onset(0.3, "sidA"))
        self.assertEqual(t.interruptions, 0)

    def test_audio_starting_after_generation_does_not_reopen_the_window(self):
        """bot_started must not clear _interrupted_this_response mid-response."""
        t = tracker()
        t.llm_started(0.0)
        self.assertTrue(t.user_onset(0.3, "sidA"))
        t.bot_started(0.4)
        self.assertFalse(
            t.user_onset(0.5, "sidA"),
            "already cancelling this response; re-interrupting only churns the pipeline",
        )
        self.assertEqual(t.interruptions, 1)

    def test_window_closes_on_assistant_turn_stopped(self):
        t = tracker()
        t.llm_started(0.0)
        t.bot_started(0.2)
        t.bot_stopped(1.0)
        t.assistant_turn_stopped(1.05)
        self.assertFalse(t.user_onset(1.5, "sidA"))


class TestLevelTriggeredYield(unittest.TestCase):
    """If someone is already talking when the bot starts, yield immediately."""

    def test_bot_starting_while_user_already_speaking_yields(self):
        t = tracker()
        t.user_onset(0.0, "sidA")           # no response in flight yet — returns False
        t.llm_started(0.5)
        self.assertTrue(
            t.bot_started(1.0),
            "sidA never stopped talking; the bot must not start over them",
        )
        self.assertEqual(t.interruptions, 1)

    def test_bot_starting_after_user_finished_speaks_normally(self):
        t = tracker()
        t.user_onset(0.0, "sidA")
        t.user_offset(0.5, "sidA")
        t.llm_started(0.6)
        self.assertFalse(t.bot_started(1.0))
        self.assertEqual(t.interruptions, 0)

    def test_one_speaker_leaving_does_not_clear_another(self):
        t = tracker()
        t.user_onset(0.0, "sidA")
        t.user_onset(0.1, "sidB")
        t.user_offset(0.5, "sidA")
        t.llm_started(0.6)
        self.assertTrue(t.bot_started(1.0), "sidB is still talking")

    def test_level_trigger_fires_at_most_once_per_response(self):
        t = tracker()
        t.user_onset(0.0, "sidA")
        t.llm_started(0.5)
        self.assertTrue(t.bot_started(1.0))
        self.assertFalse(t.user_onset(1.2, "sidA"))
        self.assertEqual(t.interruptions, 1)

    def test_stale_speaker_does_not_wedge_the_bot_into_silence(self):
        """A lost VAD stop must not mute the bot for the rest of the session."""
        t = tracker()
        t.user_onset(0.0, "sidA")           # offset never arrives
        t.llm_started(100.0)
        self.assertFalse(
            t.bot_started(100.5),
            "an onset this old means the stop frame was lost, not that "
            "someone has been talking for 100 seconds",
        )

    def test_talkover_is_measured_from_the_level_trigger(self):
        t = tracker()
        t.user_onset(0.0, "sidA")
        t.llm_started(0.5)
        t.bot_started(1.0)
        t.bot_stopped(1.4)
        self.assertEqual(len(t.talkovers_ms), 1)
        self.assertAlmostEqual(t.talkovers_ms[0], 400.0, places=3)


if __name__ == "__main__":
    unittest.main()
