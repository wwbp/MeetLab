"""Typed chat from the browser (diagnosis F13).

LiveKit's chat sends a legacy data packet, {id, timestamp: <ms since epoch>, message},
alongside its text stream; Pipecat hands the bot only the packet's bytes. Anything
that isn't a non-empty chat message is ignored.
"""
import json
from datetime import datetime, timezone
from typing import Optional


def chat_message(data: bytes) -> Optional[tuple[str, str]]:
    """(text, ISO 8601 time) of a chat packet, or None if it isn't one."""
    try:
        msg = json.loads(data)
    except ValueError:  # includes undecodable bytes
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("message"), str) or not msg["message"].strip():
        return None
    ms = msg.get("timestamp")
    when = (datetime.fromtimestamp(ms / 1000, timezone.utc) if isinstance(ms, (int, float)) and not isinstance(ms, bool)
            else datetime.now(timezone.utc))
    return msg["message"].strip(), when.isoformat()
