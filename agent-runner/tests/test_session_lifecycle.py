"""Session-end / DB-consistency tests.

Covers the three teardown paths behind the "Conversation.status stays stuck on
'running'" symptom:

  A. cancellation-safe terminal write  — bot._finalize_conversation commits even if
     the surrounding coroutine is cancelled (all-participants-leave → task.cancel()).
  B. stale-conversation reconciler      — runner.reconcile_stale_conversations closes a
     'running' row whose LiveKit room no longer exists (hard bot death / OOM).
  C. end-to-end all-users-leave         — a real bot session reaches a terminal status
     with ended_at after the only participant disconnects.

A and B are deterministic and need only the DB (+ transport-server for B's room list).
C needs the full stack. All are gated behind RUN_SESSION_LIFECYCLE_TEST=1 and run
in-container via `make test-session-lifecycle`.
"""
import asyncio
import contextlib
import json
import os
import sys
import time
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

GATED = os.getenv("RUN_SESSION_LIFECYCLE_TEST", "").strip() != "1"
SKIP_REASON = "Set RUN_SESSION_LIFECYCLE_TEST=1 to run session-lifecycle tests"

RUNNER_URL = os.getenv("AGENT_RUNNER_URL", "http://localhost:7860")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://transport-server:7880")
API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
BOT_RUNNER_SECRET = os.getenv("BOT_RUNNER_SECRET")


def _request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if BOT_RUNNER_SECRET:
        headers["Authorization"] = f"Bearer {BOT_RUNNER_SECRET}"
    req = urllib.request.Request(f"{RUNNER_URL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


async def _insert_running_conversation(room_name: str, started_at: datetime) -> str:
    from db.engine import AsyncSessionLocal
    from db.models import Conversation

    cid = uuid4().hex
    async with AsyncSessionLocal() as db:
        async with db.begin():
            db.add(Conversation(
                id=cid, room_name=room_name, bot_identity=None,
                status="running", started_at=started_at,
            ))
    return cid


async def _get_conversation(cid: str):
    from db.engine import AsyncSessionLocal
    from db.models import Conversation
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        row = await db.execute(select(Conversation).where(Conversation.id == cid))
        return row.scalar_one_or_none()


async def _await_terminal(cid: str, timeout: float = 30.0):
    """Poll until the conversation leaves 'running', or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conv = await _get_conversation(cid)
        if conv is not None and conv.status != "running":
            return conv
        await asyncio.sleep(1.0)
    return await _get_conversation(cid)


@unittest.skipIf(GATED, SKIP_REASON)
class TestSessionLifecycle(unittest.IsolatedAsyncioTestCase):

    async def asyncTearDown(self) -> None:
        from db.engine import engine
        await engine.dispose()

    async def test_a_finalize_commits_under_cancellation(self) -> None:
        """Fix 1: the terminal write lands even when its caller is cancelled."""
        from bot import _finalize_conversation

        cid = await _insert_running_conversation(f"lc-cancel-{uuid4().hex[:6]}",
                                                 datetime.now(timezone.utc))
        task = asyncio.create_task(_finalize_conversation(cid, "completed"))
        await asyncio.sleep(0)   # let the shielded write start
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        conv = await _get_conversation(cid)
        self.assertEqual(conv.status, "completed",
                         "terminal write was lost when the coroutine was cancelled")
        self.assertIsNotNone(conv.ended_at, "ended_at not set on finalize")

    async def test_b_reconciler_closes_dead_room(self) -> None:
        """Fix 2: a running conversation whose room is gone gets marked 'ended'."""
        from runner import reconcile_stale_conversations

        # Old enough to clear the min-age grace; room name guaranteed not to exist.
        cid = await _insert_running_conversation(
            f"lc-ghost-{uuid4().hex[:6]}",
            datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        await reconcile_stale_conversations(min_age_seconds=1)

        conv = await _get_conversation(cid)
        self.assertEqual(conv.status, "ended",
                         "reconciler did not close a conversation with a missing room")
        self.assertIsNotNone(conv.ended_at)

    async def test_b2_reconciler_spares_young_sessions(self) -> None:
        """The min-age grace must not close a just-created (bot-still-joining) session."""
        from runner import reconcile_stale_conversations

        cid = await _insert_running_conversation(f"lc-young-{uuid4().hex[:6]}",
                                                 datetime.now(timezone.utc))
        await reconcile_stale_conversations(min_age_seconds=60)

        conv = await _get_conversation(cid)
        self.assertEqual(conv.status, "running",
                         "reconciler wrongly closed a session inside the grace window")

    async def test_c_all_users_leave_ends_session(self) -> None:
        """End-to-end: the last participant leaving drives the session terminal."""
        from livekit import api, rtc

        room_name = f"lc-leave-{uuid4().hex[:6]}"
        resp = _request("POST", "/start", {"room_name": room_name})
        session_id = resp["session_id"]

        token = (
            api.AccessToken(API_KEY, API_SECRET)
            .with_identity(f"lc_user_{uuid4().hex[:5]}")
            .with_grants(api.VideoGrants(room_join=True, room=room_name,
                                         can_publish=True, can_subscribe=True))
            .to_jwt()
        )
        room = rtc.Room()
        await room.connect(LIVEKIT_URL, token)
        await asyncio.sleep(5)            # let the bot join and greet
        await room.disconnect()          # last participant leaves → task.cancel()

        conv = await _await_terminal(session_id, timeout=30)
        self.assertIsNotNone(conv, "conversation row missing")
        self.assertNotEqual(conv.status, "running",
                            "session stuck on 'running' after all users left")
        self.assertIsNotNone(conv.ended_at, "ended_at not set after all users left")


if __name__ == "__main__":
    unittest.main()
