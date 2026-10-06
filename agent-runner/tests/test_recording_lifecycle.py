"""Every session recorded, and its recording's state true (video sizing, 2026-10-06):
- a finished recording is marked available by the runner's own loop, not only when someone
  opens the Meetings page (10 of 10 load-test recordings sat 'pending' with their files in S3);
- auto-record waits for a free recorder instead of giving up (2 of 8 rooms had no video when
  every recorder was busy), and a session that still gets none says so: a 'failed' recording.
Against the container's Postgres:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \\
        uv run python -m unittest tests.test_recording_lifecycle -v
"""
import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")

import runner  # noqa: E402

BUSY = (502, {"error": "LiveKit: TwirpError(code=unavailable, message=twirp error unknown: no response from servers, status=503)"})


def _run(coro):
    async def fresh():
        from db.engine import engine
        await engine.dispose(close=False)  # a fresh loop per test (see test_sessions)
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(fresh())


class Fixture(unittest.TestCase):
    def setUp(self):
        self.room = f"rec-{uuid.uuid4().hex[:8]}"
        self.conv = str(uuid.uuid4())

        async def add():
            from db.engine import AsyncSessionLocal
            from db.models import Conversation
            async with AsyncSessionLocal() as db, db.begin():
                db.add(Conversation(id=self.conv, room_name=self.room, bot_identity="bot_x", status="running", meta={}))
        _run(add())

    def recordings(self):
        async def get():
            from sqlalchemy import select
            from db.engine import AsyncSessionLocal
            from db.models import MediaFile
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(MediaFile).where(MediaFile.conv_id == self.conv,
                                                                 MediaFile.type == "recording"))).scalars().all()
                return {(r.meta or {}).get("egress_id"): r.status for r in rows}
        return _run(get())


class SyncTest(Fixture):
    def test_the_loop_marks_finished_and_failed_recordings(self):
        async def add():
            from db.engine import AsyncSessionLocal
            from db.models import MediaFile
            async with AsyncSessionLocal() as db, db.begin():
                for eg in ("EG_done", "EG_broke", "EG_live"):
                    db.add(MediaFile(id=str(uuid.uuid4()), conv_id=self.conv, type="recording", status="pending",
                                     path=f"recordings/{eg}.mp4", meta={"egress_id": eg}))
        _run(add())
        states = {"EG_done": 3, "EG_broke": 4, "EG_live": 1}  # COMPLETE, FAILED, ACTIVE
        lk = mock.MagicMock()
        lk.__aenter__.return_value = lk
        lk.egress.list_egress = mock.AsyncMock(
            side_effect=lambda req: SimpleNamespace(items=[SimpleNamespace(status=states[req.egress_id])]))
        with mock.patch.object(runner.api, "LiveKitAPI", return_value=lk):
            _run(runner.sync_pending_recordings())
        self.assertEqual(self.recordings(), {"EG_done": "available", "EG_broke": "failed", "EG_live": "pending"})

    def test_the_background_tick_syncs_recordings(self):
        with mock.patch.object(runner, "sync_pending_recordings", mock.AsyncMock()) as sync, \
             mock.patch.object(runner, "reconcile_stale_conversations", mock.AsyncMock()), \
             mock.patch.object(runner, "fail_silent_sessions", mock.AsyncMock()), \
             mock.patch.object(runner, "rejoin_dead_sessions", mock.AsyncMock()):
            _run(runner.reconcile_tick())
        sync.assert_awaited_once()


class LoopTest(unittest.TestCase):
    def test_startup_starts_the_background_loop(self):
        """#206 slid reconcile_tick between the startup decorator and the loop: startup ran one
        tick and never started the loop, so dead bots went undetected (staging, 2026-10-06)."""
        startup = runner.app.router.on_startup
        self.assertIn(runner._start_conversation_reconcile_loop, startup)
        self.assertNotIn(runner.reconcile_tick, startup)


class ForgottenRecordingTest(Fixture):
    """A recording LiveKit no longer knows (a restarted LiveKit forgets them) is resolved once,
    from the file itself, instead of being asked about, and warned about, every tick."""

    def add(self, eg, hours_ago):
        async def add():
            from datetime import datetime, timedelta, timezone
            from db.engine import AsyncSessionLocal
            from db.models import MediaFile
            async with AsyncSessionLocal() as db, db.begin():
                db.add(MediaFile(id=str(uuid.uuid4()), conv_id=self.conv, type="recording", status="pending",
                                 path=f"recordings/{eg}.mp4", meta={"egress_id": eg},
                                 created_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago)))
        _run(add())

    def sync(self, stored):
        lk = mock.MagicMock()
        lk.__aenter__.return_value = lk
        lk.egress.list_egress = mock.AsyncMock(side_effect=Exception("TwirpError(code=not_found, message=egress not found)"))
        with mock.patch.object(runner.api, "LiveKitAPI", return_value=lk), \
             mock.patch.object(runner.storage, "exists", side_effect=lambda p: p in stored):
            _run(runner.sync_pending_recordings())

    def test_its_file_is_there_so_it_is_available(self):
        self.add("EG_kept", 1)
        self.sync({"recordings/EG_kept.mp4"})
        self.assertEqual(self.recordings(), {"EG_kept": "available"})

    def test_no_file_after_six_hours_so_it_failed(self):
        self.add("EG_lost", 7)
        self.sync(set())
        self.assertEqual(self.recordings(), {"EG_lost": "failed"})

    def test_no_file_yet_so_it_may_still_be_uploading(self):
        self.add("EG_young", 1)
        self.sync(set())
        self.assertEqual(self.recordings(), {"EG_young": "pending"})


class AutoRecordTest(Fixture):
    def test_a_busy_recorder_is_waited_for(self):
        calls = iter([BUSY, BUSY, (200, {"egress_id": "EG_ok"})])
        with mock.patch.object(runner, "start_recording_for_room", mock.AsyncMock(side_effect=lambda r: next(calls))) as start:
            status, _ = _run(runner.record_with_retry(self.room, give_up_after_s=5, every_s=0.01))
        self.assertEqual(status, 200)
        self.assertEqual(start.await_count, 3)
        self.assertEqual(self.recordings(), {}, "no failure stored when it got a recorder")

    def test_a_session_that_gets_no_recorder_says_so(self):
        with mock.patch.object(runner, "start_recording_for_room", mock.AsyncMock(return_value=BUSY)):
            status, _ = _run(runner.record_with_retry(self.room, give_up_after_s=0.05, every_s=0.01))
        self.assertEqual(status, 502)
        self.assertEqual(list(self.recordings().values()), ["failed"], "the console must show this session has no video")

    def test_other_refusals_are_not_retried(self):
        with mock.patch.object(runner, "start_recording_for_room", mock.AsyncMock(return_value=(404, {"error": "no active session"}))) as start:
            _run(runner.record_with_retry(self.room, give_up_after_s=5, every_s=0.01))
        self.assertEqual(start.await_count, 1, "a session that ended has nothing to wait for")


if __name__ == "__main__":
    unittest.main()
