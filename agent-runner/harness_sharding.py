"""Spreading simulated rooms across processes, because the load generator has a GIL too.

The 2026-08-20 ramp looked like a server collapse above five rooms. It was not:
at twenty rooms the harness attempted 26 turns in six minutes — roughly 1.3 per
room — while production sat at 14% CPU. One Python process cannot drive twenty
real-time audio publishers and twenty audio-stream consumers simultaneously, for
precisely the reason the runner could not host twenty bots in one process.

Measuring a concurrency limit with a tool that has a lower one produces a number
about the tool. So the generator gets the same treatment as the thing it
measures: rooms are sharded across worker processes, each with its own
interpreter and event loop.

These functions are pure. The uneven splits and the result merging are where the
mistakes live, and finding them in a unit test is cheaper than finding them
thirty minutes into a ramp.
"""


def shard_worker_count(*, rooms: int, cpus: int) -> int:
    """How many processes to spread `rooms` across.

    Bounded by cores, because more processes than cores adds context switching to
    a workload whose whole job is precise timing. Bounded by rooms, because an
    empty shard is a process that starts, does nothing, and still costs a second
    of interpreter startup on every run.
    """
    return max(1, min(rooms, cpus))


def shard_rooms(total: int, workers: int) -> list[list[int]]:
    """Split room indices across workers, remainder spread rather than piled.

    Indices are preserved, never renumbered: `conversation_for(idx)` selects which
    script a room runs, so renumbering would silently change what each room says
    and break comparison against earlier runs.

    The remainder is spread one-per-worker rather than appended to the last shard.
    Concentrating it makes that one process the slowest, and the run is only as
    fast as its slowest shard — which is the bottleneck this whole change exists
    to remove.
    """
    workers = max(1, min(workers, total)) if total else 0
    if not workers:
        return []

    base, extra = divmod(total, workers)
    shards: list[list[int]] = []
    start = 0
    for w in range(workers):
        size = base + (1 if w < extra else 0)
        shards.append(list(range(start, start + size)))
        start += size
    return shards


def combine_shards(shards: list[dict]) -> dict:
    """Merge per-worker results into one set of totals.

    `expected` is summed from every shard including those that failed, so a
    crashed worker cannot shrink the denominator and flatter the run. Unanswered
    turns are carried through as ``(turn_end, None)`` rather than filtered, since
    dropping them here would restore the exact bug the reply-rate metric exists
    to catch.
    """
    expected = sum(s.get("expected", 0) for s in shards)
    pairs: list[tuple] = []
    for s in shards:
        pairs.extend(s.get("pairs", []))
    return {"expected": expected, "pairs": pairs}
