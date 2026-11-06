"""A group room's bot greets when the group is here, not when the first person is (R18).

The greeting used to go out 1 s after the first human joined, so in a group room the
others missed it. The closing-message timer must stay anchored at the first join, so it
still matches the browser's session timer.
"""
import unittest
from types import SimpleNamespace

from meetlab_bot.bot import first_join, greeting_due
from meetlab_bot.bot_task import runner_args_for


class GreetingDueTest(unittest.TestCase):
    def test_greets_once_everyone_expected_is_here(self):
        self.assertFalse(greeting_due(humans_present=2, expected=3, first_join_s=0, now_s=10))
        self.assertTrue(greeting_due(humans_present=3, expected=3, first_join_s=0, now_s=10))

    def test_greets_after_60_s_even_if_someone_is_missing(self):
        self.assertFalse(greeting_due(humans_present=1, expected=3, first_join_s=100, now_s=159))
        self.assertTrue(greeting_due(humans_present=1, expected=3, first_join_s=100, now_s=160))

    def test_no_expected_count_means_one_person_is_enough(self):  # size 1, and old sessions
        self.assertTrue(greeting_due(humans_present=1, expected=None, first_join_s=0, now_s=0))
        self.assertTrue(greeting_due(humans_present=1, expected=1, first_join_s=0, now_s=0))


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    async def sleep(self, s):
        self.t += s


class FirstJoinTest(unittest.IsolatedAsyncioTestCase):
    async def run_first_join(self, humans, expected, greet=True):
        clock, said, close_anchor = FakeClock(), [], []

        async def say():
            said.append(clock.t)

        await first_join(start_close=close_anchor.append, greet=say if greet else None,
                         humans_present=lambda: humans(clock.t), expected=expected,
                         now=clock.now, sleep=clock.sleep)
        return said, close_anchor

    async def test_size_one_greets_after_the_usual_second(self):
        said, close_anchor = await self.run_first_join(lambda t: 1, expected=None)
        self.assertEqual(said, [1.0])
        self.assertEqual(close_anchor, [0.0])

    async def test_group_greets_when_the_last_member_arrives(self):
        said, _ = await self.run_first_join(lambda t: 1 if t < 7 else 3, expected=3)
        self.assertEqual(said, [7.0])

    async def test_closing_deadline_stays_at_first_join_when_greeting_is_delayed(self):
        said, close_anchor = await self.run_first_join(lambda t: 1, expected=3)
        self.assertEqual(said, [60.0])
        self.assertEqual(close_anchor, [0.0])

    async def test_a_resumed_bot_never_greets_but_keeps_its_closing_timer(self):
        said, close_anchor = await self.run_first_join(lambda t: 3, expected=3, greet=False)
        self.assertEqual(said, [])
        self.assertEqual(close_anchor, [0.0])


class RunnerArgsTest(unittest.TestCase):
    def test_bot_task_passes_the_expected_group_size(self):
        row = SimpleNamespace(id="s1", room_name="r1", bot_identity="bot_r1", meta={"expected_humans": 3})
        self.assertEqual(runner_args_for(row, url="wss://lk", token="t").body, {"expected_humans": 3})

    def test_an_ordinary_session_has_no_expected_size(self):
        row = SimpleNamespace(id="s1", room_name="r1", bot_identity="bot_r1", meta=None)
        self.assertEqual(runner_args_for(row, url="wss://lk", token="t").body, {"expected_humans": None})


if __name__ == "__main__":
    unittest.main()
