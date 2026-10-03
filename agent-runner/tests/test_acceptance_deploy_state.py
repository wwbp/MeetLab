"""A GPU service's deploy, read from ECS's own description (acceptance_staging.deploy_state):
ready, still coming up, or one that will never finish, said at once with ECS's reason."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
from acceptance_staging import deploy_state  # noqa: E402

T0 = datetime(2026, 10, 3, 3, 45, tzinfo=timezone.utc)


def service(rollout="IN_PROGRESS", events=(), old=False, reason=""):
    deployments = [{"status": "PRIMARY", "rolloutState": rollout, "rolloutStateReason": reason, "createdAt": T0}]
    if old:
        deployments.append({"status": "ACTIVE", "rolloutState": "COMPLETED", "createdAt": T0 - timedelta(hours=2)})
    return {"deployments": deployments, "events": [{"createdAt": T0 + timedelta(minutes=m), "message": msg} for m, msg in events]}


class TestDeployState(unittest.TestCase):
    def test_a_finished_deploy_is_ready(self):
        self.assertEqual(deploy_state(service("COMPLETED")), ("ready", ""))

    def test_a_deploy_coming_up_is_waited_for(self):
        self.assertEqual(deploy_state(service(events=[(1, "(service x) has started 1 tasks: (task a).")]))[0], "waiting")

    def test_a_task_that_cannot_be_placed_fails_at_once_with_ecs_reason(self):
        # The FP8 deploy, 2026-10-03: one GPU machine, the old task holding it.
        state, why = deploy_state(service(old=True, events=[
            (30, "(service x) was unable to place a task. Reason: TaskFailedToStart: RESOURCE:GPU."),
            (1, "(service x) has started 1 tasks: (task a)."),
        ]))
        self.assertEqual(state, "failed")
        self.assertIn("RESOURCE:GPU", why)

    def test_placement_trouble_from_an_earlier_deploy_is_history(self):
        self.assertEqual(deploy_state(service(events=[(-60, "(service x) was unable to place a task. Reason: x")]))[0], "waiting")

    def test_a_deploy_the_circuit_breaker_failed_is_failed(self):
        state, why = deploy_state(service("FAILED", reason="ECS deployment circuit breaker: tasks failed to start."))
        self.assertEqual(state, "failed")
        self.assertIn("circuit breaker", why)


if __name__ == "__main__":
    unittest.main()
