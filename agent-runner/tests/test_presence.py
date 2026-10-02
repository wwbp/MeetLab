"""When a bot leaves its room (presence.py), decided from LiveKit's live roster.

Diagnosis F1: the bot decided "everyone left" from its own cached roster, which
missed humans already in the room when it joined, so it left while one remained.
A page refresh ended the session; a bot whose humans never came never left.
"""
import asyncio
import unittest

from presence import Presence, humans, observe, should_leave, step, wait_until_empty

START = Presence(empty_since=None, seen_human=False)


class HumansTests(unittest.TestCase):
    def test_counts_everyone_but_bots(self):
        self.assertEqual(humans(["alice__1", "bot_room_abc", "bob__2"]), 2)


class ObserveTests(unittest.TestCase):
    def test_an_empty_room_is_empty_from_the_first_look(self):
        self.assertEqual(observe(START, humans=0, now=10.0), Presence(empty_since=10.0, seen_human=False))

    def test_emptiness_keeps_its_start_time(self):
        self.assertEqual(observe(Presence(10.0, True), humans=0, now=40.0), Presence(10.0, True))

    def test_a_human_clears_emptiness_and_is_remembered(self):
        self.assertEqual(observe(Presence(10.0, False), humans=1, now=40.0), Presence(None, True))


class ShouldLeaveTests(unittest.TestCase):
    def test_never_while_a_human_is_in_the_room(self):
        self.assertFalse(should_leave(humans=1, empty_since=None, now=1e9, grace=60))

    def test_not_within_the_grace(self):  # a refresh: gone, back in seconds
        self.assertFalse(should_leave(humans=0, empty_since=100.0, now=159.0, grace=60))

    def test_once_the_room_has_been_empty_for_the_grace(self):
        self.assertTrue(should_leave(humans=0, empty_since=100.0, now=160.0, grace=60))


class JoinOrderTests(unittest.TestCase):
    """The rule over a sequence of roster looks, as the bot sees them."""

    def _run(self, looks, arrival=900, rejoin=60):
        state = START
        for now, n in looks:
            state, leave = step(state, n, now, arrival, rejoin)
            if leave:
                return now
        return None

    def test_humans_first_one_leaves_the_bot_stays(self):  # F1
        self.assertIsNone(self._run([(0, 2), (5, 1), (300, 1)]))

    def test_bot_first_then_humans_arrive_late(self):  # a late Prolific participant
        self.assertIsNone(self._run([(0, 0), (600, 1), (900, 1)]))

    def test_nobody_ever_comes_the_bot_leaves_after_the_arrival_grace(self):
        self.assertEqual(self._run([(0, 0), (899, 0), (900, 0)]), 900)

    def test_a_refresh_does_not_end_the_session(self):
        self.assertIsNone(self._run([(0, 1), (10, 0), (25, 1), (200, 1)]))

    def test_the_last_human_leaving_ends_it_after_the_rejoin_grace(self):
        self.assertEqual(self._run([(0, 1), (10, 0), (69, 0), (70, 0)]), 70)


class WaitUntilEmptyTests(unittest.TestCase):
    """The loop bot.py runs: look at the live roster, sleep, look again."""

    def test_returns_once_the_last_human_has_been_gone_for_the_rejoin_grace(self):
        rosters = iter([["alice__1", "bob__2"], ["bob__2"], [], [], []])  # one look per 30 s
        clock = iter([0.0, 30.0, 60.0, 90.0, 120.0])
        looks = []

        def roster():
            looks.append(1)
            return next(rosters)

        async def sleep(_):
            pass

        asyncio.run(wait_until_empty(roster, arrival=900, rejoin=60, clock=lambda: next(clock), sleep=sleep))
        self.assertEqual(len(looks), 5, "left at 120 s: empty since 60 s, plus the 60 s grace")


if __name__ == "__main__":
    unittest.main()
