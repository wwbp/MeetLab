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
        enableExecuteCommand=True,  # ECS Exec: debugging, and kill -9 in the heartbeat acceptance test
        propagateTags="TASK_DEFINITION",
        overrides={"containerOverrides": [{
            "name": target.container,
            "command": ["python", "-m", "bot_task", "--session-id", session_id],
        }]},
    )
    tasks, failures = resp.get("tasks") or [], resp.get("failures") or []
    if failures or not tasks:
        raise DispatchError(f"RunTask for session {session_id} failed: {failures or 'no task returned'}")
    return tasks[0]["taskArn"]


def stop_bot_task(ecs, session_id: str, target: EcsBotTarget) -> None:
    """Stop the task started for session_id, if any. SIGTERM: the bot ends gracefully."""
    for arn in ecs.list_tasks(cluster=target.cluster, startedBy=session_id)["taskArns"]:
        ecs.stop_task(cluster=target.cluster, task=arn, reason=f"session {session_id} stopped")


# --- Local: one Docker container per meeting, mirroring ECS (decision D5) -------------
#
# The bot container copies the runner's own container (same image, mounts, network,
# environment) with the bot_task command, so a local code edit reaches local bots as
# it reaches the runner. agent-runner reaches Docker only through a socket proxy that
# allows container create, start, stop and inspect (.devcontainer/docker-compose.yml).

def _container_name(session_id: str) -> str:
    return f"meetlab-bot-{session_id}"


def bot_container_spec(me: dict, session_id: str) -> dict:
    mounts = [
        {"Type": m["Type"], "Source": m["Name"] if m["Type"] == "volume" else m["Source"], "Target": m["Destination"]}
        for m in me.get("Mounts", []) if m["Type"] in ("bind", "volume")
    ]
    return {
        "Image": me["Image"],
        "Cmd": ["python", "-m", "bot_task", "--session-id", session_id],
        "Env": me["Config"]["Env"],
        "Labels": {"meetlab.session": session_id},
        "StopTimeout": 120,  # same as the ECS stopTimeout: SIGTERM, then this long
        "HostConfig": {
            "Mounts": mounts,
            "NetworkMode": next(iter(me["NetworkSettings"]["Networks"])),
            "AutoRemove": True,
            "Init": True,  # an init as PID 1, as in ECS (initProcessEnabled): signals reach python
        },
    }


def run_bot_container(docker, session_id: str, runner_container: str) -> str:
    """Start the bot container for session_id; a retried start is the same container."""
    status, me = docker.call("GET", f"/containers/{runner_container}/json")
    if status != 200:
        raise DispatchError(f"cannot inspect the runner container {runner_container}: {status} {me}")
    name = _container_name(session_id)
    status, body = docker.call("POST", f"/containers/create?name={name}", bot_container_spec(me, session_id))
    if status not in (201, 409):  # 409: already created for this session
        raise DispatchError(f"creating {name} failed: {status} {body}")
    status, body = docker.call("POST", f"/containers/{name}/start")
    if status not in (204, 304):  # 304: already running
        raise DispatchError(f"starting {name} failed: {status} {body}")
    return name


def stop_bot_container(docker, session_id: str) -> None:
    """SIGTERM with the full grace period; a bot already gone is fine."""
    status, body = docker.call("POST", f"/containers/{_container_name(session_id)}/stop?t=120")
    if status not in (204, 304, 404):
        raise DispatchError(f"stopping {_container_name(session_id)} failed: {status} {body}")


class DockerApi:
    """Just enough of the Docker Engine API, over DOCKER_HOST=tcp://host:port."""

    def __init__(self, docker_host: str):
        self.base = docker_host.replace("tcp://", "http://", 1).rstrip("/")

    def call(self, method, path, body=None):
        import json
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=150) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw) if raw else {}
            except ValueError:
                return e.code, {"message": raw.decode(errors="replace")}
