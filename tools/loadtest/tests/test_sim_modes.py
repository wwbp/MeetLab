"""The audio-mode simulations' pure parts (sim_modes.py): verdicts on handwritten timelines,
one metrics row per run, press timing, the grid, pairing and the offline report."""
import unittest
from collections import Counter

from meetlab_loadtest.sim_modes import (TIMING, cells, hold_timing, metrics, report, row, run_list,
                                        verdict)

MODES = ("normal", "ptt", "baton")
TEXT = {
    "a": "What should we prioritise for the leadership role this quarter?",
    "b": "We are seeing higher latency on the transcription service since Tuesday.",
    "c": "Can you explain what a retrieval augmented generation system does?",
}
SENTENCES = [6.0, 9.0, 12.0]


def line(who, start, end, **flags):
    return {"who": who, "text": TEXT[who], "start": start, "end": end, **flags}


def timeline(lines, bot, mode="normal", **extra):
    """Everything right: each line stored whole under its speaker; ptt and baton press 0.25 s
    before a line and release 0.3 s after, and baton's floor follows the hold."""
    holds = [(x["who"], x["start"] - .25, x["end"] + .3) for x in lines] if mode != "normal" else []
    return {"lines": lines, "bot": bot, "stored": [(x["end"] + .5, x["who"], x["text"]) for x in lines],
            "holds": holds, "floor": holds if mode == "baton" else [], "end": 60.0, **extra}


def good(mode, scenario, people):
    who = "abc"[:people]
    if scenario == "turn_taking":
        lines = [line(who[k % people], 10.0 * k, 10.0 * k + 3) for k in range(6)]
        return timeline(lines, [(10.0 * k + 3.8, 10.0 * k + 8) for k in range(6)], mode)
    if scenario == "overlap" and mode != "baton":
        return timeline([line(w, .15 * k, 3 + .15 * k) for k, w in enumerate("abc")], [(4.0, 8.0)], mode)
    if scenario == "overlap":
        lines = [line("a", 0.0, 3.0), line("b", 3.35, 6.35), line("c", 6.7, 9.7)]
        holds = [("a", -.25, 3.3), ("b", -.1, 6.65), ("c", .05, 10.0)]
        floor = [("a", -.25, 3.3), ("b", 3.3, 6.65), ("c", 6.65, 10.0)]
        return timeline(lines, [(10.5, 14.0)], mode, holds=holds, floor=floor)
    if scenario == "interrupt":
        other = who[-1]
        if mode == "baton":
            return timeline([line("a", 0.0, 3.0), line(other, 6.25, 9.0)], [(3.8, 6.0), (9.8, 12.0)], mode,
                            holds=[("a", -.25, 3.3), (other, 5.0, 9.3)],
                            floor=[("a", -.25, 3.3), (other, 6.0, 9.3)], bot_sentence_ends=SENTENCES)
        return timeline([line("a", 0.0, 3.0), line(other, 5.0, 8.0)], [(3.8, 5.5), (8.8, 11.0)], mode)
    if scenario == "long_hold":
        return timeline([line("a", 0.0, 46.0)], [(47.0, 50.0)], mode)
    if scenario == "press_generating":
        return timeline([line("a", 0.0, 3.0), line("b", 3.85, 7.0)], [(7.8, 11.0)], mode)
    if scenario == "walked_away" and mode == "ptt":
        return timeline([line("a", 0.0, 3.0), line("b", 5.25, 8.0)], [(18.5, 23.0)], mode,
                        holds=[("a", -.25, None), ("b", 5.0, 8.3)], end=30.0)
    if scenario == "walked_away":
        return timeline([line("a", 0.0, 3.0), line("b", 18.25, 22.0)], [(23.0, 26.0)], mode,
                        holds=[("a", -.25, None), ("b", 5.0, 22.3)],
                        floor=[("a", -.25, 18.0), ("b", 18.0, 22.3)], end=30.0)
    if scenario == "late_release":
        return timeline([line("a", 0.0, 3.0)], [(5.5, 9.0)], mode, holds=[("a", -.25, 5.0)],
                        floor=[("a", -.25, 5.0)] if mode == "baton" else [])
    raise AssertionError(scenario)


class Case(unittest.TestCase):
    def assertFails(self, mode, scenario, tl, needle):
        v = verdict(mode, scenario, tl)
        self.assertEqual(v["status"], "fail", v)
        self.assertIn(needle, " | ".join(v["problems"]))

    def assertPasses(self, mode, scenario, tl):
        self.assertEqual(verdict(mode, scenario, tl), {"status": "pass", "problems": []})


class EveryCell(Case):
    def test_each_of_the_28_cells_has_a_clean_timeline(self):
        self.assertEqual(len(cells()), 28)
        for scenario, people, mode in cells():
            with self.subTest(scenario=scenario, people=people, mode=mode):
                self.assertPasses(mode, scenario, good(mode, scenario, people))

    def test_a_cell_outside_the_plan_is_an_error_not_a_pass(self):
        with self.assertRaises(ValueError):
            verdict("normal", "walked_away", good("ptt", "walked_away", 2))
        with self.assertRaises(ValueError):
            verdict("loud", "turn_taking", good("ptt", "turn_taking", 1))

    def test_a_timeline_where_nobody_spoke_fails_in_every_cell(self):
        self.assertEqual(len(cells()), 28)
        for scenario, people, mode in cells():
            with self.subTest(scenario=scenario, people=people, mode=mode):
                self.assertFails(mode, scenario, {"lines": [], "bot": [], "stored": [], "end": 60.0},
                                 "nobody spoke")


class Infrastructure(Case):
    def test_an_infrastructure_signal_makes_a_bad_run_invalid_not_failed(self):
        for signal in ("lag", "stt-cpu restart"):
            tl = good("ptt", "turn_taking", 1)
            v = verdict("ptt", "turn_taking", {**tl, "bot": [], "infra": [signal]})
            self.assertEqual(v["status"], "invalid")
            self.assertTrue(v["problems"], "what went wrong stays on the record")

    def test_a_clean_run_with_a_signal_is_still_invalid(self):
        tl = {**good("ptt", "turn_taking", 1), "infra": ["lag"]}
        self.assertEqual(verdict("ptt", "turn_taking", tl), {"status": "invalid", "problems": []})

    def test_the_same_bad_run_without_a_signal_fails(self):
        tl = {**good("ptt", "turn_taking", 1), "bot": [], "infra": []}
        self.assertEqual(verdict("ptt", "turn_taking", tl)["status"], "fail")

    def test_an_outcome_cannot_be_passed_off_as_infrastructure(self):
        tl = {**good("baton", "turn_taking", 1), "infra": ["stuck floor"]}
        with self.assertRaises(ValueError):
            verdict("baton", "turn_taking", tl)


class Holds(Case):
    def test_bot_audible_while_someone_holds_fails(self):
        for mode in ("ptt", "baton"):
            tl = good(mode, "turn_taking", 1)
            self.assertFails(mode, "turn_taking", {**tl, "bot": [(2.0, 8.0)] + tl["bot"][1:]}, "while a held")

    def test_bot_starting_the_instant_of_a_press_loses_the_tie(self):
        tl = timeline([line("a", 0.0, 3.0), line("b", 8.25, 11.0)], [(8.0, 8.2), (11.8, 13.0)], "ptt")
        self.assertFails("ptt", "turn_taking", tl, "while b held")

    def test_ptt_press_alone_does_not_yield_the_bot_speech_does(self):
        tl = timeline([line("a", 0.0, 3.0), line("b", 5.0, 8.0)], [(3.8, 5.5), (8.8, 11.0)], "ptt")
        self.assertEqual(tl["holds"][1][1], 4.75)  # the press lands while the bot is audible
        self.assertPasses("ptt", "interrupt", tl)

    def test_ptt_bot_still_audible_past_a_second_after_speech_fails(self):
        tl = timeline([line("a", 0.0, 3.0), line("b", 5.0, 8.0)], [(3.8, 6.5), (8.8, 11.0)], "ptt")
        self.assertFails("ptt", "interrupt", tl, "after b's speech")

    def test_baton_bot_audible_over_the_holder_fails(self):
        tl = good("baton", "interrupt", 3)
        self.assertFails("baton", "interrupt", {**tl, "bot": [(3.8, 7.5), (9.8, 12.0)],
                                                "bot_sentence_ends": [7.5]}, "while c held the floor")

    def test_a_mic_open_without_a_press_fails(self):
        for mode in ("ptt", "baton"):
            tl = good(mode, "turn_taking", 1)
            self.assertFails(mode, "turn_taking", {**tl, "holds": tl["holds"][1:]}, "with no press")

    def test_normal_room_with_floor_events_fails(self):
        tl = good("normal", "turn_taking", 1)
        self.assertFails("normal", "turn_taking", {**tl, "holds": [("a", 0.0, 3.0)]}, "floor events")


class Overlap(Case):
    def test_overlap_heard_in_baton_fails(self):
        tl = good("baton", "overlap", 3)
        lines = [line("a", 0.0, 3.0), line("b", 2.5, 6.35), line("c", 6.7, 9.7)]
        self.assertFails("baton", "overlap", {**tl, "lines": lines}, "overlap heard")

    def test_baton_handover_inside_the_release_tail_is_not_overlap(self):
        tl = good("baton", "overlap", 3)
        lines = [line("a", 0.0, 3.0), line("b", 2.9, 6.35), line("c", 6.7, 9.7)]
        self.assertPasses("baton", "overlap", {**tl, "lines": lines})

    def test_baton_queue_out_of_order_fails(self):
        tl = good("baton", "overlap", 3)
        floor = [("a", -.25, 3.3), ("c", 3.3, 6.65), ("b", 6.65, 10.0)]
        self.assertFails("baton", "overlap", {**tl, "floor": floor}, "queue")

    def test_overlap_is_fine_in_normal_and_ptt_but_must_have_happened(self):
        for mode in ("normal", "ptt"):
            lines = [line("a", 0.0, 3.0), line("b", 4.0, 7.0), line("c", 8.0, 11.0)]
            self.assertFails(mode, "overlap", timeline(lines, [(12.0, 14.0)], mode), "did not happen")

    def test_merged_and_misattributed_turns_fail(self):
        tl = good("normal", "overlap", 3)
        self.assertFails("normal", "overlap", {**tl, "stored": [(4.0, "a", TEXT["a"] + " " + TEXT["b"]),
                                                                (4.0, "c", TEXT["c"])]}, "holds a's and b's")
        self.assertFails("normal", "overlap", {**tl, "stored": [(4.0, "a", TEXT["b"]), (4.0, "b", TEXT["a"]),
                                                                (4.0, "c", TEXT["c"])]}, "stored b's words")


class Stuck(Case):
    def silent(self, mode, seconds):
        """a holds on after speaking, with nobody audible for `seconds`; then the bot speaks."""
        hold = [("a", -.25, 3 + seconds + 1)]
        return timeline([line("a", 0.0, 3.0)], [(3 + seconds, 3 + seconds + 2)], mode, holds=hold,
                        floor=hold if mode == "baton" else [])

    def test_held_over_20_s_with_nobody_audible_fails(self):
        for mode in ("ptt", "baton"):
            self.assertFails(mode, "late_release", self.silent(mode, 21.0), "stuck floor")

    def test_20_s_exactly_is_not_stuck_and_neither_is_the_15_s_idle_release(self):
        for mode in ("ptt", "baton"):
            for seconds in (15.0, 20.0):
                self.assertEqual(metrics(mode, self.silent(mode, seconds))["stuck_floors"], 0)

    def test_baton_floor_is_what_counts_not_the_button(self):
        tl = good("baton", "walked_away", 2)  # the button stays down for 30 s; the floor moves on at 18
        self.assertEqual(metrics("baton", tl)["stuck_floors"], 0)
        stuck = {**tl, "lines": tl["lines"][:1], "bot": [(26.0, 28.0)], "floor": [("a", -.25, 25.0), ("b", 25.0, 28.0)]}
        self.assertFails("baton", "walked_away", stuck, "stuck floor")

    def test_ptt_hold_that_blocks_the_bot_forever_is_stuck(self):
        tl = {**good("ptt", "walked_away", 2), "bot": []}
        self.assertFails("ptt", "walked_away", tl, "stuck floor")


class Clipping(Case):
    @staticmethod
    def without_last(tl):
        return [(t, w, text.rsplit(" ", 1)[0]) for t, w, text in tl["stored"]]

    def test_a_clipped_last_word_fails_in_every_mode(self):
        for mode, scenario in (("normal", "long_hold"), ("ptt", "late_release"), ("baton", "late_release")):
            tl = good(mode, scenario, 1)
            self.assertFails(mode, scenario, {**tl, "stored": self.without_last(tl)}, "clipped last")

    def test_a_clipped_first_word_fails(self):
        tl = good("ptt", "late_release", 1)
        stored = [(t, w, text.split(" ", 1)[1]) for t, w, text in tl["stored"]]
        self.assertFails("ptt", "late_release", {**tl, "stored": stored}, "clipped first")

    def test_the_same_word_earlier_on_does_not_hide_the_clip(self):
        tl = good("ptt", "late_release", 1)
        lines = [{**tl["lines"][0], "text": "we know what we know"}]
        self.assertFails("ptt", "late_release", {**tl, "lines": lines, "stored": [(3.5, "a", "we know what we")]},
                         "clipped last")

    def test_one_extra_word_from_speech_to_text_is_not_a_clip(self):
        tl = good("ptt", "late_release", 1)
        self.assertPasses("ptt", "late_release", {**tl, "stored": [(3.5, "a", "um " + TEXT["a"] + " okay")]})

    def test_a_scripted_slip_clips_the_person_not_the_system(self):
        tl = good("ptt", "late_release", 1)
        stored = self.without_last(tl)
        slip = {**tl["lines"][0], "release_early": True}
        self.assertPasses("ptt", "late_release", {**tl, "lines": [slip], "stored": stored})
        wrong_slip = {**tl["lines"][0], "press_late": True}
        self.assertFails("ptt", "late_release", {**tl, "lines": [wrong_slip], "stored": stored}, "clipped last")

    def test_a_line_never_stored_fails(self):
        tl = good("baton", "late_release", 1)
        self.assertFails("baton", "late_release", {**tl, "stored": []}, "never transcribed")


class TurnTaking(Case):
    def test_slo_unanswered_turns_fail(self):
        tl = good("normal", "turn_taking", 1)
        self.assertFails("normal", "turn_taking", {**tl, "bot": tl["bot"][1:]}, "answered")

    def test_slo_a_slow_reply_fails(self):
        tl = good("normal", "turn_taking", 1)
        self.assertFails("normal", "turn_taking", {**tl, "bot": [(5.5, 8.0)] + tl["bot"][1:]}, "reply p95")

    def test_slo_reply_runs_from_the_release_when_someone_holds_past_their_speech(self):
        tl = good("ptt", "turn_taking", 1)  # 2.9 s after the last frame, 1.9 s after the release
        holds = [("a", -.25, 4.0)] + tl["holds"][1:]
        self.assertPasses("ptt", "turn_taking", {**tl, "holds": holds, "bot": [(5.9, 8.0)] + tl["bot"][1:]})

    def test_slo_misheard_words_fail(self):
        tl = good("normal", "turn_taking", 1)
        stored = [(t, w, "what should we xx yy zz leadership role this quarter") for t, w, _ in tl["stored"]]
        self.assertFails("normal", "turn_taking", {**tl, "stored": stored}, "word error")


class Interrupt(Case):
    def test_nobody_interrupting_is_not_a_pass(self):
        tl = timeline([line("a", 0.0, 3.0), line("b", 9.0, 11.0)], [(3.8, 8.0), (11.8, 13.0)])
        self.assertFails("normal", "interrupt", tl, "did not happen")

    def test_baton_bot_yields_at_its_sentence_end_not_past_it_not_before(self):
        tl = good("baton", "interrupt", 1)
        self.assertFails("baton", "interrupt", {**tl, "bot": [(3.8, 9.0), (9.8, 12.0)]}, "sentence")  # one too far
        self.assertFails("baton", "interrupt", {**tl, "bot": [(3.8, 5.2), (9.8, 12.0)]}, "sentence")  # cut short

    def test_baton_with_no_press_during_the_bot_did_not_happen(self):
        tl = good("baton", "turn_taking", 1)
        self.assertFails("baton", "interrupt", tl, "did not happen")


class LongHold(Case):
    def test_a_45_s_turn_stored_as_two_fails(self):
        for mode in MODES:
            tl = good(mode, "long_hold", 1)
            words = TEXT["a"].split()
            stored = [(10.0, "a", " ".join(words[:4])), (46.5, "a", " ".join(words[4:]))]
            self.assertFails(mode, "long_hold", {**tl, "stored": stored}, "2 turns")

    def test_a_short_hold_did_not_exercise_it(self):
        tl = timeline([line("a", 0.0, 10.0)], [(11.0, 13.0)], "ptt")
        self.assertFails("ptt", "long_hold", tl, "did not happen")

    def test_bot_replying_mid_turn_fails_in_normal_too(self):
        tl = good("normal", "long_hold", 1)
        self.assertFails("normal", "long_hold", {**tl, "bot": [(20.0, 20.5)]}, "during the long turn")

    def test_baton_timing_out_mid_turn_fails(self):
        tl = good("baton", "long_hold", 1)
        self.assertFails("baton", "long_hold", {**tl, "floor": [("a", -.25, 20.0), ("b", 20.0, 30.0)]},
                         "lost the baton")


class PressGenerating(Case):
    def test_bot_speaking_while_the_press_is_live_fails(self):
        for mode in ("ptt", "baton"):
            tl = good(mode, "press_generating", 2)
            self.assertFails(mode, "press_generating", {**tl, "bot": [(4.5, 6.0), (7.8, 11.0)]}, "while b held")

    def test_normal_bot_starting_over_speech_must_stop_within_a_second(self):
        tl = good("normal", "press_generating", 2)
        self.assertPasses("normal", "press_generating", {**tl, "bot": [(4.5, 5.0), (7.8, 11.0)]})
        self.assertFails("normal", "press_generating", {**tl, "bot": [(4.5, 6.0), (7.8, 11.0)]}, "over b's speech")

    def test_second_person_arriving_after_the_reply_did_not_exercise_it(self):
        tl = timeline([line("a", 0.0, 3.0), line("b", 9.0, 11.0)], [(3.8, 8.0), (11.8, 13.0)], "ptt")
        self.assertFails("ptt", "press_generating", tl, "did not happen")


class WalkedAway(Case):
    def test_baton_granted_too_early_too_late_or_never_fails(self):
        tl = good("baton", "walked_away", 2)
        self.assertFails("baton", "walked_away", {**tl, "floor": [("a", -.25, 10.0), ("b", 10.0, 22.3)]}, "granted")
        self.assertFails("baton", "walked_away", {**tl, "floor": [("a", -.25, 23.5), ("b", 23.5, 26.0)]}, "granted")
        self.assertFails("baton", "walked_away", {**tl, "floor": tl["floor"][:1]}, "granted")

    def test_ptt_bot_speaking_before_15_s_of_silence_fails(self):
        tl = good("ptt", "walked_away", 2)
        self.assertFails("ptt", "walked_away", {**tl, "bot": [(14.0, 18.0)]}, "while a held")

    def test_ptt_bot_need_not_speak_if_it_has_nothing_to_say(self):
        tl = {**good("ptt", "walked_away", 2), "end": 20.0}
        self.assertPasses("ptt", "walked_away", {**tl, "bot": []})

    def test_a_release_on_time_is_not_walking_away(self):
        tl = timeline([line("a", 0.0, 3.0)], [(4.0, 6.0)], "ptt")
        self.assertFails("ptt", "walked_away", tl, "did not happen")


class LateRelease(Case):
    def test_bot_not_waiting_for_the_release_fails(self):
        for mode in ("ptt", "baton"):
            tl = good(mode, "late_release", 1)
            self.assertFails(mode, "late_release", {**tl, "bot": [(4.0, 9.0)]}, "while a held")

    def test_bot_never_answering_is_not_waiting(self):
        tl = {**good("baton", "late_release", 1), "bot": []}
        self.assertFails("baton", "late_release", tl, "never answered")

    def test_an_on_time_release_did_not_exercise_it(self):
        tl = timeline([line("a", 0.0, 3.0)], [(4.0, 6.0)], "ptt")
        self.assertFails("ptt", "late_release", tl, "did not happen")


class Metrics(unittest.TestCase):
    def test_reply_time_from_the_last_frame_and_from_the_release(self):
        m = metrics("ptt", timeline([line("a", 0.0, 3.0), line("a", 10.0, 13.0)], [(4.0, 6.0), (15.0, 17.0)], "ptt"))
        self.assertEqual((m["reply_p50_s"], m["reply_p95_s"]), (1.0, 2.0))
        self.assertAlmostEqual(m["reply_release_p50_s"], .7)
        self.assertAlmostEqual(m["reply_release_p95_s"], 1.7)

    def test_normal_has_no_release_to_measure_from(self):
        m = metrics("normal", timeline([line("a", 0.0, 3.0)], [(4.0, 6.0)]))
        self.assertIsNone(m["reply_release_p50_s"])
        self.assertIsNone(m["dead_p50_s"])

    def test_turns_answered_counts_overlapping_lines_as_one_turn(self):
        m = metrics("normal", good("normal", "overlap", 3))
        self.assertEqual((m["turns"], m["turns_answered"]), (1, 1.0))
        m = metrics("normal", timeline([line("a", 0.0, 3.0), line("a", 10.0, 13.0)], [(4.0, 6.0)]))
        self.assertEqual((m["turns"], m["turns_answered"]), (2, .5))

    def test_a_baton_queue_is_one_turn_too(self):
        m = metrics("baton", good("baton", "overlap", 3))
        self.assertEqual((m["turns"], m["turns_answered"]), (1, 1.0))

    def test_a_reply_after_the_next_line_started_does_not_answer_the_turn(self):
        m = metrics("normal", timeline([line("a", 0.0, 3.0), line("a", 5.0, 8.0)], [(9.0, 10.0)]))
        self.assertEqual(m["turns_answered"], .5)

    def test_hearing_comes_per_speaker_and_totals_up(self):
        tl = timeline([line("a", 0.0, 3.0), line("b", 10.0, 13.0)], [(4.0, 6.0)])
        tl["stored"] = [(3.5, "a", TEXT["a"]), (13.5, "b", "we are seeing higher latency"),
                        (14.0, "b", "on the transcription service since tuesday")]
        m = metrics("normal", tl)
        self.assertEqual(m["by_speaker"]["a"]["wer"], 0)
        self.assertEqual(m["by_speaker"]["b"]["fragmented"], 1)
        self.assertEqual((m["split"], m["missed"], m["wer"]), (1, 0, 0))

    def test_clipped_is_a_share_of_lines_with_the_mic_open_and_slips_are_kept_apart(self):
        short, tail = TEXT["a"].rsplit(" ", 1)[0], TEXT["a"].split(" ", 1)[1]
        lines = [line("a", 0.0, 3.0), line("a", 10.0, 13.0), line("a", 20.0, 23.0, release_early=True),
                 line("a", 30.0, 33.0, press_late=True)]
        tl = timeline(lines, [(4.0, 6.0)],
                      stored=[(3.5, "a", TEXT["a"]), (13.5, "a", short), (23.5, "a", short), (33.5, "a", tail)])
        m = metrics("normal", tl)
        self.assertAlmostEqual(m["clipped_last"], 1 / 3)  # 1 of the 3 lines not released early
        self.assertEqual(m["clipped_first"], 0)           # the only clip there was a late press
        self.assertEqual(m["slips_clipped"], 2)
        self.assertGreater(m["wer_all"], m["wer"])

    def test_attribution_and_merged(self):
        tl = good("normal", "overlap", 3)
        m = metrics("normal", {**tl, "stored": [(4.0, "a", TEXT["a"] + " " + TEXT["b"]), (4.0, "c", TEXT["c"])]})
        self.assertAlmostEqual(m["attribution"], 2 / 3)
        self.assertEqual(m["merged_turns"], 1)

    def test_talk_over_and_yields(self):
        tl = timeline([line("a", 0.0, 3.0), line("b", 5.0, 8.0), line("a", 20.0, 22.0)],
                      [(3.8, 5.5), (19.0, 21.5)])
        m = metrics("normal", tl)
        self.assertEqual(m["talk_over_s"], 2.0)
        self.assertEqual((m["interruptions"], m["yields"]), (2, 1))

    def test_bot_share_and_overlap(self):
        m = metrics("normal", timeline([line("a", 0.0, 4.0), line("b", 3.0, 6.0)], [(6.0, 8.0)]))
        self.assertEqual(m["bot_share"], .25)
        self.assertEqual(m["overlap_s"], 1.0)

    def test_three_at_once_counts_once_as_overlap(self):
        m = metrics("normal", timeline([line("a", 0.0, 4.0), line("b", 1.0, 4.0), line("c", 2.0, 4.0)], []))
        self.assertEqual(m["overlap_s"], 3.0)

    def test_no_audio_has_no_bot_share(self):
        self.assertIsNone(metrics("normal", {"lines": [], "bot": [], "stored": [], "end": 1.0})["bot_share"])

    def test_dead_time_is_press_to_grant_p50_and_p95(self):
        m = metrics("baton", good("baton", "overlap", 3))
        self.assertAlmostEqual(m["dead_p50_s"], 3.4)
        self.assertAlmostEqual(m["dead_p95_s"], 6.6)

    def test_a_press_never_granted_has_no_dead_time(self):
        m = metrics("baton", {**good("baton", "overlap", 3), "floor": [("a", -.25, 3.3)]})
        self.assertEqual(m["dead_p95_s"], 0)

    def test_stuck_floor_counts_events(self):
        tl = timeline([line("a", 0.0, 3.0), line("a", 25.0, 26.0)], [], "ptt", holds=[("a", -.25, 50.0)])
        self.assertEqual(metrics("ptt", tl)["stuck_floors"], 2)


class Timing(unittest.TestCase):
    KEYS = [(f"dlg-{d}", k) for d in range(200) for k in range(10)]

    def test_the_same_dialogue_and_line_always_time_the_same(self):
        self.assertEqual(hold_timing("dialogue-1", 3), hold_timing("dialogue-1", 3))
        self.assertEqual(hold_timing("dlg-1", 0), {"press_s": -.247, "release_s": .405, "press_late": False,
                                                   "release_early": False})  # pinned: survives any process

    def test_lines_differ(self):
        self.assertGreater(len({hold_timing(d, k)["press_s"] for d, k in self.KEYS}), 50)

    def test_ranges_and_shares(self):
        got = [hold_timing(d, k) for d, k in self.KEYS]
        self.assertTrue(all(-.350 <= t["press_s"] <= -.150 for t in got if not t["press_late"]))
        self.assertTrue(all(.150 <= t["release_s"] <= .450 for t in got if not t["release_early"]))
        self.assertTrue(all(t["press_s"] > 0 for t in got if t["press_late"]))
        self.assertTrue(all(t["release_s"] < 0 for t in got if t["release_early"]))
        self.assertAlmostEqual(sum(t["press_late"] for t in got) / len(got), .10, delta=.03)
        self.assertAlmostEqual(sum(t["release_early"] for t in got) / len(got), .10, delta=.03)

    def test_press_late_and_release_early_are_not_the_same_lines(self):
        got = [hold_timing(d, k) for d, k in self.KEYS]
        both = sum(t["press_late"] and t["release_early"] for t in got)
        self.assertLess(both, sum(t["press_late"] for t in got) / 2)

    def test_timing_is_a_setting(self):
        s = {**TIMING, "late_share": 1.0, "early_share": 0.0, "pre_roll_ms": (100, 0), "post_roll_ms": (50, 0)}
        got = hold_timing("x", 1, s)
        self.assertTrue(got["press_late"])
        self.assertEqual(got["release_s"], .05)
        self.assertEqual(hold_timing("x", 1, {**s, "late_share": 0.0})["press_s"], -.1)


class Grid(unittest.TestCase):
    def test_28_cells_none_repeated(self):
        self.assertEqual(len(cells()), 28)
        self.assertEqual(len(set(cells())), 28)

    def test_each_behaviour_has_the_plans_people_and_modes(self):
        want = {"turn_taking": ((1, 2, 3), MODES), "overlap": ((3,), MODES), "interrupt": ((1, 3), MODES),
                "long_hold": ((2,), MODES), "press_generating": ((2,), MODES),
                "walked_away": ((2,), ("ptt", "baton")), "late_release": ((1,), ("ptt", "baton"))}
        for scenario, (people, modes) in want.items():
            got = [(p, m) for s, p, m in cells() if s == scenario]
            self.assertEqual(sorted(got), sorted((p, m) for p in people for m in modes), scenario)


class Runs(unittest.TestCase):
    def blocks(self):
        out = {}
        for r_ in run_list():
            out.setdefault((r_["scenario"], r_["people"], r_["repeat"]), []).append(r_["mode"])
        return out

    def test_108_runs_and_the_plans_repeats(self):
        runs = run_list()
        self.assertEqual(len(runs), 108)
        per_cell = Counter((x["scenario"], x["people"], x["mode"]) for x in runs)
        fives = {c for c, n in per_cell.items() if n == 5}
        self.assertEqual(fives, {(s, p, m) for s, p, m in cells()
                                 if (s == "turn_taking" and p > 1) or s == "overlap" or (s == "interrupt" and p == 3)})
        self.assertEqual(set(per_cell.values()), {3, 5})

    def test_every_block_holds_every_mode_of_its_cell_once(self):
        blocks = self.blocks()
        self.assertTrue(blocks)
        for (scenario, people, _), modes in blocks.items():
            want = {m for s, p, m in cells() if (s, p) == (scenario, people)}
            self.assertEqual(sorted(modes), sorted(want))

    def test_mode_order_rotates_between_repeats(self):
        blocks = self.blocks()
        self.assertTrue(blocks)
        for (s, p, rep), modes in blocks.items():
            if rep:
                self.assertNotEqual(modes[0], blocks[(s, p, rep - 1)][0], (s, p, rep))

    def test_no_mode_is_favoured_by_position(self):
        first = Counter(modes[0] for modes in self.blocks().values() if len(modes) == 3)
        self.assertEqual(sum(first.values()), 32)
        self.assertLessEqual(max(first.values()) - min(first.values()), 2)

    def test_the_list_is_the_same_every_time(self):
        self.assertEqual(run_list(), run_list())


def r(mode, repeat=0, scenario="overlap", people=3, status="pass", **fields):
    base = {"reply_p50_s": 1.0, "reply_release_p50_s": None, "wer": .02, "clipped_first": 0.0, "clipped_last": 0.0,
            "stuck_floors": 0, "turns_answered": 1.0, "attribution": 1.0, "talk_over_s": 0.0, "bot_share": .3,
            "overlap_s": 0.0}
    return {"mode": mode, "repeat": repeat, "scenario": scenario, "people": people, "status": status, **base, **fields}


class Report(unittest.TestCase):
    def test_invalid_runs_are_counted_by_mode_and_left_out_of_the_pairs(self):
        rows = [r("normal", k) for k in range(5)] + [r("ptt", k) for k in range(5)]
        rows += [r("ptt", 0, status="invalid"), r("ptt", 1, status="invalid"), r("baton", 0, status="invalid")]
        got = report(rows)
        self.assertEqual(got["invalid"], {"normal": 0, "ptt": 2, "baton": 1})
        self.assertEqual(got["modes"]["ptt"]["valid"], 5)
        self.assertEqual(got["modes"]["baton"]["valid"], 0)
        self.assertIsNone(got["modes"]["baton"]["fail_rate"])

    def test_a_retry_replaces_only_the_invalid_attempt(self):
        rows = [r("normal", 0), r("ptt", 0, status="invalid", wer=.9), r("ptt", 0, wer=.03)]
        self.assertAlmostEqual(report(rows)["paired"]["ptt"]["wer"]["diff"], .01)

    def test_a_failed_run_stays_in_so_a_mode_cannot_hide_what_it_causes(self):
        rows = [r("normal", k) for k in range(5)] + [r("ptt", k, status="fail", wer=.5) for k in range(5)]
        got = report(rows)
        self.assertEqual((got["modes"]["ptt"]["failed"], got["modes"]["ptt"]["valid"]), (5, 5))
        self.assertAlmostEqual(got["paired"]["ptt"]["wer"]["diff"], .48)

    def test_a_clean_5_of_5_still_allows_about_45_percent(self):
        got = report([r("normal", k) for k in range(5)])["modes"]["normal"]
        self.assertEqual(got["fail_rate"], 0)
        self.assertAlmostEqual(got["fail_upper_95"], .451, places=3)

    def test_upper_bound_with_failures(self):
        got = report([r("normal", k, status="fail" if k == 0 else "pass") for k in range(5)])["modes"]["normal"]
        self.assertEqual(got["fail_rate"], .2)
        self.assertAlmostEqual(got["fail_upper_95"], .657, places=3)

    def test_pairs_are_matched_on_scenario_people_and_repeat(self):
        rows = [r("normal", 0, wer=.1), r("ptt", 0, wer=.1), r("normal", 1, wer=.3), r("ptt", 2, wer=.9),
                r("normal", 0, scenario="turn_taking", people=2, wer=.2), r("ptt", 0, scenario="turn_taking", wer=.9)]
        got = report(rows)["paired"]["ptt"]["wer"]
        self.assertEqual(got["pairs"], 1)
        self.assertEqual(got["diff"], 0)

    def test_intervals_come_from_the_pair_differences(self):
        rows = []
        for k, d in enumerate([0, .01, .02, .03, .04, .05, .06, .07, .08, .09]):
            rows += [r("normal", k, wer=.1), r("ptt", k, wer=.1 + d)]
        got = report(rows)["paired"]["ptt"]["wer"]
        self.assertAlmostEqual(got["diff"], .045)
        self.assertLess(got["low"], got["diff"])
        self.assertGreater(got["high"], got["diff"])
        self.assertEqual(got, report(rows)["paired"]["ptt"]["wer"])  # same seed, same answer

    def test_gate_outcome_compares_the_whole_interval_with_the_margin(self):
        same = [x for k in range(8) for x in (r("normal", k), r("ptt", k))]
        self.assertEqual(report(same)["paired"]["ptt"]["wer"]["outcome"], "within")
        worse = [x for k in range(8) for x in (r("normal", k), r("ptt", k, wer=.2))]
        self.assertEqual(report(worse)["paired"]["ptt"]["wer"]["outcome"], "worse")
        mixed = [x for k, w in enumerate([.02, .02, .02, .02, .06, .06, .06, .06])
                 for x in (r("normal", k), r("ptt", k, wer=w))]
        self.assertEqual(report(mixed)["paired"]["ptt"]["wer"]["outcome"], "unclear")

    def test_judged_quality_gates_downward_when_present(self):
        rows = [x for k in range(8) for x in (r("normal", k, judged=4.5), r("ptt", k, judged=3.5))]
        self.assertEqual(report(rows)["paired"]["ptt"]["judged"]["outcome"], "worse")
        rows = [x for k in range(8) for x in (r("normal", k, judged=4.5), r("ptt", k, judged=4.5))]
        self.assertEqual(report(rows)["paired"]["ptt"]["judged"]["outcome"], "within")
        rows = [x for k in range(8) for x in (r("normal", k), r("ptt", k))]
        self.assertNotIn("judged", report(rows)["paired"]["ptt"])

    def test_reply_gate_compares_ptt_from_release_with_normal_from_last_frame(self):
        rows = [x for k in range(8) for x in (r("normal", k), r("ptt", k, reply_p50_s=1.4, reply_release_p50_s=1.0))]
        got = report(rows)["paired"]["ptt"]
        self.assertAlmostEqual(got["reply_p50_s"]["diff"], .4)
        self.assertIsNone(got["reply_p50_s"]["margin"])
        self.assertEqual(got["reply_gate_s"]["outcome"], "within")

    def test_stuck_floors_are_counted_on_valid_runs(self):
        rows = [r("normal", 0), r("baton", 0, status="fail", stuck_floors=2),
                r("baton", 1, status="invalid", stuck_floors=9)]
        self.assertEqual(report(rows)["modes"]["baton"]["stuck_floors"], 2)

    def test_cells_normal_has_no_part_in_are_rates_only(self):
        rows = [r("ptt", 0, scenario="walked_away", people=2), r("baton", 0, scenario="walked_away", people=2)]
        got = report(rows)
        self.assertEqual(got["modes"]["baton"]["valid"], 1)
        self.assertEqual(got["paired"]["baton"], {})

    def test_row_joins_run_verdict_and_metrics(self):
        got = row({"scenario": "late_release", "people": 1, "repeat": 0, "mode": "ptt"}, good("ptt", "late_release", 1))
        self.assertEqual((got["status"], got["repeat"], got["turns_answered"]), ("pass", 0, 1.0))
        self.assertEqual(report([got])["modes"]["ptt"]["valid"], 1)


if __name__ == "__main__":
    unittest.main()
