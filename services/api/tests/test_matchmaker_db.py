"""The matchmaker against real Postgres: the per-link lock across engines, setup exactly once,
retry, the ticket lifecycle, and the /tickets endpoints against tests/fixtures/matchmaking.
Bots are not started (the Docker dispatch and the LiveKit room are mocked). Needs the stack's
Postgres at head:
    docker compose -f .devcontainer/docker-compose.yml run --rm --no-deps -T \\
        -w /app/services/api tests python -m unittest tests.test_matchmaker_db -v
"""
import asyncio
import json
import os
import pathlib
import unittest
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.test_group_links_contract import FIXTURES, _env

_env()
from meetlab_api import matchmaker  # noqa: E402
from meetlab_api.dispatch import DispatchError  # noqa: E402
from meetlab_api.links import hash_password  # noqa: E402
from meetlab_core.db.models import Conversation, Match, StartLink, Ticket  # noqa: E402


def _pid():
    return uuid.uuid4().hex[:24]


class _DbCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = mock.patch.dict(os.environ, {"BOT_DISPATCHER": "docker", "DOCKER_HOST": "tcp://docker-socket-proxy:2375"})
        cls._env.start()
        from meetlab_api import runner
        cls.runner = runner
        cls._run = mock.patch.object(runner, "run_bot_container", return_value="meetlab-bot-test")
        cls.run_bot = cls._run.start()
        cls._stop = mock.patch.object(runner, "stop_bot_container")
        cls._stop.start()
        cls._room = mock.patch.object(runner, "_create_livekit_room", new=mock.AsyncMock())
        cls._room.start()
        runner.engine.sync_engine.dispose(close=False)
        cls._client = TestClient(runner.app)
        cls.client = cls._client.__enter__()
        cls.auth = {"Authorization": f"Bearer {runner.BOT_RUNNER_SECRET}"}
        cls.hooks = SimpleNamespace(setup_match=runner.setup_match, stop_room=runner.stop_room)

    @classmethod
    def tearDownClass(cls):
        cls.client.portal.call(cls.runner.engine.dispose)
        cls._client.__exit__(None, None, None)
        for p in (cls._room, cls._stop, cls._run, cls._env):
            p.stop()

    # ── helpers ────────────────────────────────────────────────────────────

    def _call(self, fn, *args):
        async def go():
            return await fn(*args)
        return self.client.portal.call(go)

    def _db(self, fn):
        async def go():
            async with self.runner.AsyncSessionLocal() as db, db.begin():
                return await fn(db)
        return self.client.portal.call(go)

    def _sql(self, sql, **params):
        return self._db(lambda db: db.execute(text(sql), params))

    def _link(self, size=2, delay_s=0, max_wait_s=300, password=None, active=True, pool=("global",)):
        link_id = uuid.uuid4().hex[:22]

        async def add(db):
            db.add(StartLink(link_id=link_id, name="test", pool=list(pool), size=size, min_size=size,
                             delay_s=delay_s, max_wait_s=max_wait_s, active=active,
                             password_hash=password and hash_password(password)))
        self._db(add)
        return link_id

    def _create(self, link_id, pid, hooks=None, **body):
        return self._call(matchmaker.create_ticket, hooks or self.hooks,
                          {"link_id": link_id, "participant_id": pid, **body})

    def _poll(self, ticket, hooks=None):
        return self._call(matchmaker.poll_ticket, hooks or self.hooks, ticket)

    def _ready(self, ticket):
        return self._call(matchmaker.ready_ticket, self.hooks, ticket)

    def _tickets(self, link_id):
        return self._db(lambda db: db.scalars(select(Ticket).where(Ticket.link_id == link_id)
                                              .order_by(Ticket.queued_at))).all()

    def _matches(self, link_id):
        return self._db(lambda db: db.scalars(select(Match).where(Match.link_id == link_id))).all()

    def _conversations(self, room, running_only=False):
        q = select(Conversation).where(Conversation.room_name == room)
        if running_only:
            q = q.where(Conversation.status == "running")
        return self._db(lambda db: db.scalars(q)).all()

    @staticmethod
    def _no_setup():
        return SimpleNamespace(setup_match=mock.AsyncMock(), stop_room=mock.AsyncMock())


class MatchmakerDbTest(_DbCase):
    # ── the lock ───────────────────────────────────────────────────────────

    def test_30_concurrent_tickets_through_two_engines_make_10_matches_of_3(self):
        link_id = self._link(size=3)
        other = create_async_engine(self.runner.engine.url, pool_size=15)
        engines = [self.runner.AsyncSessionLocal, async_sessionmaker(other, expire_on_commit=False)]
        hooks = self._no_setup()

        async def go():
            try:
                return await asyncio.gather(*(
                    matchmaker.create_ticket(hooks, {"link_id": link_id, "participant_id": _pid()}, engines[n % 2])
                    for n in range(30)))
            finally:
                await other.dispose()
        replies = self.client.portal.call(go)
        self.assertTrue(all(isinstance(r, dict) for r in replies), replies)
        matches = self._matches(link_id)
        self.assertEqual(len(matches), 10)
        by_room = {}
        for t in self._tickets(link_id):
            self.assertEqual(t.state, "found")
            by_room.setdefault(t.room_name, []).append(t)
        self.assertEqual(sorted(len(v) for v in by_room.values()), [3] * 10)
        self.assertEqual(set(by_room), {m.room_name for m in matches})

    # ── setup ──────────────────────────────────────────────────────────────

    def _formed_match(self, size=2, **link):
        """A match of `size` whose room is not set up yet."""
        link_id = self._link(size=size, **link)
        replies = [self._create(link_id, _pid(), hooks=self._no_setup()) for _ in range(size)]
        [m] = self._matches(link_id)
        self.assertIsNone(m.session_id)
        return link_id, m.room_name, [r["ticket"] for r in replies]

    def test_three_concurrent_polls_on_a_new_match_start_one_bot(self):
        _, room, tickets = self._formed_match(size=3)

        async def go():
            return await asyncio.gather(*(matchmaker.poll_ticket(self.hooks, t) for t in tickets))
        self.client.portal.call(go)
        self.assertEqual(len(self._conversations(room)), 1)

    def test_poll_after_the_session_ended_starts_no_new_bot(self):
        link_id, room, [a, b] = self._formed_match()
        self._ready(a)
        reply = self._ready(b)
        self.assertEqual(reply["view"], {"kind": "starting", "room": room, "starts_in_ms": 0})
        self._call(self.runner.stop_room, room)
        self._poll(a)
        self.assertEqual(len(self._conversations(room)), 1)
        self.assertEqual([t.state for t in self._tickets(link_id)], ["placed", "placed"])

    def test_a_failed_setup_is_completed_by_a_later_poll_with_the_same_setting(self):
        link_id, room, [a, b] = self._formed_match(pool=("global", "other-scope"))
        [m] = self._matches(link_id)
        self.run_bot.side_effect = [DispatchError("no capacity")]
        try:
            self._poll(a)
        finally:
            self.run_bot.side_effect = None
        self.assertEqual(self._conversations(room, running_only=True), [])
        self._sql("UPDATE matches SET setup_claimed_at = now() - interval '31 seconds' WHERE room_name = :r", r=room)
        self._poll(b)
        [conv] = self._conversations(room, running_only=True)
        self.assertEqual(conv.meta["bot_config_scope"], m.bot_config_scope)
        [after] = self._matches(link_id)
        self.assertEqual((after.bot_config_scope, after.session_id), (m.bot_config_scope, conv.id))

    def test_failed_ready_check_stops_the_room_and_requeues(self):
        link_id, room, [a, b] = self._formed_match()
        self._poll(a)  # sets the room up
        self.assertEqual(len(self._conversations(room, running_only=True)), 1)
        self._ready(a)
        self._sql("UPDATE matches SET ready_deadline = now() - interval '1 second' WHERE room_name = :r", r=room)
        reply = self._poll(a)
        self.assertEqual(reply["view"]["kind"], "searching")
        self.assertTrue(reply["view"]["requeued"])
        self.assertEqual(self._poll(b)["view"], {"kind": "missed"})
        self.assertEqual(self._conversations(room, running_only=True), [])
        [m] = self._matches(link_id)
        self.assertIsNotNone(m.closed_at)
        events = self._db(lambda db: db.scalars(text("SELECT type FROM events WHERE room_name = :r")
                                                .bindparams(r=room))).all()
        self.assertIn("ready_failed", events)

    # ── the ticket lifecycle ───────────────────────────────────────────────

    def test_re_post_for_a_removed_ticket_is_a_new_ticket(self):
        link_id, room, [a, b] = self._formed_match()
        pid_b = self._tickets(link_id)[1].participant_id
        self._ready(a)
        self._sql("UPDATE matches SET ready_deadline = now() - interval '1 second' WHERE room_name = :r", r=room)
        self._poll(a)
        reply = self._create(link_id, pid_b)
        self.assertNotEqual(reply["ticket"], b)
        self.assertEqual(reply["view"]["kind"], "ready_check")  # with a, requeued: a new match
        states = {t.id: t.state for t in self._tickets(link_id)}
        self.assertEqual(states, {a: "found", b: "removed", reply["ticket"]: "found"})

    def test_re_post_after_done_keeps_both_rows(self):
        link_id, pid = self._link(size=1), _pid()
        first = self._create(link_id, pid)
        room = first["view"]["room"]
        self.assertEqual(first["view"], {"kind": "starting", "room": room, "starts_in_ms": 0})
        self.assertEqual(self._create(link_id, pid)["ticket"], first["ticket"])  # still placed: same ticket
        self._call(self.runner.stop_room, room)
        self._sql("UPDATE conversations SET ended_at = now() - interval '121 seconds' WHERE room_name = :r", r=room)
        second = self._create(link_id, pid)
        self.assertNotEqual(second["ticket"], first["ticket"])
        rows = {t.id: t for t in self._tickets(link_id)}
        self.assertEqual((rows[first["ticket"]].state, rows[first["ticket"]].room_name), ("done", room))
        self.assertEqual(rows[second["ticket"]].state, "placed")

    def test_a_stopped_link_returns_an_existing_ticket_and_refuses_new_ones(self):
        link_id = self._link(size=3)
        pid = _pid()
        first = self._create(link_id, pid)
        self._sql("UPDATE start_links SET active = false WHERE link_id = :l", l=link_id)
        self.assertEqual(self._create(link_id, pid)["ticket"], first["ticket"])
        closed = self._create(link_id, _pid())
        self.assertEqual((closed.status_code, json.loads(closed.body)), (410, {"error": "closed"}))

    def test_the_assignment_record_joins_ticket_match_session_and_config(self):
        """R43: who was in which room with which bot setting, from one SQL join."""
        link_id, room, [a, b] = self._formed_match()
        self._poll(a)
        rows = self._sql(
            "SELECT t.participant_id, t.room_name, m.bot_config_scope, c.meta->>'bot_config_scope', b.scope"
            " FROM tickets t JOIN matches m ON m.room_name = t.room_name"
            " JOIN conversations c ON c.room_name = m.room_name JOIN bot_config b ON b.scope = m.room_name"
            " WHERE t.link_id = :l ORDER BY t.queued_at", l=link_id).all()
        pids = [t.participant_id for t in self._tickets(link_id)]
        self.assertEqual(rows, [(p, room, "global", "global", room) for p in pids])

    def test_balanced_assignment_over_matches(self):
        link_id = self._link(size=1, pool=("global", "other-scope"))
        hooks = self._no_setup()
        for _ in range(4):
            self._create(link_id, None, hooks=hooks)
        counts = sorted(m.bot_config_scope for m in self._matches(link_id))
        self.assertEqual(counts, ["global", "global", "other-scope", "other-scope"])

    def test_expired_ticket_is_recorded(self):
        link_id = self._link(size=3, max_wait_s=60)
        reply = self._create(link_id, _pid())
        self._sql("UPDATE tickets SET queued_at = now() - interval '61 seconds' WHERE id = :t", t=reply["ticket"])
        self.assertEqual(self._poll(reply["ticket"])["view"], {"kind": "expired"})
        events = self._db(lambda db: db.scalars(text("SELECT payload->>'ticket' FROM events WHERE type = 'ticket_expired'"))).all()
        self.assertIn(reply["ticket"], events)


class TicketEndpointsTest(_DbCase):
    """The /tickets routes, behind the runner's secret, against the fixtures' shapes."""

    def _fixture(self, name):
        return json.loads((FIXTURES / name).read_text())

    def _post(self, path, body=None, auth=True):
        return self.client.post(path, json=body, headers=self.auth if auth else {})

    def assertLike(self, reply, fixture):
        """Same status; same error, or same keys and view kind/fields as the fixture."""
        want = fixture["response"]
        self.assertEqual(reply.status_code, want["status"], reply.text)
        body = reply.json()
        if "error" in want["body"]:
            self.assertEqual(body, want["body"])
        else:
            self.assertEqual(set(body), set(want["body"]))
            self.assertEqual(set(body["view"]), set(want["body"]["view"]))
            self.assertEqual(body["view"]["kind"], want["body"]["view"]["kind"])

    def test_every_route_needs_the_secret(self):
        for path in ("/tickets", f"/tickets/{uuid.uuid4()}/poll", f"/tickets/{uuid.uuid4()}/ready"):
            self.assertEqual(self._post(path, {}, auth=False).status_code, 401, path)

    def test_create_group(self):
        link_id = self._link(size=3, password="blue-river")
        f = self._fixture("tickets_create_group.json")
        reply = self._post("/tickets", {**f["request"]["body"], "link_id": link_id})
        self.assertLike(reply, f)
        self.assertEqual(reply.json()["view"], f["response"]["body"]["view"])

    def test_create_size1_legacy(self):
        f = self._fixture("tickets_create_size1.json")
        link_id = uuid.uuid4().hex[:22]
        reply = self._post("/tickets", {**f["request"]["body"], "link_id": link_id})
        self.assertLike(reply, f)
        self.assertEqual(reply.json()["view"]["starts_in_ms"], 0)
        self.assertRegex(reply.json()["view"]["room"], r"^link-[0-9a-z]+-[0-9a-f]{8}$")
        row = self._db(lambda db: db.get(StartLink, link_id))
        self.assertTrue(row.legacy)

    def test_create_errors(self):
        unknown = self._fixture("tickets_create_unknown.json")
        self.assertLike(self._post("/tickets", unknown["request"]["body"]), unknown)
        wrong = self._fixture("tickets_create_wrong_password.json")
        link_id = self._link(size=3, password="blue-river")
        self.assertLike(self._post("/tickets", {**wrong["request"]["body"], "link_id": link_id}), wrong)
        self.assertLike(self._post("/tickets", {**wrong["request"]["body"], "link_id": link_id, "password": None}), wrong)
        closed = self._fixture("tickets_create_closed.json")
        link_id = self._link(size=3, password="blue-river", active=False)
        self.assertLike(self._post("/tickets", {**closed["request"]["body"], "link_id": link_id}), closed)

    def test_a_wrong_password_never_returns_an_existing_ticket(self):
        link_id, pid = self._link(size=3, password="blue-river"), _pid()
        self._post("/tickets", {"link_id": link_id, "participant_id": pid, "password": "blue-river"})
        reply = self._post("/tickets", {"link_id": link_id, "participant_id": pid, "password": "nope"})
        self.assertEqual(reply.status_code, 403)

    def test_bad_bodies(self):
        for body in ([], {"link_id": 3}, {"link_id": "x", "participant_id": 5}):
            self.assertEqual(self._post("/tickets", body).status_code, 400, body)

    def test_unknown_tickets(self):
        for name in ("tickets_poll_unknown.json", "tickets_ready_unknown.json"):
            f = self._fixture(name)
            self.assertLike(self._post(f["request"]["path"]), f)
        self.assertEqual(self._post("/tickets/not-a-uuid/poll").status_code, 404)

    def test_poll_ready_check_then_ready(self):
        link_id = self._link(size=2)
        a = self._post("/tickets", {"link_id": link_id, "participant_id": _pid()}).json()["ticket"]
        self._post("/tickets", {"link_id": link_id, "participant_id": _pid()})
        self.assertLike(self._post(f"/tickets/{a}/poll"), self._fixture("tickets_poll_ready_check.json"))
        reply = self._post(f"/tickets/{a}/ready")
        self.assertLike(reply, self._fixture("tickets_ready.json"))
        self.assertTrue(reply.json()["view"]["pressed"])


if __name__ == "__main__":
    unittest.main()
