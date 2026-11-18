"""A simulated participant's way in through a group start link (group_link.py): take a ticket,
poll, answer the ready check with a Prolific ID, receive the room. Driven against a fake of
meet's three public routes that answers in the runner's reply shapes, on a virtual clock."""
import asyncio
import json
import re
import unittest
import uuid
from pathlib import Path

from meetlab_loadtest.group_link import EntryFailed, link_settings, prolific_id, take_ticket, to_room

FIXTURES = Path(__file__).parents[3] / "tests" / "fixtures" / "matchmaking"  # the runner's contract
LIVE = ("searching", "found", "ready")


class Clock:
    """Virtual time for several participants at once: it moves only when everyone is asleep,
    to the earliest waker."""

    def __init__(self):
        self.now, self.wakes = 0.0, []

    def clock(self):
        return self.now

    async def sleep(self, s):
        wake = self.now + s
        self.wakes.append(wake)
        while self.now < wake:
            await asyncio.sleep(0)  # everyone else runs to their own sleep
            if self.now < wake == min(self.wakes):
                self.now = wake
        self.wakes.remove(wake)


class FakeLink(Clock):
    """meet's /api/start-link, /status and /ready over a small matchmaker (matchmaker.step's
    rules: first come first served, `size` at a time; a missed ready check removes the one who
    missed and requeues the rest; a wait past max_wait_s expires)."""

    def __init__(self, size, ready_check_s=60, max_wait_s=300, setup_s=0, in_use=()):
        super().__init__()
        self.size, self.ready_check_s, self.max_wait_s, self.setup_s = size, ready_check_s, max_wait_s, setup_s
        self.in_use, self.tickets, self.matches, self.calls = set(in_use), {}, {}, []

    def _step(self):
        for room, m in self.matches.items():
            group = [t for t in self.tickets.values() if t["room"] == room and t["state"] in ("found", "ready")]
            if m["starts_at"] is None and m["deadline"] <= self.now and any(t["state"] != "ready" for t in group):
                for t in group:
                    t.update({"state": "searching", "room": None, "requeued": True} if t["state"] == "ready"
                             else {"state": "removed"})
            elif m["starts_at"] is None and group and all(t["state"] == "ready" for t in group):
                m["starts_at"] = self.now
        live = [t for t in self.tickets.values() if t["state"] == "searching"]  # in queue order
        while len(live) >= self.size:
            room = f"link-fake-{len(self.matches)}"
            self.matches[room] = {"deadline": self.now + self.ready_check_s, "starts_at": None}
            for t in live[:self.size]:
                t.update(state="found", room=room)
            live = live[self.size:]
        for t in live:
            if t["queued"] <= self.now - self.max_wait_s:
                t["state"] = "expired"

    def _view(self, t):
        m = self.matches.get(t["room"])
        if t["state"] == "searching":
            return {"kind": "searching", "found": 1, "size": self.size, "requeued": t["requeued"],
                    "expires_in_ms": round((t["queued"] + self.max_wait_s - self.now) * 1000)}
        if t["state"] in ("found", "ready"):
            if m["starts_at"] is None:
                return {"kind": "ready_check", "deadline_in_ms": round((m["deadline"] - self.now) * 1000),
                        "pressed": t["state"] == "ready"}
            return {"kind": "starting", "starts_in_ms": 0,
                    "room": t["room"] if self.now >= m["starts_at"] + self.setup_s else None}
        return {"kind": {"expired": "expired", "removed": "missed"}[t["state"]]}

    async def post(self, path, body):
        self.calls.append((path, body))
        if path == "/api/start-link":
            t = {"id": str(uuid.UUID(int=len(self.tickets))), "pid": body.get("participant_id"),
                 "state": "searching", "queued": self.now, "room": None, "requeued": False}
            self.tickets[t["id"]] = t
        else:
            t = self.tickets.get(body["ticket"])
            if t is None:
                return 404, {"error": "not_found"}
            self._step()
            if path == "/api/start-link/ready" and t["state"] == "found":
                pid = body["participant_id"]
                if not re.fullmatch(r"[0-9a-f]{24}", pid):
                    return 400, {"error": "Enter your Prolific ID"}
                if pid in self.in_use or any(o["pid"] == pid and o is not t and o["state"] in LIVE
                                             for o in self.tickets.values()):
                    return 409, {"error": "id_in_use"}
                t.update(pid=pid, state="ready")
        self._step()
        return 200, {"ticket": t["id"], "link_id": "fake", "participant_id": t["pid"], "view": self._view(t)}

    async def enter(self, name, after=0.0):
        """One simulated participant, arriving `after` seconds in."""
        await self.sleep(after)
        taken = self.clock()
        reply = await take_ticket(self.post, "tok", prolific_id(name))
        return await to_room(reply, self.post, lambda n: prolific_id(name, n), self.sleep, self.clock, taken_at=taken)

    def ready_ids(self):
        return [body["participant_id"] for path, body in self.calls if path == "/api/start-link/ready"]


def run(coro):
    return asyncio.run(coro)


async def together(*coros):
    return await asyncio.gather(*coros)


class EnterTests(unittest.TestCase):
    def test_tickets_wait_the_match_forms_everyone_confirms_and_gets_the_same_room(self):
        link = FakeLink(size=3, setup_s=4)
        a, b, c = run(together(link.enter("a"), link.enter("b"), link.enter("c", after=10)))
        self.assertEqual({a["room"], b["room"], c["room"]}, {"link-fake-0"})
        self.assertEqual([p["participant_id"] for p in (a, b, c)], [prolific_id(n) for n in "abc"])
        self.assertEqual(sorted(link.ready_ids()), sorted(prolific_id(n) for n in "abc"))  # Ready once each, own ID
        # Time to match: the first two waited for the third, who was matched on arrival.
        self.assertEqual((a["match_s"], b["match_s"], c["match_s"]), (10, 10, 0))
        # Ready to room: the room takes 4 s to set up after the last Ready; seen at the next 2 s poll.
        for p in (a, b, c):
            self.assertTrue(4 <= p["ready_to_room_s"] <= 6, p)
            self.assertEqual((p["requeues"], p["id_retries"]), (0, 0))

    def test_four_people_through_one_link_fill_one_room(self):
        link = FakeLink(size=4)
        got = run(together(*(link.enter(n) for n in "abcd")))
        self.assertEqual({p["room"] for p in got}, {"link-fake-0"})
        self.assertEqual(len({p["participant_id"] for p in got}), 4)

    def test_someone_misses_the_ready_check_the_others_go_back_in_line_and_are_matched_again(self):
        link = FakeLink(size=2, ready_check_s=10)

        async def scenario():
            late = (await link.post("/api/start-link", {"token": "tok", "participant_id": prolific_id("late")}))[1]
            a, c = await together(link.enter("a"), link.enter("c", after=20))
            with self.assertRaises(EntryFailed) as missed:  # their page, when they come back to it
                await to_room(late, link.post, lambda n: prolific_id("late", n), link.sleep, link.clock)
            return a, c, str(missed.exception)
        a, c, missed = run(scenario())
        self.assertEqual(missed, "missed")
        self.assertEqual((a["room"], c["room"]), ("link-fake-1", "link-fake-1"))
        self.assertEqual((a["requeues"], c["requeues"]), (1, 0))
        self.assertEqual(a["match_s"], 20)  # measured to the match that held

    def test_a_ticket_nobody_joins_expires(self):
        link = FakeLink(size=2, max_wait_s=60)
        with self.assertRaises(EntryFailed) as e:
            run(link.enter("a"))
        self.assertEqual(str(e.exception), "expired")
        self.assertEqual(link.now, 60)

    def test_a_prolific_id_already_in_the_line_is_refused_and_retried_with_another(self):
        link = FakeLink(size=2, in_use=[prolific_id("a")])
        a, b = run(together(link.enter("a"), link.enter("b")))
        self.assertEqual(a["room"], b["room"])
        self.assertEqual((a["participant_id"], a["id_retries"]), (prolific_id("a", 1), 1))
        self.assertEqual(b["id_retries"], 0)
        self.assertEqual(sorted(link.ready_ids()), sorted([prolific_id("a"), prolific_id("a", 1), prolific_id("b")]))

    def test_no_room_by_the_deadline_fails_and_says_where_it_stopped(self):
        link = FakeLink(size=2, max_wait_s=3600)

        async def scenario():
            reply = await take_ticket(link.post, "tok", prolific_id("a"))
            return await to_room(reply, link.post, None, link.sleep, link.clock, timeout_s=30)
        with self.assertRaises(EntryFailed) as e:
            run(scenario())
        self.assertIn("still searching", str(e.exception))


def fixture(name):
    r = json.loads((FIXTURES / f"{name}.json").read_text())["response"]
    return r["status"], r["body"]


class ContractTests(unittest.TestCase):
    """The same client against the runner's own recorded replies (tests/fixtures/matchmaking),
    which meet's routes pass through unchanged."""

    def drive(self, *replies, **kw):
        script, calls, clock = iter(replies), [], Clock()

        async def post(path, body):
            calls.append((path, body))
            return next(script)

        async def scenario():
            reply = await take_ticket(post, "tok", "5f8e2a1b3c4d5e6f7a8b9c0d")
            return await to_room(reply, post, lambda n: prolific_id("x", n), clock.sleep, clock.clock, **kw)
        try:
            return run(scenario()), calls, clock.now
        except EntryFailed as e:
            return e, calls, clock.now

    def test_the_recorded_replies_lead_to_the_room_after_the_countdown(self):
        got, calls, now = self.drive(
            fixture("tickets_create_group"), fixture("tickets_poll_ready_check"), fixture("tickets_ready"),
            fixture("tickets_poll_starting_setup"), fixture("tickets_poll_starting"))
        ticket = "6f1c2b3a-4d5e-4f60-8a7b-9c0d1e2f3a4b"
        self.assertEqual(calls, [
            ("/api/start-link", {"token": "tok", "participant_id": "5f8e2a1b3c4d5e6f7a8b9c0d"}),
            ("/api/start-link/status", {"ticket": ticket}),
            ("/api/start-link/ready", {"ticket": ticket, "participant_id": "5f8e2a1b3c4d5e6f7a8b9c0d"}),
            ("/api/start-link/status", {"ticket": ticket}),
            ("/api/start-link/status", {"ticket": ticket}),
        ])
        self.assertEqual(got["room"], "link-mh0abc12-1a2b3c4d")
        self.assertEqual((got["match_s"], got["ready_to_room_s"]), (2, 4))
        self.assertEqual(now, 6 + 8)  # three polls, then the page's 8 s countdown before it goes to the room

    def test_every_ending_the_runner_can_send_is_a_failure_with_its_name(self):
        for name, why in (("tickets_poll_expired", "expired"), ("tickets_poll_missed", "missed"),
                          ("tickets_poll_done", "done"), ("tickets_poll_unknown", "ticket gone (404)")):
            got, _, _ = self.drive(fixture("tickets_create_group"), fixture(name))
            self.assertIsInstance(got, EntryFailed, name)
            self.assertEqual(str(got), why)

    def test_a_requeued_ticket_is_counted(self):
        got, _, _ = self.drive(fixture("tickets_create_group"), fixture("tickets_poll_ready_check"), fixture("tickets_ready"),
                               fixture("tickets_poll_searching_requeued"), fixture("tickets_poll_ready_check"),
                               fixture("tickets_ready"), (200, {**fixture("tickets_poll_starting")[1],
                                                                "view": {"kind": "starting", "room": "r", "starts_in_ms": 0}}))
        self.assertEqual((got["requeues"], got["room"]), (1, "r"))

    def test_a_failed_poll_is_tried_again_as_the_page_does(self):
        got, calls, _ = self.drive(fixture("tickets_create_group"), (502, {}), (429, {"error": "Too many requests"}),
                                   fixture("tickets_poll_expired"))
        self.assertEqual(str(got), "expired")
        self.assertEqual(len(calls), 4)

    def test_a_refused_join_says_why(self):
        for name, status in (("tickets_create_closed", 410), ("tickets_create_wrong_password", 403)):
            got, _, _ = self.drive(fixture(name))
            self.assertIn(f"join refused ({status})", str(got))
        got, _, _ = self.drive((429, {"error": "Too many meetings started — try again shortly"}))
        self.assertEqual(str(got), "join refused (429): Too many meetings started — try again shortly")

    def test_a_size_1_link_is_not_a_group_link(self):
        got, _, _ = self.drive((200, {"url": "/rooms/r", "roomName": "r"}))  # meet's reply for size 1
        self.assertIn("no ticket", str(got))


class IdsAndSettingsTests(unittest.TestCase):
    def test_prolific_ids_are_24_lowercase_hex_distinct_and_the_same_every_run(self):
        ids = {prolific_id("run", room, person) for room in range(100) for person in range(4)}
        self.assertEqual(len(ids), 400)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{24}", i) for i in ids))
        self.assertEqual(prolific_id("run", 3, 1), prolific_id("run", 3, 1))
        self.assertNotEqual(prolific_id("run", 3, 1), prolific_id("run", 3, 1, 1))  # the retry after a 409

    def test_the_link_is_one_full_group_per_room_on_the_runs_settings(self):
        s = link_settings("ab12cd", 4)
        self.assertEqual((s["size"], s["min_size"], s["pool"]), (4, 4, ["load-ab12cd"]))
        # within meet's limits (lib/start-link.ts linkSettings)
        self.assertTrue(0 <= s["delay_s"] <= 300 and 60 <= s["max_wait_s"] <= 1800 and 10 <= s["ready_check_s"] <= 300)
        for size in (1, 9):
            with self.assertRaises(ValueError):
                link_settings("ab12cd", size)


if __name__ == "__main__":
    unittest.main()
