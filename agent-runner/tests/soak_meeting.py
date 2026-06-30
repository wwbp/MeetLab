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
import json
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
# Cost control: pin the cheapest LLM and the cheaper (OpenAI) TTS for soak runs so a
# long multi-room run doesn't run up API bills. Override if you need a specific model.
# STT defaults to the bot default (parakeet, self-hosted = no per-use cost).
LLM_MODEL = env("LLM_MODEL", "gpt-5.4-nano")
TTS_PROVIDER = env("TTS_PROVIDER", "openai")
MIN_GAP = float(env("MIN_GAP", "3"))
MAX_GAP = float(env("MAX_GAP", "8"))
SETTLE_SECS = float(env("SETTLE_SECS", "4"))
STAGGER = float(env("STAGGER", "4"))
COLLECT_TIMEOUT = float(env("COLLECT_TIMEOUT", "30"))

# Mode controls how strict the pass/fail verdict is:
#   sanity  — small run, everything must work: a bot that never replies is a FAIL.
#   stress  — load run, the local CPU sidecar is meant to saturate: no-reply rooms
#             and high latency are REPORTED, not failed. Correctness (every session
#             ends terminal) is enforced in both modes.
SOAK_MODE = env("SOAK_MODE", "stress").lower()
STRICT = SOAK_MODE in ("sanity", "realism")
# A room whose bot fell silent more than this many seconds before the room ended is
# flagged as a likely mid-run drop (TTL expiry, crash, OOM).
DROP_GAP_SECS = float(env("DROP_GAP_SECS", "60"))
# Where to write the machine-readable run log for later tracing/debugging.
SOAK_RESULTS_PATH = env("SOAK_RESULTS_PATH", "")


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
                    "turns": 0, "status": None, "ended_at": None, "error": None,
                    "talk_end": None, "quiet_gap_s": None, "dropped_early": False}
    rooms: list[rtc.Room] = []
    try:
        set_config(room_name, stt_model=STT_MODEL, endpointing_ms=ENDPOINTING_MS,
                   llm_model=LLM_MODEL, tts_provider=TTS_PROVIDER)
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
        # Unix epoch when talking stopped — reference for early-drop detection
        # (Utterance.ts is also unix epoch, so the two are directly comparable).
        result["talk_end"] = time.time()
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
    metas = await query_bot_metas(sid)
    result["metas"] = metas

    # Early-drop detection: did the bot fall silent well before the room ended?
    # Compares the last bot turn's timestamp against when we stopped talking.
    ts_list = [m["ts"] for m in metas if isinstance(m.get("ts"), (int, float))]
    if ts_list and result.get("talk_end") is not None:
        gap = result["talk_end"] - max(ts_list)
        result["quiet_gap_s"] = round(gap, 1)
        result["dropped_early"] = gap > DROP_GAP_SECS
        if result["dropped_early"]:
            print(f"  ⚠ {result['room']}: bot quiet for {gap:.0f}s before room end "
                  f"— possible mid-run drop")
    return result


def _write_artifact(run_id: str, results: list[dict], verdict: dict) -> str:
    """Write a machine-readable run log for later tracing/debugging."""
    path = SOAK_RESULTS_PATH or f"soak-results-{run_id}.json"
    payload = {
        "run_id": run_id, "mode": SOAK_MODE,
        "config": {"rooms": ROOMS, "users_per_room": USERS_PER_ROOM,
                   "duration_min": DURATION_MIN, "stt_model": STT_MODEL or "(default)",
                   "endpointing_ms": ENDPOINTING_MS or "(default)"},
        "verdict": verdict,
        "rooms": [
            {"room": r["room"], "session_id": r["session_id"], "status": r["status"],
             "ended_at": r["ended_at"].isoformat() if r.get("ended_at") else None,
             "turns": r["turns"], "quiet_gap_s": r.get("quiet_gap_s"),
             "dropped_early": r.get("dropped_early"), "error": r.get("error")}
            for r in results
        ],
    }
    try:
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"  (could not write results artifact to {path}: {e})")
    return path


def _report(run_id: str, results: list[dict]) -> bool:
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
    dropped = [r for r in results if r.get("dropped_early")]

    bar = "─" * 78
    print(f"\n{bar}")
    print(f"SOAK [{SOAK_MODE}]  rooms={ROOMS} users/room={USERS_PER_ROOM} "
          f"duration={DURATION_MIN}min stt={STT_MODEL or '(default)'} ep={ENDPOINTING_MS or '(default)'}")
    print(bar)
    print(f"  sessions: {len(results)}   bot turns: {total_turns}   "
          f"errored rooms: {len(errored)}")
    print(f"  stt_ms   P50={_pct(stt,50)}  P95={_pct(stt,95)}   (n={len(stt)})")
    print(f"  total_ms P50={_pct(total,50)}  P95={_pct(total,95)}")
    print(f"  qdepth   max={max(qdepth) if qdepth else 0}   spikes={spikes}  self_echo={echoes}")
    print("  bot interruptions / talk-over: see meetlab.bot_interruptions_total + "
          "meetlab.bot_talkover_ms (agent-runner logs/metrics)")
    print(bar)
    print(f"{'room':>16}  {'turns':>5}  {'status':>9}  {'stt_p50':>7}  {'quiet_s':>7}")
    for r in results:
        rs = [m['meta'].get('timing', {}).get('stt_ms') for m in r.get('metas', [])]
        rs = [x for x in rs if isinstance(x, (int, float))]
        flag = "  DROP?" if r.get("dropped_early") else ""
        print(f"{r['room']:>16}  {r['turns']:>5}  {str(r['status']):>9}  "
              f"{str(_pct(rs,50)):>7}  {str(r.get('quiet_gap_s')):>7}{flag}"
              + (f"   ERROR {r['error']}" if r['error'] else ""))
    print(bar)

    # Verdict. DB-consistency (terminal status) is ALWAYS enforced — it's correctness,
    # not load. No-reply and early-drop are hard fails only in strict (sanity) mode;
    # under stress the CPU sidecar is expected to saturate, so they're warnings.
    ok = True
    if hung:
        ok = False
        print(f"  ✗ {len(hung)} session(s) stuck on 'running' (never finalized): "
              f"{', '.join(r['room'] for r in hung)}")
    else:
        print("  ✓ all sessions reached a terminal status with ended_at")

    def _fail_or_warn(items, label):
        nonlocal ok
        if not items:
            return
        rooms = ', '.join(r['room'] for r in items)
        if STRICT:
            ok = False
            print(f"  ✗ {len(items)} {label}: {rooms}")
        else:
            print(f"  ⚠ {len(items)} {label} (tolerated in '{SOAK_MODE}' mode): {rooms}")

    _fail_or_warn(no_turns, "session(s) produced zero bot turns")
    _fail_or_warn(dropped, "session(s) went quiet early (possible mid-run drop)")

    verdict = {"ok": ok, "hung": len(hung), "no_turns": len(no_turns),
               "dropped_early": len(dropped), "errored": len(errored),
               "stt_ms_p50": _pct(stt, 50), "stt_ms_p95": _pct(stt, 95)}
    path = _write_artifact(run_id, results, verdict)
    print(f"  results → {path}")
    print(f"{bar}\n")
    return ok


async def main() -> None:
    run_id = uuid4().hex[:6]
    speech, sr = load_speech()
    deadline = time.monotonic() + DURATION_MIN * 60
    print(f"[soak] run={run_id} mode={SOAK_MODE} rooms={ROOMS} users/room={USERS_PER_ROOM} "
          f"duration={DURATION_MIN}min target={LIVEKIT_URL}")

    results = await asyncio.gather(*(
        _run_room(idx, run_id, speech, sr, deadline) for idx in range(ROOMS)
    ))
    results = await asyncio.gather(*(_finalize_and_collect(r) for r in results))

    ok = _report(run_id, results)

    from db.engine import engine
    await engine.dispose()

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
