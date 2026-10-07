"""When the bot may be interrupted — and the floor that keeps it from being mute.

History, because this file has now been driven by two opposite production reports.

**2026-08-19 morning.** "Interruption feels non-existent, bots keep speaking."
The window opened on the bot's first TTS audio, so a user talking during LLM
generation could not interrupt at all, and edge-triggered onset never fired again
while they kept talking. Fix: open the window at generation (`llm_started`) and
level-trigger at `bot_started`.

**2026-08-19 09:00 EDT, a real multi-person sync.** The opposite failure. 17 TTS
generations, 12 reached audio, **22 yields** — the bot was interrupted more often
than it spoke. Its greeting was cancelled 244ms in. Because a cancelled response
never completes, it never enters the LLM context, so the model regenerated
near-identical questions turn after turn ("What do the participants think about
A, B and C…" seven times). Silent stretches, apparent lag, and answers that
looked wrong were all the same root cause.

Why: `llm_started` made the bot interruptible during its entire think-time, and in
a group meeting people are talking to *each other* almost continuously. The bot
was permanently suppressed.

The rule now, one concept covering both failures:

    no audio yet          -> nothing to interrupt, nothing yields
    audio < floor         -> the bot keeps the floor; it always gets a phrase out
    audio >= floor        -> any onset yields, and each new sentence re-checks
                             whether someone is still talking

`llm_started` is gone: the level trigger at `bot_started` already covers the case
it was added for, without letting ordinary conversation kill a response before it
makes a sound.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from interruption import InterruptionTracker

FLOOR_MS = 600


def tracker(floor_ms: int = FLOOR_MS):
    return InterruptionTracker(min_bot_speech_ms=floor_ms)


class TestNothingInterruptsBeforeTheBotHasSpoken(unittest.TestCase):
    """The regression that suppressed the 2026-08-19 sync."""

    def test_speech_during_generation_does_not_yield(self):
        t = tracker()
        # The bot is thinking. There is no audio to cancel, and cancelling here
        # throws away a whole response for free.
        self.assertFalse(t.user_onset(0.3, "sidA"))
        self.assertEqual(t.interruptions, 0)

    def test_speech_with_no_response_at_all_does_not_yield(self):
        t = tracker()
        self.assertFalse(t.user_onset(0.3, "sidA"))

    def test_the_level_trigger_still_covers_the_talking_through_case(self):
        """This is why llm_started was redundant.

        Someone talks all the way through generation. They are still going when
        audio starts, so the bot yields then — one response's worth of work lost
        at most, instead of every response dying mid-thought.
        """
        t = tracker(floor_ms=0)
        t.user_onset(0.0, "sidA")
        self.assertTrue(t.bot_started(1.0))
        self.assertEqual(t.interruptions, 1)


class TestTheBotKeepsAFloor(unittest.TestCase):
    """A guaranteed speaking window, so the bot cannot be livelocked into silence."""

    def test_onset_inside_the_floor_is_ignored(self):
        t = tracker()
        t.bot_started(0.0)
        self.assertFalse(
            t.user_onset(0.244, "sidA"),
            "244ms is where the real greeting died; the bot must survive it",
        )
        self.assertEqual(t.interruptions, 0)

    def test_onset_after_the_floor_yields(self):
        t = tracker()
        t.bot_started(0.0)
        self.assertTrue(t.user_onset(0.7, "sidA"))
        self.assertEqual(t.interruptions, 1)

    def test_the_floor_runs_from_first_audio_not_each_sentence(self):
        """BotStartedSpeaking fires per sentence. The floor is per response."""
        t = tracker()
        t.bot_started(0.0)
        t.bot_stopped(0.5)
        t.bot_started(0.55)  # sentence two
        self.assertTrue(
            t.user_onset(0.65, "sidA"),
            "0.65s of audio has played across the response; the floor is spent",
        )

    def test_a_new_response_gets_a_fresh_floor(self):
        t = tracker()
        t.bot_started(0.0)
        self.assertTrue(t.user_onset(0.7, "sidA"))
        t.bot_stopped(0.8)
        t.assistant_turn_stopped(0.9)

        t.bot_started(5.0)
        self.assertFalse(t.user_onset(5.2, "sidA"), "inside the new response's floor")
        self.assertTrue(t.user_onset(5.7, "sidA"))

    def test_someone_still_talking_yields_at_the_next_sentence(self):
        """The re-check that makes the floor safe rather than merely rude.

        The bot starts over someone and keeps its floor, but each sentence
        boundary asks again — so it stops after a phrase, not after the whole
        response.
        """
        t = tracker()
        t.user_onset(0.0, "sidA")  # talking before the bot began
        self.assertFalse(t.bot_started(1.0), "floor: the bot gets its phrase")
        t.bot_stopped(1.5)
        self.assertTrue(
            t.bot_started(1.7), "sidA never stopped; yield at the sentence boundary"
        )

    def test_a_speaker_who_stopped_does_not_trigger_the_recheck(self):
        t = tracker()
        t.user_onset(0.0, "sidA")
        t.user_offset(0.5, "sidA")
        t.bot_started(1.0)
        t.bot_stopped(1.5)
        self.assertFalse(t.bot_started(1.7))
        self.assertEqual(t.interruptions, 0)


class TestOncePerResponse(unittest.TestCase):
    def test_a_second_onset_in_the_same_response_is_a_no_op(self):
        t = tracker()
        t.bot_started(0.0)
        self.assertTrue(t.user_onset(0.7, "sidA"))
        self.assertFalse(t.user_onset(0.9, "sidA"), "already cancelling")
        self.assertEqual(t.interruptions, 1)

    def test_the_window_closes_when_the_response_ends(self):
        t = tracker()
        t.bot_started(0.0)
        t.bot_stopped(1.0)
        t.assistant_turn_stopped(1.05)
        self.assertFalse(t.user_onset(1.5, "sidA"))

    def test_talkover_is_measured_from_the_onset_that_armed_it(self):
        t = tracker()
        t.bot_started(0.0)
        t.user_onset(0.7, "sidA")
        t.bot_stopped(1.1)
        self.assertEqual(len(t.talkovers_ms), 1)
        self.assertAlmostEqual(t.talkovers_ms[0], 400.0, places=3)


class TestStaleSpeakers(unittest.TestCase):
    def test_a_lost_stop_frame_does_not_wedge_the_bot_into_silence(self):
        t = tracker(floor_ms=0)
        t.user_onset(0.0, "sidA")  # offset never arrives
        self.assertFalse(
            t.bot_started(100.0),
            "an onset this old is a dropped stop, not a 100-second turn",
        )

    def test_one_speaker_leaving_does_not_clear_another(self):
        t = tracker(floor_ms=0)
        t.user_onset(0.0, "sidA")
        t.user_onset(0.1, "sidB")
        t.user_offset(0.5, "sidA")
        self.assertTrue(t.bot_started(1.0), "sidB is still talking")


if __name__ == "__main__":
    unittest.main()
