"""Live acceptance tests for v2 staging: what a bot session must do, end to end.

Runs from a laptop and from CI (the `acceptance` job in .github/workflows/infra-v2.yml)
the same way. Every scenario keeps a stand-in participant in the room, so the
room-gone cleanup can never be what closes a session and hide a failure.

  start      a bot started through meet's console joins the room
  stoptask   ECS StopTask (SIGTERM): exit 0, the bot leaves, it records 'completed'   (4c PR 4)
  removed    the bot removed from the room: its task stops by itself, exit 0          (4c PR 4)
  kill9      kill -9 inside the task (ECS Exec): exit 137, and the heartbeat check
             fails the session within 60 s while the participant is still there, and
             is able to stop its task (a hung bot must not keep running)              (4c PR 5)

A scenario that cannot reach its situation (e.g. the participant drops before the
kill) is a FAIL, not a skip: an acceptance test that didn't test anything passed nothing.

    CONSOLE_PASSWORD=... LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \\
    uv run --no-project --with livekit --with livekit-api --with boto3 \\
        python agent-runner/tests/acceptance_staging.py [scenario ...]

Needs AWS credentials that can read the staging logs and tasks, stop and exec into
bot tasks, and the session-manager-plugin on PATH (for kill9).
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import timedelta

import boto3
from livekit import api, rtc

CLUSTER = os.getenv("ECS_CLUSTER", "meetlab-v2-staging")
MEET = os.getenv("MEET_URL", "https://meet-staging.wwbp.org")
REGION = "us-east-1"
ecs = boto3.client("ecs", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
_web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())


class Fail(Exception):
    pass


def _post(path, body):
    req = urllib.request.Request(MEET + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"}, method="POST")
    return json.loads(_web.open(req, timeout=30).read())


def _token(room, identity):
    return (api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
            .with_identity(identity).with_grants(api.VideoGrants(room_join=True, room=room))
            .with_ttl(timedelta(minutes=30)).to_jwt())


def _task(session_id):
    for status in ("RUNNING", "STOPPED"):
        arns = ecs.list_tasks(cluster=CLUSTER, startedBy=session_id, desiredStatus=status)["taskArns"]
        if arns:
            return arns[0]
    return None


def _describe(arn):
    return ecs.describe_tasks(cluster=CLUSTER, tasks=[arn])["tasks"][0]


def _log_lines(group, session_id, since):
    ev = logs.filter_log_events(logGroupName=group, startTime=int(since * 1000), filterPattern=f'"{session_id}"')
    return [e["message"] for e in ev["events"]]


async def _until(check, timeout, what):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if await check() if asyncio.iscoroutinefunction(check) else check():
            return time.time() - t0
        await asyncio.sleep(2)
    raise Fail(f"{what} not within {timeout} s")


class Meeting:
    """A room with a stand-in participant and a bot started through meet."""

    async def __aenter__(self):
        self.room = f"accept-{uuid.uuid4().hex[:6]}"
        self.human = rtc.Room()
        await self.human.connect(os.environ["LIVEKIT_URL"], _token(self.room, "human_standin"))
        _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
        _post("/api/concierge/rooms", {"name": self.room})
        self.session = _post(f"/api/concierge/rooms/{self.room}/bots", {})["request"]["runnerSessionId"]
        self.started = time.time()
        self.join_s = await _until(self.bot_present, 420, "bot in the room")
        self.task = _task(self.session)
        if not self.task:
            raise Fail("no task for the session")
        return self

    def bot_present(self):
        if not self.human.isconnected():
            raise Fail("the stand-in participant dropped out of the room")
        return any(p.identity.startswith("bot_") for p in self.human.remote_participants.values())

    def require_participant(self):
        if not self.human.isconnected():
            raise Fail("the stand-in participant dropped out; the room-gone cleanup could have closed the session")

    async def __aexit__(self, *exc):
        await self.human.disconnect()


async def scenario_start():
    async with Meeting() as m:
        return f"bot joined after {m.join_s:.0f}s"


async def scenario_stoptask():
    async with Meeting() as m:
        ecs.stop_task(cluster=CLUSTER, task=m.task, reason="acceptance: stoptask")
        await _until(lambda: _describe(m.task)["lastStatus"] == "STOPPED", 150, "task stopped")
        m.require_participant()
        code = _describe(m.task)["containers"][0].get("exitCode")
        if code != 0:
            raise Fail(f"exit code {code}, expected 0 (graceful)")
        await _until(lambda: not m.bot_present(), 30, "bot gone from the room")
        await _until(lambda: any("ended (completed)" in l for l in _log_lines("/meetlab-v2/staging/bot", m.session, m.started)),
                     60, "bot logged ended (completed)")
        return "exit 0, left the room, recorded completed"


async def scenario_removed():
    async with Meeting() as m:
        lk = api.LiveKitAPI(os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        try:
            for p in (await lk.room.list_participants(api.ListParticipantsRequest(room=m.room))).participants:
                if p.identity.startswith("bot_"):
                    await lk.room.remove_participant(api.RoomParticipantIdentity(room=m.room, identity=p.identity))
        finally:
            await lk.aclose()
        await _until(lambda: _describe(m.task)["lastStatus"] == "STOPPED", 150, "task stopped by itself")
        m.require_participant()
        code = _describe(m.task)["containers"][0].get("exitCode")
        if code != 0:
            raise Fail(f"exit code {code}, expected 0")
        return "task stopped by itself, exit 0"


# No single quotes: the whole thing runs inside sh -c '...'. Skips PID 1 (the launcher;
# Linux ignores in-namespace signals to PID 1) and kills the bot's python process.
_KILL9 = ('for p in /proc/[0-9]*; do n=${p#/proc/}; [ "$n" = 1 ] && continue; '
          'c=$(tr "\\000" " " < $p/cmdline 2>/dev/null); '
          'case "$c" in *python*bot_task*) echo killing $n; kill -9 $n;; esac; done')


async def scenario_kill9():
    async with Meeting() as m:
        await _until(lambda: (_describe(m.task)["containers"][0].get("managedAgents") or [{}])[0].get("lastStatus") == "RUNNING",
                     120, "ECS Exec agent running")
        await asyncio.sleep(22)  # at least two heartbeats
        killed_at = time.time()
        # An interactive Exec session never returns without a terminal, so don't wait for
        # it: the evidence that the kill landed is the task's exit code (137 = SIGKILL).
        exec_ = subprocess.Popen(["aws", "ecs", "execute-command", "--region", REGION, "--cluster", CLUSTER,
                                  "--task", m.task, "--container", "bot", "--interactive",
                                  "--command", f"sh -c '{_KILL9}'"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await _until(lambda: _describe(m.task)["lastStatus"] == "STOPPED", 120, "killed task stopped")
        exec_.kill()
        code = _describe(m.task)["containers"][0].get("exitCode")
        if code != 137:
            raise Fail(f"exit code {code}, expected 137 (SIGKILL): the kill did not land")
        if any("ended (" in l for l in _log_lines("/meetlab-v2/staging/bot", m.session, m.started)):
            raise Fail("the bot recorded its own end: this was not a silent death")

        def failed_by_heartbeat():
            m.require_participant()
            return any("heartbeat: failed" in l for l in _log_lines("/meetlab-v2/staging/agent-runner", m.session, killed_at))
        await _until(failed_by_heartbeat, 90, "session failed by the heartbeat check")
        after = time.time() - killed_at
        if any("could not stop the bot" in l for l in _log_lines("/meetlab-v2/staging/agent-runner", m.session, killed_at)):
            raise Fail("the reconciler failed the session but could not stop its bot (a hung bot would keep running)")
        if after > 60:
            raise Fail(f"failed by heartbeat only {after:.0f}s after the kill (limit 60 s)")
        return f"exit 137, failed by heartbeat {after:.0f}s after the kill, participant still in the room"


SCENARIOS = {"start": scenario_start, "stoptask": scenario_stoptask, "removed": scenario_removed, "kill9": scenario_kill9}


async def main(names):
    results = []
    for name in names:
        t0 = time.time()
        try:
            detail, ok = await SCENARIOS[name](), True
        except Fail as e:
            detail, ok = str(e), False
        except Exception as e:  # an error in the harness is a failure too, with its cause
            detail, ok = f"{type(e).__name__}: {e}", False
        results.append((name, ok, detail, time.time() - t0))
        print(f"{'PASS' if ok else 'FAIL'} {name:9} {time.time() - t0:5.0f}s  {detail}", flush=True)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Staging acceptance\n\n| Scenario | Result | Time | Detail |\n|---|---|---|---|\n")
            for name, ok, detail, secs in results:
                f.write(f"| {name} | {'PASS' if ok else '**FAIL**'} | {secs:.0f} s | {detail} |\n")
    return all(ok for _, ok, _, _ in results)


if __name__ == "__main__":
    names = sys.argv[1:] or list(SCENARIOS)
    sys.exit(0 if asyncio.run(main(names)) else 1)
