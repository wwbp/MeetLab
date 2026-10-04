"""Two small decisions the study features rest on, kept pure and testable.

Both exist because a paid study has requirements a demo does not: it has to end
at a known time, and every session has to be attributable to a payable
participant.
"""
import re

# What the bot says when the session limit runs out. Overridable per room via
# bot_config.closing_message; this is the default the column ships with.
CLOSING_MESSAGE = (
    "That's all the time we have for today. Thank you so much for talking with me. "
    "Please leave the room now, and copy the completion code shown on your screen "
    "into the survey."
)

# Prolific participant IDs are 24 lowercase hex characters.
_PROLIFIC_ID = re.compile(r"^[0-9a-f]{24}$")


def closing_due(*, joined_at: float | None, now: float, limit_minutes: int) -> bool:
    """Whether the session has run past its configured limit.

    limit_minutes <= 0 means unlimited, matching bot_config's existing
    convention and meet's lib/session-limit.ts. joined_at is None before the
    first participant arrives, so an empty room never counts down.
    """
    if joined_at is None or limit_minutes <= 0:
        return False
    return (now - joined_at) >= limit_minutes * 60


def prolific_id(raw: str | None) -> str | None:
    """The participant's Prolific ID, or None if it is not one.

    Normalises case and whitespace so a pasted ID and a redirected one are the
    same participant rather than two rows.
    """
    if not raw:
        return None
    candidate = raw.strip().lower()
    return candidate if _PROLIFIC_ID.match(candidate) else None


def speaker_meta(display_name: str, raw_metadata: str | None) -> dict:
    """A person's speaker record: their name and the Prolific ID their token carries (set by
    /api/connection-details from the pre-join form), the identifier a paid study is matched
    and paid on. Built one way whether they joined before the bot or after."""
    meta = {"role": "participant", "display_name": display_name}
    raw = (raw_metadata or "").strip()
    pid = prolific_id(raw)
    if pid:
        meta["prolific_id"] = pid
    elif raw:
        # The form validates before joining, so this is a bypassed or stale client. Keep it:
        # an unmatched session is a participant who worked and cannot be paid.
        meta["prolific_id_invalid"] = raw[:200]
    return meta
