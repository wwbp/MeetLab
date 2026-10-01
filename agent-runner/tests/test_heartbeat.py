"""A bot that dies without a word still has its session closed (step 4c, PR 5;
design iteration 6).

PR 4 made every graceful way out record a status. A hard death (OOM, a lost
instance, SIGKILL) runs no code at all, and the existing reconciler only closes a
session once its LiveKit room is gone: with people still in the room, the row stays
'running' and the meeting silently has no bot. The bot now writes heartbeat_at
every 10 s; a session silent for 30 s is failed and its task stopped.

Pure rule + DB behaviour against the container's Postgres. Run with:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_heartbeat -v
"""
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from db.engine import AsyncSessionLocal, engine
from db.models import Conversation
from heartbeat import START_GRACE, TTL, beat, fail_silent_sessions, silent_sessions

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def row(status="running", started_ago=600, beat_ago=None):
    return SimpleNamespace(
        id=str(uuid.uuid4()), status=status,
        started_at=NOW - timedelta(seconds=started_ago),
        heartbeat_at=None if beat_ago is None else NOW - timedelta(seconds=beat_ago),
    )


class SilentSessionsTest(unittest.TestCase):
    def test_a_fresh_heartbeat_is_alive(self):
        self.assertEqual(silent_sessions([row(beat_ago=5)], NOW), [])

    def test_a_heartbeat_older_than_the_ttl_is_silent(self):
        r = row(beat_ago=TTL.total_seconds() + 1)
        self.assertEqual(silent_sessions([r], NOW), [r])

    def test_a_bot_still_starting_gets_the_start_grace(self):
        # A cold start from an empty pool took 132-166 s in the spike.
        self.assertEqual(silent_sessions([row(started_ago=170)], NOW), [])

    def test_a_bot_that_never_beat_is_silent_after_the_start_grace(self):
        r = row(started_ago=START_GRACE.total_seconds() + 1)
        self.assertEqual(silent_sessions([r], NOW), [r])

    def test_only_running_sessions_count(self):
        self.assertEqual(silent_sessions([row(status="completed", beat_ago=999)], NOW), [])


class FailSilentSessionsTest(unittest.IsolatedAsyncioTestCase):
    # Each test has its own event loop, and pooled connections can't cross loops:
    # start and finish with an empty pool, whatever ran before.
    async def asyncSetUp(self):
        await engine.dispose()

    async def asyncTearDown(self):
        await engine.dispose()

    async def insert(self, beat_ago):
        cid = str(uuid.uuid4())
        async with AsyncSessionLocal() as db, db.begin():
            db.add(Conversation(id=cid, room_name=f"hb-{cid[:8]}", bot_identity="bot_hb", status="running",
                                started_at=datetime.now(timezone.utc) - timedelta(seconds=600),
                                heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=beat_ago)))
        return cid

    async def status(self, cid):
        async with AsyncSessionLocal() as db:
            return (await db.execute(select(Conversation.status).where(Conversation.id == cid))).scalar_one()

    async def test_a_silent_session_is_failed_and_its_bot_stopped_a_live_one_is_not(self):
        silent, alive = await self.insert(beat_ago=120), await self.insert(beat_ago=2)
        stopped = []
        closed = await fail_silent_sessions(AsyncSessionLocal, stop=stopped.append)
        self.assertIn(silent, closed)
        self.assertNotIn(alive, closed)
        self.assertEqual(await self.status(silent), "error")
        self.assertEqual(await self.status(alive), "running")
        self.assertIn(silent, stopped)

    async def test_a_beat_marks_the_session_alive(self):
        cid = await self.insert(beat_ago=120)
        await beat(AsyncSessionLocal, cid)
        self.assertNotIn(cid, await fail_silent_sessions(AsyncSessionLocal, stop=lambda _: None))
        self.assertEqual(await self.status(cid), "running")


if __name__ == "__main__":
    unittest.main()
