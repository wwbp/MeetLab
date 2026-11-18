"""How a timed session ends (docs/session-limits.md): the bot finishes its reply, waits for a
pause, speaks the closing line nobody can interrupt, then the room closes after a buffer.

Prod 2026-11-18 (link-mwmartxs): the closing line was queued while a reply was generating and
a participant's next words flushed both; it was never heard and nothing ended the meeting."""
import unittest

from meetlab_bot.session_end import SessionEnd


def end(grace=30):
    return SessionEnd(grace_s=grace)


class SessionEndTest(unittest.TestCase):
    def test_nothing_happens_before_the_limit(self):
        e = end()
        self.assertEqual(e.tick(0, bot_busy=False, someone_talking=False), None)
        self.assertFalse(e.muted)

    def test_at_the_limit_people_are_muted_so_nobody_starts_or_cuts_a_reply(self):
        e = end()
        e.limit_reached(100)
        self.assertTrue(e.muted)

    def test_a_reply_in_progress_plays_to_its_end_first(self):
        e = end()
        e.limit_reached(100)
        self.assertIsNone(e.tick(101, bot_busy=True, someone_talking=False))
        self.assertIsNone(e.tick(115, bot_busy=True, someone_talking=False))
        self.assertEqual(e.tick(116, bot_busy=False, someone_talking=False), "say_closing")

    def test_a_reply_that_never_ends_is_given_up_on(self):
        e = end()
        e.limit_reached(100)
        self.assertIsNone(e.tick(129, bot_busy=True, someone_talking=False))
        self.assertEqual(e.tick(130, bot_busy=True, someone_talking=False), "say_closing")

    def test_waits_for_a_person_to_pause(self):
        e = end()
        e.limit_reached(100)
        self.assertIsNone(e.tick(101, bot_busy=False, someone_talking=True))
        self.assertIsNone(e.tick(105, bot_busy=False, someone_talking=True))
        self.assertEqual(e.tick(106, bot_busy=False, someone_talking=False), "say_closing")

    def test_waits_at_most_10_s_for_a_pause(self):
        e = end()
        e.limit_reached(100)
        self.assertIsNone(e.tick(100, bot_busy=False, someone_talking=True))
        self.assertIsNone(e.tick(109.9, bot_busy=False, someone_talking=True))
        self.assertEqual(e.tick(110, bot_busy=False, someone_talking=True), "say_closing")

    def test_the_pause_wait_starts_after_the_reply(self):
        e = end()
        e.limit_reached(100)
        self.assertIsNone(e.tick(115, bot_busy=True, someone_talking=True))
        self.assertIsNone(e.tick(116, bot_busy=False, someone_talking=True))
        self.assertIsNone(e.tick(125.9, bot_busy=False, someone_talking=True))
        self.assertEqual(e.tick(126, bot_busy=False, someone_talking=True), "say_closing")

    def test_says_the_closing_line_once(self):
        e = end()
        e.limit_reached(100)
        self.assertEqual(e.tick(100, bot_busy=False, someone_talking=False), "say_closing")
        self.assertIsNone(e.tick(101, bot_busy=True, someone_talking=False))

    def test_the_buffer_runs_from_the_end_of_the_closing_line(self):
        e = end(grace=30)
        e.limit_reached(100)
        e.tick(100, bot_busy=False, someone_talking=False)
        self.assertIsNone(e.tick(101, bot_busy=True, someone_talking=False))  # closing playing
        self.assertIsNone(e.tick(104, bot_busy=False, someone_talking=False))  # it ended at 104
        self.assertIsNone(e.tick(105, bot_busy=False, someone_talking=False))  # 1 s quiet: over
        self.assertIsNone(e.tick(133.9, bot_busy=False, someone_talking=True))
        self.assertEqual(e.tick(134, bot_busy=False, someone_talking=True), "close_room")

    def test_closes_the_room_once(self):
        e = end(grace=0)
        e.limit_reached(100)
        e.tick(100, bot_busy=False, someone_talking=False)
        e.tick(101, bot_busy=True, someone_talking=False)
        self.assertIsNone(e.tick(102, bot_busy=False, someone_talking=False))
        self.assertEqual(e.tick(103, bot_busy=False, someone_talking=False), "close_room")
        self.assertIsNone(e.tick(200, bot_busy=False, someone_talking=False))

    def test_a_closing_line_that_never_plays_still_ends_the_meeting(self):
        e = end(grace=30)
        e.limit_reached(100)
        e.tick(100, bot_busy=False, someone_talking=False)
        self.assertIsNone(e.tick(119.9, bot_busy=False, someone_talking=False))
        # No audio within 20 s: count the line as over, then the buffer.
        self.assertIsNone(e.tick(120, bot_busy=False, someone_talking=False))
        self.assertEqual(e.tick(150, bot_busy=False, someone_talking=False), "close_room")

    def test_no_closing_line_goes_straight_to_the_buffer(self):
        e = SessionEnd(grace_s=30, has_closing=False)
        e.limit_reached(100)
        self.assertIsNone(e.tick(100, bot_busy=False, someone_talking=False))
        self.assertEqual(e.tick(130, bot_busy=False, someone_talking=False), "close_room")

    def test_heard_only_once_its_audio_played_and_ended(self):
        e = end()
        e.limit_reached(100)
        e.tick(100, bot_busy=False, someone_talking=False)
        self.assertFalse(e.closing_heard)
        e.tick(101, bot_busy=True, someone_talking=False)
        self.assertFalse(e.closing_heard)
        e.tick(103, bot_busy=False, someone_talking=False)
        self.assertFalse(e.closing_heard)  # not yet 1 s of quiet
        e.tick(104, bot_busy=False, someone_talking=False)
        self.assertTrue(e.closing_heard)

    def test_never_heard_when_no_audio_came(self):
        e = end()
        e.limit_reached(100)
        e.tick(100, bot_busy=False, someone_talking=False)
        e.tick(121, bot_busy=False, someone_talking=False)
        self.assertFalse(e.closing_heard)

    def test_a_pause_between_closing_sentences_is_not_its_end(self):
        # Prod-like: "Time is up." <0.4 s gap> "Thank you, goodbye." The gap is not the end.
        e = end(grace=5)
        e.limit_reached(100)
        e.tick(100, bot_busy=False, someone_talking=False)
        e.tick(100.5, bot_busy=True, someone_talking=False)
        e.tick(101.5, bot_busy=False, someone_talking=False)   # gap
        self.assertFalse(e.closing_heard)
        e.tick(101.9, bot_busy=True, someone_talking=False)    # second sentence
        e.tick(103.0, bot_busy=False, someone_talking=False)
        self.assertFalse(e.closing_heard)
        e.tick(104.0, bot_busy=False, someone_talking=False)   # 1 s quiet: over at 103
        self.assertTrue(e.closing_heard)
        self.assertIsNone(e.tick(107.9, bot_busy=False, someone_talking=False))
        self.assertEqual(e.tick(108.0, bot_busy=False, someone_talking=False), "close_room")

    def test_the_limit_counts_once(self):
        e = end()
        e.limit_reached(100)
        e.limit_reached(150)
        self.assertEqual(e.tick(100, bot_busy=False, someone_talking=False), "say_closing")


class MutesThePipeline(unittest.TestCase):
    """Muting is what keeps a participant from starting a new reply or cutting the closing line:
    Pipecat's user aggregator drops their frames while a mute strategy says so."""

    def test_people_are_muted_from_the_limit(self):
        import asyncio

        from pipecat.frames.frames import UserStartedSpeakingFrame
        from meetlab_bot.bot import build_user_aggregator_params

        e = end()
        (mute,) = build_user_aggregator_params(None, session_end=e).user_mute_strategies
        frame = UserStartedSpeakingFrame()
        self.assertFalse(asyncio.run(mute.process_frame(frame)))
        e.limit_reached(100)
        self.assertTrue(asyncio.run(mute.process_frame(frame)))

    def test_no_session_end_no_mute(self):
        from meetlab_bot.bot import build_user_aggregator_params

        self.assertEqual(build_user_aggregator_params(None).user_mute_strategies, [])


class PlaybackClockTest(unittest.TestCase):
    """Local run 2026-11-18: Pipecat said "bot stopped speaking" 0.6 s into a closing line that
    played for 3 s, because speech is synthesized faster than it plays. The audio sent says
    how long it really plays."""

    def test_quiet_before_any_audio(self):
        from meetlab_bot.session_end import PlaybackClock
        self.assertFalse(PlaybackClock().playing(0))

    def test_playing_for_as_long_as_the_audio_sent(self):
        from meetlab_bot.session_end import PlaybackClock
        c = PlaybackClock()
        for _ in range(30):          # 3 s of audio, all sent within the first 0.3 s
            c.sent(10.0, 0.1)
        self.assertTrue(c.playing(10.6))
        self.assertTrue(c.playing(12.9))
        self.assertFalse(c.playing(13.0))

    def test_audio_after_a_gap_starts_from_then(self):
        from meetlab_bot.session_end import PlaybackClock
        c = PlaybackClock()
        c.sent(10.0, 1.0)
        self.assertFalse(c.playing(15.0))
        c.sent(20.0, 1.0)
        self.assertTrue(c.playing(20.5))
        self.assertFalse(c.playing(21.0))

    def test_an_interruption_cuts_it(self):
        from meetlab_bot.session_end import PlaybackClock
        c = PlaybackClock()
        c.sent(10.0, 5.0)
        c.cut(11.0)
        self.assertFalse(c.playing(11.0))


class ClosesTheRoom(unittest.TestCase):
    """A close that fails once must not leave people in a room with a muted bot."""

    def _run(self, outcomes):
        import asyncio

        from meetlab_bot.session_end import close_with_retries
        calls, sleeps = [], []

        async def delete():
            calls.append(1)
            o = outcomes[len(calls) - 1]
            if o:
                raise o

        async def sleep(s):
            sleeps.append(s)

        return asyncio.run(close_with_retries(delete, sleep=sleep)), len(calls), sleeps

    def test_closes_at_once_when_it_works(self):
        self.assertEqual(self._run([None]), (True, 1, []))

    def test_tries_again_after_a_failure(self):
        ok, calls, sleeps = self._run([RuntimeError("503"), None])
        self.assertEqual((ok, calls, len(sleeps)), (True, 2, 1))

    def test_gives_up_after_three(self):
        ok, calls, _ = self._run([RuntimeError("x")] * 3)
        self.assertEqual((ok, calls), (False, 3))


class SpeechAfterTheLimit(unittest.TestCase):
    """People are muted for the bot from the limit, but what they say is still research data
    and still counts towards their completion code."""

    def test_stored_under_the_speaker(self):
        from meetlab_bot.session_end import after_limit_utterance
        self.assertEqual(after_limit_utterance(" Thanks, bye. ", "sid1", {"sid1": "ana__x1"}),
                         {"speaker_id": "ana__x1", "text": "Thanks, bye.", "meta": {"source": "speech", "after_limit": True}})

    def test_nothing_for_silence_or_a_stranger(self):
        from meetlab_bot.session_end import after_limit_utterance
        self.assertIsNone(after_limit_utterance("  ", "sid1", {"sid1": "ana__x1"}))
        self.assertIsNone(after_limit_utterance("hello", "sid9", {"sid1": "ana__x1"}))
        self.assertIsNone(after_limit_utterance("hello", None, {"sid1": "ana__x1"}))


if __name__ == "__main__":
    unittest.main()
