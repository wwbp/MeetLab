"""How many processes serve bots, and which one runs the singleton jobs.

The 2026-08-20 ramp found the real ceiling, and it is not CPU. Ten concurrent
sessions streamed 480 utterances and produced **zero** replies while the box sat
at 80% — audio ingestion and voice detection kept flowing, and the expensive
chain (transcript commit, language model, speech synthesis) starved. Five
sessions were fine at a 500ms median; there is no gradual degradation between
them, just a cliff.

The cause is that every bot runs as an asyncio task inside one FastAPI process:
one interpreter, one GIL, one event loop. Adding cores cannot help a single
Python process, which is why the instance-size change alone would have bought
nothing.

So serve with several worker processes. Each is a real OS process with its own
GIL and event loop, and the load balancer spreads /start across them.

That introduces one problem worth solving properly: jobs that must run *once*
now have several candidates. The conversation reconciler is idempotent, so
duplicates waste work rather than corrupt it, but N processes per instance times
M instances means N*M redundant LiveKit round-trips every couple of minutes. A
Postgres advisory lock elects a single runner across every process and every
instance at once, which is the same mechanism that will be needed when the web
tier's in-memory locks finally move to the database.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from process_concurrency import (
    RECONCILER_LOCK_KEY,
    should_run_singleton,
    worker_count,
)


class TestWorkerCount(unittest.TestCase):
    def test_it_defaults_to_more_than_one(self):
        """A single process is the ceiling the ramp found. The default should
        not reproduce it."""
        self.assertGreater(worker_count(env={}), 1)

    def test_it_is_configurable(self):
        self.assertEqual(worker_count(env={"AGENT_RUNNER_WORKERS": "6"}), 6)

    def test_it_never_drops_below_one(self):
        """A zero or negative value would serve nothing at all."""
        self.assertEqual(worker_count(env={"AGENT_RUNNER_WORKERS": "0"}), 1)
        self.assertEqual(worker_count(env={"AGENT_RUNNER_WORKERS": "-3"}), 1)

    def test_nonsense_falls_back_rather_than_crashing_the_service(self):
        """A typo in an environment property must not stop the runner booting."""
        self.assertGreater(worker_count(env={"AGENT_RUNNER_WORKERS": "four"}), 0)

    def test_it_can_be_pinned_to_one_for_local_work(self):
        """Debugging is far easier in a single process."""
        self.assertEqual(worker_count(env={"AGENT_RUNNER_WORKERS": "1"}), 1)


class TestSingletonElection(unittest.TestCase):
    """Exactly one process across the whole fleet runs the periodic jobs."""

    def test_the_process_holding_the_lock_runs_it(self):
        self.assertTrue(should_run_singleton(acquired=True))

    def test_every_other_process_stands_down(self):
        self.assertFalse(should_run_singleton(acquired=False))

    def test_an_unavailable_database_does_not_elect_anyone(self):
        """Failing closed is right here: the reconciler is a safety net, and a
        fleet that all decide they are the leader is worse than a delayed
        cleanup."""
        self.assertFalse(should_run_singleton(acquired=None))

    def test_the_lock_key_is_a_stable_constant(self):
        """Two different keys elect two leaders, which defeats the point."""
        self.assertIsInstance(RECONCILER_LOCK_KEY, int)
        self.assertEqual(RECONCILER_LOCK_KEY, abs(RECONCILER_LOCK_KEY))


if __name__ == "__main__":
    unittest.main()
