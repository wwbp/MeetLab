"""Serving bots from several processes, and electing one to run periodic jobs.

The 2026-08-20 ramp located the real ceiling. Ten concurrent sessions streamed
480 utterances and produced **zero** replies while the instance sat at 80% CPU:
audio ingestion and voice detection kept flowing, while transcript commit,
language model and speech synthesis starved. Five sessions were comfortable at a
500ms median. There is no gradual band between the two — it is a cliff.

It is not CPU. Every bot runs as an asyncio task inside one FastAPI process, so
the whole fleet shares one interpreter, one GIL and one event loop. A bigger
instance cannot help a single Python process, which is why instance sizing alone
would have bought nothing.

Several worker processes fix it: each is a real OS process with its own GIL and
loop, and requests spread across them. The cost is that jobs which must run once
now have several candidates, which is what the advisory lock below settles —
across every process *and* every instance, not just within one box.
"""
import os

# Postgres advisory lock identifying the periodic-reconciler role. Arbitrary but
# fixed: two values would elect two leaders and defeat the purpose. Advisory
# locks are released automatically when the holding session ends, so a process
# that dies simply hands the role to whoever asks next.
RECONCILER_LOCK_KEY = 8_142_026


def worker_count(env: dict | None = None) -> int:
    """How many worker processes should serve the app.

    Defaults to 4 rather than 1. One process is the configuration the ramp found
    the ceiling in, so it is not a safe default to fall back to — a typo in an
    environment property should not silently restore the bottleneck.

    Pin to 1 locally: a single process is far easier to debug, and concurrency
    is not what a laptop is testing.
    """
    env = os.environ if env is None else env
    raw = env.get("AGENT_RUNNER_WORKERS", "")
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return 4
    return max(1, n)


def should_run_singleton(*, acquired: bool | None) -> bool:
    """Whether this process should run the periodic jobs.

    Args:
        acquired: True if this process holds the advisory lock, False if another
            holds it, None if the question could not be answered — a database
            that is unreachable, for instance.

    Returns:
        True only when the lock is definitely held. An unanswerable question
        elects nobody: the reconciler is a safety net that runs every couple of
        minutes, so a delayed sweep costs little, while a fleet that all assume
        leadership costs a redundant LiveKit round-trip per process per cycle.
    """
    return acquired is True
