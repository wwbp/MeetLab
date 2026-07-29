"""Durable event log: POST /events writes, GET /events reads back with filters.

The events table is the one place an admin can see what happened across the whole
pipeline (meet, runner, bot), so these tests pin the contract the console depends
on: severity is a real filterable field, unknown severities are rejected rather
than silently stored, and reads are newest-first and bounded.
"""
import importlib
import os
import sys
import types
import unittest
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class EventsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
        os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
        os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")

        fake_bot_module = types.ModuleType("bot")

        async def fake_bot(_runner_args):
            return None

        fake_bot_module.bot = fake_bot
        sys.modules["bot"] = fake_bot_module

        if "runner" in sys.modules:
            cls.runner_module = importlib.reload(sys.modules["runner"])
        else:
            import runner as runner_module

            cls.runner_module = runner_module

        cls._client_ctx = TestClient(cls.runner_module.app)
        cls.client = cls._client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client_ctx.__exit__(None, None, None)

    def setUp(self):
        # Unique room per test so filtered reads can't see other tests' rows.
        self.room = f"events-test-{uuid.uuid4().hex[:8]}"

    def _post(self, **body):
        body.setdefault("room_name", self.room)
        return self.client.post("/events", json=body)

    def _get(self, **params):
        params.setdefault("room", self.room)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.client.get(f"/events?{query}")

    # ── severity ────────────────────────────────────────────────────────────

    def test_event_defaults_to_info_severity(self):
        self.assertEqual(self._post(type="room.created").status_code, 202)
        events = self._get().json()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["severity"], "info")

    def test_severity_is_stored_and_returned(self):
        for severity in ("info", "warning", "error"):
            with self.subTest(severity=severity):
                r = self._post(type="thing.happened", severity=severity)
                self.assertEqual(r.status_code, 202, r.text)
        severities = {e["severity"] for e in self._get().json()["events"]}
        self.assertEqual(severities, {"info", "warning", "error"})

    def test_unknown_severity_is_rejected(self):
        # Better to fail the write than to store a value the console can't filter.
        r = self._post(type="thing.happened", severity="critical")
        self.assertEqual(r.status_code, 400)
        self.assertIn("severity", r.json().get("error", ""))

    def test_filter_by_severity(self):
        self._post(type="a", severity="info")
        self._post(type="b", severity="error")
        self._post(type="c", severity="error")
        errors = self._get(severity="error").json()["events"]
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(e["severity"] == "error" for e in errors))

    # ── reading ─────────────────────────────────────────────────────────────

    def test_events_come_back_newest_first(self):
        for name in ("first", "second", "third"):
            self._post(type=name)
        types_returned = [e["type"] for e in self._get().json()["events"]]
        self.assertEqual(types_returned, ["third", "second", "first"])

    def test_payload_round_trips(self):
        self._post(type="bot.failed", severity="error", detail="boom", attempt=2)
        event = self._get().json()["events"][0]
        self.assertEqual(event["payload"]["detail"], "boom")
        self.assertEqual(event["payload"]["attempt"], 2)
        # Routing fields are columns, not payload noise.
        self.assertNotIn("room_name", event["payload"])
        self.assertNotIn("severity", event["payload"])

    def test_response_shape_is_what_the_console_reads(self):
        self._post(type="bot.started", source="concierge")
        event = self._get().json()["events"][0]
        for key in ("id", "type", "severity", "room_name", "conv_id", "payload", "created_at"):
            self.assertIn(key, event, f"GET /events row missing {key}")

    def test_limit_is_honoured_and_bounded(self):
        for i in range(5):
            self._post(type=f"e{i}")
        self.assertEqual(len(self._get(limit=2).json()["events"]), 2)
        # Absurd limits are clamped, not passed to the database.
        self.assertLessEqual(len(self._get(limit=100000).json()["events"]), 500)

    def test_bad_limit_falls_back_instead_of_erroring(self):
        self._post(type="only")
        self.assertEqual(self._get(limit="abc").status_code, 200)

    def test_filter_by_room_excludes_other_rooms(self):
        self._post(type="mine")
        other = self.client.post(
            "/events", json={"type": "theirs", "room_name": f"{self.room}-other"}
        )
        self.assertEqual(other.status_code, 202)
        types_returned = [e["type"] for e in self._get().json()["events"]]
        self.assertEqual(types_returned, ["mine"])

    def test_type_is_still_required(self):
        r = self.client.post("/events", json={"room_name": self.room, "severity": "error"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
