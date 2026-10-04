"""Focused investigation probe; run inside the local agent-runner container.

Uses the existing local Postgres and LiveKit, creates uniquely named audit rows
and rooms, and removes them afterward. Never run against production. TTS is
synthetic; participants publish no audio and send no model prompts. This invokes
the real start handler directly, so it does not test HTTP routing or browser UI.
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, "/app")
import config  # load configured environment before overriding probe-only settings

os.environ["ENABLE_TRACING"] = "false"
os.environ["BOT_MOCK_TTS"] = "1"
os.environ["DISABLE_CONVERSATION_RECONCILE"] = "1"
assert os.environ.get("LIVEKIT_URL", "").startswith("ws://transport-server:"), "Local LiveKit only"
assert "@postgres:" in os.environ.get("DATABASE_URL", ""), "Local Postgres only"

from fastapi import BackgroundTasks
from livekit import api, rtc
from loguru import logger
from sqlalchemy import delete, select

import bot
import runner
from db.engine import AsyncSessionLocal, engine
from db.models import BotConfig, Conversation, Event, MediaFile, Speaker, Utterance

logger.remove()
logger.add(sys.stderr, level="ERROR")
prefix = "diagnosis-" + uuid4().hex[:10]
sessions = []
rooms = []
humans = []
tasks = []
identities = []


def result(name, **values):
    print(json.dumps({"probe": name, **values}), flush=True)


async def start(room):
    background = BackgroundTasks()
    request = SimpleNamespace(json=AsyncMock(return_value={"room_name": room}))
    response = await runner.start_bot(request, background, None)
    assert isinstance(response, dict), "start failed"
    sessions.append(response["session_id"])
    identities.append(response["bot_identity"])
    return response, background


async def human(room, suffix):
    identity = prefix + "-" + suffix
    identities.append(identity)
    token = (api.AccessToken().with_identity(identity)
             .with_grants(api.VideoGrants(room_join=True, room=room)).to_jwt())
    connection = rtc.Room()
    await connection.connect(os.environ["LIVEKIT_URL"], token)
    humans.append(connection)
    return connection


async def status(cid):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Conversation.status).where(Conversation.id == cid))).scalar()


async def wait_presence(lk, room, identity):
    for _ in range(100):
        participants = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
        if any(p.identity == identity for p in participants.participants):
            return
        await asyncio.sleep(.1)
    raise AssertionError("bot never joined")


async def new_room(lk, suffix):
    room = prefix + "-" + suffix
    rooms.append(room)
    await lk.room.create_room(api.CreateRoomRequest(name=room))
    async with AsyncSessionLocal() as db, db.begin():
        db.add(BotConfig(scope=room, auto_record=False, session_limit_minutes=0))
    return room


async def main():
    async with api.LiveKitAPI() as lk:
        try:
            room = await new_room(lk, "duplicate")
            a, b = await asyncio.gather(start(room), start(room))
            result("concurrent_direct_start", accepted=2,
                   distinct_sessions=a[0]["session_id"] != b[0]["session_id"],
                   scheduled_bots=len(a[1].tasks) + len(b[1].tasks))
            # Background callbacks deliberately not run in this duplicate probe.
            await human(room, "orphan-human")
            # Restrict the real reconciler's selection to this probe's rows.
            with patch.object(runner, "select", lambda *args: select(*args).where(Conversation.id.in_(sessions))):
                await runner.reconcile_stale_conversations(min_age_seconds=0)
            result("room_exists_bots_never_started", statuses=[await status(a[0]["session_id"]),
                                                             await status(b[0]["session_id"])])

            room = await new_room(lk, "group")
            first = await human(room, "first")
            second = await human(room, "second")
            response, background = await start(room)
            task = asyncio.create_task(background())
            tasks.append(task)
            await wait_presence(lk, room, response["bot_identity"])
            await asyncio.sleep(2)
            result("group_before_any_leave", task_done=task.done(), status=await status(response["session_id"]))
            assert not task.done(), "group probe invalid: bot stopped before departure"
            await first.disconnect()
            await asyncio.sleep(2)
            result("one_of_two_humans_leaves", task_done=task.done(), status=await status(response["session_id"]))
            await second.disconnect()
            await asyncio.wait_for(asyncio.shield(task), 25)
            result("last_human_leaves", task_done=task.done(), status=await status(response["session_id"]))
            await human(room, "second")
            participants = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
            result("human_rejoins_after_last_leave", bot_present=any(
                p.identity == response["bot_identity"] for p in participants.participants))

            room = await new_room(lk, "bot-first")
            response, background = await start(room)
            task = asyncio.create_task(background())
            tasks.append(task)
            await wait_presence(lk, room, response["bot_identity"])
            first = await human(room, "later-first")
            second = await human(room, "later-second")
            await asyncio.sleep(2)
            assert not task.done(), "bot-first probe invalid: bot stopped before departure"
            await first.disconnect()
            await asyncio.sleep(2)
            result("bot_first_one_of_two_leaves", task_done=task.done(), status=await status(response["session_id"]))
            await second.disconnect()
            await asyncio.wait_for(asyncio.shield(task), 25)
            result("bot_first_last_human_leaves", task_done=task.done(), status=await status(response["session_id"]))

            room = await new_room(lk, "remove")
            await human(room, "remaining")
            response, background = await start(room)
            task = asyncio.create_task(background())
            tasks.append(task)
            await wait_presence(lk, room, response["bot_identity"])
            await asyncio.sleep(2)
            await lk.room.remove_participant(api.RoomParticipantIdentity(room=room, identity=response["bot_identity"]))
            await asyncio.sleep(3)
            result("bot_removed_human_remains", task_done=task.done(), status=await status(response["session_id"]))

            args = SimpleNamespace(room_name=room, url=os.environ["LIVEKIT_URL"],
                                   token="unused", session_id="unused", bot_identity="unused")
            with patch.object(bot, "load_bot_config", AsyncMock(side_effect=RuntimeError("probe setup failure"))), \
                 patch.object(bot, "_finalize_conversation", AsyncMock()) as finalize:
                try:
                    await bot.bot(args)
                except RuntimeError:
                    result("setup_failure", finalizer_calls=finalize.await_count)
        finally:
            for connection in humans:
                await connection.disconnect()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 30)
            for room in rooms:
                try:
                    await lk.room.delete_room(api.DeleteRoomRequest(room=room))
                except Exception:
                    pass
            async with AsyncSessionLocal() as db, db.begin():
                await db.execute(delete(MediaFile).where(MediaFile.conv_id.in_(sessions)))
                await db.execute(delete(Utterance).where(Utterance.conv_id.in_(sessions)))
                await db.execute(delete(Event).where(Event.conv_id.in_(sessions)))
                await db.execute(delete(Conversation).where(Conversation.id.in_(sessions)))
                await db.execute(delete(Speaker).where(Speaker.id.in_(identities)))
                await db.execute(delete(BotConfig).where(BotConfig.scope.in_(rooms)))
            await engine.dispose()
            result("cleanup", rooms_removed=len(rooms), sessions_removed=len(sessions))


asyncio.run(main())
