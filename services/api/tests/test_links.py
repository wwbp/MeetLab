"""Group start links, lane 2: the /links endpoints (create, list with counts, stop/start,
the start page's public view) and the legacy JWT link's record.

LinkFieldsTest is pure; the rest need the stack's Postgres at head:
    docker compose -f .devcontainer/docker-compose.yml run --rm --no-deps -T \\
        -w /app/services/api tests python -m unittest tests.test_links -v
"""
import json
import os
import pathlib
import re
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "matchmaking"
SECRET = "links-test-secret"
AUTH = {"Authorization": f"Bearer {SECRET}"}


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _env():
    os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
    os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
    os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")


class LinkFieldsTest(unittest.TestCase):
    """R1, R2: what a new link may be, and its defaults."""

    def test_defaults(self):
        from meetlab_api.links import link_fields
        self.assertEqual(link_fields({"name": " Pilot ", "pool": ["global"]}),
                         {"name": "Pilot", "pool": ["global"], "size": 1, "min_size": 1, "delay_s": 0,
                          "max_wait_s": 300, "ready_check_s": 60})

    def test_min_size_defaults_to_size(self):
        from meetlab_api.links import link_fields
        self.assertEqual(link_fields({"name": "P", "pool": ["a"], "size": 3})["min_size"], 3)

    def test_the_fixture_is_accepted(self):
        from meetlab_api.links import link_fields
        body = dict(_fixture("links_create")["request"]["body"])
        body.pop("password")
        self.assertEqual(link_fields(body), {**body, "name": "Pilot 3, groups of 3"})

    def test_invalid_fixture_names_the_name_first(self):
        from meetlab_api.links import link_fields
        f = _fixture("links_create_invalid")
        self.assertEqual(link_fields(f["request"]["body"]), f["response"]["body"]["error"])

    def test_out_of_range(self):
        from meetlab_api.links import link_fields
        ok = {"name": "P", "pool": ["a"], "size": 3}
        for bad in ({"name": "  "}, {"name": 3}, {"pool": []}, {"pool": "a"}, {"pool": ["a", ""]},
                    {"size": 0}, {"size": 9}, {"size": True}, {"size": "3"}, {"size": 2.5},
                    {"min_size": 0}, {"min_size": 4}, {"delay_s": -1}, {"delay_s": 301},
                    {"max_wait_s": 59}, {"max_wait_s": 1801},
                    {"ready_check_s": 9}, {"ready_check_s": 301}):
            with self.subTest(bad):
                self.assertIsInstance(link_fields({**ok, **bad}), str)
        self.assertIsInstance(link_fields({**ok, "size": 8, "min_size": 1, "delay_s": 300, "max_wait_s": 1800,
                                              "ready_check_s": 300}),
                              dict)


class LinksApiTest(unittest.TestCase):
    """The /links endpoints against the stack's Postgres."""

    @classmethod
    def setUpClass(cls):
        _env()
        from meetlab_api import runner
        cls.runner = runner
        cls._secret = mock.patch.object(runner, "BOT_RUNNER_SECRET", SECRET)
        cls._secret.start()
        runner.engine.sync_engine.dispose(close=False)  # connections an earlier class's loop left
        cls._client = TestClient(runner.app)
        cls.client = cls._client.__enter__()
        cls._settings("friendly", "neutral")  # the fixtures' pool

    @classmethod
    def _settings(cls, *scopes):
        from sqlalchemy.dialects.postgresql import insert
        from meetlab_core.db.models import BotConfig

        async def go():
            async with cls.runner.AsyncSessionLocal() as db, db.begin():
                await db.execute(insert(BotConfig).values([{"scope": s} for s in scopes]).on_conflict_do_nothing())
        cls.client.portal.call(go)

    @classmethod
    def tearDownClass(cls):
        cls.client.portal.call(cls.runner.engine.dispose)
        cls._client.__exit__(None, None, None)
        cls._secret.stop()

    def _db(self, fn):
        async def go():
            async with self.runner.AsyncSessionLocal() as db, db.begin():
                return await fn(db)
        return self.client.portal.call(go)

    def _create(self, **body):
        r = self.client.post("/links", headers=AUTH, json={"name": "test", "pool": ["global"], **body})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["link_id"]

    def _listed(self, link_id):
        r = self.client.get("/links", headers=AUTH)
        self.assertEqual(r.status_code, 200, r.text)
        [row] = [row for row in r.json()["links"] if row["link_id"] == link_id]
        return row

    def _row(self, link_id):
        from meetlab_core.db.models import StartLink
        return self._db(lambda db: db.get(StartLink, link_id))

    # --- passwords (R26, R28)

    def test_a_password_is_stored_only_as_a_salted_scrypt_hash(self):
        from meetlab_api.links import verify_password
        link_id = self._create(password="blue-river")
        stored = self._row(link_id).password_hash
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn("blue-river", stored)
        self.assertTrue(verify_password("blue-river", stored))

    def test_an_empty_password_is_none(self):
        for body in ({}, {"password": ""}, {"password": None}):
            with self.subTest(body):
                link_id = self._create(**body)
                self.assertIsNone(self._row(link_id).password_hash)
                self.assertFalse(self._listed(link_id)["has_password"])

    def test_a_password_must_be_a_string(self):
        r = self.client.post("/links", headers=AUTH, json={"name": "t", "pool": ["global"], "password": 5})
        self.assertEqual((r.status_code, r.json()), (400, {"error": "password must be a string"}))

    def test_the_list_never_has_a_hash(self):
        self._create(password="blue-river")
        text = self.client.get("/links", headers=AUTH).text
        for leak in ("password_hash", "scrypt$", "blue-river"):
            self.assertNotIn(leak, text)

    # --- create and list (R1, R23, R24)

    def test_create_returns_a_22_char_base64url_id(self):
        body = _fixture("links_create")["request"]["body"]
        ids = {self._create(**body) for _ in range(2)}
        self.assertEqual(len(ids), 2)
        for link_id in ids:
            self.assertRegex(link_id, r"^[A-Za-z0-9_-]{22}$")

    def test_a_pool_setting_that_does_not_exist_is_400(self):
        r = self.client.post("/links", headers=AUTH, json={"name": "t", "pool": ["global", "no-such-setting"]})
        self.assertEqual((r.status_code, r.json()), (400, {"error": "unknown bot settings: no-such-setting"}))

    def test_invalid_body_is_400(self):
        f = _fixture("links_create_invalid")
        r = self.client.post("/links", headers=AUTH, json=f["request"]["body"])
        self.assertEqual((r.status_code, r.json()), (f["response"]["status"], f["response"]["body"]))
        for raw in ("not json", "[]"):
            r = self.client.post("/links", headers={**AUTH, "Content-Type": "application/json"}, content=raw)
            self.assertEqual(r.status_code, 400, raw)

    def test_list_row_has_the_fixture_shape(self):
        body = _fixture("links_create")["request"]["body"]
        row = self._listed(self._create(**body))
        expected = _fixture("links_list")["response"]["body"]["links"][0]
        self.assertEqual(set(row), set(expected))
        self.assertEqual(set(row["counts"]), set(expected["counts"]))
        self.assertEqual({k: row[k] for k in ("name", "pool", "size", "min_size", "delay_s", "max_wait_s",
                                              "has_password", "active", "legacy", "stopped_at")},
                         {**{k: body[k] for k in ("name", "pool", "size", "min_size", "delay_s", "max_wait_s")},
                          "has_password": True, "active": True, "legacy": False, "stopped_at": None})
        self.assertRegex(row["created_at"], r"^\d{4}-\d\d-\d\dT")

    def test_list_is_newest_first(self):
        first, second = self._create(), self._create()
        ids = [row["link_id"] for row in self.client.get("/links", headers=AUTH).json()["links"]]
        self.assertLess(ids.index(second), ids.index(first))

    def test_counts_by_ticket_state(self):
        from meetlab_core.db.models import Ticket
        link_id = self._create(size=3)
        states = ["searching", "searching", "expired", "removed", "done", "done", "done"]

        async def add(db):
            db.add_all(Ticket(link_id=link_id, participant_id=uuid.uuid4().hex, state=s) for s in states)
        self._db(add)
        self.assertEqual(self._listed(link_id)["counts"],
                         {"searching": 2, "found": 0, "ready": 0, "placed": 0, "done": 3, "expired": 1,
                          "removed": 1})
        self.assertEqual(set(self._listed(self._create())["counts"].values()), {0})

    # --- stop / start (R25)

    def test_stop_then_start(self):
        link_id = self._create()
        r = self.client.patch(f"/links/{link_id}", headers=AUTH, json={"active": False})
        self.assertEqual((r.status_code, r.json()), (200, {"link_id": link_id, "active": False}))
        row = self._listed(link_id)
        self.assertFalse(row["active"])
        self.assertIsNotNone(row["stopped_at"])
        self.assertFalse(self.client.get(f"/links/{link_id}/public", headers=AUTH).json()["active"])

        r = self.client.patch(f"/links/{link_id}", headers=AUTH, json={"active": True})
        self.assertEqual(r.json(), {"link_id": link_id, "active": True})
        row = self._listed(link_id)
        self.assertEqual((row["active"], row["stopped_at"]), (True, None))

    def test_start_is_refused_while_a_pool_setting_is_missing(self):
        import uuid
        scope = f"gone-{uuid.uuid4().hex[:8]}"
        self._settings(scope)
        link_id = self._create(pool=[scope])
        self.client.patch(f"/links/{link_id}", headers=AUTH, json={"active": False})
        from sqlalchemy import text
        self._db(lambda db: db.execute(text("DELETE FROM bot_config WHERE scope = :s"), {"s": scope}))
        r = self.client.patch(f"/links/{link_id}", headers=AUTH, json={"active": True})
        self.assertEqual((r.status_code, r.json()), (409, {"error": f"unknown bot settings: {scope}"}))
        self.assertFalse(self._listed(link_id)["active"])

    def test_stopping_twice_keeps_the_first_stop_time(self):
        link_id = self._create()
        self.client.patch(f"/links/{link_id}", headers=AUTH, json={"active": False})
        first = self._listed(link_id)["stopped_at"]
        self.client.patch(f"/links/{link_id}", headers=AUTH, json={"active": False})
        self.assertEqual(self._listed(link_id)["stopped_at"], first)

    def test_patch_unknown_is_404(self):
        f = _fixture("links_patch_unknown")
        r = self.client.patch(f["request"]["path"], headers=AUTH, json=f["request"]["body"])
        self.assertEqual((r.status_code, r.json()), (404, f["response"]["body"]))

    def test_patch_needs_a_boolean(self):
        link_id = self._create()
        for body in ({}, {"active": "false"}, {"active": 0}):
            r = self.client.patch(f"/links/{link_id}", headers=AUTH, json=body)
            self.assertEqual(r.status_code, 400, body)
        self.assertTrue(self._listed(link_id)["active"])

    # --- the start page's view (Flow 1)

    def test_public_view(self):
        link_id = self._create(size=3, password="blue-river")
        r = self.client.get(f"/links/{link_id}/public", headers=AUTH)
        self.assertEqual((r.status_code, r.json()), (200, _fixture("links_public")["response"]["body"]))
        r = self.client.get(f"/links/{self._create()}/public", headers=AUTH)
        self.assertEqual(r.json(), {"active": True, "needs_password": False, "size": 1})

    def test_public_unknown_is_404(self):
        f = _fixture("links_public_unknown")
        r = self.client.get(f"/links/{uuid.uuid4().hex[:22]}/public", headers=AUTH)
        self.assertEqual((r.status_code, r.json()), (404, f["response"]["body"]))

    # --- the legacy record (R3, R30)

    def test_a_legacy_link_is_listed_and_can_be_stopped(self):
        from meetlab_api.links import ensure_legacy_link
        link_id = uuid.uuid4().hex[:22]
        self._db(lambda db: ensure_legacy_link(db, link_id, ["global"]))
        row = self._listed(link_id)
        self.assertEqual({k: row[k] for k in ("name", "legacy", "size", "delay_s", "has_password", "active")},
                         {"name": "Older link", "legacy": True, "size": 1, "delay_s": 0, "has_password": False,
                          "active": True})
        self.client.patch(f"/links/{link_id}", headers=AUTH, json={"active": False})
        self._db(lambda db: ensure_legacy_link(db, link_id, ["global"]))  # its next use
        self.assertFalse(self._listed(link_id)["active"])
        self.assertEqual(self.client.get(f"/links/{link_id}/public", headers=AUTH).json(),
                         {"active": False, "needs_password": False, "size": 1})

    # --- R22

    def test_every_route_needs_the_secret(self):
        from meetlab_api.links import router
        routes = [(m, r.path) for r in router.routes for m in r.methods]
        self.assertEqual({p for _, p in routes}, {"/links", "/links/{link_id}", "/links/{link_id}/public"})
        for method, path in routes:
            url = re.sub(r"\{\w+\}", "Qm9vdHN0cmFwTGlua0lEMQ", path)
            for headers in ({}, {"Authorization": "Bearer nope"}):
                r = self.client.request(method, url, headers=headers, json={})
                self.assertEqual(r.status_code, 401, (method, path, headers))


if __name__ == "__main__":
    unittest.main()
