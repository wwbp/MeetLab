"""Spreading simulated rooms across processes, because the harness has a GIL too.

The 2026-08-20 ramp reported a collapse above five rooms. It was not the server:
at twenty rooms the harness attempted only 26 turns in six minutes — about 1.3
per room — while production sat at 14% CPU. One Python process cannot drive
twenty real-time audio publishers and twenty audio-stream consumers at once, for
exactly the reason the runner could not host twenty bots in one process.

So the load generator gets the same fix as the thing it measures: shard the rooms
across worker processes, each with its own interpreter and event loop.

The sharding and the aggregation are pure, so the interesting cases — uneven
splits, more workers than rooms, combining partial results — are tested here
rather than discovered during a thirty-minute ramp.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from harness_sharding import combine_shards, shard_rooms, shard_worker_count


class TestSharding(unittest.TestCase):
    def test_rooms_are_split_evenly(self):
        self.assertEqual(shard_rooms(8, 2), [[0, 1, 2, 3], [4, 5, 6, 7]])

    def test_an_uneven_split_spreads_the_remainder(self):
        """Piling the remainder on one worker makes that shard the bottleneck,
        which is the problem being solved."""
        shards = shard_rooms(7, 3)
        sizes = sorted(len(s) for s in shards)
        self.assertEqual(sizes, [2, 2, 3])

    def test_every_room_is_run_exactly_once(self):
        for total, workers in [(1, 1), (5, 2), (20, 6), (40, 8), (13, 5)]:
            flat = [r for s in shard_rooms(total, workers) for r in s]
            self.assertEqual(sorted(flat), list(range(total)), f"{total}/{workers}")

    def test_more_workers_than_rooms_produces_no_empty_shards(self):
        """An empty shard is a process that starts, does nothing, and still costs
        a second of startup on every run."""
        shards = shard_rooms(3, 8)
        self.assertEqual(len(shards), 3)
        self.assertTrue(all(s for s in shards))

    def test_room_indices_are_preserved_not_renumbered(self):
        """conversation_for(idx) picks the script, so renumbering would change
        which conversation a room holds and break comparability between runs."""
        shards = shard_rooms(6, 2)
        self.assertIn(5, shards[1])


class TestWorkerCount(unittest.TestCase):
    def test_it_never_exceeds_the_room_count(self):
        self.assertEqual(shard_worker_count(rooms=3, cpus=16), 3)

    def test_it_is_bounded_by_cores(self):
        """More processes than cores just adds context switching to a workload
        that is already timing-sensitive."""
        self.assertEqual(shard_worker_count(rooms=100, cpus=8), 8)

    def test_a_single_room_stays_in_one_process(self):
        """Debugging one room should not involve process boundaries."""
        self.assertEqual(shard_worker_count(rooms=1, cpus=8), 1)


class TestCombining(unittest.TestCase):
    def test_totals_add_up(self):
        combined = combine_shards([
            {"expected": 10, "pairs": [(1.0, 1.5)]},
            {"expected": 6, "pairs": [(2.0, 2.4), (3.0, None)]},
        ])
        self.assertEqual(combined["expected"], 16)
        self.assertEqual(len(combined["pairs"]), 3)

    def test_unanswered_turns_survive_combining(self):
        """Dropping the misses while merging would restore the exact bug the
        reply-rate metric exists to catch."""
        combined = combine_shards([{"expected": 2, "pairs": [(1.0, None)]}])
        self.assertEqual(combined["pairs"], [(1.0, None)])

    def test_a_shard_that_died_still_counts_against_the_total(self):
        """A crashed worker must not quietly shrink the denominator and make the
        run look successful."""
        combined = combine_shards([
            {"expected": 10, "pairs": [(1.0, 1.2)]},
            {"expected": 10, "pairs": [], "error": "worker died"},
        ])
        self.assertEqual(combined["expected"], 20)
        self.assertEqual(len(combined["pairs"]), 1)

    def test_nothing_in_nothing_out(self):
        self.assertEqual(combine_shards([]), {"expected": 0, "pairs": []})


if __name__ == "__main__":
    unittest.main()
