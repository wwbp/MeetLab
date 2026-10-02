"""A session's status and how it may change (design iterations 1 and 3).

A session runs until one event ends it, and then never changes. Five writers can end
one, at nearly the same moment: the bot's own finalizer, the heartbeat sweep, the
console's Stop, the room-gone sweep and a failed dispatch. end() is the only way to:
a compare-and-set UPDATE, so the first writer wins and a late one changes nothing.
"""
from datetime import datetime, timezone

from sqlalchemy import update

from db.models import Conversation

RUNNING = "running"

ENDS = {
    "finished": "completed",         # the bot ended normally (empty room, SIGTERM, removed)
    "crashed": "error",              # the bot's pipeline failed
    "stopped": "completed",          # the console's Stop
    "silent": "error",               # no heartbeat (heartbeat.py)
    "room_gone": "ended",            # the room closed under it
    "dispatch_failed": "error",      # ECS or Docker would not start the bot
}


class IllegalTransition(ValueError):
    pass


def transition(status: str, event: str) -> str:
    if status != RUNNING:
        raise IllegalTransition(f"a {status} session never changes")
    if event not in ENDS:
        raise IllegalTransition(f"unknown event {event!r}")
    return ENDS[event]


async def end(db, ids: list[str], event: str) -> list[str]:
    """End the still-running sessions among ids; returns those this call ended."""
    result = await db.execute(
        update(Conversation)
        .where(Conversation.id.in_(ids), Conversation.status == RUNNING)
        .values(status=transition(RUNNING, event), ended_at=datetime.now(timezone.utc))
        .returning(Conversation.id))
    return list(result.scalars())
