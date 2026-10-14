"""Serving bots from several processes.

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
loop, and requests spread across them. Periodic jobs (the conversation reconcile
loop) run in every process; their writes are conditional, so that is safe. An
advisory-lock election used to pick one, but it ran once at startup and left no
reconciler after a rolling deploy (removed 2026-10-01).
"""
import os


def worker_count(env: dict | None = None, cpus: int | None = None) -> int:
    """How many worker processes should serve the app.

    Defaults to one per core. Parallelism here comes from processes, not
    threads — that is the whole point of the GIL argument — so there is little
    value in more processes than cores to run them on, and a fixed number either
    wastes a large instance or oversubscribes a small one.

    Never fewer than two, whatever the machine reports. A single process is the
    configuration the 2026-08-20 ramp found the ceiling in (ten sessions, 480
    utterances, zero replies), so it is not a value to fall back into by
    accident — including when a typo makes an explicit setting unreadable.

    Pin AGENT_RUNNER_WORKERS=1 locally when debugging; a single process is far
    easier to follow, and concurrency is not what a laptop is testing.
    """
    env = os.environ if env is None else env
    cores = cpus if cpus is not None else (os.cpu_count() or 2)

    raw = env.get("AGENT_RUNNER_WORKERS", "")
    if str(raw).strip():
        try:
            return max(1, int(str(raw).strip()))
        except (TypeError, ValueError):
            logger_msg = f"AGENT_RUNNER_WORKERS={raw!r} is not a number; sizing to cores"
            print(logger_msg)
    return max(2, cores)
