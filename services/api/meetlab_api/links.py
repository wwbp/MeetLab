"""Group start links: stored links (/links endpoints), their passwords, legacy JWT links.

runner.py mounts the router behind the runner's secret. Request/reply shapes:
tests/fixtures/matchmaking/links_*.json.
"""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from meetlab_core.db.engine import AsyncSessionLocal
from meetlab_core.db.models import TICKET_STATES, BotConfig, StartLink, Ticket
from meetlab_core.event_log import record_event

router = APIRouter()

MAX_SIZE = 8  # Q6

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


def hash_password(password: str) -> str:
    """scrypt$<salt b64>$<hash b64>: the only form a link's password is stored in."""
    salt = os.urandom(16)
    return "$".join(("scrypt", base64.b64encode(salt).decode(), base64.b64encode(_scrypt(password, salt)).decode()))


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        return hmac.compare_digest(_scrypt(password, base64.b64decode(salt, validate=True)),
                                   base64.b64decode(digest, validate=True))
    except ValueError:
        return False


async def ensure_legacy_link(db, link_id: str, pool: list[str]) -> None:
    """An old JWT link gets its record on first use (R30): size 1, delay 0, no password.
    A row already there is kept as it is (it may have been stopped)."""
    await db.execute(pg_insert(StartLink)
                     .values(link_id=link_id, name="Older link", pool=pool, legacy=True)
                     .on_conflict_do_nothing(index_elements=["link_id"]))


def _whole(body: dict, key: str, lo: int, hi: int, default: int) -> int | None:
    v = body.get(key, default)
    return v if type(v) is int and lo <= v <= hi else None  # type(): a JSON true is not 1


def link_fields(body: dict) -> dict | str:
    """A new link's settings with the defaults filled in (R1, R2), or what is wrong."""
    name, pool = body.get("name"), body.get("pool")
    if not isinstance(name, str) or not name.strip():
        return "name is required"
    if not isinstance(pool, list) or not pool or not all(isinstance(s, str) and s.strip() for s in pool):
        return "pool must be a non-empty list of bot setting scopes"
    size = _whole(body, "size", 1, MAX_SIZE, 1)
    if size is None:
        return f"size must be a whole number from 1 to {MAX_SIZE}"
    min_size = _whole(body, "min_size", 1, size, size)
    if min_size is None:
        return "min_size must be a whole number from 1 to size"
    delay_s = _whole(body, "delay_s", 0, 300, 0)
    if delay_s is None:
        return "delay_s must be a whole number from 0 to 300"
    max_wait_s = _whole(body, "max_wait_s", 60, 1800, 300)
    if max_wait_s is None:
        return "max_wait_s must be a whole number from 60 to 1800"
    ready_check_s = _whole(body, "ready_check_s", 10, 300, 60)  # Q15: 60 s, for background tabs
    if ready_check_s is None:
        return "ready_check_s must be a whole number from 10 to 300"
    return {"name": name.strip(), "pool": [s.strip() for s in pool], "size": size, "min_size": min_size,
            "delay_s": delay_s, "max_wait_s": max_wait_s, "ready_check_s": ready_check_s}


async def missing_settings(db, pool: list[str]) -> list[str]:
    """The pool's bot settings with no bot_config row. 'global' needs none (the defaults)."""
    named = [s for s in pool if s != "global"]
    found = set(await db.scalars(select(BotConfig.scope).where(BotConfig.scope.in_(named))))
    return [s for s in named if s not in found]


async def stop_if_missing_settings(db, link: StartLink, now) -> list[str]:
    """Stop an active link whose pool names a deleted bot setting: a group would otherwise
    run with the global settings. Returns what is missing (the event's payload)."""
    missing = link.active and await missing_settings(db, link.pool)
    if missing:
        link.active, link.stopped_at = False, now
    return missing or []


async def stop_links_missing_settings(sessions=AsyncSessionLocal) -> None:
    """The background loop's pass (reconcile_tick): settings can be deleted anywhere (SQLAdmin)."""
    stopped = []
    async with sessions() as db, db.begin():
        now = datetime.now(timezone.utc)
        for link in await db.scalars(select(StartLink).where(StartLink.active).with_for_update(skip_locked=True)):
            if missing := await stop_if_missing_settings(db, link, now):
                stopped.append({"link_id": link.link_id, "missing": missing})
    for payload in stopped:
        await record_event("link_stopped", severity="warning", payload=payload)


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _json_object(request: Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


@router.post("/links")
async def create_link(request: Request):
    body = await _json_object(request)
    if body is None:
        return _error("request body must be a JSON object", 400)
    fields = link_fields(body)
    if isinstance(fields, str):
        return _error(fields, 400)
    password = body.get("password")
    if password is not None and not isinstance(password, str):
        return _error("password must be a string", 400)
    link_id = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")  # 22 chars (R4)
    async with AsyncSessionLocal() as db, db.begin():
        if missing := await missing_settings(db, fields["pool"]):
            return _error(f"unknown bot settings: {', '.join(missing)}", 400)
        db.add(StartLink(link_id=link_id, password_hash=hash_password(password) if password else None, **fields))
    return {"link_id": link_id}


@router.get("/links")
async def list_links():
    """Every link, newest first, with its tickets counted by state (R24). Never the hash."""
    async with AsyncSessionLocal() as db:
        rows = (await db.scalars(select(StartLink).order_by(StartLink.created_at.desc()))).all()
        counted = (await db.execute(
            select(Ticket.link_id, Ticket.state, func.count()).group_by(Ticket.link_id, Ticket.state))).all()
    counts = {}
    for link_id, state, n in counted:
        counts.setdefault(link_id, {})[state] = n
    return {"links": [{
        "link_id": r.link_id, "name": r.name, "pool": r.pool, "size": r.size, "min_size": r.min_size,
        "delay_s": r.delay_s, "max_wait_s": r.max_wait_s, "ready_check_s": r.ready_check_s,
        "has_password": r.password_hash is not None,
        "active": r.active, "legacy": r.legacy, "created_at": r.created_at.isoformat(),
        "stopped_at": r.stopped_at and r.stopped_at.isoformat(),
        "counts": {s: counts.get(r.link_id, {}).get(s, 0) for s in TICKET_STATES},
    } for r in rows]}


@router.patch("/links/{link_id}")
async def set_link_active(link_id: str, request: Request):
    """Stop or start a link (R25). Stopping again keeps the first stop time; starting clears it."""
    body = await _json_object(request)
    active = (body or {}).get("active")
    if not isinstance(active, bool):
        return _error("active must be true or false", 400)
    stopped_at = None if active else func.coalesce(StartLink.stopped_at, datetime.now(timezone.utc))
    async with AsyncSessionLocal() as db, db.begin():
        found = (await db.execute(update(StartLink).where(StartLink.link_id == link_id)
                                  .values(active=active, stopped_at=stopped_at)
                                  .returning(StartLink.link_id))).scalar_one_or_none()
    if found is None:
        return _error("not_found", 404)
    return {"link_id": link_id, "active": active}


@router.get("/links/{link_id}/public")
async def public_link(link_id: str):
    """What the start page needs. 404 also for a legacy link not used yet: meet treats that
    as size 1, active, no password."""
    async with AsyncSessionLocal() as db:
        row = await db.get(StartLink, link_id)
    if row is None:
        return _error("not_found", 404)
    return {"active": row.active, "needs_password": row.password_hash is not None, "size": row.size}
