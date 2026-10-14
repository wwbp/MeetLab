"""Local bots as their own containers, mirroring ECS (plan iteration 7; decision D5).

With BOT_DISPATCHER=docker the runner starts each meeting's bot as a container that
copies the runner's own one (same image, mounts, network and environment; command
bot_task), through a socket proxy that allows only container create, start, stop
and inspect. The container name carries the session ID, so a retried start is the
same container, as clientToken makes it the same ECS task.

Pure/offline: the Docker API is a fake. Run with:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_docker_dispatch -v
"""
import unittest

from meetlab_api.dispatch import DispatchError, bot_container_spec, run_bot_container, stop_bot_container

ME = {  # the runner's own container, as GET /containers/{id}/json reports it
    "Image": "sha256:runner-image",
    "Config": {"Env": ["DATABASE_URL=postgresql+asyncpg://db", "LIVEKIT_URL=ws://lk:7880",
                       "PATH=/venv/bin:/usr/bin", "PYTHONPATH=/app/packages/core:/app/services/api"]},
    "Mounts": [
        {"Type": "bind", "Source": "/Users/x/MeetLab", "Destination": "/app"},
        {"Type": "volume", "Name": "devcontainer_agent-runner-venv", "Source": "/var/lib/docker/v", "Destination": "/app/.venv"},
    ],
    "NetworkSettings": {"Networks": {"devcontainer_default": {}}},
}


class FakeDocker:
    def __init__(self, create_status=201, start_status=204, stop_status=204):
        self.calls, self.create_status, self.start_status, self.stop_status = [], create_status, start_status, stop_status

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path == "/containers/runner-host/json":
            return 200, ME
        if path.startswith("/containers/create"):
            return self.create_status, {"Id": "c1"} if self.create_status == 201 else {"message": "conflict"}
        if path.endswith("/start"):
            return self.start_status, {}
        if "/stop" in path:
            return self.stop_status, {}
        return 404, {"message": "no such path"}


class SpecTest(unittest.TestCase):
    def test_the_bot_container_is_the_runner_container_with_the_bot_image_and_command(self):
        # The bot has its own image (Pipecat; the API's has none), as in ECS; mounts, env and
        # network are the runner's, so a local code edit reaches local bots.
        spec = bot_container_spec(ME, "sess-1", "meetlab-bot:local")
        self.assertEqual(spec["Image"], "meetlab-bot:local")
        self.assertEqual(spec["Cmd"][-2:], ["--session-id", "sess-1"])
        # The runner's settings, not its image's: the bot image sets its own PATH and PYTHONPATH
        # (the API's has no services/bot on it).
        self.assertEqual(spec["Env"], ["DATABASE_URL=postgresql+asyncpg://db", "LIVEKIT_URL=ws://lk:7880"])
        self.assertEqual(spec["HostConfig"]["NetworkMode"], "devcontainer_default")
        self.assertIn({"Type": "bind", "Source": "/Users/x/MeetLab", "Target": "/app"},
                      spec["HostConfig"]["Mounts"])
        self.assertIn({"Type": "volume", "Source": "devcontainer_agent-runner-venv", "Target": "/app/.venv"},
                      spec["HostConfig"]["Mounts"])

    def test_it_cleans_up_after_itself_and_gets_time_to_finish(self):
        spec = bot_container_spec(ME, "sess-1", "meetlab-bot:local")
        self.assertTrue(spec["HostConfig"]["AutoRemove"])
        self.assertEqual(spec["StopTimeout"], 120)  # same as the ECS stopTimeout


class RunTest(unittest.TestCase):
    def test_an_init_is_pid_1_so_the_bot_can_be_killed_and_reaped(self):
        # Since the image runs python directly (no uv wrapper), python would be PID 1, and
        # PID 1 ignores a SIGKILL sent inside its container (2026-10-07: kill9 hung).
        self.assertIs(bot_container_spec(ME, "sess-1", "meetlab-bot:local")["HostConfig"]["Init"], True)

    def test_start_creates_and_starts_a_container_named_for_the_session(self):
        docker = FakeDocker()
        self.assertEqual(run_bot_container(docker, "sess-1", "runner-host", "meetlab-bot:local"), "meetlab-bot-sess-1")
        paths = [p for _, p, _ in docker.calls]
        self.assertIn("/containers/create?name=meetlab-bot-sess-1", paths)
        self.assertIn("/containers/meetlab-bot-sess-1/start", paths)

    def test_a_retried_start_is_the_same_container(self):
        # 409: a container with that name exists; 304: it is already running.
        docker = FakeDocker(create_status=409, start_status=304)
        self.assertEqual(run_bot_container(docker, "sess-1", "runner-host", "meetlab-bot:local"), "meetlab-bot-sess-1")

    def test_no_bot_image_is_a_clear_dispatch_error(self):
        with self.assertRaisesRegex(DispatchError, "BOT_IMAGE"):
            run_bot_container(FakeDocker(), "sess-1", "runner-host", "")

    def test_a_refusal_is_an_error_not_a_silent_success(self):
        with self.assertRaises(DispatchError):
            run_bot_container(FakeDocker(create_status=403), "sess-1", "runner-host", "meetlab-bot:local")


class StopTest(unittest.TestCase):
    def test_stop_sends_sigterm_with_the_full_grace_period(self):
        docker = FakeDocker()
        stop_bot_container(docker, "sess-1")
        self.assertIn(("POST", "/containers/meetlab-bot-sess-1/stop?t=120", None), docker.calls)

    def test_stopping_a_bot_that_is_already_gone_is_fine(self):
        stop_bot_container(FakeDocker(stop_status=404), "sess-1")
        stop_bot_container(FakeDocker(stop_status=304), "sess-1")


if __name__ == "__main__":
    unittest.main()
