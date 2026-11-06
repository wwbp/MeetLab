"""Who may speak in push-to-talk and baton rooms: pure rules, times passed in.

Plan: docs/audio-modes-test-plan.md. Normal rooms have no Floor at all.
"""
import unittest

from meetlab_bot.floor import Floor


class TestPushToTalk(unittest.TestCase):
    def test_a_hold_past_3s_still_counts_as_speaking(self):
        """InterruptionTracker forgets an onset after 3 s; a held button must not."""
        floor = Floor("ptt")
        floor.press(0.0, "a")
        for second in range(1, 61):
            floor.heard(float(second), "a")
            self.assertFalse(floor.idle(float(second)), f"read as silent at {second} s")

    def test_people_may_hold_at_once(self):
        floor = Floor("ptt")
        self.assertTrue(floor.press(0.0, "a"))
        self.assertTrue(floor.press(0.1, "b"))
        floor.release(1.0, "a")
        self.assertFalse(floor.idle(1.0), "b is still holding")
        floor.release(2.0, "b")
        self.assertTrue(floor.idle(2.0))


class TestBaton(unittest.TestCase):
    def test_baton_claim_race_has_one_winner(self):
        floor = Floor("baton")
        self.assertTrue(floor.press(0.0, "a"))
        self.assertFalse(floor.press(0.0, "b"))
        self.assertFalse(floor.bot_claim(0.0))
        self.assertEqual(floor.holder, "a")

    def test_a_person_beats_the_bot_on_a_tie(self):
        """People outrank the bot: it claims only at first audio, so a press in the same
        instant wins and the reply is dropped."""
        floor = Floor("baton")
        floor.press(1.0, "a")
        self.assertFalse(floor.bot_claim(1.0))
        self.assertEqual(floor.holder, "a")

    def test_the_bot_claims_a_free_baton(self):
        floor = Floor("baton")
        self.assertTrue(floor.bot_claim(0.0))
        self.assertEqual(floor.holder, Floor.BOT)
        self.assertFalse(floor.press(0.1, "a"))

    def test_release_from_non_holder_is_ignored(self):
        floor = Floor("baton")
        floor.press(0.0, "a")
        floor.release(0.1, "b")
        self.assertEqual(floor.holder, "a")

    def test_holder_disconnect_frees_baton(self):
        floor = Floor("baton")
        floor.press(0.0, "a")
        floor.disconnected(0.1, "a")
        self.assertIsNone(floor.holder)

    def test_resumed_bot_starts_with_free_baton(self):
        """Holder state dies with a bot, so a replacement must never inherit a holder."""
        self.assertIsNone(Floor("baton").holder)


class TestWalkedAway(unittest.TestCase):
    def test_walked_away_holder_loses_baton(self):
        floor = Floor("baton")
        floor.press(0.0, "a")
        floor.tick(Floor.IDLE_SECS - 0.1)
        self.assertEqual(floor.holder, "a")
        floor.tick(Floor.IDLE_SECS)
        self.assertIsNone(floor.holder)

    def test_speech_keeps_the_baton(self):
        floor = Floor("baton")
        floor.press(0.0, "a")
        for second in range(1, 61):
            floor.heard(float(second), "a")
            floor.tick(float(second))
        self.assertEqual(floor.holder, "a")

    def test_only_the_holders_speech_keeps_the_baton(self):
        floor = Floor("baton")
        floor.press(0.0, "a")
        floor.heard(Floor.IDLE_SECS - 1, "b")
        floor.tick(Floor.IDLE_SECS)
        self.assertIsNone(floor.holder)

    def test_a_late_tick_does_not_rescue_a_silent_holder(self):
        """Speech after the limit comes too late, however late the timer ran."""
        floor = Floor("baton")
        floor.press(0.0, "a")
        floor.heard(Floor.IDLE_SECS + 1, "a")
        self.assertIsNone(floor.holder)

    def test_silent_ptt_hold_stops_blocking_the_bot(self):
        floor = Floor("ptt")
        floor.press(0.0, "a")
        self.assertFalse(floor.idle(Floor.IDLE_SECS - 0.1))
        self.assertTrue(floor.idle(Floor.IDLE_SECS))
        self.assertTrue(floor.bot_claim(Floor.IDLE_SECS))

    def test_ptt_speaking_again_blocks_the_bot_again(self):
        floor = Floor("ptt")
        floor.press(0.0, "a")
        floor.heard(Floor.IDLE_SECS + 5, "a")
        self.assertFalse(floor.idle(Floor.IDLE_SECS + 5))

    def test_ptt_hold_with_speech_has_no_limit(self):
        floor = Floor("ptt")
        floor.press(0.0, "a")
        for second in range(1, 601):
            floor.heard(float(second), "a")
            self.assertFalse(floor.idle(float(second)), f"read as silent at {second} s")


class TestQueue(unittest.TestCase):
    def queued(self):
        """a holds; b, then c, wait. Their grants are collected by tick."""
        floor = Floor("baton")
        floor.press(0.0, "a")
        self.assertFalse(floor.press(0.1, "b"))
        self.assertFalse(floor.press(0.2, "c"))
        self.assertTrue(floor.waiting)
        return floor

    def test_next_is_granted_on_release_first_come(self):
        floor = self.queued()
        self.assertEqual(floor.tick(0.5), [], "nothing granted yet")
        floor.release(1.0, "a")
        self.assertEqual(floor.holder, "b")
        self.assertEqual(floor.tick(1.0), ["b"])
        self.assertEqual(floor.tick(1.0), [], "a grant is reported once")
        floor.release(2.0, "b")
        self.assertEqual(floor.tick(2.0), ["c"])
        self.assertFalse(floor.waiting)

    def test_next_is_granted_on_disconnect(self):
        floor = self.queued()
        floor.disconnected(1.0, "a")
        self.assertEqual(floor.tick(1.0), ["b"])

    def test_next_is_granted_on_idle_out_with_a_fresh_clock(self):
        floor = self.queued()
        self.assertEqual(floor.tick(Floor.IDLE_SECS), ["b"])
        self.assertEqual(floor.tick(2 * Floor.IDLE_SECS - 0.1), [], "b's 15 s start at the grant")
        self.assertEqual(floor.holder, "b")

    def test_queued_person_who_releases_leaves_the_queue(self):
        floor = self.queued()
        floor.release(0.5, "b")
        self.assertEqual(floor.holder, "a", "a queued release does not touch the holder")
        floor.release(1.0, "a")
        self.assertEqual(floor.tick(1.0), ["c"])

    def test_queued_person_who_disconnects_leaves_the_queue(self):
        floor = self.queued()
        floor.disconnected(0.5, "b")
        floor.release(1.0, "a")
        self.assertEqual(floor.tick(1.0), ["c"])

    def test_pressing_twice_keeps_one_place_in_line(self):
        floor = self.queued()
        self.assertFalse(floor.press(0.3, "b"))
        floor.release(1.0, "a")
        floor.release(1.1, "b")
        self.assertEqual(floor.tick(1.1), ["b", "c"])
        self.assertEqual(floor.holder, "c")

    def test_holder_pressing_again_keeps_the_baton_and_its_clock(self):
        floor = Floor("baton")
        floor.press(0.0, "a")
        self.assertTrue(floor.press(1.0, "a"))
        floor.tick(Floor.IDLE_SECS)
        self.assertIsNone(floor.holder)

    def test_press_on_a_silent_holders_baton_is_granted(self):
        floor = Floor("baton")
        floor.press(0.0, "a")
        self.assertTrue(floor.press(Floor.IDLE_SECS, "b"))
        self.assertEqual(floor.holder, "b")


class TestBotHolds(unittest.TestCase):
    def test_press_while_bot_holds_is_granted_when_bot_releases(self):
        floor = Floor("baton")
        floor.bot_claim(0.0)
        self.assertFalse(floor.waiting)
        self.assertFalse(floor.press(1.0, "a"))
        self.assertTrue(floor.waiting, "the bot's cue to yield after its sentence")
        self.assertEqual(floor.holder, Floor.BOT, "the sentence finishes")
        floor.release(2.0, Floor.BOT)
        self.assertEqual(floor.holder, "a")
        self.assertEqual(floor.tick(2.0), ["a"])

    def test_bot_holding_may_claim_again_unless_someone_waits(self):
        floor = Floor("baton")
        self.assertTrue(floor.bot_claim(0.0))
        self.assertTrue(floor.bot_claim(1.0), "its next sentence")
        floor.press(1.5, "a")
        self.assertFalse(floor.bot_claim(2.0))

    def test_a_silent_bot_loses_the_baton(self):
        """A stuck reply (ElevenLabs quota: no audio, no error) must not hold the room."""
        floor = Floor("baton")
        floor.bot_claim(0.0)
        floor.press(1.0, "a")
        self.assertEqual(floor.tick(Floor.IDLE_SECS), ["a"])

    def test_press_during_generation_blocks_first_audio(self):
        for mode in ("ptt", "baton"):
            with self.subTest(mode=mode):
                floor = Floor(mode)
                floor.press(1.0, "a")
                self.assertFalse(floor.bot_claim(1.2), "generating since 0.0, first audio at 1.2")
                floor.release(2.0, "a")
                self.assertTrue(floor.bot_claim(2.0))

    def test_ptt_claim_leaves_the_floor_open(self):
        """In ptt only a person holding blocks; the bot's claim holds nothing."""
        floor = Floor("ptt")
        self.assertTrue(floor.bot_claim(0.0))
        self.assertTrue(floor.press(0.1, "a"))
        self.assertIsNone(floor.holder)


class TestTimeouts(unittest.TestCase):
    """The event log's `timeout`: each hold that went silent, reported once."""

    def test_silent_baton_holder_times_out_once(self):
        floor = Floor("baton")
        floor.press(0.0, "a")
        self.assertEqual(floor.timeouts(Floor.IDLE_SECS - 0.1), [])
        self.assertEqual(floor.timeouts(Floor.IDLE_SECS), ["a"])
        self.assertEqual(floor.timeouts(Floor.IDLE_SECS + 1), [])

    def test_silent_ptt_hold_times_out_once_and_again_after_speaking(self):
        floor = Floor("ptt")
        floor.press(0.0, "a")
        self.assertEqual(floor.timeouts(Floor.IDLE_SECS), ["a"])
        self.assertEqual(floor.timeouts(Floor.IDLE_SECS + 1), [])
        floor.heard(20.0, "a")
        self.assertEqual(floor.timeouts(20.0 + Floor.IDLE_SECS), ["a"])

    def test_a_release_is_not_a_timeout(self):
        floor = Floor("ptt")
        floor.press(0.0, "a")
        floor.release(1.0, "a")
        self.assertEqual(floor.timeouts(Floor.IDLE_SECS + 1), [])

    def test_a_ptt_release_after_a_timeout_clears_it(self):
        floor = Floor("ptt")
        floor.press(0.0, "a")
        floor.timeouts(Floor.IDLE_SECS)
        floor.release(16.0, "a")
        floor.press(17.0, "a")
        self.assertEqual(floor.timeouts(17.0 + Floor.IDLE_SECS), ["a"])


class TestWhoIsWhere(unittest.TestCase):
    def test_queue_is_in_order_and_holds_is_per_person(self):
        floor = Floor("baton")
        floor.press(0.0, "a")
        floor.press(0.1, "b")
        floor.press(0.2, "c")
        self.assertEqual(floor.queue, ["b", "c"])
        self.assertTrue(floor.holds("a"))
        self.assertFalse(floor.holds("b"))


class TestModes(unittest.TestCase):
    def test_normal_has_no_floor(self):
        """Normal rooms keep today's code path: nothing may build a Floor for them."""
        with self.assertRaises(ValueError):
            Floor("normal")


if __name__ == "__main__":
    unittest.main()
