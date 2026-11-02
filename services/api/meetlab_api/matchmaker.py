"""Group start links: the matchmaker (tickets → matches) and the /tickets endpoints.

step() and ticket_view() are pure (no DB, no clock, no randomness); the endpoints below wrap
them in one locked transaction per link. The router is mounted by runner.py behind the
runner's secret. Request/reply shapes: tests/fixtures/matchmaking/*.json.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from fastapi import APIRouter

TICKET_LIVE_S = 90   # a searching ticket polled within this is live (matchable)
READY_CHECK_S = 20   # a group's ready-check window (Q15)
DONE_AFTER_S = 120   # a placed ticket is done once its room has had no running session this long
SETUP_CLAIM_S = 30   # a setup claim older than this may be retried (runner.setup_match)

router = APIRouter()


# What step() asks for; the caller applies them in one transaction, then StopRoom after commit.

@dataclass(frozen=True)
class UpdateTicket:
    id: str
    fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CreateMatch:
    room_name: str
    bot_config_scope: str
    size: int
    ready_deadline: datetime | None
    starts_at: datetime | None
    members: tuple[str, ...]  # ticket ids, now found with room_name set


@dataclass(frozen=True)
class UpdateMatch:
    room_name: str
    fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StopRoom:
    room_name: str


@dataclass(frozen=True)
class Event:
    name: str  # match_formed | ready_failed | ticket_expired (record_event)
    payload: dict = field(default_factory=dict)


# step()'s inputs. The link is any object with size, min_size, delay_s, max_wait_s (a StartLink).

@dataclass(frozen=True)
class TicketState:
    id: str
    state: str
    queued_at: datetime
    last_seen: datetime
    room_name: str | None = None
    ready_at: datetime | None = None
    requeued_at: datetime | None = None
    placed_at: datetime | None = None


@dataclass(frozen=True)
class MatchState:
    """An open match (closed_at null)."""
    room_name: str
    ready_deadline: datetime | None
    starts_at: datetime | None
    session_id: str | None
    session_ended_at: datetime | None  # null while setup is pending or a session runs in the room


def _s(n: float) -> timedelta:
    return timedelta(seconds=n)


def _fifo(tickets):
    return sorted(tickets, key=lambda t: (t.queued_at, t.id))


def step(link, tickets: list[TicketState], matches: list[MatchState], now: datetime, new_match) -> list:
    """One matchmaker step for one link: the changes to apply, in order.
    new_match() -> (room_name, bot_config_scope) names each match it makes."""
    out: list = []
    ts = {t.id: t for t in tickets}

    def update(t: TicketState, **fields) -> None:
        ts[t.id] = replace(t, **fields)
        out.append(UpdateTicket(t.id, fields))

    def members(room: str) -> list[TicketState]:
        return _fifo(t for t in ts.values() if t.room_name == room and t.state in ("found", "ready", "placed"))

    for m in matches:
        group = members(m.room_name)
        # 1. Ready-check deadline passed with someone not ready: remove them, requeue the rest.
        if (m.starts_at is None and m.ready_deadline is not None and m.ready_deadline <= now
                and any(t.state != "ready" for t in group)):
            ready = [t for t in group if t.state == "ready"]
            missed = [t for t in group if t.state != "ready"]
            for t in ready:
                update(t, state="searching", room_name=None, ready_at=None, requeued_at=now)
            for t in missed:
                update(t, state="removed")
            out += [UpdateMatch(m.room_name, {"closed_at": now}), StopRoom(m.room_name),
                    Event("ready_failed", {"room": m.room_name, "removed": [t.id for t in missed],
                                           "requeued": [t.id for t in ready]})]
            continue
        # 2. Everyone ready: the countdown starts.
        starts_at = m.starts_at
        if starts_at is None and group and all(t.state == "ready" for t in group):
            starts_at = now + _s(link.delay_s)
            out.append(UpdateMatch(m.room_name, {"starts_at": starts_at}))
        started = starts_at is not None and starts_at <= now
        # 3. Started and set up: members are in the room.
        if started and m.session_id is not None:
            for t in group:
                if t.state in ("found", "ready"):
                    update(t, state="placed", placed_at=now)
        # 4. No session in the room for DONE_AFTER_S (past a rejoin's gap): the match is over.
        if started and m.session_ended_at is not None and m.session_ended_at <= now - _s(DONE_AFTER_S):
            for t in members(m.room_name):
                if t.state == "placed":
                    update(t, state="done")
            out.append(UpdateMatch(m.room_name, {"closed_at": now}))

    # 5. Live searching tickets, oldest first, `size` at a time; then relaxation (R17) once.
    live = _fifo(t for t in ts.values() if t.state == "searching" and t.last_seen >= now - _s(TICKET_LIVE_S))
    full = len(live) // link.size * link.size
    groups = [live[i:i + link.size] for i in range(0, full, link.size)]
    rest = live[full:]
    if rest and len(rest) >= link.min_size and rest[0].queued_at <= now - _s(link.max_wait_s):
        groups.append(rest)
    for group in groups:
        room, scope = new_match()
        out.append(CreateMatch(room, scope, len(group),
                               now + _s(READY_CHECK_S) if link.size > 1 else None,
                               now + _s(link.delay_s) if link.size == 1 else None,
                               tuple(t.id for t in group)))
        for t in group:
            update(t, state="found", room_name=room)
        out.append(Event("match_formed", {"room": room, "size": len(group), "scope": scope,
                                          "tickets": [t.id for t in group]}))

    # 6. Still searching past max wait, live or not: expired.
    for t in _fifo(ts.values()):
        if t.state == "searching" and t.queued_at <= now - _s(link.max_wait_s):
            update(t, state="expired")
            out.append(Event("ticket_expired", {"ticket": t.id}))
    return out


def _ms(until: datetime, now: datetime) -> int:
    return max(0, round((until - now).total_seconds() * 1000))


def ticket_view(ticket, match, live_searching_count: int, link, now: datetime) -> dict:
    """What the waiting page shows for one ticket; match is its MatchState, or None."""
    if ticket.state == "searching":
        return {"kind": "searching", "found": live_searching_count, "size": link.size,
                "expires_in_ms": _ms(ticket.queued_at + _s(link.max_wait_s), now),
                "requeued": ticket.requeued_at is not None}
    if ticket.state in ("found", "ready", "placed") and match is not None:
        if match.starts_at is None:
            return {"kind": "ready_check", "deadline_in_ms": _ms(match.ready_deadline, now),
                    "pressed": ticket.state == "ready"}
        return {"kind": "starting", "room": match.room_name if match.session_id else None,
                "starts_in_ms": _ms(match.starts_at, now)}
    return {"kind": {"expired": "expired", "removed": "missed"}.get(ticket.state, "done")}


def pick_setting(pool: list[str], counts: dict[str, int], choice) -> str:
    """Balanced assignment (R42): the pool's least-used setting on this link; choice breaks ties."""
    least = min(counts.get(scope, 0) for scope in pool)
    return choice([scope for scope in pool if counts.get(scope, 0) == least])
