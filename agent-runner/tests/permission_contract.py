"""What each deployed staging role must, and must never, be allowed to do.

Every AWS call the code makes at runtime is listed in ALLOW, and a few calls each
role must never make (another project's resources, another task family, CI roles)
in DENY. IAM simulates each one against the deployed role, permissions boundary
included, so a missing permission fails here in seconds instead of in a live bot
(ecs:ListTasks, 2026-10-01).

Adding an AWS call to the code means adding it here.

    uv run --no-project --with boto3 python agent-runner/tests/permission_contract.py

Needs iam:SimulatePrincipalPolicy on the meetlab-v2-staging-* roles and users.
"""
import os
import sys
from dataclasses import dataclass, field

ACCOUNT = os.getenv("AWS_ACCOUNT_ID", "848180123498")
ARN = f"arn:aws:ecs:us-east-1:{ACCOUNT}"
CLUSTER = f"{ARN}:cluster/meetlab-v2-staging"
OTHER_CLUSTER = f"{ARN}:cluster/bcfg-twilio-bot-dev-Cluster-c7MF3TrrR6WL"  # another lab project
ROLE = f"arn:aws:iam::{ACCOUNT}:role"
RUNNER, BOT = "meetlab-v2-staging-runner-task", "meetlab-v2-staging-bot-task"
EGRESS = "user/meetlab-v2-staging-egress-writer"  # LiveKit uploads video with its key
EXECUTION = "meetlab-v2-staging-task-execution"  # ECS: image pulls and task secrets
SECRET = f"arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret"
GROUP = f"arn:aws:autoscaling:us-east-1:{ACCOUNT}:autoScalingGroup:00000000-0000-0000-0000-000000000000:autoScalingGroupName"
IN_CLUSTER = {"ecs:cluster": CLUSTER}
MEDIA = f"arn:aws:s3:::meetlab-v2-staging-media-{ACCOUNT}"
TO_ECS = {"iam:PassedToService": "ecs-tasks.amazonaws.com"}


@dataclass(frozen=True)
class Call:
    role: str
    action: str
    resource: str
    context: dict = field(default_factory=dict, hash=False, compare=False)


ALLOW = [
    # dispatch.py run_bot_task / stop_bot_task, heartbeat.py via runner._stop_bot
    Call(RUNNER, "ecs:RunTask", f"{ARN}:task-definition/meetlab-v2-staging-bot:1", IN_CLUSTER),
    Call(RUNNER, "iam:PassRole", f"{ROLE}/meetlab-v2-staging-task-execution", TO_ECS),
    Call(RUNNER, "iam:PassRole", f"{ROLE}/{BOT}", TO_ECS),
    Call(RUNNER, "ecs:ListTasks", "*", IN_CLUSTER),
    Call(RUNNER, "ecs:StopTask", f"{ARN}:task/meetlab-v2-staging/0000"),
    Call(RUNNER, "ecs:DescribeTasks", f"{ARN}:task/meetlab-v2-staging/0000"),
    # storage.py: per-speaker audio written by the bot, read back by the runner
    Call(BOT, "s3:PutObject", f"{MEDIA}/recordings/speaker.wav"),
    Call(RUNNER, "s3:GetObject", f"{MEDIA}/recordings/speaker.wav"),
    # LiveKit Cloud egress, with the key the runner sends in each recording request
    Call(EGRESS, "s3:PutObject", f"{MEDIA}/recordings/room-recording.mp4"),
    Call(EGRESS, "s3:AbortMultipartUpload", f"{MEDIA}/recordings/room-recording.mp4"),
    # capacity.py: Prepare for study warms the bot pool, and AWS cools it at the end time
    *[Call(RUNNER, f"autoscaling:{a}", f"{GROUP}/meetlab-v2-staging-bots") for a in
      ("UpdateAutoScalingGroup", "PutScheduledUpdateGroupAction", "DeleteScheduledAction")],
    Call(RUNNER, "autoscaling:DescribeAutoScalingGroups", "*"),
    Call(RUNNER, "autoscaling:DescribeScheduledActions", "*"),
    # The STT NIM's image pull and NGC_API_KEY (staging stt_nim.tf)
    Call(EXECUTION, "secretsmanager:GetSecretValue", f"{SECRET}:meetlab-v2/staging/ngc-AbCdEf"),
    # ECS Exec into a bot (the kill9 acceptance scenario; debugging)
    *[Call(BOT, f"ssmmessages:{a}", "*") for a in
      ("CreateControlChannel", "CreateDataChannel", "OpenControlChannel", "OpenDataChannel")],
]

DENY = [
    Call(RUNNER, "ecs:RunTask", f"{ARN}:task-definition/meetlab-v2-staging-meet:1", IN_CLUSTER),
    Call(RUNNER, "ecs:StopTask", f"{OTHER_CLUSTER.replace(':cluster/', ':task/')}/0000"),
    Call(RUNNER, "ecs:ListTasks", "*", {"ecs:cluster": OTHER_CLUSTER}),
    Call(RUNNER, "iam:PassRole", f"{ROLE}/meetlab-v2-tf-apply", TO_ECS),
    Call(BOT, "ecs:RunTask", f"{ARN}:task-definition/meetlab-v2-staging-bot:1", IN_CLUSTER),
    Call(BOT, "s3:GetObject", f"{MEDIA}/recordings/speaker.wav"),
    Call(BOT, "s3:DeleteObject", f"{MEDIA}/recordings/speaker.wav"),
    Call(BOT, "s3:PutObject", f"{MEDIA}/elsewhere/speaker.wav"),
    Call(RUNNER, "s3:PutObject", f"{MEDIA}/recordings/speaker.wav"),
    Call(EGRESS, "s3:GetObject", f"{MEDIA}/recordings/room-recording.mp4"),
    Call(EGRESS, "s3:DeleteObject", f"{MEDIA}/recordings/room-recording.mp4"),
    Call(EGRESS, "s3:ListBucket", MEDIA),
    Call(EGRESS, "s3:PutObject", f"{MEDIA}/elsewhere/room-recording.mp4"),
    Call(EGRESS, "s3:PutAccountPublicAccessBlock", "*"),  # v1's egress key could
    Call(RUNNER, "s3:DeleteObject", f"{MEDIA}/recordings/speaker.wav"),
    Call(EXECUTION, "secretsmanager:GetSecretValue", f"{SECRET}:meetlab-v2/staging/other-AbCdEf"),
    Call(RUNNER, "autoscaling:UpdateAutoScalingGroup", f"{GROUP}/meetlab-v2-staging-ecs"),  # services
    Call(RUNNER, "autoscaling:UpdateAutoScalingGroup", f"{GROUP}/meetlab-v2-staging-stt-nim"),  # the GPU
    Call(BOT, "autoscaling:UpdateAutoScalingGroup", f"{GROUP}/meetlab-v2-staging-bots"),
    Call(BOT, "ssm:GetParameter", f"arn:aws:ssm:us-east-1:{ACCOUNT}:parameter/copilot/bcfg-twilio-bot/dev/secrets/OPENAI_API_KEY"),
]


def violations(allow, deny, simulate):
    """[(call, why)] for each call whose decision breaks the contract."""
    out = []
    for c in allow:
        d = simulate(c)
        if d != "allowed":
            out.append((c, f"needed but {d}"))
    for c in deny:
        d = simulate(c)
        if d == "allowed":
            out.append((c, "must be denied but allowed"))
    return out


def principal_arn(principal: str) -> str:
    """'user/<name>' is an IAM user; anything else is a role name."""
    return f"arn:aws:iam::{ACCOUNT}:{principal}" if principal.startswith("user/") else f"{ROLE}/{principal}"


def iam_simulate(iam):
    def simulate(c: Call) -> str:
        r = iam.simulate_principal_policy(
            PolicySourceArn=principal_arn(c.role), ActionNames=[c.action], ResourceArns=[c.resource],
            ContextEntries=[{"ContextKeyName": k, "ContextKeyValues": [v], "ContextKeyType": "string"}
                            for k, v in c.context.items()])
        return r["EvaluationResults"][0]["EvalDecision"]
    return simulate


if __name__ == "__main__":
    import boto3

    found = violations(ALLOW, DENY, iam_simulate(boto3.client("iam")))
    for c, why in found:
        print(f"FAIL {c.role} {c.action} {c.resource}: {why}")
    print(f"permission contract: {len(ALLOW)} needed, {len(DENY)} forbidden, {len(found)} violation(s)")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### Permission contract\n\n{len(ALLOW)} needed, {len(DENY)} forbidden: "
                    f"**{'PASS' if not found else f'{len(found)} violation(s)'}**\n\n")
            f.writelines(f"- `{c.role}` `{c.action}` on `{c.resource}`: {why}\n" for c, why in found)
    sys.exit(1 if found else 0)
