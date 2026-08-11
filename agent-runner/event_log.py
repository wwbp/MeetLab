"""Durable event log — the single place an admin can see what the pipeline did.

Two ways in:

1. Explicit — `record_event(...)` for things worth naming (a session started, a
   recording failed to start), including from meet via `POST /events`.
2. Automatic — `install_error_event_sink()` mirrors every WARNING and ERROR that
   any module logs into the same table, so a new `logger.error` is visible in the
   console without anyone remembering to wire it up.

Both are best-effort by design: telemetry must never take down the operation it
describes, and an error handler that raises inside logging would loop.
"""
import asyncio

from loguru import logger

from db.engine import AsyncSessionLocal
from db.models import Event

EVENT_SEVERITIES = ("info", "warning", "error")

# Long enough to be useful in a console row, short enough that a runaway message
# can't bloat the table.
MAX_MESSAGE_CHARS = 2_048

_LEVEL_TO_SEVERITY = {
    "WARNING": "warning",
    "ERROR": "error",
    "CRITICAL": "error",
}


async def record_event(
    event_type: str,
    severity: str = "info",
    room_name: str | None = None,
    conv_id: str | None = None,
    payload: dict | None = None,
) -> None:
    """Append one row to the event log. Never raises."""
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(
                    Event(
                        type=event_type,
                        severity=severity,
                        room_name=room_name,
                        conv_id=conv_id,
                        payload=payload or {},
                    )
                )
    except Exception as exc:  # pragma: no cover - defensive
        # Deliberately not logger.error: that would re-enter the sink.
        print(f"record_event failed ({event_type}): {exc}", flush=True)


def event_from_log_record(record) -> dict | None:
    """Translate a loguru record into `record_event` kwargs, or None to skip it.

    Pure and total: returns None for anything below WARNING and for any record it
    cannot read, because raising here would happen inside logging itself.
    """
    try:
        level_name = record["level"].name
        severity = _LEVEL_TO_SEVERITY.get(level_name)
        if severity is None:
            return None

        extra = dict(record.get("extra") or {})
        room_name = extra.pop("room_name", None)
        conv_id = extra.pop("conv_id", None)
        module = record.get("name") or "runner"

        payload = {
            "message": str(record.get("message", ""))[:MAX_MESSAGE_CHARS],
            "level": level_name,
            "module": module,
            "function": record.get("function"),
            "line": record.get("line"),
        }
        exception = record.get("exception")
        if exception is not None and getattr(exception, "type", None) is not None:
            payload["exception"] = exception.type.__name__
        if extra:
            payload["extra"] = extra

        return {
            "event_type": f"{module}.log.{severity}",
            "severity": severity,
            "room_name": room_name,
            "conv_id": conv_id,
            "payload": payload,
        }
    except Exception:
        return None


def install_error_event_sink(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Mirror WARNING+ logs into the event log.

    The sink is synchronous and may run from any thread, so the write is handed to
    the captured event loop. If there is no loop yet (import time) or the hand-off
    fails, the log line still reaches stdout/CloudWatch — we just lose the row,
    which is the right trade for never breaking logging.
    """

    def sink(message) -> None:
        try:
            event = event_from_log_record(message.record)
            if event is None:
                return
            target = loop or asyncio.get_event_loop()
            target.call_soon_threadsafe(lambda: target.create_task(record_event(**event)))
        except Exception:
            pass

    logger.add(sink, level="WARNING", format="{message}")
