"""Idle-room longevity benchmark: start a bot in a room, let NOBODY join, and measure
how long the room + bot stay up.

Why this is interesting: a bot whose humans never come leaves after the arrival grace
(presence.py, BOT_ARRIVAL_GRACE_SECONDS, default 15 min); before that rule it lived until
its JWT expired. This benchmark measures that lifetime empirically AND checks
whether the session finalizes cleanly at teardown or hangs on 'running' (the DB-consistency
risk) — noting whether it's the graceful path (completed) or the reconciler net (ended).

No human joins → no STT/LLM/TTS work → the run is free.

Env: MAX_SECONDS (default 1200 = 20min), POLL_SECONDS (default 10).
Run: make bench-idle-room
"""
import asyncio
import os
import sys
import time
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from livekit import api

from _sim_common import API_KEY, API_SECRET, LIVEKIT_URL, conversation_status, request

MAX_SECONDS = float(os.getenv("MAX_SECONDS", "1200"))
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "10"))


def _http_url(ws: str) -> str:
    return ws.replace("wss://", "https://").replace("ws://", "http://")


async def _room_state(lk, room_name: str, bot_identity: str):
    """Return (room_exists, bot_present, num_participants). None on transient API error."""
    try:
        rooms = await lk.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
    except Exception:
        return (None, None, None)
    if not rooms.rooms:
        return (False, False, 0)
    try:
        ps = await lk.room.list_participants(api.ListParticipantsRequest(room=room_name))
        parts = list(ps.participants)
    except Exception:
        parts = []
    # Nobody joins, so the only participant should be the bot; match by identity and,
    # as a fallback, the bot_ identity prefix (see isBotParticipant in the desk API).
    bot_present = any(p.identity == bot_identity or p.identity.startswith("bot_") for p in parts)
    return (True, bot_present, len(parts))


async def main() -> None:
    room_name = f"idle-{int(time.time())}-{uuid4().hex[:6]}"
    print(f"[bench-idle-room] room={room_name} max={MAX_SECONDS:.0f}s poll={POLL_SECONDS:.0f}s — nobody will join")

    resp = request("POST", "/start", {"room_name": room_name})
    session_id = resp["session_id"]
    bot_identity = resp.get("bot_identity", "")
    print(f"  bot started: session={session_id} identity={bot_identity}")

    lk = api.LiveKitAPI(url=_http_url(LIVEKIT_URL), api_key=API_KEY, api_secret=API_SECRET)
    start = time.monotonic()
    dropped_after = None
    last = None
    joined = False  # confirm the bot actually joined before we trust a "gone" reading
    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed > MAX_SECONDS:
                break
            exists, bot_present, n = await _room_state(lk, room_name, bot_identity)
            if bot_present:
                joined = True
            if last != (exists, bot_present, n):
                print(f"  t={elapsed:6.0f}s  room_exists={exists}  bot_present={bot_present}  participants={n}")
                last = (exists, bot_present, n)
            # Only count a drop once we've seen the bot join (avoid the join-race window).
            if joined and (exists is False or bot_present is False):
                dropped_after = elapsed
                break
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await lk.aclose()

    # After a drop, poll for finalization: the graceful path finalizes ~instantly; the
    # safety-net reconciler runs every 120s. Wait up to ~150s to see which (or neither).
    def _is_terminal(s, e):
        return s in ("completed", "error", "ended") and e is not None

    status, ended_at = await conversation_status(session_id)
    finalize_after = None
    if dropped_after is not None:
        fin_start = time.monotonic()
        while not _is_terminal(status, ended_at) and time.monotonic() - fin_start < 150:
            await asyncio.sleep(5)
            status, ended_at = await conversation_status(session_id)
        if _is_terminal(status, ended_at):
            finalize_after = time.monotonic() - fin_start
    terminal = _is_terminal(status, ended_at)

    bar = "─" * 72
    print(f"\n{bar}")
    print("IDLE-ROOM LONGEVITY  (bot only, no participants)")
    print(bar)
    if not joined:
        print("  ⚠ bot never observed in the room — check agent-runner logs (start failed?).")
        print(f"  session: status={status!r} ended_at={ended_at!r}")
    elif dropped_after is None:
        print(f"  room STILL UP after {MAX_SECONDS:.0f}s ({MAX_SECONDS/60:.1f} min) — no drop observed.")
        print(f"  raise MAX_SECONDS to find the ceiling (expected ≈ BOT_TOKEN_TTL_MINUTES).")
        print(f"  session: status={status!r} (still running — expected while the room is alive).")
    else:
        print(f"  bot-only room stayed up {dropped_after:.0f}s (~{dropped_after/60:.1f} min), then the bot dropped.")
        print(f"  → expected mechanism: bot JWT expiry (BOT_TOKEN_TTL_MINUTES).")
        if terminal:
            howfin = (f"~{finalize_after:.0f}s after drop" if finalize_after and finalize_after > 8
                      else "promptly")
            print(f"  session at drop: status={status!r} ended_at={ended_at!r} → finalized {howfin}.")
        else:
            print(f"  session at drop: status={status!r} ended_at={ended_at!r} "
                  f"→ ⚠ still not terminal after ~150s (hung — reconciler didn't close it).")
    print(f"{bar}\n")


if __name__ == "__main__":
    asyncio.run(main())
