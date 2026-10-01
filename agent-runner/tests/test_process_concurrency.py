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
    worker_count,
)


class TestWorkerCount(unittest.TestCase):
    def test_it_defaults_to_more_than_one(self):
        """A single process is the ceiling the ramp found. The default should
        not reproduce it."""
        self.assertGreater(worker_count(env={}), 1)

    def test_it_follows_the_machine_when_not_told_otherwise(self):
        """A fixed 4 wastes a bigger instance and oversubscribes a smaller one.
        Sizing to cores keeps one process per core, which is what the GIL
        argument implies: parallelism comes from processes, and there is no
        point having more of them than cores to run them on."""
        self.assertEqual(worker_count(env={}, cpus=8), 8)
        self.assertEqual(worker_count(env={}, cpus=2), 2)

    def test_it_never_goes_below_two_however_small_the_box(self):
        """One process is the bottleneck this exists to remove, so even a
        single-core machine gets two."""
        self.assertGreaterEqual(worker_count(env={}, cpus=1), 2)

    def test_an_explicit_setting_still_wins(self):
        self.assertEqual(worker_count(env={"AGENT_RUNNER_WORKERS": "3"}, cpus=16), 3)

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


if __name__ == "__main__":
    unittest.main()
