"""A bot that dies mid-meeting rejoins with context (rejoin.py; the user's decision, 2026-10-05).

When a running bot goes silent (no heartbeat) or crashes while people are still in the room,
the runner starts a new bot for the room that continues the conversation from the stored turns.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Row

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rejoin import MAX_REJOINS, WINDOW, chain, context_messages, rejoin_due  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def session(sid, ended_by="silent", ago=timedelta(seconds=20), resumes=None):
    meta = {"ended_by": ended_by} if ended_by else {}
    if resumes:
        meta["resumes"] = resumes
    return Row(id=sid, room_name="r1", status="error", ended_at=NOW - ago, meta=meta)


class TestDue(unittest.TestCase):
    def test_a_bot_that_died_with_people_in_the_room_rejoins(self):
        self.assertTrue(rejoin_due(session("s1"), chain_length=1, humans=2, room_running=False, now=NOW))
        self.assertTrue(rejoin_due(session("s1", "crashed"), chain_length=1, humans=1, room_running=False, now=NOW))

    def test_only_deaths_rejoin(self):
        # Stopped, finished, room gone or never dispatched: nobody wants that bot back.
        for event in ("stopped", "finished", "room_gone", "dispatch_failed", None):
            self.assertFalse(rejoin_due(session("s1", event), 1, 2, False, NOW), event)

    def test_an_empty_room_or_a_room_with_a_bot_gets_none(self):
        self.assertFalse(rejoin_due(session("s1"), 1, humans=0, room_running=False, now=NOW))
        self.assertFalse(rejoin_due(session("s1"), 1, humans=2, room_running=True, now=NOW))

    def test_an_old_death_is_left_alone(self):
        self.assertFalse(rejoin_due(session("s1", ago=WINDOW + timedelta(seconds=1)), 1, 2, False, NOW))

    def test_a_bot_that_keeps_dying_stops_being_restarted(self):
        self.assertTrue(rejoin_due(session("s1"), MAX_REJOINS, 2, False, NOW))
        self.assertFalse(rejoin_due(session("s1"), MAX_REJOINS + 1, 2, False, NOW))


class TestChain(unittest.TestCase):
    def test_the_sessions_of_one_conversation_oldest_first(self):
        rows = {r.id: r for r in (session("a"), session("b", resumes="a"), session("c", resumes="b"))}
        self.assertEqual(chain(rows, "c"), ["a", "b", "c"])
        self.assertEqual(chain(rows, "a"), ["a"])

    def test_a_missing_link_ends_the_chain(self):
        self.assertEqual(chain({"b": session("b", resumes="gone")}, "b"), ["b"])


class TestContext(unittest.TestCase):
    def test_turns_come_back_as_the_llm_saw_them(self):
        turns = [Row(speaker_id="ana__x1", text="Hi, I'm Ana."), Row(speaker_id="bot_r1_1", text="Hello Ana!"),
                 Row(speaker_id="ben__x2", text="And I'm Ben."), Row(speaker_id="bot_r1_2", text="Hi Ben.")]
        self.assertEqual(context_messages(turns, names={"ana__x1": "Ana"}), [
            {"role": "user", "content": "Ana: Hi, I'm Ana."},
            {"role": "assistant", "content": "Hello Ana!"},
            {"role": "user", "content": "ben: And I'm Ben."},  # no stored name: the identity, as live
            {"role": "assistant", "content": "Hi Ben."},
        ])


if __name__ == "__main__":
    unittest.main()
