import importlib
import os
import sys
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient


def _room(name: str) -> str:
    """A fresh room name. A room holds one running session at a time, and the stubbed
    bots here never end, so a fixed name would meet the previous run's session."""
    return f"{name}-{uuid.uuid4().hex[:6]}"


class RunnerStartApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
        os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
        os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")
        # Every bot is its own container or task (4c). The Docker dispatcher is
        # patched for the whole class, so these tests never start a real bot.
        cls._env = mock.patch.dict(os.environ, {"BOT_DISPATCHER": "docker", "DOCKER_HOST": "tcp://docker-socket-proxy:2375"})
        cls._env.start()

        if "runner" in sys.modules:
            cls.runner_module = importlib.reload(sys.modules["runner"])
        else:
            import runner as runner_module

            cls.runner_module = runner_module

        # Use TestClient as a context manager so all requests share a single
        # anyio BlockingPortal (one event loop). Without this, each post() call
        # creates a new event loop and asyncpg raises "Future attached to a
        # different loop" when the pool tries to reuse connections.
        cls._run = mock.patch.object(cls.runner_module, "run_bot_container", return_value="meetlab-bot-test")
        cls.run_bot = cls._run.start()
        cls._stop = mock.patch.object(cls.runner_module, "stop_bot_container")
        cls._stop.start()
        cls._client_ctx = TestClient(cls.runner_module.app)
        cls.client = cls._client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client_ctx.__exit__(None, None, None)
        cls._stop.stop()
        cls._run.stop()
        cls._env.stop()

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
            json={"room_name": _room("test-room"), "bot_identity": identity},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("bot_identity"), identity)

    # ------------------------------------------------------------------
    # Successful start
    # ------------------------------------------------------------------

    def test_explicit_bot_identity_echoed(self):
        room_name = _room("runner-test-room")
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
        response = self.client.post("/start", json={"room_name": _room("auto-id-room")})
        self.assertEqual(response.status_code, 200, response.text)
        bot_identity = response.json().get("bot_identity", "")
        self.assertTrue(
            bot_identity.startswith("bot_"),
            f"Expected bot_ prefix, got: {bot_identity}",
        )

    def test_response_contains_session_id_and_room_name(self):
        response = self.client.post("/start", json={"room_name": _room("session-id-room")})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("session_id", payload)
        self.assertIn("room_name", payload)
        self.assertIn("bot_identity", payload)
        self.assertIn("message", payload)

    def test_room_name_is_stripped(self):
        room = _room("padded-room")
        response = self.client.post("/start", json={"room_name": f"  {room}  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("room_name"), room)

    # ------------------------------------------------------------------
    # One running session per room (design iteration 2, diagnosis F3): a repeated
    # start -- a double click, a retry after meet's 10 s timeout -- returns the
    # session already running instead of putting a second bot in the meeting.
    # ------------------------------------------------------------------

    def _db(self, fn):
        """Run an async DB function on the TestClient's event loop (its pool lives there)."""
        async def go():
            async with self.runner_module.AsyncSessionLocal() as db:
                async with db.begin():
                    return await fn(db)
        return self.client.portal.call(go)

    def test_a_second_start_for_a_room_returns_its_running_session(self):
        room, before = _room("double-start"), self.run_bot.call_count
        first = self.client.post("/start", json={"room_name": room})
        second = self.client.post("/start", json={"room_name": room})
        self.assertEqual((first.status_code, second.status_code), (200, 200), second.text)
        self.assertEqual(second.json()["session_id"], first.json()["session_id"])
        self.assertEqual(second.json()["bot_identity"], first.json()["bot_identity"])
        self.assertEqual(self.run_bot.call_count - before, 1, "a second start must not start a second bot")

    def test_a_second_start_launches_no_second_ecs_task(self):
        ecs = mock.Mock()
        ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:task/1"}], "failures": []}
        room = _room("double-start-ecs")
        with mock.patch.dict(os.environ, self.ECS_ENV), \
             mock.patch.object(self.runner_module, "_ecs_client", return_value=ecs):
            self.client.post("/start", json={"room_name": room})
            self.client.post("/start", json={"room_name": room})
        self.assertEqual(ecs.run_task.call_count, 1)

    def test_a_room_can_start_again_once_its_session_has_ended(self):
        room = _room("restart")
        first = self.client.post("/start", json={"room_name": room}).json()["session_id"]
        Conversation = self.runner_module.Conversation
        self._db(lambda db: db.execute(
            self.runner_module.update(Conversation).where(Conversation.id == first).values(status="completed")))
        second = self.client.post("/start", json={"room_name": room}).json()["session_id"]
        self.assertNotEqual(second, first)

    def test_the_database_refuses_a_second_running_session_for_a_room(self):
        # The rule lives in Postgres, so it holds across runner processes and instances.
        from sqlalchemy.exc import IntegrityError

        Conversation, room = self.runner_module.Conversation, _room("db-rule")

        async def two_running(db):
            db.add(Conversation(id=str(uuid.uuid4()), room_name=room, status="running"))
            await db.flush()
            db.add(Conversation(id=str(uuid.uuid4()), room_name=room, status="running"))
            await db.flush()
        with self.assertRaises(IntegrityError):
            self._db(two_running)

    # ------------------------------------------------------------------
    # /stop: the console's Stop button (plan iteration 6). Removing the bot from
    # LiveKit does nothing before it has joined (5 s warm, up to 166 s cold), and
    # the bot then joined anyway (acceptance stop_early, 2026-10-01).
    # ------------------------------------------------------------------

    def _status(self, session_id):
        Conversation = self.runner_module.Conversation
        return self._db(lambda db: db.scalar(
            self.runner_module.select(Conversation.status).where(Conversation.id == session_id)))

    def test_stop_ends_the_rooms_session_at_its_task(self):
        ecs = mock.Mock()
        ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:task/1"}], "failures": []}
        ecs.list_tasks.return_value = {"taskArns": ["arn:task/1"]}
        room = _room("stop")
        with mock.patch.dict(os.environ, self.ECS_ENV), \
             mock.patch.object(self.runner_module, "_ecs_client", return_value=ecs):
            sid = self.client.post("/start", json={"room_name": room}).json()["session_id"]
            response = self.client.post("/stop", json={"room_name": room})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stopped"], sid)
        ecs.stop_task.assert_called_once()
        self.assertEqual(ecs.stop_task.call_args.kwargs["task"], "arn:task/1")
        self.assertEqual(self._status(sid), "completed")

    def test_a_stopped_room_can_start_a_new_bot_at_once(self):
        room = _room("stop-restart")
        first = self.client.post("/start", json={"room_name": room}).json()["session_id"]
        self.client.post("/stop", json={"room_name": room})
        second = self.client.post("/start", json={"room_name": room}).json()["session_id"]
        self.assertNotEqual(second, first)

    def test_record_reaches_a_bot_in_another_process(self):
        # Since 4c the bot is its own task; before this fix, console Record started
        # egress but never per-speaker capture.
        # The request is a flag on the session row, set whatever egress does next
        # (here the room is not in LiveKit, so egress fails).
        room = _room("record")
        sid = self.client.post("/start", json={"room_name": room}).json()["session_id"]
        self.client.post("/recordings/start", json={"room_name": room})
        Conversation = self.runner_module.Conversation
        requested = self._db(lambda db: db.scalar(
            self.runner_module.select(Conversation.recording_requested).where(Conversation.id == sid)))
        self.assertTrue(requested)

    def test_a_rooms_running_session_is_what_meet_shows(self):
        # Iteration 9: meet keeps no claim of its own; it asks for the room's session.
        room = _room("session")
        self.assertIsNone(self.client.get(f"/rooms/{room}/session").json()["session"])
        started = self.client.post("/start", json={"room_name": room}).json()
        session = self.client.get(f"/rooms/{room}/session").json()["session"]
        self.assertEqual((session["session_id"], session["bot_identity"]), (started["session_id"], started["bot_identity"]))
        self.client.post("/stop", json={"room_name": room})
        self.assertIsNone(self.client.get(f"/rooms/{room}/session").json()["session"])

    def _start_with_auto_record(self, on: bool):
        import time as _time
        from types import SimpleNamespace

        started = mock.AsyncMock(return_value=(200, {}))
        with mock.patch.object(self.runner_module, "load_bot_config",
                               mock.AsyncMock(return_value=SimpleNamespace(auto_record=on))), \
             mock.patch.object(self.runner_module, "start_recording_for_room", started):
            room = _room("auto-record")
            response = self.client.post("/start", json={"room_name": room})
            deadline = _time.monotonic() + 2
            while on and not started.await_count and _time.monotonic() < deadline:
                _time.sleep(0.05)
        self.assertEqual(response.status_code, 200, response.text)
        return room, started

    def test_auto_record_starts_the_room_recording_with_the_session(self):
        # Before the bot joins, so its greeting is recorded; in the runner, which
        # holds the egress key (the bot task does not: auto-record recorded no video).
        room, started = self._start_with_auto_record(True)
        started.assert_awaited_once_with(room)

    def test_without_auto_record_nothing_is_recorded_at_start(self):
        _, started = self._start_with_auto_record(False)
        started.assert_not_awaited()

    def test_stopping_a_room_with_no_bot_is_not_an_error(self):
        response = self.client.post("/stop", json={"room_name": _room("never-started")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["stopped"])

    # ------------------------------------------------------------------
    # BOT_DISPATCHER=ecs: the bot runs as its own ECS task (step 4c)
    # ------------------------------------------------------------------

    ECS_ENV = {"BOT_DISPATCHER": "ecs", "ECS_CLUSTER": "c", "BOT_TASK_DEFINITION": "td", "BOT_CAPACITY_PROVIDER": "cp"}

    def test_ecs_dispatch_runs_a_task_keyed_by_the_session(self):
        ecs = mock.Mock()
        ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:task/1"}], "failures": []}
        with mock.patch.dict(os.environ, self.ECS_ENV), \
             mock.patch.object(self.runner_module, "_ecs_client", return_value=ecs):
            response = self.client.post("/start", json={"room_name": _room("ecs-room")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(ecs.run_task.call_args.kwargs["clientToken"], response.json()["session_id"])

    DOCKER_ENV = {"BOT_DISPATCHER": "docker", "DOCKER_HOST": "tcp://docker-socket-proxy:2375"}

    def test_docker_dispatch_starts_a_container_keyed_by_the_session(self):
        with mock.patch.dict(os.environ, self.DOCKER_ENV), \
             mock.patch.object(self.runner_module, "run_bot_container", return_value="meetlab-bot-x") as run:
            response = self.client.post("/start", json={"room_name": _room("docker-room")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(run.call_args.args[1], response.json()["session_id"])

    def test_docker_refusal_is_reported_not_swallowed(self):
        with mock.patch.dict(os.environ, self.DOCKER_ENV), \
             mock.patch.object(self.runner_module, "run_bot_container",
                               side_effect=self.runner_module.DispatchError("starting meetlab-bot-x failed: 403")):
            response = self.client.post("/start", json={"room_name": _room("docker-refused")})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("403", response.json()["error"])

    def test_docker_stop_stops_the_sessions_container(self):
        room = _room("docker-stop")
        with mock.patch.dict(os.environ, self.DOCKER_ENV), \
             mock.patch.object(self.runner_module, "run_bot_container", return_value="meetlab-bot-x"), \
             mock.patch.object(self.runner_module, "stop_bot_container") as stop:
            sid = self.client.post("/start", json={"room_name": room}).json()["session_id"]
            self.client.post("/stop", json={"room_name": room})
        self.assertEqual(stop.call_args.args[1], sid)

    def test_docker_stop_does_not_wait_for_the_container_to_exit(self):
        # Docker's stop call blocks until the container exits (up to the 120 s grace);
        # ECS StopTask returns at once. meet gives /stop 10 s, so a slow exit under
        # load left the session open (integration test, 2026-10-01).
        import time as _time

        room = _room("docker-slow-stop")
        with mock.patch.dict(os.environ, self.DOCKER_ENV), \
             mock.patch.object(self.runner_module, "run_bot_container", return_value="meetlab-bot-x"), \
             mock.patch.object(self.runner_module, "stop_bot_container", side_effect=lambda *a: _time.sleep(3)):
            self.client.post("/start", json={"room_name": room})
            t0 = _time.monotonic()
            response = self.client.post("/stop", json={"room_name": room})
            took = _time.monotonic() - t0
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLess(took, 1.0, f"/stop waited {took:.1f}s for the container to exit")

    def test_without_a_dispatcher_start_refuses_and_leaves_no_running_session(self):
        # The in-process path is gone (#113): a runner with no dispatcher must
        # say so, not leave a 'running' session that no bot will ever join.
        room = _room("no-dispatcher")
        env = {k: v for k, v in os.environ.items() if k != "BOT_DISPATCHER"}
        with mock.patch.dict(os.environ, env, clear=True):
            refused = self.client.post("/start", json={"room_name": room})
        self.assertEqual(refused.status_code, 500, refused.text)
        self.assertIn("BOT_DISPATCHER", refused.json()["error"])
        again = self.client.post("/start", json={"room_name": room})
        self.assertNotIn("already_running", again.json(), "the refused start left a running session")

    # ------------------------------------------------------------------
    # Prepare for study: pre-warm the bot pool (capacity.py)
    # ------------------------------------------------------------------

    def _asg(self):
        import capacity
        asg = mock.Mock()
        asg.describe_auto_scaling_groups.return_value = {"AutoScalingGroups": [
            {"MinSize": 2, "MaxSize": 2, "DesiredCapacity": 2, "Instances": []}]}
        asg.describe_scheduled_actions.return_value = {"ScheduledUpdateGroupActions": []}
        return asg, capacity

    def test_prewarm_raises_the_bot_pool_minimum_until_the_end_time(self):
        import datetime as dt
        asg, capacity = self._asg()
        until = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()
        with mock.patch.dict(os.environ, {**self.ECS_ENV, "BOT_ASG_NAME": "meetlab-v2-staging-bots"}), \
             mock.patch.object(self.runner_module, "_asg_client", return_value=asg), \
             mock.patch.object(self.runner_module, "_ecs_client", return_value=mock.Mock()):
            response = self.client.post("/capacity/prewarm", json={"sessions": 4, "until": until})
        self.assertEqual(response.status_code, 200, response.text)
        asg.update_auto_scaling_group.assert_called_once_with(AutoScalingGroupName="meetlab-v2-staging-bots", MinSize=2)
        self.assertEqual(response.json()["instances"], 2)
        self.assertIn("ready_instances", response.json())

    def test_prewarm_rejects_a_bad_request(self):
        asg, _ = self._asg()
        with mock.patch.dict(os.environ, {**self.ECS_ENV, "BOT_ASG_NAME": "meetlab-v2-staging-bots"}), \
             mock.patch.object(self.runner_module, "_asg_client", return_value=asg), \
             mock.patch.object(self.runner_module, "_ecs_client", return_value=mock.Mock()):
            for body in ({"sessions": 4}, {"sessions": "four", "until": "2026-10-02T12:00:00+00:00"},
                         {"sessions": 4, "until": "2000-01-01T00:00:00+00:00"}, {"sessions": 4, "until": "2026-10-02T12:00:00"}):
                response = self.client.post("/capacity/prewarm", json=body)
                self.assertEqual(response.status_code, 400, (body, response.text))
        asg.update_auto_scaling_group.assert_not_called()

    def test_cancel_prewarm_returns_to_the_always_warm_baseline(self):
        asg, _ = self._asg()
        with mock.patch.dict(os.environ, {**self.ECS_ENV, "BOT_ASG_NAME": "meetlab-v2-staging-bots", "BOT_POOL_MIN": "1"}), \
             mock.patch.object(self.runner_module, "_asg_client", return_value=asg), \
             mock.patch.object(self.runner_module, "_ecs_client", return_value=mock.Mock()):
            response = self.client.delete("/capacity/prewarm")
        self.assertEqual(response.status_code, 200, response.text)
        asg.update_auto_scaling_group.assert_called_once_with(AutoScalingGroupName="meetlab-v2-staging-bots", MinSize=1)

    def test_local_bots_have_nothing_to_prewarm(self):
        # The class runs with the Docker dispatcher: each bot is a local container.
        status = self.client.get("/capacity")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertFalse(status.json()["available"])
        self.assertEqual(self.client.post("/capacity/prewarm", json={"sessions": 1, "until": "x"}).status_code, 409)

    def test_ecs_refusal_is_reported_not_swallowed(self):
        ecs = mock.Mock()
        ecs.run_task.return_value = {"tasks": [], "failures": [{"reason": "RESOURCE:MEMORY"}]}
        with mock.patch.dict(os.environ, self.ECS_ENV), \
             mock.patch.object(self.runner_module, "_ecs_client", return_value=ecs):
            response = self.client.post("/start", json={"room_name": _room("ecs-full-room")})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("RESOURCE:MEMORY", response.json()["error"])

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

    # ------------------------------------------------------------------
    # POST /events
    # ------------------------------------------------------------------

    def test_events_happy_path_returns_202(self):
        response = self.client.post(
            "/events",
            json={"type": "participant_joined", "room_name": "test-room"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json().get("status"), "accepted")

    def test_events_missing_type_returns_400(self):
        response = self.client.post("/events", json={"room_name": "test-room"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("type", response.json().get("error", ""))

    def test_events_whitespace_type_returns_400(self):
        response = self.client.post("/events", json={"type": "   ", "room_name": "test-room"})
        self.assertEqual(response.status_code, 400)

    def test_events_non_object_body_returns_400(self):
        response = self.client.post("/events", json=[])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "request body must be a JSON object")

    def test_events_invalid_json_returns_400(self):
        response = self.client.post(
            "/events",
            content="{",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------
    # GET /config
    # ------------------------------------------------------------------

    def test_config_get_returns_all_expected_fields(self):
        response = self.client.get("/config")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in (
            "scope", "system_prompt", "greeting",
            "stt_endpointing_ms", "user_speech_timeout_ms",
            "llm_model", "tts_voice", "tts_provider",
            "stt_model", "stt_vad_mode", "stt_delay", "auto_record",
            "session_limit_minutes",
        ):
            self.assertIn(key, body, f"GET /config response missing field: {key}")
        self.assertEqual(body["scope"], "global")

    def test_config_get_with_room_param_returns_scope_field(self):
        response = self.client.get("/config?room=some-room")
        self.assertEqual(response.status_code, 200)
        self.assertIn("scope", response.json())

    # ------------------------------------------------------------------
    # PUT /config
    # ------------------------------------------------------------------

    def test_config_put_updates_field_and_echoes_back(self):
        # Use an isolated scope so we don't corrupt global config for other tests.
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "llm_model": "gpt-4o-mini"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("llm_model"), "gpt-4o-mini")

    def test_every_column_default_survives_a_config_round_trip(self):
        """GET a config, PUT it back under a new scope — start-link's exact flow.

        Regression: the stt_endpointing_ms validator was an allow-list of
        (50, 100, 200). When the column default moved to 450 to fix RC2, every
        round-trip started returning 400 — so /api/start-link answered 502 and
        no participant could join through a link. Unit tests all passed; only the
        integration suite caught it.

        This asserts the invariant that was violated: a config the API hands out
        must be one the API accepts back. It holds for every field at once, so a
        future default that drifts outside its validator fails here.
        """
        from db.models import BotConfig

        source = f"rt-src-{uuid.uuid4().hex[:8]}"
        # Materialise a row carrying the column defaults.
        created = self.client.put("/config", json={"scope": source})
        self.assertEqual(created.status_code, 200, created.text)

        fetched = self.client.get(f"/config?scope={source}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        fields = fetched.json()

        # start-link copies everything it got, minus the scope, under a new one.
        fields.pop("scope", None)
        fields.pop("updated_at", None)
        echoed = self.client.put("/config", json={**fields, "scope": f"rt-dst-{uuid.uuid4().hex[:8]}"})

        self.assertEqual(
            echoed.status_code, 200,
            f"a config the API returned was rejected on write back: {echoed.text}",
        )
        # And specifically the field that broke: the default must be writable.
        self.assertEqual(
            echoed.json().get("stt_endpointing_ms"),
            BotConfig.__table__.c.stt_endpointing_ms.default.arg,
        )

    def test_config_put_accepts_the_clause_start(self):
        # B4: the voice starts at the first clause (clause_aggregator.py); still only known values.
        ok = self.client.put("/config", json={"tts_aggregation_mode": "clause"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json().get("tts_aggregation_mode"), "clause")
        self.assertEqual(self.client.put("/config", json={"tts_aggregation_mode": "paragraph"}).status_code, 400)

    def test_config_put_valid_stt_delay_accepted(self):
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "stt_delay": "low"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("stt_delay"), "low")

    def test_config_put_null_stt_delay_clears_field(self):
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "stt_delay": None},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json().get("stt_delay"))

    def test_config_put_auto_record_true_then_false(self):
        on = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "auto_record": True},
        )
        self.assertEqual(on.status_code, 200, on.text)
        self.assertEqual(on.json().get("auto_record"), True)
        off = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "auto_record": False},
        )
        self.assertEqual(off.status_code, 200, off.text)
        self.assertEqual(off.json().get("auto_record"), False)

    def test_config_put_session_limit_minutes_roundtrip(self):
        on = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "session_limit_minutes": 30},
        )
        self.assertEqual(on.status_code, 200, on.text)
        self.assertEqual(on.json().get("session_limit_minutes"), 30)
        # 0 clears the limit (unlimited) — must be accepted, not treated as "unset".
        off = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "session_limit_minutes": 0},
        )
        self.assertEqual(off.status_code, 200, off.text)
        self.assertEqual(off.json().get("session_limit_minutes"), 0)

    def test_config_put_session_limit_minutes_accepts_the_upper_bound(self):
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "session_limit_minutes": 1440},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("session_limit_minutes"), 1440)

    def test_config_room_scope_can_override_a_global_limit_with_unlimited(self):
        """A room row set to 0 means unlimited — it must not inherit global's cap.

        Guards against resolving this field with a falsy-fallback (`row.value or
        global.value`), which would silently re-impose the global limit on a room
        deliberately set to unlimited.
        """
        original_global = self.client.get("/config").json()["session_limit_minutes"]
        self.addCleanup(
            self.client.put,
            "/config",
            json={"scope": "global", "session_limit_minutes": original_global},
        )
        room = "test-limit-override-room"
        self.addCleanup(
            self.client.put, "/config", json={"scope": room, "session_limit_minutes": 0}
        )

        self.client.put("/config", json={"scope": "global", "session_limit_minutes": 30})
        self.client.put("/config", json={"scope": room, "session_limit_minutes": 0})

        self.assertEqual(self.client.get("/config").json()["session_limit_minutes"], 30)
        self.assertEqual(
            self.client.get(f"/config?room={room}").json()["session_limit_minutes"],
            0,
            "a room set to unlimited must not fall back to the global limit",
        )

    def test_config_unknown_room_falls_back_to_the_global_limit(self):
        original_global = self.client.get("/config").json()["session_limit_minutes"]
        self.addCleanup(
            self.client.put,
            "/config",
            json={"scope": "global", "session_limit_minutes": original_global},
        )

        self.client.put("/config", json={"scope": "global", "session_limit_minutes": 25})
        self.assertEqual(
            self.client.get("/config?room=room-with-no-config-row").json()[
                "session_limit_minutes"
            ],
            25,
        )

    def test_config_put_session_limit_minutes_rejects_bad_values(self):
        # bool is a subclass of int in Python — True must NOT sneak through as 1.
        for bad in ("30", True, -1, 1441, 2.5, None):
            with self.subTest(bad=bad):
                response = self.client.put(
                    "/config",
                    json={"scope": "test-runner-scope", "session_limit_minutes": bad},
                )
                self.assertEqual(response.status_code, 400, f"{bad!r} was accepted")
                self.assertIn("session_limit_minutes", response.json().get("error", ""))

    def test_config_put_invalid_auto_record_returns_400(self):
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "auto_record": "yes"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("auto_record", response.json().get("error", ""))

    def test_config_put_invalid_stt_model_returns_400(self):
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "stt_model": "not-a-model"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("stt_model", response.json().get("error", ""))

    def test_config_put_local_whisper_stt_model_refused(self):
        # Local Whisper was never v2's: production runs Parakeet on our server (CPU or GPU)
        # and dev mirrors it (2026-10-06), so a whisper-* model is no longer offered.
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "stt_model": "whisper-turbo"},
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_config_put_parakeet_stt_model_accepted(self):
        # parakeet-* models run on the Parakeet NIM (Experiments 6-7)
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "stt_model": "parakeet-tdt-0.6b-v2"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("stt_model"), "parakeet-tdt-0.6b-v2")

    def test_config_put_parakeet_unified_stt_model_accepted(self):
        # parakeet-unified-en-0.6b: NVIDIA's offline+streaming unified English model,
        # served (offline) by the Parakeet NIM.
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "stt_model": "parakeet-unified-en-0.6b"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("stt_model"), "parakeet-unified-en-0.6b")

    def test_config_put_smart_turn_wait_within_half_a_second_to_five(self):
        scope = f"wait-{uuid.uuid4().hex[:6]}"
        ok = self.client.put("/config", json={"scope": scope, "smart_turn_wait_ms": 1500})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["smart_turn_wait_ms"], 1500)
        for bad in (100, 9000, "soon"):
            self.assertEqual(self.client.put("/config", json={"scope": scope, "smart_turn_wait_ms": bad}).status_code, 400, bad)

    def test_config_put_accepts_smart_turn_and_refuses_anything_else(self):
        scope = f"turn-{uuid.uuid4().hex[:6]}"
        ok = self.client.put("/config", json={"scope": scope, "turn_detection": "smart_turn"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["turn_detection"], "smart_turn")
        self.assertEqual(self.client.put("/config", json={"scope": scope, "turn_detection": "magic"}).status_code, 400)

    def test_config_put_accepts_our_kokoro_voice(self):
        response = self.client.put("/config", json={"scope": f"kokoro-{uuid.uuid4().hex[:6]}", "tts_provider": "kokoro"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tts_provider"], "kokoro")

    def test_config_put_invalid_tts_provider_returns_400(self):
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "tts_provider": "google"},
        )
        self.assertEqual(response.status_code, 400)

    def test_config_put_invalid_stt_delay_returns_400(self):
        response = self.client.put(
            "/config",
            json={"scope": "test-runner-scope", "stt_delay": "turbo"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("stt_delay", response.json().get("error", ""))

    def test_config_put_non_object_body_returns_400(self):
        response = self.client.put("/config", json=[])
        self.assertEqual(response.status_code, 400)

    def test_start_persists_custom_data_on_conversation(self):
        import uuid as _uuid

        marker = f"meta-{_uuid.uuid4().hex[:10]}"
        response = self.client.post(
            "/start",
            json={
                "room_name": f"room-{marker}",
                "custom_data": {"requested_by": "start-link", "bot_config_scope": marker},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        session_id = response.json()["session_id"]

        convs = self.client.get("/conversations", params={"limit": 200}).json()["conversations"]
        conv = next((c for c in convs if c["id"] == session_id), None)
        self.assertIsNotNone(conv, "started conversation missing from /conversations")
        self.assertEqual(conv["meta"].get("bot_config_scope"), marker)
        self.assertEqual(conv["meta"].get("requested_by"), "start-link")

    def test_start_without_custom_data_has_empty_meta(self):
        response = self.client.post("/start", json={"room_name": _room("room-no-meta")})
        self.assertEqual(response.status_code, 200, response.text)
        session_id = response.json()["session_id"]
        convs = self.client.get("/conversations", params={"limit": 200}).json()["conversations"]
        conv = next((c for c in convs if c["id"] == session_id), None)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["meta"], {})

    # ------------------------------------------------------------------
    # GET /configs — list all bot_config rows by scope (start-link pool picker)
    # ------------------------------------------------------------------

    def test_configs_list_contains_upserted_scope(self):
        import uuid as _uuid

        scope = f"cfglist-{_uuid.uuid4().hex[:10]}"
        put = self.client.put("/config", json={"scope": scope, "greeting": "list me"})
        self.assertEqual(put.status_code, 200, put.text)

        response = self.client.get("/configs")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsInstance(body.get("configs"), list)
        entry = next((c for c in body["configs"] if c.get("scope") == scope), None)
        self.assertIsNotNone(entry, f"scope {scope} missing from /configs")

    def test_configs_list_sorted_by_scope_with_shape(self):
        response = self.client.get("/configs")
        self.assertEqual(response.status_code, 200, response.text)
        configs = response.json()["configs"]
        scopes = [c["scope"] for c in configs]
        self.assertEqual(scopes, sorted(scopes))
        for c in configs:
            self.assertEqual(set(c.keys()), {"scope", "updated_at"})


if __name__ == "__main__":
    unittest.main()
