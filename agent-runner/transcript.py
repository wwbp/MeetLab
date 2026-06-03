"""Transcript generation: format utterances as markdown and write to storage.

Public API:
    format_transcript(conv, utterances)  → str (markdown)
    generate_async(conv_id, media_file_id) → None  (background task)
"""

import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

import storage
from db.engine import AsyncSessionLocal
from db.models import Conversation, MediaFile, Utterance


# ── formatting ────────────────────────────────────────────────────────────────

def _display_name(speaker_id: str, speaker_meta: dict) -> str:
    """Resolve clean display name from Speaker.meta or by stripping __postfix."""
    return speaker_meta.get("display_name") or speaker_id.split("__")[0]


def _fmt_ts(unix_ts: float | None, session_start: datetime) -> str:
    """Format a unix timestamp as HH:MM:SS relative to session start."""
    if unix_ts is None:
        return "??:??:??"
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    delta = int((dt - session_start.replace(tzinfo=timezone.utc)).total_seconds())
    delta = max(delta, 0)
    h, rem = divmod(delta, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_transcript(conv: Conversation, utterances: list) -> str:
    """Render a conversation's utterances as a markdown document."""
    lines: list[str] = []

    # Header
    lines.append(f"# Transcript — {conv.room_name}")
    lines.append("")
    lines.append(f"| | |")
    lines.append(f"|---|---|")
    lines.append(f"| **Room** | {conv.room_name} |")
    lines.append(f"| **Date** | {conv.started_at.strftime('%Y-%m-%d')} |")
    lines.append(f"| **Started** | {conv.started_at.strftime('%H:%M:%S UTC')} |")
    if conv.ended_at:
        secs = int((conv.ended_at - conv.started_at).total_seconds())
        lines.append(f"| **Duration** | {secs // 60}m {secs % 60}s |")
    lines.append(f"| **Turns** | {len(utterances)} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not utterances:
        lines.append("*No utterances recorded for this session.*")
        return "\n".join(lines)

    for utt in utterances:
        meta = utt.speaker.meta if utt.speaker else {}
        name = _display_name(utt.speaker_id, meta)
        ts = _fmt_ts(utt.ts, conv.started_at)
        is_bot = meta.get("role") == "bot"
        speaker_label = f"**{name}**" + (" *(bot)*" if is_bot else "")
        lines.append(f"**[{ts}]** {speaker_label}: {utt.text}")
        lines.append("")

    return "\n".join(lines)


# ── async generator (background task) ─────────────────────────────────────────

async def generate_async(conv_id: str, media_file_id: str) -> None:
    """Query DB, format, write to storage, update MediaFile status.

    Designed to run as a FastAPI BackgroundTask — never raises to caller.
    """
    logger.info(f"transcript: starting generation for conv={conv_id} file={media_file_id}")
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == conv_id)
            )
            conv = result.scalar_one_or_none()
            if not conv:
                raise ValueError(f"Conversation {conv_id} not found")

            result = await db.execute(
                select(Utterance)
                .where(Utterance.conv_id == conv_id)
                .options(selectinload(Utterance.speaker))
                .order_by(Utterance.ts)
            )
            utterances = list(result.scalars().all())

        content = format_transcript(conv, utterances)
        filename = storage.build_filename(conv.room_name, "transcript", "md")
        path = await storage.write_file(filename, content.encode("utf-8"))

        async with AsyncSessionLocal() as db:
            async with db.begin():
                await db.execute(
                    update(MediaFile)
                    .where(MediaFile.id == media_file_id)
                    .values(
                        status="available",
                        path=path,
                        meta={"utterance_count": len(utterances)},
                    )
                )
        logger.info(
            f"transcript: done conv={conv_id} utterances={len(utterances)} path={path}"
        )

    except Exception as exc:
        logger.error(f"transcript: generation failed conv={conv_id}: {exc}")
        try:
            async with AsyncSessionLocal() as db:
                async with db.begin():
                    await db.execute(
                        update(MediaFile)
                        .where(MediaFile.id == media_file_id)
                        .values(status="failed", meta={"error": str(exc)})
                    )
        except Exception as db_exc:
            logger.error(f"transcript: failed to mark failure in DB: {db_exc}")
