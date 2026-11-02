"""A group start link's ticket, driven to its room as the waiting page does (acceptance.ticket_to_room):
poll, press Ready once in the ready check, go when the room is set; a dead end or the deadline is a Fail.
The replies are the runner's recorded contract (tests/fixtures/matchmaking)."""
import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from acceptance import Fail, ticket_to_room  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "matchmaking")


def reply(name):
    with open(os.path.join(FIXTURES, f"{name}.json")) as f:
        return json.load(f)["response"]["body"]


class Meet:
    """meet's /api/start-link/status and /ready, answering from a script of fixture names."""

    def __init__(self, polls, ready="tickets_ready"):
        self.polls, self.ready, self.calls = list(polls), ready, []

    def __call__(self, path, body):
        self.calls.append((path, body["ticket"]))
        return reply(self.ready if path.endswith("/ready") else self.polls.pop(0))


def drive(meet, first="tickets_create_group", timeout=30, clock=None):
    async def no_sleep(_):
        pass
    kw = {"clock": clock} if clock else {}
    return asyncio.run(ticket_to_room(reply(first), meet, timeout, sleep=no_sleep, **kw))


class TestTicketToRoom(unittest.TestCase):
    def test_searching_then_ready_check_then_the_room(self):
        meet = Meet(["tickets_poll_searching_requeued", "tickets_poll_ready_check", "tickets_poll_ready_check",
                     "tickets_poll_starting_setup", "tickets_poll_starting"])
        self.assertEqual(drive(meet), "link-mh0abc12-1a2b3c4d")
        paths = [p for p, _ in meet.calls]
        self.assertEqual(paths.count("/api/start-link/ready"), 1, "Ready is pressed once, not on every poll")
        self.assertEqual({t for _, t in meet.calls}, {"6f1c2b3a-4d5e-4f60-8a7b-9c0d1e2f3a4b"})

    def test_a_room_in_the_join_reply_needs_no_poll(self):
        meet = Meet([])
        self.assertEqual(drive(meet, first="tickets_poll_starting"), "link-mh0abc12-1a2b3c4d")
        self.assertEqual(meet.calls, [])

    def test_a_dead_end_fails_with_its_kind(self):
        for end in ("tickets_poll_expired", "tickets_poll_missed", "tickets_poll_done"):
            with self.subTest(end), self.assertRaises(Fail) as e:
                drive(Meet([end]))
            self.assertIn(reply(end)["view"]["kind"], str(e.exception))

    def test_no_room_by_the_deadline_fails(self):
        ticks = iter(range(0, 1000, 10))
        with self.assertRaises(Fail) as e:
            drive(Meet(["tickets_poll_searching_requeued"] * 100), timeout=30, clock=lambda: next(ticks))
        self.assertIn("searching", str(e.exception))


if __name__ == "__main__":
    unittest.main()
