"""Step 4c spike: what does running each bot as its own ECS task cost us?

Starts bots through staging meet (as an operator would), then measures, per run:
  * start → task RUNNING, start → bot in the LiveKit room   (join latency)
  * a second RunTask with the same session ID               (must be the same task)
  * stopping it, alternately by StopTask (SIGTERM) and by removing the bot from the
    room: the task must stop by itself, gracefully (exit 0, bot gone from the room),
    in under 120 s

Run 1 is cold when the bot pool is at 0 instances; later runs are warm.

    MEET_URL=https://meet-staging.wwbp.org CONSOLE_PASSWORD=... \
    LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \
    AWS_* credentials that can describe/run/stop tasks in the cluster \
    RUNS=2 uv run python tests/spike_dispatch.py
"""
import asyncio
import json
import os
import sys
import time
from uuid import uuid4

import boto3
from livekit import api

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
from _sim_common import start_bot  # noqa: E402
from dispatch import EcsBotTarget, run_bot_task  # noqa: E402

CLUSTER = os.getenv("ECS_CLUSTER", "meetlab-v2-staging")
TARGET = EcsBotTarget(cluster=CLUSTER, task_definition="meetlab-v2-staging-bot",
                      capacity_provider="meetlab-v2-staging-bots")
RUNS = int(os.getenv("RUNS", "2"))
TIMEOUT = 600
ecs = boto3.client("ecs", region_name="us-east-1")


def task_for(session_id: str) -> str | None:
    arns = ecs.list_tasks(cluster=CLUSTER, startedBy=session_id, desiredStatus="RUNNING")["taskArns"]
    arns += ecs.list_tasks(cluster=CLUSTER, startedBy=session_id, desiredStatus="STOPPED")["taskArns"]
    return arns[0] if arns else None


def task_status(arn: str) -> str:
    return ecs.describe_tasks(cluster=CLUSTER, tasks=[arn])["tasks"][0]["lastStatus"]


async def bot_in_room(lk: api.LiveKitAPI, room: str) -> bool:
    try:
        resp = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
    except Exception:
        return False
    return any(p.identity.startswith("bot_") for p in resp.participants)


async def one_run(lk: api.LiveKitAPI, label: str, stop_via: str) -> dict:
    room = f"spike-{uuid4().hex[:8]}"
    r = {"run": label, "room": room}
    t0 = time.monotonic()
    resp = start_bot(room)
    # meet answers {request: {runnerSessionId, ...}}; the runner itself answers {session_id}
    session_id = (resp.get("request") or {}).get("runnerSessionId") or resp.get("session_id")
    if not session_id:
        return {**r, "error": f"no session id in start response: {resp}"}
    r["session_id"] = session_id
    arn = running_at = joined_at = None
    while time.monotonic() - t0 < TIMEOUT and not (running_at and joined_at):
        arn = arn or task_for(session_id)
        if arn and not running_at and task_status(arn) == "RUNNING":
            running_at = time.monotonic() - t0
        if not joined_at and await bot_in_room(lk, room):
            joined_at = time.monotonic() - t0
        await asyncio.sleep(1)
    r.update(task=arn, running_s=running_at, joined_s=joined_at)
    if not arn:
        r["error"] = "no task found"
        return r

    # A retried start with the same session ID must be the same task, not a second bot.
    r["retry_same_task"] = run_bot_task(ecs, session_id, TARGET) == arn

    t1 = time.monotonic()
    if stop_via == "stoptask":
        ecs.stop_task(cluster=CLUSTER, task=arn, reason="4c spike")
    else:  # an operator removing the bot from the room
        bots = [p.identity for p in (await lk.room.list_participants(api.ListParticipantsRequest(room=room))).participants
                if p.identity.startswith("bot_")]
        for identity in bots:
            await lk.room.remove_participant(api.RoomParticipantIdentity(room=room, identity=identity))
    while time.monotonic() - t1 < 180 and task_status(arn) != "STOPPED":
        await asyncio.sleep(1)
    r["stop_via"] = stop_via
    r["stop_s"] = round(time.monotonic() - t1, 1)
    r["exit_code"] = ecs.describe_tasks(cluster=CLUSTER, tasks=[arn])["tasks"][0]["containers"][0].get("exitCode")
    r["left_room"] = not await bot_in_room(lk, room)
    return r


async def main():
    lk = api.LiveKitAPI(os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
    results = []
    try:
        for i in range(RUNS):
            results.append(await one_run(lk, "cold" if i == 0 else f"warm{i}", "stoptask" if i % 2 == 0 else "removed"))
            print(json.dumps(results[-1]))
    finally:
        await lk.aclose()
    print(f"\n{'run':6} {'running':>9} {'in room':>9} {'retry=same':>11} {'stop via':>9} {'stop':>7} {'exit':>5} {'left':>5}")
    for r in results:
        f = lambda v: f"{v:.1f}s" if isinstance(v, float) else str(v)
        print(f"{r['run']:6} {f(r.get('running_s')):>9} {f(r.get('joined_s')):>9} {str(r.get('retry_same_task')):>11} "
              f"{r.get('stop_via', ''):>9} {f(r.get('stop_s')):>7} {str(r.get('exit_code')):>5} {str(r.get('left_room')):>5}")
    ok = all(r.get("joined_s") and r.get("retry_same_task") and (r.get("stop_s") or 999) < 120
             and r.get("exit_code") == 0 and r.get("left_room") for r in results)
    print("\nVERDICT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
