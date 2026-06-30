"""Multi-room soak / load harness.

Runs the perf-test scenario the team needs before a real event: N rooms, each with
M synthetic users and one bot, holding a conversation for D minutes — all concurrent.
Each user loops the benchmark speech fixture with randomized inter-turn gaps, the two
users in a room staggered so turns mostly alternate. At the end it reports aggregate
latency, backlog (queue depth), and a DB-consistency verdict (every session must reach
a terminal status with ended_at — the hanging-status check at scale).

Two run modes, same harness — just point the env at a different STT backend:
  • Local stress:  the CPU stt-nemotron sidecar is a single serialized server, so ~20
    concurrent streams WILL saturate it. Rising qdepth / spikes is the EXPECTED result
    and the whole point — it stresses the backlog path, not real latency.
  • Prod realism:  set AGENT_RUNNER_URL / LIVEKIT_URL / NEMOTRON_STT_URL at the deployed
    stack (T4 sidecar) for true latency under load.

Knobs (env): ROOMS=10, USERS_PER_ROOM=2, DURATION_MIN=20, STT_MODEL (default=bot default),
ENDPOINTING_MS, MIN_GAP=3, MAX_GAP=8, SETTLE_SECS=4, STAGGER=4, COLLECT_TIMEOUT=30.
Soak runs should export BOT_TOKEN_TTL_MINUTES=30 on the agent-runner so the 15-min bot
token doesn't expire mid-run.

Run in-container:  make soak ROOMS=10 USERS_PER_ROOM=2 DURATION_MIN=20
"""
import asyncio
import os
import sys
import time
from uuid import uuid4
from random import Random

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from livekit import rtc

from _sim_common import (
    LIVEKIT_URL,
    capture_signal,
    conversation_status,
    env,
    load_speech,
    publish_audio_source,
    query_bot_metas,
    request,
    set_config,
    token,
)

ROOMS = int(env("ROOMS", "10"))
USERS_PER_ROOM = int(env("USERS_PER_ROOM", "2"))
DURATION_MIN = float(env("DURATION_MIN", "20"))
STT_MODEL = env("STT_MODEL", "")
ENDPOINTING_MS = env("ENDPOINTING_MS", "")
MIN_GAP = float(env("MIN_GAP", "3"))
MAX_GAP = float(env("MAX_GAP", "8"))
SETTLE_SECS = float(env("SETTLE_SECS", "4"))
STAGGER = float(env("STAGGER", "4"))
COLLECT_TIMEOUT = float(env("COLLECT_TIMEOUT", "30"))


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int(round((q / 100.0) * (len(s) - 1))))
    return s[idx]


async def _user_loop(room: rtc.Room, label: str, speech, sr: int, deadline: float, rng: Random) -> int:
    """Speak the fixture, gap, repeat until the deadline. One persistent track."""
    source = await publish_audio_source(room, label, sr)
    await asyncio.sleep(rng.uniform(0, STAGGER))  # desync the two users in a room
    turns = 0
    while time.monotonic() < deadline:
        await capture_signal(source, speech, sr)
        turns += 1
        await asyncio.sleep(rng.uniform(MIN_GAP, MAX_GAP))
    return turns


async def _run_room(idx: int, run_id: str, speech, sr: int, deadline: float) -> dict:
    """Start a bot, join USERS_PER_ROOM talkers, converse until the deadline, leave."""
    room_name = f"soak-{run_id}-r{idx:02d}"
    result: dict = {"idx": idx, "room": room_name, "session_id": None,
                    "turns": 0, "status": None, "ended_at": None, "error": None}
    rooms: list[rtc.Room] = []
    try:
        set_config(room_name, stt_model=STT_MODEL, endpointing_ms=ENDPOINTING_MS)
        resp = request("POST", "/start", {"room_name": room_name})
        result["session_id"] = resp["session_id"]

        for u in range(USERS_PER_ROOM):
            room = rtc.Room()
            await room.connect(LIVEKIT_URL, token(room_name, f"soak_r{idx:02d}_u{u}_{uuid4().hex[:4]}"))
            rooms.append(room)

        await asyncio.sleep(SETTLE_SECS)  # let the bot join and greet
        counts = await asyncio.gather(*(
            _user_loop(rooms[u], f"r{idx:02d}-u{u}", speech, sr, deadline,
                       Random(idx * 100 + u))
            for u in range(USERS_PER_ROOM)
        ))
        result["turns"] = sum(counts)
    except Exception as e:  # one room failing must not abort the whole soak
        result["error"] = repr(e)
    finally:
        for room in rooms:
            try:
                await room.disconnect()
            except Exception:
                pass
    return result


async def _finalize_and_collect(result: dict) -> dict:
    """After users leave, confirm the session went terminal and pull its bot turns."""
    sid = result["session_id"]
    if not sid:
        return result
    # Poll for terminal status (the all-users-leave teardown path).
    deadline = time.monotonic() + COLLECT_TIMEOUT
    status, ended_at = await conversation_status(sid)
    while status == "running" and time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        status, ended_at = await conversation_status(sid)
    result["status"], result["ended_at"] = status, ended_at
    result["metas"] = await query_bot_metas(sid)
    return result


def _report(results: list[dict]) -> bool:
    stt, total, qdepth = [], [], []
    spikes = echoes = total_turns = 0
    for r in results:
        for m in r.get("metas", []):
            meta = m["meta"]
            t = meta.get("timing", {})
            diag = meta.get("diag", {})
            total_turns += 1
            if isinstance(t.get("stt_ms"), (int, float)):
                stt.append(t["stt_ms"])
            if isinstance(meta.get("total_latency_ms"), (int, float)):
                total.append(meta["total_latency_ms"])
            if isinstance(diag.get("queue_depth"), (int, float)):
                qdepth.append(diag["queue_depth"])
            spikes += 1 if diag.get("stt_spike") else 0
            echoes += 1 if diag.get("self_echo") else 0

    hung = [r for r in results if r["status"] in (None, "running")]
    no_turns = [r for r in results if r["session_id"] and not r.get("metas")]
    errored = [r for r in results if r["error"]]

    bar = "─" * 78
    print(f"\n{bar}")
    print(f"SOAK  rooms={ROOMS} users/room={USERS_PER_ROOM} duration={DURATION_MIN}min "
          f"stt={STT_MODEL or '(default)'} ep={ENDPOINTING_MS or '(default)'}")
    print(bar)
    print(f"  sessions: {len(results)}   bot turns: {total_turns}   "
          f"errored rooms: {len(errored)}")
    print(f"  stt_ms   P50={_pct(stt,50)}  P95={_pct(stt,95)}   (n={len(stt)})")
    print(f"  total_ms P50={_pct(total,50)}  P95={_pct(total,95)}")
    print(f"  qdepth   max={max(qdepth) if qdepth else 0}   spikes={spikes}  self_echo={echoes}")
    print(bar)
    print(f"{'room':>14}  {'turns':>5}  {'status':>9}  {'stt_p50':>7}")
    for r in results:
        rs = [m['meta'].get('timing', {}).get('stt_ms') for m in r.get('metas', [])]
        rs = [x for x in rs if isinstance(x, (int, float))]
        print(f"{r['room']:>14}  {r['turns']:>5}  {str(r['status']):>9}  "
              f"{str(_pct(rs,50)):>7}" + (f"   ERROR {r['error']}" if r['error'] else ""))
    print(bar)

    # DB-consistency / liveness verdict (hard pass-fail; latency is informational
    # because the local CPU sidecar is expected to saturate).
    ok = True
    if hung:
        ok = False
        print(f"  ✗ {len(hung)} session(s) never reached a terminal status (status stuck "
              f"on 'running'): {', '.join(r['room'] for r in hung)}")
    else:
        print("  ✓ all sessions reached a terminal status with ended_at")
    if no_turns:
        ok = False
        print(f"  ✗ {len(no_turns)} session(s) produced zero bot turns: "
              f"{', '.join(r['room'] for r in no_turns)}")
    print(f"{bar}\n")
    return ok


async def main() -> None:
    run_id = uuid4().hex[:6]
    speech, sr = load_speech()
    deadline = time.monotonic() + DURATION_MIN * 60
    print(f"[soak] run={run_id} rooms={ROOMS} users/room={USERS_PER_ROOM} "
          f"duration={DURATION_MIN}min target={LIVEKIT_URL}")

    results = await asyncio.gather(*(
        _run_room(idx, run_id, speech, sr, deadline) for idx in range(ROOMS)
    ))
    results = await asyncio.gather(*(_finalize_and_collect(r) for r in results))

    ok = _report(results)

    from db.engine import engine
    await engine.dispose()

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
