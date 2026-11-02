"""Group start links: the matchmaker (tickets → matches) and the /tickets endpoints.

step() and ticket_view() are pure (no DB, no clock, no randomness); the endpoints below wrap
them in one locked transaction per link. The router is mounted by runner.py behind the
runner's secret. Request/reply shapes: tests/fixtures/matchmaking/*.json.
"""
import random
import secrets
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import exists, func, or_, select

from meetlab_api.links import ensure_legacy_link, stop_if_missing_settings, verify_password
from meetlab_core.db.engine import AsyncSessionLocal
from meetlab_core.db.models import Conversation, Match, StartLink, Ticket
from meetlab_core.event_log import record_event

TICKET_LIVE_S = 90   # a searching ticket polled within this is live (matchable)
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
    name: str  # match_formed | ready_failed | ticket_expired | link_stopped (record_event)
    payload: dict = field(default_factory=dict)
    severity: str = "info"  # warning: a sign a study is stuck (the Events page)


# step()'s inputs. The link is any object with size, min_size, delay_s, max_wait_s, ready_check_s
# (a StartLink).

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
                                           "requeued": [t.id for t in ready]}, "warning")]
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
                               now + _s(link.ready_check_s) if link.size > 1 else None,
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
            out.append(Event("ticket_expired", {"ticket": t.id}, "warning"))
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


# ── I/O: one transaction under the link's row lock around step() ──────────────────

NON_TERMINAL = ("searching", "found", "ready", "placed")


def _error(status: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status)


def _base36(n: int) -> str:
    digits = ""
    while n:
        n, d = divmod(n, 36)
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"[d] + digits
    return digits or "0"


async def _step(db, link: StartLink, now: datetime) -> list:
    """step() on the link's non-terminal tickets and open matches, its changes applied (the
    caller holds the link's lock). Returns what runs after commit: StopRoom, Event."""
    tickets = {t.id: t for t in await db.scalars(
        select(Ticket).where(Ticket.link_id == link.link_id, Ticket.state.in_(NON_TERMINAL)))}
    running = exists().where(Conversation.room_name == Match.room_name, Conversation.status == "running")
    ended = select(func.max(Conversation.ended_at)).where(Conversation.room_name == Match.room_name).scalar_subquery()
    matches, states = {}, []
    for m, is_running, ended_at in await db.execute(
            select(Match, running, ended).where(Match.link_id == link.link_id, Match.closed_at.is_(None))):
        matches[m.room_name] = m
        states.append(MatchState(m.room_name, m.ready_deadline, m.starts_at, m.session_id,
                                 None if m.session_id is None or is_running else ended_at))
    # Balanced assignment counts the link's matches, except those a failed ready check closed (R42).
    counts = dict((await db.execute(
        select(Match.bot_config_scope, func.count())
        .where(Match.link_id == link.link_id, or_(Match.closed_at.is_(None), Match.starts_at.is_not(None)))
        .group_by(Match.bot_config_scope))).all())

    def new_match():
        scope = pick_setting(link.pool, counts, random.choice)
        counts[scope] = counts.get(scope, 0) + 1
        return f"link-{_base36(int(now.timestamp() * 1000))}-{secrets.token_hex(4)}", scope

    after = []
    for c in step(link, [TicketState(t.id, t.state, t.queued_at, t.last_seen, t.room_name, t.ready_at,
                                     t.requeued_at, t.placed_at) for t in tickets.values()],
                  states, now, new_match):
        if isinstance(c, UpdateTicket):
            for k, v in c.fields.items():
                setattr(tickets[c.id], k, v)
        elif isinstance(c, UpdateMatch):
            for k, v in c.fields.items():
                setattr(matches[c.room_name], k, v)
        elif isinstance(c, CreateMatch):
            db.add(Match(room_name=c.room_name, link_id=link.link_id, bot_config_scope=c.bot_config_scope,
                         size=c.size, created_at=now, ready_deadline=c.ready_deadline, starts_at=c.starts_at))
            await db.flush()  # before its members point at it
        else:
            after.append(c)
    await db.flush()
    return after


async def _locked(sessions, link_id: str, act):
    """One transaction under the link's row lock: step, act(db, link, now) -> its Ticket or an
    error reply, step again. Returns (reply, after-commit changes, rooms waiting for setup)."""
    async with sessions() as db, db.begin():
        link = await db.scalar(select(StartLink).where(StartLink.link_id == link_id).with_for_update())
        if link is None:
            return _error(404, "not_found"), [], []
        now = await db.scalar(select(func.clock_timestamp()))  # after the lock: one clock for every process
        missing = await stop_if_missing_settings(db, link, now)
        after = [Event("link_stopped", {"link_id": link_id, "missing": missing}, "warning")] if missing else []
        after += await _step(db, link, now)
        t = await act(db, link, now)
        if isinstance(t, JSONResponse):
            return t, after, []
        after += await _step(db, link, now)
        match = await db.get(Match, t.room_name) if t.room_name else None
        live = await db.scalar(select(func.count()).select_from(Ticket).where(
            Ticket.link_id == link_id, Ticket.state == "searching",
            Ticket.last_seen >= now - _s(TICKET_LIVE_S)))
        pending = (await db.scalars(select(Match.room_name).where(
            Match.link_id == link_id, Match.closed_at.is_(None), Match.session_id.is_(None)))).all()
        return ({"ticket": t.id, "link_id": link_id, "participant_id": t.participant_id,
                 "view": ticket_view(t, match, live, link, now)}, after, pending)


async def _after(hooks, changes: list) -> None:
    for c in changes:
        if isinstance(c, StopRoom):
            try:
                await hooks.stop_room(c.room_name)
            except Exception as e:
                logger.error(f"stopping room {c.room_name} after a failed ready check failed: {e}")
        else:
            await record_event(c.name, severity=c.severity, room_name=c.payload.get("room"), payload=c.payload)


async def _run(hooks, sessions, link_id: str, act):
    """_locked, then what runs after commit. Rooms waiting for setup are set up here (the claim
    makes that safe from every request); then one more locked step places their members."""
    reply, after, pending = await _locked(sessions, link_id, act)
    await _after(hooks, after)
    if pending and isinstance(reply, dict):
        for room in pending:
            await hooks.setup_match(room)
        ticket_id = reply["ticket"]

        async def same(db, link, now):
            return await db.get(Ticket, ticket_id)
        reply, after, _ = await _locked(sessions, link_id, same)
        await _after(hooks, after)
    return reply


async def create_ticket(hooks, body, sessions=AsyncSessionLocal):
    """POST /tickets: the participant's live ticket on the link, else a new one.
    Order: unknown link 404, wrong password 403, existing ticket, stopped link 410, new."""
    if not (isinstance(body, dict) and isinstance(body.get("link_id"), str)
            and isinstance(body.get("participant_id"), (str, type(None)))):
        return _error(400, "bad_request")
    pid = body.get("participant_id") or None
    legacy = body.get("legacy")
    if legacy is not None:
        pool = legacy.get("pool") if isinstance(legacy, dict) else None
        if not (isinstance(pool, list) and pool and all(isinstance(p, str) for p in pool)):
            return _error(400, "bad_request")
        async with sessions() as db, db.begin():
            await ensure_legacy_link(db, body["link_id"], pool)

    async def act(db, link, now):
        if link.password_hash and not verify_password(str(body.get("password") or ""), link.password_hash):
            return _error(403, "wrong_password")
        t = pid and await db.scalar(select(Ticket).where(
            Ticket.link_id == link.link_id, Ticket.participant_id == pid, Ticket.state.in_(NON_TERMINAL)))
        if t:
            t.last_seen = now
            return t
        if not link.active:
            return _error(410, "closed")
        t = Ticket(id=str(uuid.uuid4()), link_id=link.link_id, participant_id=pid, state="searching",
                   queued_at=now, last_seen=now)
        db.add(t)
        await db.flush()
        return t
    return await _run(hooks, sessions, body["link_id"], act)


async def _ticket_link(sessions, ticket_id: str) -> str | None:
    try:
        uuid.UUID(ticket_id)
    except ValueError:
        return None
    async with sessions() as db:
        return await db.scalar(select(Ticket.link_id).where(Ticket.id == ticket_id))


async def poll_ticket(hooks, ticket_id: str, sessions=AsyncSessionLocal):
    """POST /tickets/{id}/poll: the waiting page is still open (the ticket is live); its view."""
    link_id = await _ticket_link(sessions, ticket_id)
    if link_id is None:
        return _error(404, "not_found")

    async def act(db, link, now):
        t = await db.get(Ticket, ticket_id)
        t.last_seen = now
        return t
    return await _run(hooks, sessions, link_id, act)


async def ready_ticket(hooks, ticket_id: str, sessions=AsyncSessionLocal):
    """POST /tickets/{id}/ready: Ready pressed; counts only in `found` during the ready check."""
    link_id = await _ticket_link(sessions, ticket_id)
    if link_id is None:
        return _error(404, "not_found")

    async def act(db, link, now):
        t = await db.get(Ticket, ticket_id)
        t.last_seen = now
        m = await db.get(Match, t.room_name) if t.room_name else None
        if t.state == "found" and m and m.starts_at is None and m.ready_deadline and m.ready_deadline > now:
            t.state, t.ready_at = "ready", now
        return t
    return await _run(hooks, sessions, link_id, act)


@router.post("/tickets")
async def post_ticket(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "bad_request")
    return await create_ticket(request.app.state, body)


@router.post("/tickets/{ticket_id}/poll")
async def post_poll(ticket_id: str, request: Request):
    return await poll_ticket(request.app.state, ticket_id)


@router.post("/tickets/{ticket_id}/ready")
async def post_ready(ticket_id: str, request: Request):
    return await ready_ticket(request.app.state, ticket_id)
