"""The runner's rejoin sweep (rejoin.py, runner.rejoin_dead_sessions): a room whose bot died with
people still in it gets a new bot that resumes the session, once. Against the container's Postgres:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \\
        uv run python -m unittest tests.test_rejoin_sweep -v
"""
import asyncio
import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")

import runner  # noqa: E402


class RejoinSweepTest(unittest.TestCase):
    def setUp(self):
        self.room = f"rejoin-{uuid.uuid4().hex[:8]}"

    def _run(self, coro):
        async def fresh():
            from db.engine import engine
            await engine.dispose(close=False)  # a fresh loop per test (see test_sessions)
            try:
                return await coro
            finally:  # and leave no connection tied to this loop for the next test's app
                await engine.dispose()
        return asyncio.run(fresh())

    async def _dead(self, ended_by):
        from db.engine import AsyncSessionLocal
        from db.models import Conversation
        sid = str(uuid.uuid4())
        async with AsyncSessionLocal() as db, db.begin():
            db.add(Conversation(id=sid, room_name=self.room, bot_identity="bot_x", status="error",
                                ended_at=datetime.now(timezone.utc), meta={"ended_by": ended_by, "agent_name": "a"}))
        return sid

    async def _room_sessions(self):
        from sqlalchemy import select
        from db.engine import AsyncSessionLocal
        from db.models import Conversation
        async with AsyncSessionLocal() as db:
            return list((await db.execute(select(Conversation).where(Conversation.room_name == self.room))).scalars())

    def _sweep(self, humans=2):
        with mock.patch.dict(os.environ, {"BOT_DISPATCHER": "docker"}), \
             mock.patch.object(runner, "_humans_in_room", new=mock.AsyncMock(return_value=humans)), \
             mock.patch.object(runner, "run_bot_container", return_value="meetlab-bot-x") as run, \
             mock.patch.object(runner, "_docker_api"):
            self._run(runner.rejoin_dead_sessions())
        return run

    # The sweep acts on the whole (shared test) database: assertions look at this room only.
    def _running(self):
        return [r for r in self._run(self._room_sessions()) if r.status == "running"]

    def test_a_dead_bots_room_gets_one_new_bot_that_resumes_it(self):
        dead = self._run(self._dead("silent"))
        run = self._sweep()
        rows = self._run(self._room_sessions())
        new = [r for r in rows if r.status == "running"]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0].meta.get("resumes"), dead)
        self.assertEqual(new[0].meta.get("agent_name"), "a")  # the room's start settings carry over
        self.assertNotIn("ended_by", new[0].meta)
        self.assertIn(new[0].id, [c.args[1] for c in run.call_args_list])  # its bot was dispatched
        self._sweep()
        self.assertEqual(len(self._running()), 1)  # the next tick: it already has its bot

    def test_a_stopped_bot_or_an_empty_room_gets_none(self):
        self._run(self._dead("stopped"))
        self._sweep()
        self.assertEqual(self._running(), [])
        self._run(self._dead("crashed"))
        self._sweep(humans=0)
        self.assertEqual(self._running(), [])


class LoadResumeTest(unittest.TestCase):
    """What a resuming bot reads: the chain's turns as the LLM saw them, and how long ago it began."""
    setUp, _run = RejoinSweepTest.setUp, RejoinSweepTest._run

    def test_a_resumed_session_gets_the_conversation_so_far(self):
        async def scenario():
            from datetime import timedelta
            from db.engine import AsyncSessionLocal
            from db.models import Conversation, Speaker, Utterance
            from rejoin import load_resume
            first, second = str(uuid.uuid4()), str(uuid.uuid4())
            began = datetime.now(timezone.utc) - timedelta(minutes=4)
            ana, bot = f"ana__{first[:6]}", f"bot_{first[:6]}"
            async with AsyncSessionLocal() as db, db.begin():
                db.add_all([Speaker(id=ana, meta={"display_name": "Ana"}), Speaker(id=bot, meta={"role": "bot"}),
                            Conversation(id=first, room_name=self.room, bot_identity=bot, status="error",
                                         started_at=began, ended_at=began, meta={"ended_by": "silent"}),
                            Conversation(id=second, room_name=self.room, bot_identity=bot, status="running",
                                         meta={"resumes": first})])
            async with AsyncSessionLocal() as db, db.begin():
                db.add_all([Utterance(speaker_id=bot, conv_id=first, ts=1.0, text="Hello!"),
                            Utterance(speaker_id=ana, conv_id=first, ts=2.0, text="Hi, I'm Ana.")])
            async with AsyncSessionLocal() as db:
                fresh = await load_resume(db, await db.get(Conversation, first))
                resumed = await load_resume(db, await db.get(Conversation, second))
            return fresh, resumed
        fresh, resumed = self._run(scenario())
        self.assertIsNone(fresh)  # a session that resumes nothing starts fresh, with its greeting
        self.assertEqual(resumed["messages"], [{"role": "assistant", "content": "Hello!"},
                                               {"role": "user", "content": "Ana: Hi, I'm Ana."}])
        self.assertAlmostEqual(resumed["elapsed_s"], 240, delta=5)


if __name__ == "__main__":
    unittest.main()
