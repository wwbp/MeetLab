"""Is the bot for this session still alive? (design iteration 6)

The running bot writes heartbeat_at every BEAT. A running session silent for TTL,
or that never beat within START_GRACE of starting, is failed and its bot stopped.
Safe to run from every runner at once: each write is conditional on 'running'.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select, update

from db.models import Conversation

BEAT = timedelta(seconds=10)
TTL = timedelta(seconds=30)
# Longer than a cold start from an empty bot pool (132-166 s in the 4c spike).
START_GRACE = timedelta(seconds=300)


def silent_sessions(rows, now: datetime) -> list:
    return [
        r for r in rows
        if r.status == "running"
        and ((r.heartbeat_at and now - r.heartbeat_at > TTL)
             or (not r.heartbeat_at and now - r.started_at > START_GRACE))
    ]


async def beat(db_factory, session_id: str) -> None:
    async with db_factory() as db, db.begin():
        await db.execute(update(Conversation)
                         .where(Conversation.id == session_id, Conversation.status == "running")
                         .values(heartbeat_at=datetime.now(timezone.utc)))


async def beat_forever(db_factory, session_id: str) -> None:
    while True:
        try:
            await beat(db_factory, session_id)
        except Exception as e:  # a missed beat is survivable; three in a row is not
            logger.warning(f"heartbeat for {session_id} failed: {e}")
        await asyncio.sleep(BEAT.total_seconds())


async def fail_silent_sessions(db_factory, stop) -> list[str]:
    """Fail silent sessions, call stop(session_id) for each, return their IDs."""
    now = datetime.now(timezone.utc)
    async with db_factory() as db:
        running = (await db.execute(select(Conversation).where(Conversation.status == "running"))).scalars().all()
    ids = [r.id for r in silent_sessions(running, now)]
    if not ids:
        return []
    async with db_factory() as db, db.begin():
        closed = (await db.execute(
            update(Conversation)
            .where(Conversation.id.in_(ids), Conversation.status == "running")
            .values(status="error", ended_at=now)
            .returning(Conversation.id))).scalars().all()
    for sid in closed:
        try:
            stop(sid)
        except Exception as e:
            logger.warning(f"could not stop the bot for silent session {sid}: {e}")
    if closed:
        logger.warning(f"heartbeat: failed {len(closed)} silent session(s): {', '.join(closed)}")
    return list(closed)
