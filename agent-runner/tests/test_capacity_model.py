"""The capacity model: for N rooms at once, the cheapest machines that keep every part under
its measured latency-safe ceiling (load, latency, efficiency and cost; the user's best-known
path, 2026-10-06). Pure: no AWS.
    uv run python -m unittest tests.test_capacity_model -v
"""
import unittest

from capacity_model import GPU_PRICE, PRICE, matrix, plan


class PlanTest(unittest.TestCase):
    def test_a_few_rooms_need_the_lean_base_only(self):
        p = plan(3)
        self.assertEqual((p.bot_machines, p.stt, p.db), (1, "c6i.large", "db.t4g.small"))

    def test_twenty_rooms_need_more_bots_and_nothing_bigger(self):
        p = plan(20)  # five bots per c6i.large (sweep run C)
        self.assertEqual((p.bot_machines, p.stt, p.db), (4, "c6i.large", "db.t4g.small"))

    def test_speech_moves_to_a_bigger_cpu_machine_past_its_measured_ceiling(self):
        self.assertEqual(plan(26).stt, "c6i.large")   # 3% CPU a room (run C), 80% ceiling: 26 rooms
        self.assertEqual(plan(27).stt, "c6i.xlarge")

    def test_the_database_grows_with_connections(self):
        self.assertEqual(plan(89).db, "db.t4g.small")   # ~1.6 connections a room, 80% of ~180
        self.assertEqual(plan(100).db, "db.t4g.medium")

    def test_it_names_what_binds(self):
        self.assertEqual(plan(27).binding, "speech-to-text CPU")
        self.assertEqual(plan(100).binding, "database connections")

    def test_more_rooms_never_cost_less(self):
        costs = [plan(n).usd_per_hour for n in range(1, 101)]
        self.assertEqual(costs, sorted(costs))

    def test_refuses_what_was_never_measured(self):
        with self.assertRaises(ValueError):
            plan(101)  # tested to 100 rooms (spike, 2026-10-05)


class GpuTest(unittest.TestCase):
    def test_the_gpu_is_never_the_cheaper_way_to_hear_100_rooms(self):
        # Runs A and B (2026-10-06): the GPU was ~15 ms faster at p95 and no more accurate;
        # a CPU machine holds the tested 100 rooms for less than the GPU (before its licence).
        self.assertEqual(plan(100).stt, "c6i.2xlarge")
        self.assertLess(PRICE["c6i.2xlarge"], GPU_PRICE)


class MatrixTest(unittest.TestCase):
    def test_one_row_per_size_with_monthly_cost(self):
        rows = matrix([1, 20, 100])
        self.assertEqual([r["rooms"] for r in rows], [1, 20, 100])
        self.assertAlmostEqual(rows[0]["usd_per_month"], rows[0]["usd_per_hour"] * 730, places=2)


if __name__ == "__main__":
    unittest.main()
