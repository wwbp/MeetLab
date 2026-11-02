"""The matchmaker's rules (pure: no DB, no clock, no randomness). One test per rule of step(),
then ticket_view and pick_setting. Runs without the stack:
    python -m unittest tests.test_matchmaker -v   (from services/api)
"""
import itertools
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from meetlab_api.matchmaker import (
    CreateMatch, Event, MatchState, StopRoom, TicketState, UpdateMatch, UpdateTicket,
    pick_setting, step, ticket_view,
)

T0 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


def s(n):
    return timedelta(seconds=n)


def link(size=3, min_size=None, delay_s=10, max_wait_s=300):
    return SimpleNamespace(size=size, min_size=size if min_size is None else min_size,
                           delay_s=delay_s, max_wait_s=max_wait_s)


def ticket(id, state="searching", queued=T0, seen=None, room=None, ready_at=None, requeued_at=None):
    return TicketState(id=id, state=state, queued_at=queued, last_seen=seen or queued,
                       room_name=room, ready_at=ready_at, requeued_at=requeued_at)


def match(room="r1", deadline=None, starts_at=None, session_id=None, ended=None):
    return MatchState(room_name=room, ready_deadline=deadline, starts_at=starts_at,
                      session_id=session_id, session_ended_at=ended)


def rooms():
    """new_match, deterministic: r-1, r-2, … all with scope "friendly"."""
    counter = itertools.count(1)
    return lambda: (f"r-{next(counter)}", "friendly")


def run(lnk, tickets, matches=(), now=T0):
    return step(lnk, list(tickets), list(matches), now, rooms())


def ticket_updates(changes):
    """Ticket id → its fields, the later change winning (as the caller applies them)."""
    out = {}
    for c in changes:
        if isinstance(c, UpdateTicket):
            out.setdefault(c.id, {}).update(c.fields)
    return out


def of(changes, kind):
    return [c for c in changes if isinstance(c, kind)]


class MatchRuleTest(unittest.TestCase):
    """Rule 5: live searching tickets, oldest first, `size` at a time."""

    def test_no_match_below_size(self):
        self.assertEqual(run(link(3), [ticket("a"), ticket("b")]), [])

    def test_oldest_size_form_a_match(self):
        tickets = [ticket(i, queued=T0 + s(n)) for n, i in enumerate("abcd")]
        changes = run(link(3, delay_s=10), tickets, now=T0 + s(5))
        [m] = of(changes, CreateMatch)
        self.assertEqual(m, CreateMatch("r-1", "friendly", 3, T0 + s(5 + 20), None, ("a", "b", "c")))
        self.assertEqual(ticket_updates(changes),
                         {i: {"state": "found", "room_name": "r-1"} for i in "abc"})
        self.assertEqual(of(changes, Event), [Event("match_formed", {"room": "r-1", "size": 3, "scope": "friendly",
                                                                     "tickets": ["a", "b", "c"]})])

    def test_never_more_than_size_and_several_at_once(self):
        tickets = [ticket(f"t{n:02}", queued=T0 + s(n)) for n in range(7)]
        changes = run(link(3), tickets, now=T0 + s(10))
        self.assertEqual([m.members for m in of(changes, CreateMatch)],
                         [("t00", "t01", "t02"), ("t03", "t04", "t05")])
        self.assertNotIn("t06", ticket_updates(changes))

    def test_fifo_ties_broken_by_ticket_id(self):
        tickets = [ticket("c"), ticket("a"), ticket("b"), ticket("d")]
        [m] = of(run(link(3), tickets), CreateMatch)
        self.assertEqual(m.members, ("a", "b", "c"))

    def test_size_one_starts_at_once_without_ready_check(self):
        changes = run(link(1, delay_s=0), [ticket("a")], now=T0 + s(1))
        [m] = of(changes, CreateMatch)
        self.assertEqual((m.size, m.ready_deadline, m.starts_at), (1, None, T0 + s(1)))

    def test_size_one_with_a_delay(self):
        [m] = of(run(link(1, delay_s=30), [ticket("a")]), CreateMatch)
        self.assertEqual(m.starts_at, T0 + s(30))

    def test_non_live_ticket_is_not_matched_nor_removed(self):
        stale = ticket("a", seen=T0)
        changes = run(link(2), [stale, ticket("b", queued=T0 + s(100))], now=T0 + s(100))
        self.assertEqual(changes, [])

    def test_revived_ticket_keeps_its_queue_time(self):
        revived = ticket("z", queued=T0, seen=T0 + s(200))  # silent a while, then polled again
        newer = [ticket("b", queued=T0 + s(150), seen=T0 + s(200)), ticket("c", queued=T0 + s(160), seen=T0 + s(200))]
        [m] = of(run(link(2), [*newer, revived], now=T0 + s(200)), CreateMatch)
        self.assertEqual(m.members, ("z", "b"))

    def test_polled_every_60s_survives_a_ten_minute_wait(self):
        lnk = link(2, max_wait_s=30 * 60)
        a = ticket("a")
        for minute in range(1, 11):
            now = T0 + s(60 * minute)
            a = ticket("a", seen=now)
            self.assertEqual(run(lnk, [a], now=now), [], minute)
        [m] = of(run(lnk, [a, ticket("b", queued=T0 + s(600))], now=T0 + s(600)), CreateMatch)
        self.assertEqual(m.members, ("a", "b"))

    def test_only_searching_tickets_are_matched(self):
        tickets = [ticket("a"), ticket("b", state="found", room="r0"), ticket("c")]
        self.assertEqual(run(link(3), tickets, [match("r0", deadline=T0 + s(20))]), [])


class RelaxationRuleTest(unittest.TestCase):
    """Rule 5, R17: past max wait, start with at least min size."""

    def test_relaxes_to_what_is_there_once_the_oldest_waited_max_wait(self):
        lnk = link(4, min_size=2, max_wait_s=300)
        tickets = [ticket("a"), ticket("b", queued=T0 + s(100), seen=T0 + s(299)),
                   ticket("c", queued=T0 + s(200), seen=T0 + s(299))]
        self.assertEqual(run(lnk, tickets, now=T0 + s(299)), [])
        changes = run(lnk, [ticket("a", seen=T0 + s(300)), *tickets[1:]], now=T0 + s(300))
        [m] = of(changes, CreateMatch)
        self.assertEqual((m.members, m.size), (("a", "b", "c"), 3))
        self.assertEqual(of(changes, Event)[0].name, "match_formed")

    def test_no_relaxation_below_min_size(self):
        lnk = link(4, min_size=3, max_wait_s=300)
        tickets = [ticket("a", seen=T0 + s(300)), ticket("b", queued=T0 + s(10), seen=T0 + s(300))]
        self.assertEqual(of(run(lnk, tickets, now=T0 + s(300)), CreateMatch), [])


class ExpireRuleTest(unittest.TestCase):
    """Rule 6: a searching ticket past max wait, live or not, expires."""

    def test_expires_live_and_non_live(self):
        lnk = link(3, max_wait_s=300)
        tickets = [ticket("a", seen=T0 + s(300)), ticket("b"), ticket("c", queued=T0 + s(1), seen=T0 + s(300))]
        changes = run(lnk, tickets, now=T0 + s(300))
        self.assertEqual(ticket_updates(changes), {"a": {"state": "expired"}, "b": {"state": "expired"}})
        self.assertEqual(of(changes, Event), [Event("ticket_expired", {"ticket": "a"}),
                                              Event("ticket_expired", {"ticket": "b"})])

    def test_not_before_max_wait(self):
        self.assertEqual(run(link(3, max_wait_s=300), [ticket("a", seen=T0 + s(299))], now=T0 + s(299)), [])


class ReadyCheckRuleTest(unittest.TestCase):
    """Rule 1: at the deadline, non-ready members are removed and the ready ones requeued."""

    def test_failed_ready_check(self):
        deadline = T0 + s(20)
        tickets = [ticket("a", "ready", room="r0", ready_at=T0 + s(3)), ticket("b", "found", room="r0"),
                   ticket("c", "ready", queued=T0 + s(1), room="r0", ready_at=T0 + s(4))]
        changes = run(link(3), tickets, [match("r0", deadline=deadline, session_id="s1")], now=deadline)
        requeue = {"state": "searching", "room_name": None, "ready_at": None, "requeued_at": deadline}
        self.assertEqual(ticket_updates(changes), {"a": requeue, "b": {"state": "removed"}, "c": requeue})
        self.assertIn(UpdateMatch("r0", {"closed_at": deadline}), changes)
        self.assertIn(StopRoom("r0"), changes)
        self.assertIn(Event("ready_failed", {"room": "r0", "removed": ["b"], "requeued": ["a", "c"]}), changes)

    def test_requeued_members_keep_their_queue_time_and_are_matched_again(self):
        deadline = T0 + s(20)
        tickets = [ticket("a", "ready", room="r0", seen=deadline), ticket("b", "found", room="r0"),
                   ticket("n", queued=T0 + s(15), seen=deadline)]
        changes = run(link(2), tickets, [match("r0", deadline=deadline)], now=deadline)
        [m] = of(changes, CreateMatch)
        self.assertEqual(m.members, ("a", "n"))

    def test_not_before_the_deadline(self):
        tickets = [ticket("a", "ready", room="r0"), ticket("b", "found", room="r0")]
        self.assertEqual(run(link(2), tickets, [match("r0", deadline=T0 + s(20))], now=T0 + s(19)), [])

    def test_never_touches_a_started_match(self):
        tickets = [ticket("a", "placed", room="r0"), ticket("b", "found", room="r0")]
        m = match("r0", deadline=T0 + s(20), starts_at=T0 + s(30), session_id=None)
        self.assertEqual(run(link(2), tickets, [m], now=T0 + s(25)), [])

    def test_never_touches_other_rooms_or_searching_tickets(self):
        deadline = T0 + s(20)
        tickets = [ticket("a", "found", room="r0"), ticket("b", "found", room="r0"),
                   ticket("x", "ready", room="r9"), ticket("y", "placed", room="r8"), ticket("s", seen=deadline)]
        changes = run(link(2), tickets, [match("r0", deadline=deadline), match("r9", deadline=T0 + s(40)),
                                        match("r8", starts_at=T0, session_id="s8")], now=deadline)
        self.assertEqual(set(ticket_updates(changes)), {"a", "b"})


class AllReadyRuleTest(unittest.TestCase):
    """Rule 2: every member ready → the countdown starts."""

    def test_all_ready_sets_starts_at(self):
        tickets = [ticket("a", "ready", room="r0"), ticket("b", "ready", room="r0")]
        changes = run(link(2, delay_s=10), tickets, [match("r0", deadline=T0 + s(20))], now=T0 + s(5))
        self.assertEqual(changes, [UpdateMatch("r0", {"starts_at": T0 + s(15)})])

    def test_needs_every_member(self):
        tickets = [ticket("a", "ready", room="r0"), ticket("b", "found", room="r0")]
        self.assertEqual(run(link(2), tickets, [match("r0", deadline=T0 + s(20))], now=T0 + s(5)), [])


class PlacedRuleTest(unittest.TestCase):
    """Rule 3: at starts_at, once the room is set up, members are placed."""

    def test_placed_at_start_with_a_session(self):
        tickets = [ticket("a", "ready", room="r0"), ticket("b", "ready", room="r0")]
        changes = run(link(2), tickets, [match("r0", starts_at=T0 + s(30), session_id="s1")], now=T0 + s(30))
        self.assertEqual(ticket_updates(changes), {i: {"state": "placed", "placed_at": T0 + s(30)} for i in "ab"})

    def test_not_without_a_session(self):
        tickets = [ticket("a", "ready", room="r0")]
        self.assertEqual(run(link(1), tickets, [match("r0", starts_at=T0)], now=T0 + s(5)), [])

    def test_not_before_starts_at(self):
        tickets = [ticket("a", "ready", room="r0")]
        self.assertEqual(run(link(1), tickets, [match("r0", starts_at=T0 + s(9), session_id="s1")], now=T0 + s(5)), [])

    def test_size_one_found_ticket_is_placed(self):
        changes = run(link(1, delay_s=0), [ticket("a", "found", room="r0")], [match("r0", starts_at=T0, session_id="s")])
        self.assertEqual(ticket_updates(changes), {"a": {"state": "placed", "placed_at": T0}})


class DoneRuleTest(unittest.TestCase):
    """Rule 4: 120 s after the room's last session ended, placed tickets are done."""

    def test_done_after_120s(self):
        m = match("r0", starts_at=T0, session_id="s1", ended=T0 + s(600))
        tickets = [ticket("a", "placed", room="r0"), ticket("b", "placed", room="r0")]
        self.assertEqual(run(link(2), tickets, [m], now=T0 + s(719)), [])
        changes = run(link(2), tickets, [m], now=T0 + s(720))
        self.assertEqual(ticket_updates(changes), {"a": {"state": "done"}, "b": {"state": "done"}})
        self.assertIn(UpdateMatch("r0", {"closed_at": T0 + s(720)}), changes)

    def test_not_while_a_session_runs(self):
        m = match("r0", starts_at=T0, session_id="s1", ended=None)
        self.assertEqual(run(link(1), [ticket("a", "placed", room="r0")], [m], now=T0 + s(9999)), [])


class StepPurityTest(unittest.TestCase):
    def test_same_inputs_same_changes(self):
        tickets = [ticket(i, queued=T0 + s(n)) for n, i in enumerate("abcdef")]
        self.assertEqual(run(link(3), tickets), run(link(3), tickets))

    def test_inputs_are_not_mutated(self):
        tickets = [ticket("a"), ticket("b")]
        before = list(tickets)
        run(link(2), tickets)
        self.assertEqual(tickets, before)


class TicketViewTest(unittest.TestCase):
    def view(self, t, m=None, live=1, lnk=None, now=T0):
        return ticket_view(t, m, live, lnk or link(3, max_wait_s=300), now)

    def test_searching(self):
        self.assertEqual(self.view(ticket("a"), live=2, now=T0 + s(118)),
                         {"kind": "searching", "found": 2, "size": 3, "expires_in_ms": 182000, "requeued": False})
        self.assertTrue(self.view(ticket("a", requeued_at=T0))["requeued"])
        self.assertEqual(self.view(ticket("a"), now=T0 + s(400))["expires_in_ms"], 0)

    def test_ready_check(self):
        m = match("r0", deadline=T0 + s(20))
        self.assertEqual(self.view(ticket("a", "found", room="r0"), m, now=T0 + s(3)),
                         {"kind": "ready_check", "deadline_in_ms": 17000, "pressed": False})
        self.assertTrue(self.view(ticket("a", "ready", room="r0"), m, now=T0 + s(3))["pressed"])

    def test_starting(self):
        set_up = match("r0", starts_at=T0 + s(10), session_id="s1")
        self.assertEqual(self.view(ticket("a", "ready", room="r0"), set_up, now=T0 + s(2)),
                         {"kind": "starting", "room": "r0", "starts_in_ms": 8000})
        pending = match("r0", starts_at=T0 + s(10))
        self.assertEqual(self.view(ticket("a", "ready", room="r0"), pending, now=T0 + s(1)),
                         {"kind": "starting", "room": None, "starts_in_ms": 9000})
        self.assertEqual(self.view(ticket("a", "placed", room="r0"), set_up, now=T0 + s(99)),
                         {"kind": "starting", "room": "r0", "starts_in_ms": 0})

    def test_terminal_views(self):
        self.assertEqual(self.view(ticket("a", "expired")), {"kind": "expired"})
        self.assertEqual(self.view(ticket("a", "removed", room="r0")), {"kind": "missed"})
        self.assertEqual(self.view(ticket("a", "done", room="r0")), {"kind": "done"})


class PickSettingTest(unittest.TestCase):
    def test_least_used_wins(self):
        self.assertEqual(pick_setting(["a", "b", "c"], {"a": 2, "b": 1, "c": 2}, lambda xs: xs[0]), "b")

    def test_unused_counts_as_zero_and_ties_go_to_choice(self):
        seen = []
        pick_setting(["a", "b", "c"], {"a": 1}, lambda xs: seen.append(xs) or xs[-1])
        self.assertEqual(seen, [["b", "c"]])


if __name__ == "__main__":
    unittest.main()
