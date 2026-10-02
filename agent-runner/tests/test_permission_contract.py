"""The permission contract checker: what each deployed role must, and must never, be
allowed to do (step 4c, iteration 3 of the 2026-10-01 plan).

Most of 4c's live misses were permissions that only fail when the call is made
(ecs:ListTasks, 2026-10-01). permission_contract.py asks IAM to simulate every call
the code makes against the deployed roles, in seconds, before the live scenarios.

Pure/offline: IAM is a fake. Run with:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_permission_contract -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from permission_contract import ALLOW, DENY, Call, violations  # noqa: E402


def fake_iam(allowed: set):
    def simulate(call: Call) -> str:
        return "allowed" if (call.role, call.action, call.resource) in allowed else "implicitDeny"
    return simulate


A = Call("runner", "ecs:ListTasks", "*", {"ecs:cluster": "c"})
B = Call("runner", "ecs:RunTask", "other-family", {"ecs:cluster": "c"})


class ViolationsTest(unittest.TestCase):
    def test_a_call_the_code_makes_but_the_role_cannot_is_a_violation(self):
        # The ListTasks bug: the reconciler needed it, the role lacked it.
        v = violations([A], [], fake_iam(set()))
        self.assertEqual([(c.action, why) for c, why in v], [("ecs:ListTasks", "needed but implicitDeny")])

    def test_a_call_the_role_must_never_make_but_can_is_a_violation(self):
        v = violations([], [B], fake_iam({("runner", "ecs:RunTask", "other-family")}))
        self.assertEqual([(c.action, why) for c, why in v], [("ecs:RunTask", "must be denied but allowed")])

    def test_a_role_that_matches_its_contract_has_no_violations(self):
        self.assertEqual(violations([A], [B], fake_iam({("runner", "ecs:ListTasks", "*")})), [])


class ContractTest(unittest.TestCase):
    def test_the_contract_covers_the_calls_that_failed_live(self):
        needed = {(c.role, c.action) for c in ALLOW}
        self.assertIn(("meetlab-v2-staging-runner-task", "ecs:ListTasks"), needed)   # #102
        self.assertIn(("meetlab-v2-staging-runner-task", "ecs:RunTask"), needed)     # 4c PR 1
        self.assertIn(("meetlab-v2-staging-runner-task", "ecs:StopTask"), needed)    # heartbeat stop
        self.assertIn(("meetlab-v2-staging-bot-task", "ssmmessages:OpenDataChannel"), needed)  # ECS Exec
        self.assertIn(("meetlab-v2-staging-bot-task", "s3:PutObject"), needed)      # per-speaker audio
        self.assertIn(("meetlab-v2-staging-runner-task", "s3:GetObject"), needed)   # downloads

    def test_the_livekit_egress_key_can_only_add_recordings(self):
        # v1's egress key could read, list and delete every recording and switch off
        # the account-wide S3 public-access block; v2's must not be able to.
        egress = "user/meetlab-v2-staging-egress-writer"
        self.assertIn((egress, "s3:PutObject"), {(c.role, c.action) for c in ALLOW})
        forbidden = {(c.role, c.action) for c in DENY}
        for action in ("s3:GetObject", "s3:DeleteObject", "s3:ListBucket", "s3:PutAccountPublicAccessBlock"):
            self.assertIn((egress, action), forbidden)

    def test_users_and_roles_are_simulated_by_their_own_arns(self):
        from permission_contract import principal_arn
        self.assertTrue(principal_arn("user/meetlab-v2-staging-egress-writer").endswith(":user/meetlab-v2-staging-egress-writer"))
        self.assertTrue(principal_arn("meetlab-v2-staging-bot-task").endswith(":role/meetlab-v2-staging-bot-task"))

    def test_recordings_cannot_be_read_or_deleted_by_the_bot(self):
        forbidden = {(c.role, c.action) for c in DENY}
        self.assertIn(("meetlab-v2-staging-bot-task", "s3:DeleteObject"), forbidden)
        self.assertIn(("meetlab-v2-staging-runner-task", "s3:DeleteObject"), forbidden)

    def test_the_execution_role_reads_the_ngc_secret_and_no_other(self):
        # ECS pulls the STT NIM image and passes its NGC key with the execution role.
        ex = "meetlab-v2-staging-task-execution"
        allowed = [c.resource for c in ALLOW if (c.role, c.action) == (ex, "secretsmanager:GetSecretValue")]
        denied = [c.resource for c in DENY if (c.role, c.action) == (ex, "secretsmanager:GetSecretValue")]
        self.assertTrue(allowed and all(":secret:meetlab-v2/staging/ngc-" in r for r in allowed))
        self.assertTrue(denied and not any(":secret:meetlab-v2/staging/ngc-" in r for r in denied))

    def test_the_runner_can_prewarm_the_bot_pool_and_no_other_group(self):
        runner = "meetlab-v2-staging-runner-task"
        allowed = {c.resource for c in ALLOW if (c.role, c.action) == (runner, "autoscaling:UpdateAutoScalingGroup")}
        denied = {c.resource for c in DENY if (c.role, c.action) == (runner, "autoscaling:UpdateAutoScalingGroup")}
        self.assertTrue(allowed and all(r.endswith("/meetlab-v2-staging-bots") for r in allowed))
        self.assertTrue({r.rsplit("/", 1)[-1] for r in denied} >= {"meetlab-v2-staging-ecs", "meetlab-v2-staging-stt-nim"})

    def test_the_runner_can_see_which_machines_ecs_can_use_in_its_own_cluster_only(self):
        runner = "meetlab-v2-staging-runner-task"
        calls = lambda lst: {c.resource for c in lst if (c.role, c.action) == (runner, "ecs:ListContainerInstances")}
        self.assertTrue(calls(ALLOW) and all(r.endswith("cluster/meetlab-v2-staging") for r in calls(ALLOW)))
        self.assertTrue(calls(DENY) and not any(r.endswith("cluster/meetlab-v2-staging") for r in calls(DENY)))

    def test_every_role_has_something_it_must_never_do(self):
        self.assertTrue({c.role for c in ALLOW} <= {c.role for c in DENY})


if __name__ == "__main__":
    unittest.main()
