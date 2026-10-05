"""A session's status and how it may change (sessions.py, design iterations 1 and 3).

Five writers end sessions: the bot's own finalizer, the heartbeat sweep, the console's
Stop, the room-gone sweep and a failed dispatch. Each wrote unconditionally, so a late
writer could overwrite an earlier one (a bot finishing after the sweep failed it).
"""
import asyncio
import unittest
import uuid

from sessions import IllegalTransition, end, transition


class TransitionTests(unittest.TestCase):
    def test_every_event_ends_a_running_session_in_its_status(self):
        self.assertEqual({e: transition("running", e) for e in
                          ("finished", "crashed", "stopped", "silent", "room_gone", "dispatch_failed")},
                         {"finished": "completed", "crashed": "error", "stopped": "completed",
                          "silent": "error", "room_gone": "ended", "dispatch_failed": "error"})

    def test_an_ended_session_never_changes_again(self):
        for status in ("completed", "error", "ended"):
            with self.assertRaises(IllegalTransition):
                transition(status, "finished")

    def test_an_unknown_event_is_refused(self):
        with self.assertRaises(IllegalTransition):
            transition("running", "paused")


class EndTests(unittest.TestCase):
    """end(): the transition as one compare-and-set UPDATE (needs the test database)."""

    def _run(self, coro):
        return asyncio.run(coro)

    async def _session(self):
        from db.engine import AsyncSessionLocal, engine
        from db.models import Conversation

        await engine.dispose(close=False)  # a fresh loop per test (see test_recording_autostart)
        sid = str(uuid.uuid4())
        async with AsyncSessionLocal() as db, db.begin():
            db.add(Conversation(id=sid, room_name=f"sessions-{sid[:8]}", bot_identity="bot_x", status="running"))
        return sid

    async def _status(self, sid):
        from db.engine import AsyncSessionLocal
        from db.models import Conversation

        async with AsyncSessionLocal() as db:
            return (await db.get(Conversation, sid)).status

    def test_the_ending_event_is_kept(self):
        # A rejoin needs to know the bot died (silent, crashed), not that it was stopped:
        # the status alone ("error") can't tell a crash from a failed dispatch.
        async def scenario():
            from db.engine import AsyncSessionLocal
            from db.models import Conversation

            sid = await self._session()
            async with AsyncSessionLocal() as db, db.begin():
                await end(db, [sid], "crashed")
            async with AsyncSessionLocal() as db:
                return (await db.get(Conversation, sid)).meta
        self.assertEqual(self._run(scenario()).get("ended_by"), "crashed")

    def test_a_late_writer_cannot_overwrite_an_ended_session(self):
        async def scenario():
            from db.engine import AsyncSessionLocal

            sid = await self._session()
            async with AsyncSessionLocal() as db, db.begin():
                first = await end(db, [sid], "silent")   # the heartbeat sweep fails it
            async with AsyncSessionLocal() as db, db.begin():
                late = await end(db, [sid], "finished")  # then the bot's finalizer runs
            return first == [sid], late, await self._status(sid)

        self.assertEqual(self._run(scenario()), (True, [], "error"))


    def _failed_then(self, late_writer):
        """A session the heartbeat sweep failed, then late_writer(sid); its final status."""
        async def scenario():
            from db.engine import AsyncSessionLocal

            sid = await self._session()
            async with AsyncSessionLocal() as db, db.begin():
                await end(db, [sid], "silent")
            await late_writer(sid)
            return await self._status(sid)
        return self._run(scenario())

    def test_the_bot_finishing_late_keeps_the_sweeps_verdict(self):
        from bot import _finalize_conversation

        self.assertEqual(self._failed_then(lambda sid: _finalize_conversation(sid, "completed")), "error")

    def test_the_room_gone_sweep_keeps_a_session_that_ended_while_it_looked(self):
        # The sweep reads running sessions, asks LiveKit for its rooms, then writes:
        # the heartbeat sweep fails the session in between.
        from unittest import mock

        import runner
        from db.engine import AsyncSessionLocal

        async def scenario():
            sid = await self._session()

            async def rooms_while_the_session_fails(_):
                async with AsyncSessionLocal() as db, db.begin():
                    await end(db, [sid], "silent")
                return mock.Mock(rooms=[])

            lk = mock.MagicMock()
            lk.__aenter__.return_value.room.list_rooms = rooms_while_the_session_fails
            with mock.patch.object(runner.api, "LiveKitAPI", return_value=lk):
                await runner.reconcile_stale_conversations(min_age_seconds=0)
            return await self._status(sid)

        self.assertEqual(self._run(scenario()), "error")

if __name__ == "__main__":
    unittest.main()
