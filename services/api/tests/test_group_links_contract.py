"""Group start links, step 0: the contract the lanes build against.

Constants, password hashing, the two routers mounted behind the runner's secret, the
JSON fixtures every runner endpoint is written to, and the runner hooks (stop_room,
setup_match). The DB tests need the stack's Postgres at head:
    docker compose -f .devcontainer/docker-compose.yml run --rm --no-deps -T \\
        -w /app/services/api tests python -m unittest tests.test_group_links_contract -v
"""
import json
import os
import pathlib
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "matchmaking"

ENDPOINTS = {
    "POST /links", "GET /links", "PATCH /links/{id}", "GET /links/{id}/public",
    "POST /tickets", "POST /tickets/{id}/poll", "POST /tickets/{id}/ready",
}
VIEW_FIELDS = {
    "searching": {"found", "size", "expires_in_ms", "requeued"},
    "ready_check": {"deadline_in_ms", "pressed"},
    "starting": {"room", "starts_in_ms"},
    "expired": set(), "missed": set(), "done": set(),
}


def _env():
    os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
    os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
    os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")


class ConstantsTest(unittest.TestCase):
    def test_constants(self):
        from meetlab_api import matchmaker
        self.assertEqual((matchmaker.TICKET_LIVE_S, matchmaker.READY_CHECK_S, matchmaker.DONE_AFTER_S,
                          matchmaker.SETUP_CLAIM_S), (90, 20, 120, 30))

    def test_change_types(self):
        from meetlab_api.matchmaker import CreateMatch, Event, StopRoom, UpdateMatch, UpdateTicket
        self.assertEqual(UpdateTicket("t1", {"state": "found"}).fields, {"state": "found"})
        self.assertEqual(UpdateMatch("r", {"closed_at": 1}).room_name, "r")
        self.assertEqual(StopRoom("r").room_name, "r")
        self.assertEqual(Event("match_formed", {"room": "r"}).name, "match_formed")
        self.assertEqual(CreateMatch("r", "friendly", 3, None, None, ("t1",)).members, ("t1",))


class PasswordTest(unittest.TestCase):
    def test_hash_format_and_verify(self):
        from meetlab_api.links import hash_password, verify_password
        stored = hash_password("blue-river")
        scheme, salt, digest = stored.split("$")
        self.assertEqual(scheme, "scrypt")
        self.assertTrue(salt and digest)
        self.assertNotIn("blue-river", stored)
        self.assertTrue(verify_password("blue-river", stored))
        self.assertFalse(verify_password("blue-rive", stored))

    def test_salted(self):
        from meetlab_api.links import hash_password
        self.assertNotEqual(hash_password("x"), hash_password("x"))

    def test_malformed_hash_never_verifies(self):
        from meetlab_api.links import verify_password
        for bad in ("", "scrypt$", "plain", "md5$a$b"):
            self.assertFalse(verify_password("x", bad), bad)


class FixturesTest(unittest.TestCase):
    def setUp(self):
        self.fixtures = {p.name: json.loads(p.read_text()) for p in sorted(FIXTURES.glob("*.json"))}

    def test_every_endpoint_and_view_has_a_fixture(self):
        self.assertEqual({f["endpoint"] for f in self.fixtures.values()}, ENDPOINTS)
        views = {f["response"]["body"]["view"]["kind"]
                 for f in self.fixtures.values() if "view" in f["response"]["body"]}
        self.assertEqual(views, set(VIEW_FIELDS))

    def test_fixture_shapes(self):
        for name, f in self.fixtures.items():
            with self.subTest(name):
                self.assertEqual(f["request"]["method"], f["endpoint"].split()[0])
                self.assertTrue(f["request"]["path"].startswith("/"))
                body = f["response"]["body"]
                if f["response"]["status"] != 200:
                    self.assertIsInstance(body["error"], str)
                elif "view" in body:
                    self.assertEqual(set(body), {"ticket", "link_id", "participant_id", "view"})
                    uuid.UUID(body["ticket"])
                    view = dict(body["view"])
                    self.assertEqual(set(view) - {"kind"}, VIEW_FIELDS[view.pop("kind")])
                self.assertNotIn("password_hash", json.dumps(f))


# Both routers start empty, so a probe route is added to each before runner imports them; in a
# subprocess, so no other test sees the probes or a reloaded runner.
_PROBE = """
from fastapi.testclient import TestClient
from meetlab_api import links, matchmaker
for name, router in (("links", links.router), ("matchmaker", matchmaker.router)):
    router.add_api_route(f"/__probe/{name}", lambda name=name: {"router": name}, methods=["GET"])
from meetlab_api import runner
client = TestClient(runner.app)
for name in ("links", "matchmaker"):
    denied = client.get(f"/__probe/{name}").status_code
    ok = client.get(f"/__probe/{name}", headers={"Authorization": f"Bearer {runner.BOT_RUNNER_SECRET}"})
    print(name, denied, ok.status_code, ok.json()["router"])
"""


class RoutersMountedTest(unittest.TestCase):
    def test_each_router_is_behind_the_runner_secret(self):
        _env()
        out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.split("\n")[-3:], ["links 401 200 links", "matchmaker 401 200 matchmaker", ""],
                         out.stderr[-2000:])

    def test_database_page_never_shows_a_password_hash(self):
        _env()
        from meetlab_api import runner
        view = runner.StartLinkAdmin()
        for columns in (view.get_list_columns(), view.get_details_columns(), view.get_export_columns(),
                        view.get_form_columns()):
            names = [c if isinstance(c, str) else c[0] for c in columns]
            self.assertIn("link_id", names)
            self.assertNotIn("password_hash", names)
        registered = {type(v).__name__ for v in runner.admin.views}
        self.assertLessEqual({"StartLinkAdmin", "TicketAdmin", "MatchAdmin"}, registered)

    def test_hooks_on_app_state(self):
        _env()
        from meetlab_api import runner
        self.assertIs(runner.app.state.stop_room, runner.stop_room)
        self.assertIs(runner.app.state.setup_match, runner.setup_match)


class RunnerHooksDbTest(unittest.TestCase):
    """setup_match and ensure_legacy_link against the stack's Postgres."""

    @classmethod
    def setUpClass(cls):
        _env()
        cls._env = mock.patch.dict(os.environ, {"BOT_DISPATCHER": "docker", "DOCKER_HOST": "tcp://docker-socket-proxy:2375"})
        cls._env.start()
        from meetlab_api import runner
        cls.runner = runner
        cls._run = mock.patch.object(runner, "run_bot_container", return_value="meetlab-bot-test")
        cls.run_bot = cls._run.start()
        cls._stop = mock.patch.object(runner, "stop_bot_container")
        cls.stop_bot = cls._stop.start()
        cls._room = mock.patch.object(runner, "_create_livekit_room", new=mock.AsyncMock())
        cls.create_room = cls._room.start()
        # Drop (without closing) connections an earlier test class's event loop left in the pool.
        runner.engine.sync_engine.dispose(close=False)
        cls._client = TestClient(runner.app)
        cls.client = cls._client.__enter__()

    @classmethod
    def tearDownClass(cls):
        # The pool's connections belong to this client's event loop: close them here, so the
        # next test class (a new loop) never gets one.
        cls.client.portal.call(cls.runner.engine.dispose)
        cls._client.__exit__(None, None, None)
        for p in (cls._room, cls._stop, cls._run, cls._env):
            p.stop()

    def _db(self, fn):
        async def go():
            async with self.runner.AsyncSessionLocal() as db, db.begin():
                return await fn(db)
        return self.client.portal.call(go)

    def _call(self, fn, *args):
        async def go():
            return await fn(*args)
        return self.client.portal.call(go)

    def _match(self, size=2, **fields):
        from meetlab_core.db.models import Match, StartLink
        link_id, room = uuid.uuid4().hex[:22], f"link-test-{uuid.uuid4().hex[:8]}"

        async def add(db):
            db.add(StartLink(link_id=link_id, name="test", pool=["global"], size=size, min_size=size,
                             delay_s=0, max_wait_s=300))
            await db.flush()
            db.add(Match(room_name=room, link_id=link_id, bot_config_scope="global", size=size, **fields))
        self._db(add)
        return link_id, room

    def _row(self, room):
        from meetlab_core.db.models import Match
        return self._db(lambda db: db.get(Match, room))

    def _running(self, room):
        from sqlalchemy import select
        from meetlab_core.db.models import Conversation

        async def running(db):
            return (await db.scalars(select(Conversation).where(
                Conversation.room_name == room, Conversation.status == "running"))).all()
        return self._db(running)

    def test_setup_starts_one_bot_with_the_match_meta(self):
        link_id, room = self._match(size=3)
        self._call(self.runner.setup_match, room)
        self._call(self.runner.setup_match, room)  # a second request: claimed, no second bot
        [conv] = self._running(room)
        self.assertEqual(conv.meta, {"requested_by": "start-link", "bot_config_scope": "global",
                                     "link_id": link_id, "expected_humans": 3})
        self.assertTrue(conv.bot_identity.startswith("bot_"))
        self.assertEqual(self._row(room).session_id, conv.id)
        self.create_room.assert_any_await(room)
        from meetlab_core.db.models import BotConfig
        from sqlalchemy import select
        self.assertIsNotNone(self._db(lambda db: db.scalar(select(BotConfig).where(BotConfig.scope == room))))

    def test_a_closed_match_is_never_set_up(self):
        _, room = self._match(closed_at=datetime.now(timezone.utc))
        self._call(self.runner.setup_match, room)
        self.assertEqual(self._running(room), [])

    def test_a_fresh_claim_blocks_and_a_stale_one_is_retried(self):
        _, room = self._match(setup_claimed_at=datetime.now(timezone.utc))
        self._call(self.runner.setup_match, room)
        self.assertEqual(self._running(room), [])
        _, room = self._match(setup_claimed_at=datetime.now(timezone.utc) - timedelta(seconds=31))
        self._call(self.runner.setup_match, room)
        self.assertEqual(len(self._running(room)), 1)

    def test_a_failed_setup_leaves_the_match_for_a_retry(self):
        _, room = self._match()
        self.create_room.side_effect = [RuntimeError("livekit down")]
        try:
            self._call(self.runner.setup_match, room)
        finally:
            self.create_room.side_effect = None
        self.assertIsNone(self._row(room).session_id)
        self.assertEqual(self._running(room), [])

    def test_a_match_closed_during_setup_is_stopped(self):
        _, room = self._match()
        self.create_room.side_effect = self._close  # the ready check fails while the room is made
        try:
            self._call(self.runner.setup_match, room)
        finally:
            self.create_room.side_effect = None
        self.assertIsNotNone(self._row(room).session_id, "the bot was started, then stopped")
        self.assertEqual(self._running(room), [])

    async def _close(self, room):
        from sqlalchemy import update
        from meetlab_core.db.models import Match
        async with self.runner.AsyncSessionLocal() as db, db.begin():
            await db.execute(update(Match).where(Match.room_name == room).values(closed_at=datetime.now(timezone.utc)))

    def test_stop_room_ends_the_running_session(self):
        _, room = self._match()
        self._call(self.runner.setup_match, room)
        self.assertEqual(len(self._running(room)), 1)
        self._call(self.runner.stop_room, room)
        self.assertEqual(self._running(room), [])

    def test_ensure_legacy_link_makes_one_row(self):
        from meetlab_api.links import ensure_legacy_link
        from meetlab_core.db.models import StartLink
        link_id = uuid.uuid4().hex[:22]
        self._db(lambda db: ensure_legacy_link(db, link_id, ["friendly"]))
        self._db(lambda db: ensure_legacy_link(db, link_id, ["other"]))
        row = self._db(lambda db: db.get(StartLink, link_id))
        self.assertEqual((row.legacy, row.pool, row.size, row.delay_s, row.password_hash, row.active),
                         (True, ["friendly"], 1, 0, None, True))


if __name__ == "__main__":
    unittest.main()
