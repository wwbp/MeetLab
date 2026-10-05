"""A bot that dies mid-meeting rejoins with context (the user's decision, 2026-10-05).

A session that ended because its bot died (no heartbeat, or its pipeline crashed) while people
were still in the room is resumed: the runner starts a new session for the room with
meta.resumes = the dead one, and the new bot loads the conversation so far into its LLM
context instead of greeting again. The sessions of one conversation form a chain via resumes.
"""
from datetime import timedelta

DEATHS = ("silent", "crashed")    # sessions.ENDS events where the bot died, not ended
WINDOW = timedelta(minutes=2)     # a death older than this is history, not an outage
MAX_REJOINS = 3                   # a bot that keeps dying stops being restarted


def rejoin_due(row, chain_length: int, humans: int, room_running: bool, now) -> bool:
    """Should this ended session's room get a new bot that continues it?"""
    return ((row.meta or {}).get("ended_by") in DEATHS
            and row.ended_at is not None and now - row.ended_at <= WINDOW
            and humans > 0 and not room_running
            and chain_length <= MAX_REJOINS)


def chain(rows: dict, sid: str) -> list[str]:
    """The sessions of one conversation, oldest first, following meta.resumes back from sid."""
    out = [sid]
    while (prev := (rows[out[0]].meta or {}).get("resumes")) in rows and prev not in out:
        out.insert(0, prev)
    return out


def context_messages(turns, names: dict) -> list[dict]:
    """Stored turns as the LLM saw them live: people as "Name: text" (SpeakerLabelInjector's
    form, the identity before "__" when no name was stored), the bot's lines as its own."""
    return [{"role": "assistant", "content": t.text} if t.speaker_id.startswith("bot_")
            else {"role": "user", "content": f"{names.get(t.speaker_id) or t.speaker_id.split('__')[0]}: {t.text}"}
            for t in turns]


async def load_resume(db, row) -> dict | None:
    """For a session that resumes a dead one: the conversation so far as LLM messages, and the
    seconds since the conversation began (the session-limit clock keeps running). None otherwise."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from db.models import Conversation, Speaker, Utterance

    if not (row.meta or {}).get("resumes"):
        return None
    rows = {r.id: r for r in (await db.execute(
        select(Conversation).where(Conversation.room_name == row.room_name))).scalars()}
    ids = chain(rows, row.id)[:-1]  # the dead sessions before this one
    turns = (await db.execute(select(Utterance).where(Utterance.conv_id.in_(ids))
                              .order_by(Utterance.ts))).scalars().all()
    speakers = (await db.execute(select(Speaker).where(
        Speaker.id.in_({t.speaker_id for t in turns})))).scalars().all()
    names = {s.id: (s.meta or {}).get("display_name") for s in speakers}
    began = rows[ids[0]].started_at if ids else row.started_at
    return {"messages": context_messages(turns, names),
            "elapsed_s": (datetime.now(timezone.utc) - began).total_seconds()}
