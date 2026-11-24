"""Floor events reach the events table (docs/audio-modes-test-plan.md, "Settings, stack and live").

The bot's tests (services/bot/tests/test_floor_pipeline.py) fake record_event. Here a real
FloorRoom, given LiveKit's signals by a fake room, writes through the real record_event to the
stack's Postgres, and the rows are read back by room as the console reads them (GET /events).

Run in the container so the database is reachable:
    docker compose -f .devcontainer/docker-compose.yml run --rm --no-deps -T tests \
        python -m unittest tests.integration.test_audio_mode -v
"""
import asyncio
import os
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")

from livekit import rtc

from meetlab_api import runner
from meetlab_bot.floor import Floor
from meetlab_bot.floor_room import FloorRoom
from meetlab_core.db.engine import AsyncSessionLocal, engine
from meetlab_core.db.models import Conversation

BOT = "bot_test"


class _Room:
    """The part of rtc.Room a FloorRoom listens to."""

    def __init__(self):
        self.handlers = {}

    def on(self, name):
        def register(fn):
            self.handlers.setdefault(name, []).append(fn)
            return fn
        return register

    def emit(self, name, *args):
        for fn in self.handlers[name]:
            fn(*args)


class FloorEventsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # A fresh loop per test: abandon the pool's old-loop connections (test_recording_autostart).
        await engine.dispose(close=False)
        self.addAsyncCleanup(engine.dispose, close=False)
        self.now = 0.0

    async def _floor(self, mode):
        """A FloorRoom listening to a fake room, for a session that exists (events.conv_id is a
        foreign key: an event for an unknown session is not stored)."""
        self.room_name = f"audio-mode-{mode}-{uuid.uuid4().hex[:8]}"
        self.conv_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as db, db.begin():
            db.add(Conversation(id=self.conv_id, room_name=self.room_name, bot_identity=BOT, status="running"))
        floor = FloorRoom(mode, bot_identity=BOT, room_name=self.room_name, conv_id=self.conv_id,
                          enqueue=mock.AsyncMock(), vad_stop_secs=0.2, now=lambda: self.now)
        self.room = _Room()
        client = SimpleNamespace(room=self.room, connect=mock.AsyncMock())
        floor.listen_from_connect(client)
        await client.connect()
        return floor

    async def _stored(self, count):
        """The room's rows as the console reads them, once all `count` have been written:
        the floor never waits on its event writes."""
        for _ in range(100):
            events = (await runner.list_events(room=self.room_name, _=None))["events"]
            if len(events) >= count:
                break
            await asyncio.sleep(0.05)
        return events

    def _assert_rows(self, events, expected, before):
        self.assertEqual(sorted((e["type"], e["payload"]["who"]) for e in events), sorted(expected))
        for e in events:
            self.assertEqual((e["room_name"], e["conv_id"], e["severity"]), (self.room_name, self.conv_id, "info"))
            self.assertGreaterEqual(e["payload"]["at"], before)
            self.assertLessEqual(e["payload"]["at"], time.time())

    async def test_floor_events_reach_events_table(self):
        before = time.time()
        floor = await self._floor("baton")

        def signal(t, who, kind):
            self.now = t
            self.room.emit("data_received", SimpleNamespace(
                topic="meetlab.floor", participant=SimpleNamespace(identity=who),
                data=b'{"type":"%s"}' % kind.encode()))

        signal(1.0, "alice", "claim")
        signal(1.1, "bob", "claim")
        signal(2.0, "alice", "release")
        await floor.tick(2.0 + Floor.IDLE_SECS)  # bob never spoke

        expected = [("floor.claim", "alice"), ("floor.grant", "alice"), ("floor.claim", "bob"),
                    ("floor.deny", "bob"), ("floor.release", "alice"), ("floor.grant", "bob"),
                    ("floor.timeout", "bob")]
        self._assert_rows(await self._stored(len(expected)), expected, before)

    async def test_ptt_floor_events_reach_events_table(self):
        before = time.time()
        await self._floor("ptt")
        alice = SimpleNamespace(identity="alice")

        def mic(muted):
            return SimpleNamespace(source=rtc.TrackSource.SOURCE_MICROPHONE, muted=muted)

        self.now = 1.0
        self.room.emit("track_unmuted", alice, mic(False))
        self.now = 2.0
        self.room.emit("track_muted", alice, mic(True))

        expected = [("floor.press", "alice"), ("floor.release", "alice")]
        self._assert_rows(await self._stored(len(expected)), expected, before)


if __name__ == "__main__":
    unittest.main()
