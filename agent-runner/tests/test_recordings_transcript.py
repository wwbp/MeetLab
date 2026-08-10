"""Regression and unit tests for recording and transcript endpoints.

Covers the bugs that reached production:

  1. Unhandled LiveKit TwirpError in recording endpoints
     start_room_composite_egress raises TwirpError(not_found) when the room
     doesn't exist in LiveKit. Without a try/except, FastAPI returned a 500.
     Fixed: LiveKit errors are caught and returned as 404 or 502.

  2. SQLAlchemy double-begin crash (transcript queue)
     queue_transcript executed a bare SELECT (auto-beginning a transaction
     via autobegin) then called async with db.begin() again, raising
     "InvalidRequestError: A transaction is already begun on this Session."
     Fixed: single db.begin() context wraps the entire check-then-insert.

Tests call the already-running agent-runner server at localhost:7860 via
requests so they don't create a second TestClient / event loop that would
conflict with test_runner_start.py's shared asyncpg connection pool.

The real LiveKit server (transport-server) is used for calls that require
it; for error-handling tests we rely on the fact that the test rooms don't
exist in LiveKit, so start_room_composite_egress always raises TwirpError
— which is exactly the condition the error-handling fix guards against.
"""

import os
import unittest

import requests

BASE = os.environ.get("AGENT_RUNNER_URL", "http://localhost:7860")
AUTH_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('BOT_RUNNER_SECRET', 'changeme')}",
    "Content-Type": "application/json",
}


def _post(path, **kwargs):
    return requests.post(f"{BASE}{path}", headers=AUTH_HEADERS, **kwargs)


def _start_bot(room_name: str) -> str:
    """Create a running Conversation via POST /start; return session_id."""
    r = _post("/start", json={"room_name": room_name})
    assert r.status_code == 200, f"bot start failed for {room_name!r}: {r.text}"
    return r.json()["session_id"]


# ─── 1. proto import smoke ─────────────────────────────────────────────────────

class EgressProtoImportTests(unittest.TestCase):
    """Ensures the proto types used by recording endpoints are importable."""

    def test_all_egress_proto_types_importable(self):
        from livekit.protocol.egress import (
            EncodedFileOutput,
            ListEgressRequest,
            RoomCompositeEgressRequest,
            S3Upload,
            StopEgressRequest,
        )
        req = ListEgressRequest(room_name="probe")
        self.assertEqual(req.room_name, "probe")
        stop = StopEgressRequest(egress_id="egr-probe")
        self.assertEqual(stop.egress_id, "egr-probe")


# ─── 2. recording endpoint tests ──────────────────────────────────────────────

class RecordingEndpointTests(unittest.TestCase):

    # ── input validation ────────────────────────────────────────────────────

    def test_start_missing_room_name_returns_400(self):
        self.assertEqual(_post("/recordings/start", json={}).status_code, 400)

    def test_start_invalid_room_name_type_returns_400(self):
        self.assertEqual(_post("/recordings/start", json={"room_name": 42}).status_code, 400)

    def test_start_malformed_json_returns_400(self):
        r = requests.post(
            f"{BASE}/recordings/start",
            headers={**AUTH_HEADERS, "Content-Type": "application/json"},
            data="{bad",
        )
        self.assertEqual(r.status_code, 400)

    def test_stop_missing_room_name_returns_400(self):
        self.assertEqual(_post("/recordings/stop", json={}).status_code, 400)

    # ── DB guard ────────────────────────────────────────────────────────────

    def test_start_no_running_session_returns_404(self):
        """Room with no Conversation(status=running) → 404 before touching LiveKit."""
        r = _post("/recordings/start", json={"room_name": "ghost-room-xyz-000"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("no active session", r.json()["error"])

    # ── LiveKit error handling (regression) ─────────────────────────────────

    def test_start_with_session_but_no_livekit_room_returns_4xx_not_500(self):
        """Regression: unhandled TwirpError(not_found) returned 500.

        A running session in the DB but no corresponding LiveKit room
        (typical for test bots that never actually joined) means
        start_room_composite_egress raises TwirpError(not_found).
        With the fix, this becomes 404, not an unhandled 500.
        """
        _start_bot("rec-lk-notfound-test-room")
        r = _post("/recordings/start", json={"room_name": "rec-lk-notfound-test-room"})
        self.assertNotEqual(r.status_code, 500, f"must not be 500: {r.text}")
        # The assertion this test exists for is the line above: a LiveKit error must
        # be handled, never an unhandled 500. The exact success/failure code depends
        # on environment and must not make the test flaky:
        #   production (room absent in LiveKit)            → 404
        #   dev, bot joined and egress already running      → 409
        #   dev, real egress container accepts the job      → 200
        #   any other LiveKit error                         → 502
        # 200 became reachable once the dev stack started running a real `egress`
        # service; before that this list was [404, 409, 502] and flaked ~1 run in 3.
        self.assertIn(r.status_code, [200, 404, 409, 502])

    def test_stop_no_active_egress_returns_404(self):
        """list_egress returns empty for an unknown room → 404 from active-check guard."""
        r = _post("/recordings/stop", json={"room_name": "no-egress-room-xyz"})
        self.assertEqual(r.status_code, 404)


# ─── 3. transcript queue tests ────────────────────────────────────────────────

class TranscriptQueueTests(unittest.TestCase):
    """Regression: SQLAlchemy double-begin crash (InvalidRequestError → 500)."""

    @classmethod
    def setUpClass(cls):
        cls.session_id = _start_bot("transcript-regression-shared-room")

    def test_nonexistent_conv_returns_404_not_500(self):
        """Regression: double-begin also fired for missing conv_id → was 500."""
        r = _post("/conversations/00000000-0000-0000-0000-000000000000/transcript")
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("not found", r.json()["error"])

    def test_queue_returns_200_or_409_never_500(self):
        """Regression: double db.begin() caused 500; fixed path returns 200."""
        r = _post(f"/conversations/{self.session_id}/transcript")
        self.assertNotEqual(r.status_code, 500, f"must not be 500: {r.text}")
        self.assertIn(r.status_code, [200, 409])
        if r.status_code == 200:
            data = r.json()
            self.assertIn("media_file_id", data)
            self.assertEqual(data["status"], "pending")

    def test_second_request_returns_409_with_media_file_id(self):
        """Idempotency: duplicate transcript request returns 409 + existing id."""
        _post(f"/conversations/{self.session_id}/transcript")  # ensure one exists
        r = _post(f"/conversations/{self.session_id}/transcript")
        self.assertEqual(r.status_code, 409, r.text)
        data = r.json()
        self.assertIn("media_file_id", data)
        self.assertIn("already exists", data.get("error", ""))

    def test_fresh_session_always_returns_200(self):
        """A session with no prior transcript always gets 200 (not 409 or 500)."""
        sid = _start_bot("transcript-fresh-each-run-room")
        r = _post(f"/conversations/{sid}/transcript")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("media_file_id", r.json())


if __name__ == "__main__":
    unittest.main()
