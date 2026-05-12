import importlib
import os
import sys
import types
import unittest

from fastapi.testclient import TestClient


class RunnerStartApiTests(unittest.TestCase):
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

        cls.client = TestClient(cls.runner_module.app)

    # ------------------------------------------------------------------
    # Input validation — room_name
    # ------------------------------------------------------------------

    def test_missing_room_name_returns_400(self):
        response = self.client.post("/start", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json().get("error"),
            "room_name is required and must be a non-empty string",
        )

    def test_invalid_room_name_type_returns_400(self):
        response = self.client.post("/start", json={"room_name": 1234})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json().get("error"),
            "room_name is required and must be a non-empty string",
        )

    def test_whitespace_only_room_name_returns_400(self):
        response = self.client.post("/start", json={"room_name": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json().get("error"),
            "room_name is required and must be a non-empty string",
        )

    def test_null_room_name_returns_400(self):
        response = self.client.post("/start", json={"room_name": None})
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # Input validation — body structure
    # ------------------------------------------------------------------

    def test_non_object_body_returns_400(self):
        response = self.client.post("/start", json=[])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "request body must be a JSON object")

    def test_malformed_json_returns_400(self):
        response = self.client.post(
            "/start",
            content="{",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "request body must be valid JSON")

    # ------------------------------------------------------------------
    # Input validation — bot_identity
    # ------------------------------------------------------------------

    def test_empty_bot_identity_returns_400(self):
        response = self.client.post(
            "/start",
            json={"room_name": "test-room", "bot_identity": ""},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json().get("error"),
            "bot_identity must be a non-empty string",
        )

    def test_whitespace_bot_identity_returns_400(self):
        response = self.client.post(
            "/start",
            json={"room_name": "test-room", "bot_identity": "   "},
        )
        self.assertEqual(response.status_code, 400)

    def test_bot_identity_too_long_returns_400(self):
        long_identity = "b" * 129
        response = self.client.post(
            "/start",
            json={"room_name": "test-room", "bot_identity": long_identity},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json().get("error"),
            "bot_identity must be 128 characters or fewer",
        )

    def test_bot_identity_exactly_128_chars_accepted(self):
        identity = "bot_" + "x" * 124
        response = self.client.post(
            "/start",
            json={"room_name": "test-room", "bot_identity": identity},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("bot_identity"), identity)

    # ------------------------------------------------------------------
    # Successful start
    # ------------------------------------------------------------------

    def test_explicit_bot_identity_echoed(self):
        room_name = "runner-test-room"
        bot_identity = "bot_runner_test_identity"
        response = self.client.post(
            "/start",
            json={"room_name": room_name, "bot_identity": bot_identity},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload.get("room_name"), room_name)
        self.assertEqual(payload.get("bot_identity"), bot_identity)

    def test_generated_bot_identity_has_bot_prefix(self):
        response = self.client.post("/start", json={"room_name": "auto-id-room"})
        self.assertEqual(response.status_code, 200, response.text)
        bot_identity = response.json().get("bot_identity", "")
        self.assertTrue(
            bot_identity.startswith("bot_"),
            f"Expected bot_ prefix, got: {bot_identity}",
        )

    def test_response_contains_session_id_and_room_name(self):
        response = self.client.post("/start", json={"room_name": "session-id-room"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("session_id", payload)
        self.assertIn("room_name", payload)
        self.assertIn("bot_identity", payload)
        self.assertIn("message", payload)

    def test_room_name_is_stripped(self):
        response = self.client.post("/start", json={"room_name": "  padded-room  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("room_name"), "padded-room")

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def test_health_check_returns_healthy(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("status"), "healthy")

    # ------------------------------------------------------------------
    # _room_slug helper
    # ------------------------------------------------------------------

    def test_room_slug_helper(self):
        from runner import _room_slug

        self.assertEqual(_room_slug("my-room"), "my_room")
        self.assertEqual(_room_slug("abc123"), "abc123")
        self.assertEqual(_room_slug("a" * 30), "a" * 24)
        self.assertEqual(_room_slug("!!!"), "room")
        self.assertEqual(_room_slug(""), "room")


if __name__ == "__main__":
    unittest.main()
