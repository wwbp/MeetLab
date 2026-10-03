"""Load-test plan: the standard shapes, per-step measures, and the SLO verdict (load_plan.py)."""
import unittest

from load_plan import SHAPES, Step, room_window, schedule, step_bounds, step_of, summarise_step, verdict


class TestShapes(unittest.TestCase):
    def test_every_shape_ends_with_no_rooms_so_teardown_is_checked(self):
        for shape in SHAPES:
            self.assertEqual(schedule(shape, target=50)[-1].rooms, 0, shape)

    def test_smoke_is_one_room(self):
        self.assertEqual({s.rooms for s in schedule("smoke", target=50)}, {1, 0})

    def test_load_ramps_up_to_the_target_and_holds_it_longest(self):
        steps = schedule("load", target=50)
        rooms = [s.rooms for s in steps[:-1]]
        self.assertEqual(rooms, sorted(rooms))
        self.assertEqual(max(rooms), 50)
        self.assertEqual(max(steps, key=lambda s: s.hold_s).rooms, 50)

    def test_stress_goes_past_the_target_to_double(self):
        self.assertEqual(max(s.rooms for s in schedule("stress", target=50)), 100)

    def test_spike_starts_everyone_at_once(self):
        self.assertEqual(schedule("spike", target=50)[0].rooms, 50)

    def test_soak_holds_the_target_for_an_hour(self):
        self.assertGreaterEqual(max(s.hold_s for s in schedule("soak", target=50) if s.rooms == 50), 3600)

    def test_breakpoint_climbs_in_even_steps_to_triple_and_stops_at_the_first_failure(self):
        steps = schedule("breakpoint", target=50)
        climb = [s.rooms for s in steps[:-1]]
        self.assertEqual(climb[-1], 150)
        self.assertEqual(len({b - a for a, b in zip(climb, climb[1:])}), 1)
        self.assertTrue(all(s.stop_on_failure for s in steps[:-1]))

    def test_hold_can_be_shortened_for_a_rehearsal(self):
        self.assertTrue(all(s.hold_s == 30 for s in schedule("stress", target=4, hold_s=30)[:-1]))

    def test_an_unknown_shape_is_refused(self):
        with self.assertRaises(ValueError):
            schedule("chaos", target=50)


class TestStepMeasures(unittest.TestCase):
    def test_measures_what_a_participant_experiences(self):
        turns = [1000.0] * 18 + [3000.0, None]  # ms to the bot's first audio; None = no reply
        m = summarise_step(Step(rooms=4, hold_s=60), turns=turns, joins_s=[30.0, 40.0, 50.0, 200.0],
                           start_errors=1, disconnects=0)
        self.assertEqual((m["rooms"], m["turns"], m["replied"]), (4, 20, 19))
        self.assertAlmostEqual(m["reply_rate"], 0.95)
        self.assertEqual(m["p50_ms"], 1000.0)
        self.assertEqual(m["p95_ms"], 3000.0)
        self.assertEqual(m["join_p95_s"], 200.0)
        self.assertEqual(m["start_errors"], 1)

    def test_an_empty_step_reports_nothing_rather_than_zero_latency(self):
        m = summarise_step(Step(rooms=0, hold_s=60), turns=[], joins_s=[], start_errors=0, disconnects=0)
        self.assertIsNone(m["p95_ms"])
        self.assertIsNone(m["reply_rate"])


class TestVerdict(unittest.TestCase):
    good = {"rooms": 10, "turns": 100, "reply_rate": 0.99, "p95_ms": 1500.0, "start_errors": 0, "disconnects": 0}

    def test_a_healthy_step_passes(self):
        self.assertEqual(verdict(self.good), (True, []))

    def test_each_slo_names_itself_when_broken(self):
        cases = {"reply_rate": 0.90, "p95_ms": 2500.0, "start_errors": 1, "disconnects": 2}
        for field, bad in cases.items():
            ok, why = verdict({**self.good, field: bad})
            self.assertFalse(ok, field)
            self.assertTrue(any(field in w for w in why), why)

    def test_a_step_with_rooms_but_no_turns_fails(self):
        ok, why = verdict({**self.good, "turns": 0, "reply_rate": None, "p95_ms": None})
        self.assertFalse(ok)

    def test_the_closing_step_is_judged_on_errors_alone(self):
        # Turns still in flight when the rooms leave land in it; there is no load to judge.
        closing = {"rooms": 0, "turns": 1, "reply_rate": 1.0, "p95_ms": 2178.0, "start_errors": 0, "disconnects": 0}
        self.assertTrue(verdict(closing)[0])
        self.assertFalse(verdict({**closing, "disconnects": 1})[0])


class TestTiming(unittest.TestCase):
    steps = [Step(2, 100), Step(4, 100), Step(0, 120)]

    def test_steps_follow_each_other_from_the_start(self):
        self.assertEqual(step_bounds(self.steps, t0=1000), [(1000, 1100), (1100, 1200), (1200, 1320)])

    def test_a_room_is_in_from_the_step_that_first_needs_it_until_one_that_does_not(self):
        self.assertEqual(room_window(0, self.steps, t0=0), (0.0, 200))
        self.assertEqual(room_window(3, self.steps, t0=0)[1], 200)

    def test_a_steps_new_rooms_arrive_spread_over_its_first_30_seconds(self):
        self.assertEqual([room_window(i, self.steps, t0=0)[0] for i in (2, 3)], [100.0, 115.0])

    def test_a_room_no_step_needs_never_starts(self):
        self.assertIsNone(room_window(9, self.steps, t0=0))

    def test_a_measurement_belongs_to_the_step_it_happened_in(self):
        bounds = step_bounds(self.steps, t0=0)
        self.assertEqual([step_of(t, bounds) for t in (0, 99.9, 100, 319, 320)], [0, 0, 1, 2, None])


if __name__ == "__main__":
    unittest.main()
