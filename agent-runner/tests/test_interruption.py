"""InterruptionTracker — detects and measures the bot speaking over a user.

Pure state machine fed three signals (bot_started / bot_stopped / user_onset).
A talk-over is a user starting to speak while the bot is still speaking; we count
it and measure how long the bot kept going (talkover_ms). record=False keeps these
tests free of metric side effects.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from interruption import InterruptionTracker


class TestInterruptionTracker(unittest.TestCase):
    def _tracker(self):
        return InterruptionTracker(record=False)

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

    def test_onset_after_bot_stopped_is_clean(self):
        t = self._tracker()
        t.bot_started(0.0)
        t.bot_stopped(1.0)
        t.user_onset(1.5, "sidA")           # normal turn after the bot finished
        self.assertEqual(t.interruptions, 0)
        self.assertEqual(t.talkovers_ms, [])

    def test_two_separate_bot_windows_each_can_interrupt(self):
        t = self._tracker()
        t.bot_started(0.0); t.user_onset(0.5, "s"); t.bot_stopped(1.0)
        t.bot_started(5.0); t.user_onset(5.2, "s"); t.bot_stopped(5.6)
        self.assertEqual(t.interruptions, 2)
        self.assertEqual(len(t.talkovers_ms), 2)

    def test_clean_bot_window_with_no_overlap(self):
        t = self._tracker()
        t.bot_started(0.0)
        t.bot_stopped(2.0)
        self.assertEqual(t.interruptions, 0)
        self.assertEqual(t.talkovers_ms, [])

    def test_summary_reports_count_and_max(self):
        t = self._tracker()
        t.bot_started(0.0); t.user_onset(0.5, "s"); t.bot_stopped(1.0)   # 500ms
        t.bot_started(5.0); t.user_onset(5.1, "s"); t.bot_stopped(5.9)   # 800ms
        s = t.summary()
        self.assertEqual(s["interruptions"], 2)
        self.assertAlmostEqual(s["talkover_ms_max"], 800.0, places=3)


if __name__ == "__main__":
    unittest.main()
