"""When a bot leaves its room, decided from LiveKit's live roster (diagnosis F1).

Pure: bot.py looks at the roster every few seconds and asks. Two graces, because an
empty room means two things: nobody has come yet (wait long; participants are late),
or everyone left (wait briefly; a page refresh comes straight back).
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable, Optional

BOT_PREFIX = "bot_"  # bots are named bot_* (runner.py; meet's isBotParticipant)


@dataclass(frozen=True)
class Presence:
    empty_since: Optional[float]  # when the room last became empty; None while a human is in it
    seen_human: bool


def humans(identities: Iterable[str]) -> int:
    return sum(1 for i in identities if not i.startswith(BOT_PREFIX))


def observe(state: Presence, humans: int, now: float) -> Presence:
    if humans:
        return Presence(empty_since=None, seen_human=True)
    return Presence(empty_since=now if state.empty_since is None else state.empty_since,
                    seen_human=state.seen_human)


def should_leave(humans: int, empty_since: Optional[float], now: float, grace: float) -> bool:
    return humans == 0 and empty_since is not None and now - empty_since >= grace


def step(state: Presence, humans: int, now: float, arrival: float, rejoin: float) -> tuple[Presence, bool]:
    """One look at the roster: the new state, and whether to leave now."""
    state = observe(state, humans, now)
    return state, should_leave(humans, state.empty_since, now, rejoin if state.seen_human else arrival)


async def wait_until_empty(roster: Callable[[], Iterable[str]], arrival: float, rejoin: float,
                           interval: float = 5.0, clock: Callable[[], float] = time.monotonic,
                           sleep: Callable[[float], Awaitable] = asyncio.sleep) -> None:
    """Return once the room has been empty past its grace; bot.py then ends the session."""
    state = Presence(empty_since=None, seen_human=False)
    while True:
        state, leave = step(state, humans(roster()), clock(), arrival, rejoin)
        if leave:
            return
        await sleep(interval)
