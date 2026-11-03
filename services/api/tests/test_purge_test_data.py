"""Production's live tests leave nothing behind (user, 2026-10-14): after each run, everything
of the test rooms goes, and nothing else. A test room is exactly `accept-` + 6 hex characters
(acceptance.Meeting); its sessions, turns, events, settings, recording rows and files go,
and so do the speakers only it had. A speaker who also spoke in a real room stays, as does any
room merely starting with "accept-".
Against the container's Postgres:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \\
        python -m unittest tests.test_purge_test_data -v
"""
import asyncio
import os
import unittest
import uuid
from unittest import mock

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")

from meetlab_api import runner  # noqa: E402


def _run(coro):
    async def fresh():
        from meetlab_core.db.engine import engine
        await engine.dispose(close=False)
        try:
            return await coro
        finally:
            await engine.dispose()
    return asyncio.run(fresh())


class PurgeTestDataTest(unittest.TestCase):
    def setUp(self):
        h = uuid.uuid4().hex
        self.test_room, self.real_room, self.lookalike = f"accept-{h[:6]}", f"study-{h[:6]}", f"accept-notatest-{h[:6]}"
        self.test_conv, self.real_conv, self.like_conv = (str(uuid.uuid4()) for _ in range(3))
        self.test_only, self.shared, self.real_only = (f"{n}__{h[:4]}" for n in ("standin", "Ada", "Bea"))
        self.test_file = f"recordings/20261014T000000Z-{self.test_room}-recording.mp4"
        self.real_file = f"recordings/20261014T000000Z-{self.real_room}-recording.mp4"

        async def add():
            from meetlab_core.db.engine import AsyncSessionLocal
            from meetlab_core.db.models import BotConfig, Conversation, Event, MediaFile, Speaker, Utterance
            async with AsyncSessionLocal() as db, db.begin():
                for s in (self.test_only, self.shared, self.real_only):
                    db.add(Speaker(id=s, meta={}))
                for conv, room in ((self.test_conv, self.test_room), (self.real_conv, self.real_room), (self.like_conv, self.lookalike)):
                    db.add(Conversation(id=conv, room_name=room, bot_identity="bot_x", status="ended", meta={}))
                await db.flush()
                db.add_all([
                    Utterance(id=str(uuid.uuid4()), speaker_id=self.test_only, conv_id=self.test_conv, ts=1.0, text="test", meta={}),
                    Utterance(id=str(uuid.uuid4()), speaker_id=self.shared, conv_id=self.test_conv, ts=2.0, text="test", meta={}),
                    Utterance(id=str(uuid.uuid4()), speaker_id=self.shared, conv_id=self.real_conv, ts=3.0, text="real", meta={}),
                    Utterance(id=str(uuid.uuid4()), speaker_id=self.real_only, conv_id=self.like_conv, ts=4.0, text="real", meta={}),
                    MediaFile(id=str(uuid.uuid4()), conv_id=self.test_conv, type="recording", status="available", path=self.test_file, meta={}),
                    MediaFile(id=str(uuid.uuid4()), conv_id=self.real_conv, type="recording", status="available", path=self.real_file, meta={}),
                    Event(conv_id=self.test_conv, type="room_finished", severity="info", room_name=self.test_room, payload={}),
                    Event(conv_id=None, type="webhook", severity="info", room_name=self.test_room, payload={}),
                    Event(conv_id=self.real_conv, type="room_finished", severity="info", room_name=self.real_room, payload={}),
                    BotConfig(scope=self.test_room), BotConfig(scope=self.real_room),
                ])
        _run(add())

    def tearDown(self):
        async def drop():
            from sqlalchemy import delete
            from meetlab_core.db.engine import AsyncSessionLocal
            from meetlab_core.db.models import BotConfig, Conversation, Event, MediaFile, Speaker, Utterance
            convs = [self.test_conv, self.real_conv, self.like_conv]
            async with AsyncSessionLocal() as db, db.begin():
                await db.execute(delete(Event).where(Event.room_name.in_([self.test_room, self.real_room, self.lookalike])))
                await db.execute(delete(MediaFile).where(MediaFile.conv_id.in_(convs)))
                await db.execute(delete(Utterance).where(Utterance.conv_id.in_(convs)))
                await db.execute(delete(Conversation).where(Conversation.id.in_(convs)))
                await db.execute(delete(Speaker).where(Speaker.id.in_([self.test_only, self.shared, self.real_only])))
                await db.execute(delete(BotConfig).where(BotConfig.scope.in_([self.test_room, self.real_room])))
        _run(drop())

    def state(self):
        async def get():
            from sqlalchemy import func, select
            from meetlab_core.db.engine import AsyncSessionLocal
            from meetlab_core.db.models import BotConfig, Conversation, Event, MediaFile, Speaker
            async with AsyncSessionLocal() as db:
                count = lambda q: db.scalar(select(func.count()).select_from(q.subquery()))  # noqa: E731
                return {
                    "conversations": {r for r in (await db.execute(select(Conversation.room_name).where(
                        Conversation.id.in_([self.test_conv, self.real_conv, self.like_conv])))).scalars()},
                    "speakers": set((await db.execute(select(Speaker.id).where(
                        Speaker.id.in_([self.test_only, self.shared, self.real_only])))).scalars()),
                    "files": set((await db.execute(select(MediaFile.path).where(
                        MediaFile.path.in_([self.test_file, self.real_file])))).scalars()),
                    "test_events": await count(select(Event).where(Event.room_name == self.test_room)),
                    "real_events": await count(select(Event).where(Event.room_name == self.real_room)),
                    "settings": set((await db.execute(select(BotConfig.scope).where(
                        BotConfig.scope.in_([self.test_room, self.real_room])))).scalars()),
                }
        return _run(get())

    def test_everything_of_the_test_rooms_goes_and_nothing_else(self):
        deleted = []
        with mock.patch.object(runner.storage, "delete_file", side_effect=lambda p: deleted.append(p)):
            counts = _run(runner.purge_test_data())

        after = self.state()
        self.assertEqual(after["conversations"], {self.real_room, self.lookalike}, "only the test room's session goes")
        self.assertEqual(after["speakers"], {self.shared, self.real_only}, "a speaker of a real room stays")
        self.assertEqual(after["files"], {self.real_file})
        self.assertEqual((after["test_events"], after["real_events"]), (0, 1))
        self.assertEqual(after["settings"], {self.real_room})
        self.assertEqual(deleted, [self.test_file], "only the test session's file is deleted from storage")
        self.assertGreaterEqual(counts["conversations"], 1)

    def test_it_is_reachable_only_with_the_runner_secret(self):
        from fastapi.testclient import TestClient
        with mock.patch.object(runner, "BOT_RUNNER_SECRET", "s3cret"), \
                mock.patch.object(runner.storage, "delete_file"):
            client = TestClient(runner.app)
            self.assertIn(client.delete("/test-data").status_code, (401, 403))
            ok = client.delete("/test-data", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.state()["conversations"], {self.real_room, self.lookalike})


if __name__ == "__main__":
    unittest.main()


class PurgeGroupLinkTestDataTest(unittest.TestCase):
    """A live test's group link: its pool is a test scope (accept-…), its rooms are link-….
    The link, its tickets, matches, rooms' sessions, events and settings go; a real link stays."""

    def setUp(self):
        from datetime import datetime, timezone
        h = uuid.uuid4().hex
        self.scope = f"accept-{h[:6]}"
        self.test_link, self.real_link = f"tl-{h[:8]}", f"rl-{h[:8]}"
        self.test_room, self.real_room = f"link-t{h[:8]}", f"link-r{h[:8]}"
        self.test_conv, self.real_conv = str(uuid.uuid4()), str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        async def add():
            from meetlab_core.db.engine import AsyncSessionLocal
            from meetlab_core.db.models import BotConfig, Conversation, Event, Match, StartLink, Ticket
            async with AsyncSessionLocal() as db, db.begin():
                db.add_all([BotConfig(scope=self.scope), BotConfig(scope=self.test_room), BotConfig(scope=self.real_room),
                            StartLink(link_id=self.test_link, name="t", pool=[self.scope], size=2),
                            StartLink(link_id=self.real_link, name="r", pool=["global"], size=2)])
                await db.flush()
                for link, room, scope, conv in ((self.test_link, self.test_room, self.scope, self.test_conv),
                                                (self.real_link, self.real_room, "global", self.real_conv)):
                    db.add(Match(room_name=room, link_id=link, bot_config_scope=scope, size=2, created_at=now))
                    db.add(Conversation(id=conv, room_name=room, bot_identity="bot_x", status="ended", meta={}))
                    await db.flush()
                    db.add(Ticket(id=str(uuid.uuid4()), link_id=link, participant_id=None, state="done",
                                  queued_at=now, last_seen=now, room_name=room))
                    db.add(Event(conv_id=None, type="match_formed", severity="info", room_name=room, payload={}))
        _run(add())

    def tearDown(self):
        async def drop():
            from sqlalchemy import delete
            from meetlab_core.db.engine import AsyncSessionLocal
            from meetlab_core.db.models import BotConfig, Conversation, Event, Match, StartLink, Ticket
            links, rooms = [self.test_link, self.real_link], [self.test_room, self.real_room]
            async with AsyncSessionLocal() as db, db.begin():
                await db.execute(delete(Event).where(Event.room_name.in_(rooms)))
                await db.execute(delete(Ticket).where(Ticket.link_id.in_(links)))
                await db.execute(delete(Conversation).where(Conversation.room_name.in_(rooms)))
                await db.execute(delete(Match).where(Match.link_id.in_(links)))
                await db.execute(delete(StartLink).where(StartLink.link_id.in_(links)))
                await db.execute(delete(BotConfig).where(BotConfig.scope.in_([self.scope, *rooms])))
        _run(drop())

    def test_the_test_link_goes_and_the_real_one_stays(self):
        _run(runner.purge_test_data())

        async def left():
            from sqlalchemy import select
            from meetlab_core.db.engine import AsyncSessionLocal
            from meetlab_core.db.models import BotConfig, Conversation, Event, Match, StartLink, Ticket
            rooms = [self.test_room, self.real_room]
            async with AsyncSessionLocal() as db:
                return {
                    "links": set(await db.scalars(select(StartLink.link_id).where(
                        StartLink.link_id.in_([self.test_link, self.real_link])))),
                    "tickets": set(await db.scalars(select(Ticket.room_name).where(Ticket.room_name.in_(rooms)))),
                    "matches": set(await db.scalars(select(Match.room_name).where(Match.room_name.in_(rooms)))),
                    "conversations": set(await db.scalars(select(Conversation.room_name).where(
                        Conversation.room_name.in_(rooms)))),
                    "events": set(await db.scalars(select(Event.room_name).where(Event.room_name.in_(rooms)))),
                    "settings": set(await db.scalars(select(BotConfig.scope).where(
                        BotConfig.scope.in_([self.scope, *rooms])))),
                }
        self.assertEqual(_run(left()), {"links": {self.real_link}, "tickets": {self.real_room},
                                        "matches": {self.real_room}, "conversations": {self.real_room},
                                        "events": {self.real_room}, "settings": {self.real_room}})
