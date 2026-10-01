"""Unit tests for running a bot as its own process (step 4c, PR 1).

  bot_token.py  the LiveKit token a bot joins with, shared by the runner and bot_task
  bot_task.py   `python -m bot_task --session-id X`: one process per meeting
  dispatch.py   the runner asking ECS for that process

Pure/offline: no LiveKit, no AWS, no database. Run with:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_bot_task -v
"""
import time
import unittest
from types import SimpleNamespace

import jwt

from bot_task import parse_args, runner_args_for
from bot_token import bot_token
from dispatch import DispatchError, EcsBotTarget, run_bot_task


class BotTokenTest(unittest.TestCase):
    def claims(self, **kw):
        tok = bot_token(room_name="r1", identity="bot_r1_abc", key="k", secret="s" * 32, ttl_minutes=15, **kw)
        return jwt.decode(tok, "s" * 32, algorithms=["HS256"])

    def test_grants_join_publish_and_agent_for_one_room_only(self):
        c = self.claims()
        self.assertEqual(c["sub"], "bot_r1_abc")
        self.assertEqual(c["video"]["room"], "r1")
        self.assertTrue(c["video"]["roomJoin"])
        self.assertTrue(c["video"]["canPublish"])
        self.assertTrue(c["video"]["agent"])

    def test_expires_after_the_ttl(self):
        self.assertAlmostEqual(self.claims()["exp"] - time.time(), 15 * 60, delta=5)


class BotTaskTest(unittest.TestCase):
    def test_session_id_is_required(self):
        with self.assertRaises(SystemExit):
            parse_args([])
        self.assertEqual(parse_args(["--session-id", "abc"]).session_id, "abc")

    def test_runner_args_come_from_the_session_row(self):
        row = SimpleNamespace(id="s1", room_name="r1", bot_identity="bot_r1_abc", meta={"requested_by": "concierge"})
        args = runner_args_for(row, url="wss://lk", token="t")
        self.assertEqual((args.session_id, args.room_name, args.bot_identity, args.url, args.token),
                         ("s1", "r1", "bot_r1_abc", "wss://lk", "t"))


class FakeEcs:
    def __init__(self, response):
        self.response, self.calls = response, []

    def run_task(self, **kw):
        self.calls.append(kw)
        return self.response


TARGET = EcsBotTarget(cluster="meetlab-v2-staging", task_definition="meetlab-v2-staging-bot",
                      capacity_provider="meetlab-v2-staging-bots", container="bot")


class DispatchTest(unittest.TestCase):
    def test_a_retried_start_cannot_launch_a_second_bot(self):
        # ECS returns the original task for a repeated clientToken, so the session ID
        # is the idempotency key: a retry of the same session is the same bot.
        ecs = FakeEcs({"tasks": [{"taskArn": "arn:task/1"}], "failures": []})
        run_bot_task(ecs, "sess-123", TARGET)
        self.assertEqual(ecs.calls[0]["clientToken"], "sess-123")

    def test_the_task_is_told_which_session_it_runs(self):
        ecs = FakeEcs({"tasks": [{"taskArn": "arn:task/1"}], "failures": []})
        run_bot_task(ecs, "sess-123", TARGET)
        call = ecs.calls[0]
        override = call["overrides"]["containerOverrides"][0]
        self.assertEqual(override["name"], "bot")
        self.assertEqual(override["command"][-2:], ["--session-id", "sess-123"])
        self.assertEqual(call["startedBy"], "sess-123")
        self.assertEqual(call["capacityProviderStrategy"], [{"capacityProvider": "meetlab-v2-staging-bots", "weight": 1}])

    def test_returns_the_task_arn(self):
        ecs = FakeEcs({"tasks": [{"taskArn": "arn:task/1"}], "failures": []})
        self.assertEqual(run_bot_task(ecs, "sess-123", TARGET), "arn:task/1")

    def test_an_ecs_refusal_is_an_error_not_a_silent_success(self):
        # e.g. no capacity: RunTask answers 200 with failures and no task.
        ecs = FakeEcs({"tasks": [], "failures": [{"reason": "RESOURCE:CPU"}]})
        with self.assertRaises(DispatchError) as ctx:
            run_bot_task(ecs, "sess-123", TARGET)
        self.assertIn("RESOURCE:CPU", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
