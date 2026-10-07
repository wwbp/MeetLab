"""InterruptionTracker — detects and measures the bot speaking over a user.

Pure state machine fed four signals: bot_started / bot_stopped (audio boundaries),
assistant_turn_stopped (the response genuinely ending), and user_onset.

The audio and response windows are deliberately different — see interruption.py.
Enforcement is gated on the response window so an onset in the gap between two
sentences still interrupts; talkover_ms is measured against real audio so the
numbers stay comparable with the pilot. these tests are free of
metric side effects.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from interruption import InterruptionTracker


class TestInterruptionTracker(unittest.TestCase):
    def _tracker(self):
        # min_bot_speech_ms=0 disables the barge-in floor. These tests are about
        # window semantics, once-per-response counting and talkover measurement —
        # all orthogonal to how long the bot is guaranteed before it may be cut
        # off. The floor itself is covered in test_interruption_windows.py.
        return InterruptionTracker(min_bot_speech_ms=0)

    def test_user_speaking_while_bot_silent_is_not_an_interruption(self):
        t = self._tracker()
        t.user_onset(1.0, "sidA")          # bot not speaking
        self.assertEqual(t.interruptions, 0)
        self.assertEqual(t.talkovers_ms, [])

    def test_user_onset_during_bot_speech_counts_and_measures(self):
        t = self._tracker()
        t.bot_started(10.0)
        t.user_onset(10.5, "sidA")          # user interrupts 0.5s in
        t.bot_stopped(11.0)                 # bot keeps going until 11.0
        self.assertEqual(t.interruptions, 1)
        self.assertEqual(len(t.talkovers_ms), 1)
        self.assertAlmostEqual(t.talkovers_ms[0], 500.0, places=3)  # 11.0 - 10.5 → 500ms

    def test_multiple_onsets_in_one_window_count_once(self):
        t = self._tracker()
        t.bot_started(0.0)
        t.user_onset(0.2, "sidA")
        t.user_onset(0.4, "sidB")           # VAD flicker / second speaker, same window
        t.bot_stopped(1.0)
        self.assertEqual(t.interruptions, 1)
        self.assertAlmostEqual(t.talkovers_ms[0], 800.0, places=3)  # from first onset

    def test_onset_after_the_response_finished_is_clean(self):
        """A normal turn after the bot is done — not a talk-over.

        Note bot_stopped alone is NOT enough: that fires in the gap between
        sentences too. The response has to actually end.
        """
        t = self._tracker()
        t.bot_started(0.0)
        t.bot_stopped(1.0)
        t.assistant_turn_stopped(1.05)
        t.user_onset(1.5, "sidA")
        self.assertEqual(t.interruptions, 0)
        self.assertEqual(t.talkovers_ms, [])

    def test_two_separate_bot_responses_each_can_interrupt(self):
        """assistant_turn_stopped is what separates responses. Without it, a
        bot_started after bot_stopped is the next *sentence* of the same
        response — which must not re-arm the interruption."""
        t = self._tracker()
        t.bot_started(0.0); t.user_onset(0.5, "s"); t.bot_stopped(1.0)
        t.assistant_turn_stopped(1.1)
        t.bot_started(5.0); t.user_onset(5.2, "s"); t.bot_stopped(5.6)
        self.assertEqual(t.interruptions, 2)
        self.assertEqual(len(t.talkovers_ms), 2)

    def test_clean_bot_window_with_no_overlap(self):
        t = self._tracker()
        t.bot_started(0.0)
        t.bot_stopped(2.0)
        self.assertEqual(t.interruptions, 0)
        self.assertEqual(t.talkovers_ms, [])

    # ── RC3: the tracker must also tell the caller to *act* ───────────────────
    #
    # Detection was always here; enforcement was not. user_onset() now returns
    # True exactly when the bot should yield, so bot.py can push an
    # InterruptionFrame. Returning the signal (rather than bot.py re-deriving it)
    # keeps the "once per bot-speaking window" rule in one place.

    def test_user_onset_signals_interrupt_when_the_bot_is_speaking(self):
        t = self._tracker()
        t.bot_started(0.0)
        self.assertTrue(t.user_onset(0.5, "sidA"))

    def test_user_onset_does_not_signal_when_the_bot_is_silent(self):
        """Normal turn-taking. Interrupting here would cancel nothing and could
        discard the user's own in-flight turn."""
        t = self._tracker()
        self.assertFalse(t.user_onset(1.0, "sidA"))

        t.bot_started(2.0)
        t.bot_stopped(3.0)
        t.assistant_turn_stopped(3.05)
        self.assertFalse(t.user_onset(3.5, "sidA"))

    def test_only_the_first_onset_in_a_window_signals(self):
        """VAD flicker, or a second speaker joining in, must not re-interrupt.

        The bot is already being cancelled; repeated InterruptionFrames would
        churn the pipeline for no gain.
        """
        t = self._tracker()
        t.bot_started(0.0)
        self.assertTrue(t.user_onset(0.2, "sidA"))
        self.assertFalse(t.user_onset(0.4, "sidA"))
        self.assertFalse(t.user_onset(0.6, "sidB"))

    def test_a_later_sentence_does_not_re_arm_within_one_response(self):
        """The counterpart to test_a_new_response_re_arms_the_window: without a
        response boundary, the bot is already being cancelled."""
        t = self._tracker()
        t.bot_started(0.0)
        self.assertTrue(t.user_onset(0.5, "s"))
        t.bot_stopped(1.0)

        t.bot_started(5.0)
        self.assertFalse(t.user_onset(5.2, "s"))

    def test_signalling_does_not_change_what_gets_measured(self):
        """Enforcement must not distort the metric that proved the problem."""
        t = self._tracker()
        t.bot_started(10.0)
        t.user_onset(10.5, "s")
        t.bot_stopped(11.0)

        self.assertEqual(t.interruptions, 1)
        self.assertAlmostEqual(t.talkovers_ms[0], 500.0, places=3)

    # ── The enforcement window must span inter-sentence gaps ──────────────────
    #
    # Live testing found interruption working "sometimes". Cause: Pipecat's output
    # transport declares the bot stopped after BOT_VAD_STOP_SECS = 0.35s of no
    # audio, and with sentence-level TTS the gap while the next sentence is
    # synthesised (TTS TTFB p50 215ms + aggregation ~213ms) straddles that
    # threshold. Gaps under 350ms kept the window open and interruption worked;
    # gaps over it closed the window and the bot talked straight through.
    #
    # So the enforcement gate is now "is a bot response in flight", ended by the
    # assistant turn finishing — not "is audio playing right now".
    # talkover_ms still measures real audio, so the numbers stay comparable with
    # the pilot.

    def test_a_gap_between_sentences_still_counts_as_the_bot_speaking(self):
        t = self._tracker()
        t.bot_started(0.0)
        t.bot_stopped(1.0)           # sentence 1 audio ends; more is coming

        self.assertTrue(
            t.user_onset(1.2, "s"),
            "an onset in the gap between sentences must still interrupt",
        )

    def test_the_window_closes_when_the_assistant_turn_ends(self):
        t = self._tracker()
        t.bot_started(0.0)
        t.bot_stopped(1.0)
        t.assistant_turn_stopped(1.1)

        self.assertFalse(
            t.user_onset(1.5, "s"),
            "once the response is finished this is ordinary turn-taking",
        )

    def test_talkover_is_still_measured_from_real_audio_end(self):
        """The metric must keep meaning what it meant during the pilot."""
        t = self._tracker()
        t.bot_started(10.0)
        t.user_onset(10.5, "s")
        t.bot_stopped(11.0)          # audio actually stopped here
        t.assistant_turn_stopped(11.4)

        self.assertEqual(t.interruptions, 1)
        self.assertAlmostEqual(t.talkovers_ms[0], 500.0, places=3)

    def test_one_interruption_per_response_not_per_sentence(self):
        t = self._tracker()
        t.bot_started(0.0)
        self.assertTrue(t.user_onset(0.2, "s"))
        t.bot_stopped(0.5)
        # Second sentence starts; the user is still going. Already interrupting.
        t.bot_started(0.8)
        self.assertFalse(t.user_onset(0.9, "s"))

    def test_a_new_response_re_arms_the_window(self):
        t = self._tracker()
        t.bot_started(0.0)
        self.assertTrue(t.user_onset(0.1, "s"))
        t.bot_stopped(0.5)
        t.assistant_turn_stopped(0.6)

        t.bot_started(5.0)
        self.assertTrue(t.user_onset(5.1, "s"))

    def test_summary_reports_count_and_max(self):
        t = self._tracker()
        t.bot_started(0.0); t.user_onset(0.5, "s"); t.bot_stopped(1.0)   # 500ms
        t.assistant_turn_stopped(1.1)
        t.bot_started(5.0); t.user_onset(5.1, "s"); t.bot_stopped(5.9)   # 800ms
        s = t.summary()
        self.assertEqual(s["interruptions"], 2)
        self.assertAlmostEqual(s["talkover_ms_max"], 800.0, places=3)


if __name__ == "__main__":
    unittest.main()
