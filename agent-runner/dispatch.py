"""The runner asking ECS to run one bot task for one session.

The session ID is the RunTask clientToken: ECS returns the original task for a
repeated token, so a retried start can never put a second bot in the meeting.
"""
from dataclasses import dataclass
from typing import Mapping


class DispatchError(RuntimeError):
    """ECS did not start the task (no capacity, bad definition, ...)."""


@dataclass(frozen=True)
class EcsBotTarget:
    cluster: str
    task_definition: str
    capacity_provider: str
    container: str = "bot"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "EcsBotTarget":
        return cls(cluster=env["ECS_CLUSTER"], task_definition=env["BOT_TASK_DEFINITION"],
                   capacity_provider=env["BOT_CAPACITY_PROVIDER"])


def run_bot_task(ecs, session_id: str, target: EcsBotTarget) -> str:
    resp = ecs.run_task(
        cluster=target.cluster,
        taskDefinition=target.task_definition,
        capacityProviderStrategy=[{"capacityProvider": target.capacity_provider, "weight": 1}],
        clientToken=session_id,
        startedBy=session_id,
        enableECSManagedTags=True,
        propagateTags="TASK_DEFINITION",
        overrides={"containerOverrides": [{
            "name": target.container,
            "command": ["uv", "run", "python", "-m", "bot_task", "--session-id", session_id],
        }]},
    )
    tasks, failures = resp.get("tasks") or [], resp.get("failures") or []
    if failures or not tasks:
        raise DispatchError(f"RunTask for session {session_id} failed: {failures or 'no task returned'}")
    return tasks[0]["taskArn"]
