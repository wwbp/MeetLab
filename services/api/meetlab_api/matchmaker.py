"""Group start links: the matchmaker (tickets → matches) and the /tickets endpoints.

Step 0 holds only the contract: the router (mounted by runner.py behind the runner's secret),
the constants and the change types step() returns. Request/reply shapes:
tests/fixtures/matchmaking/*.json.
"""
from dataclasses import dataclass, field
from datetime import datetime

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
