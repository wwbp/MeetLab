"""Load-test plan: the standard shapes, what each step measures, and the SLO verdict.

Pure, so it is the same for every stack we test (a profile only changes a room's bot
config) and every result is comparable. The driver is tests/load_run.py.

Shapes (the usual taxonomy): smoke (does it work at all), load (the expected size),
stress (past it, to 2x), spike (everyone at once, as when a study opens), soak (an hour,
for leaks) and breakpoint (climb until an SLO breaks: the capacity). Every shape ends at
0 rooms, so every run also checks that sessions close.
"""
import math
from dataclasses import dataclass

# What a participant must get, for every profile; a step that misses one fails.
MIN_REPLY_RATE = 0.95     # the bot answers 19 turns in 20
MAX_P95_MS = 2000         # end of speech to the bot's first audio; past 2 s feels broken (docs/performance-tests.md)


@dataclass(frozen=True)
class Step:
    rooms: int
    hold_s: int
    stop_on_failure: bool = False


def _climb(points, hold_s, stop_on_failure=False):
    return [Step(r, hold_s, stop_on_failure) for r in points]


def _ramp(target, n=5):
    return sorted({max(1, round(target * i / n)) for i in range(1, n + 1)})


SHAPES = {
    "smoke": lambda t, h: [Step(1, h or 180)],
    "load": lambda t, h: _climb(_ramp(t)[:-1], h or 120) + [Step(t, h or 900)],
    "stress": lambda t, h: _climb(sorted({max(1, round(t * f)) for f in (0.25, 0.5, 0.75, 1, 1.25, 1.5, 2)}), h or 300),
    "spike": lambda t, h: [Step(t, h or 300)],
    "soak": lambda t, h: _climb(_ramp(t)[:-1], h or 120) + [Step(t, h or 3600)],
    "breakpoint": lambda t, h: _climb(range(max(1, t // 5), 3 * t + 1, max(1, t // 5)), h or 300, True),
}


def schedule(shape: str, target: int, hold_s: int = 0) -> list[Step]:
    """The steps of a run; hold_s shortens every step for a rehearsal."""
    if shape not in SHAPES:
        raise ValueError(f"shape must be one of {', '.join(SHAPES)}")
    return SHAPES[shape](target, hold_s) + [Step(0, 120)]


def _pct(values, q):
    """Nearest-rank percentile: a value that was actually observed."""
    v = sorted(values)
    return v[max(0, math.ceil(q * len(v)) - 1)] if v else None


def summarise_step(step: Step, *, turns, joins_s, start_errors: int, disconnects: int) -> dict:
    """turns: ms from the end of a participant's turn to the bot's first audio, None if it never answered."""
    heard = [t for t in turns if t is not None]
    return {
        "rooms": step.rooms, "turns": len(turns), "replied": len(heard),
        "reply_rate": len(heard) / len(turns) if turns else None,
        "p50_ms": _pct(heard, 0.50), "p95_ms": _pct(heard, 0.95), "p99_ms": _pct(heard, 0.99),
        "join_p95_s": _pct(joins_s, 0.95), "start_errors": start_errors, "disconnects": disconnects,
    }


def verdict(m: dict) -> tuple[bool, list[str]]:
    why = []
    if m["rooms"]:  # the closing step (0 rooms) is judged on errors alone
        if not m["turns"]:
            why.append("turns: none completed")
        if m["reply_rate"] is not None and m["reply_rate"] < MIN_REPLY_RATE:
            why.append(f"reply_rate {m['reply_rate']:.0%} < {MIN_REPLY_RATE:.0%}")
        if m["p95_ms"] is not None and m["p95_ms"] > MAX_P95_MS:
            why.append(f"p95_ms {m['p95_ms']:.0f} > {MAX_P95_MS}")
    for field in ("start_errors", "disconnects"):
        if m[field]:
            why.append(f"{field} {m[field]}")
    return not why, why


ARRIVAL_S = 30  # a step's new rooms arrive spread over its first 30 s (a spike is 0 to N in 30 s)


def step_bounds(steps: list[Step], t0: float) -> list[tuple[float, float]]:
    out, t = [], t0
    for s in steps:
        out.append((t, t + s.hold_s))
        t += s.hold_s
    return out


def room_window(i: int, steps: list[Step], t0: float) -> tuple[float, float] | None:
    """When room i (0-based) starts and when it leaves; None if no step needs it.
    Every worker computes this alone from the same clock, so they need no coordination."""
    bounds = step_bounds(steps, t0)
    first = next((k for k, s in enumerate(steps) if s.rooms > i), None)
    if first is None:
        return None
    last = next(k for k in range(first, len(steps)) if steps[k].rooms <= i)  # schedules end at 0
    before = steps[first - 1].rooms if first else 0
    new = steps[first].rooms - before
    return bounds[first][0] + ARRIVAL_S * (i - before) / new, bounds[last][0]


def step_of(t: float, bounds: list[tuple[float, float]]) -> int | None:
    return next((k for k, (a, b) in enumerate(bounds) if a <= t < b), None)
