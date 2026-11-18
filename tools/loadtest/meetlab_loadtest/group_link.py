"""A simulated participant's way into a room through a group start link, as the waiting page
goes (services/web/app/start/[token]/StartClient.tsx): take a ticket, poll every 2 s, answer
the ready check with a Prolific ID, receive the room. HTTP only, through meet's public routes;
`post(path, body) -> (status, reply)`, the clock and sleep are passed in, so it is unit-tested
against a fake (tools/loadtest/tests/test_group_link.py). The driver is load_run.py (ENTRY=group_link).
"""
import hashlib

POLL_S = 2.0  # StartClient.tsx POLL_MS


class EntryFailed(Exception):
    """This participant got no room; the message says how it ended."""


def prolific_id(*key) -> str:
    """A valid Prolific ID (24 lowercase hex), the same every run, distinct per key."""
    return hashlib.md5(":".join(map(str, key)).encode()).hexdigest()[:24]


def link_settings(run_id: str, size: int) -> dict:
    """The run's own link (meet's POST /api/console/start-link): full groups only, on the bot
    settings the run stored under its scope, no countdown, so the timings are the system's."""
    if not 2 <= size <= 8:  # size 1 gets no ticket (meet answers with the room); meet allows up to 8
        raise ValueError("a group link's room size is 2 to 8")
    return {"name": f"load test {run_id}", "pool": [f"load-{run_id}"], "size": size, "min_size": size,
            "delay_s": 0, "max_wait_s": 1800, "ready_check_s": 60}


async def take_ticket(post, token: str, participant_id: str) -> dict:
    """Join on the link: the ticket's first reply."""
    status, reply = await post("/api/start-link", {"token": token, "participant_id": participant_id})
    if status != 200:
        raise EntryFailed(f"join refused ({status}): {reply.get('error')}")
    if "ticket" not in reply:
        raise EntryFailed(f"no ticket in the join reply (a size-1 link?): {reply}")
    return reply


async def to_room(reply: dict, post, next_id, sleep, clock, taken_at: float | None = None, timeout_s: float = 600) -> dict:
    """Drive one ticket from its first reply to its room. next_id(n) is the n-th other Prolific
    ID to try when the ready check refuses one as already in the line (409).

    Returns the room, the Prolific ID that was accepted, and what the report needs:
    match_s (ticket taken to the group that held being found), ready_to_room_s (Ready accepted
    to the room being handed over: the others' Ready, then room and bot setup), requeues (times
    sent back in line because someone else missed the ready check) and id_retries.
    Raises EntryFailed: expired, missed, done, ticket gone, or no room by the deadline."""
    taken_at = clock() if taken_at is None else taken_at
    ticket, pid = reply["ticket"], reply.get("participant_id") or next_id(0)
    matched = ready_at = None
    requeues = id_retries = 0
    while True:
        view = reply["view"]
        kind = view["kind"]
        if kind not in ("searching", "ready_check", "starting"):
            raise EntryFailed(kind)
        if kind == "searching" and matched is not None:  # back in line: someone missed the ready check
            requeues, matched, ready_at = requeues + 1, None, None
        if kind != "searching" and matched is None:
            matched = clock()
        if kind == "starting" and view.get("room"):
            got = clock()
            await sleep(view["starts_in_ms"] / 1000)  # the page counts down, then goes to the room
            return {"room": view["room"], "participant_id": reply.get("participant_id") or pid,
                    "match_s": matched - taken_at, "ready_to_room_s": got - (ready_at or matched),
                    "requeues": requeues, "id_retries": id_retries}
        if clock() - taken_at > timeout_s:
            raise EntryFailed(f"no room after {timeout_s:.0f} s (still {kind})")
        if kind == "ready_check" and not view.get("pressed"):
            status, body = await post("/api/start-link/ready", {"ticket": ticket, "participant_id": pid})
            if status == 200:
                reply, ready_at = body, clock()
                continue
            if status == 409:  # someone in the line holds this ID: another one, at the next poll
                id_retries += 1
                pid = next_id(id_retries)
        await sleep(POLL_S)
        status, body = await post("/api/start-link/status", {"ticket": ticket})
        if status == 200:
            reply = body
        elif status in (400, 404):
            raise EntryFailed(f"ticket gone ({status})")
        # anything else (a 5xx, a rate limit): the page shows "Connection lost" and polls again
