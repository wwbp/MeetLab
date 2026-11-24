"""The speak filter's scenario set (speak_filter_scenarios.py): written conversations ending
where the bot must decide whether to reply, each with the decision expected of it. Data only;
no model is called here."""
import copy
import unittest
from collections import Counter

from meetlab_loadtest.speak_filter_scenarios import (
    CATEGORIES, MAX_SPEAK_SHARE, MIN_SPEAK_SHARE, load, render_state, validate,
)

# The owner's starter rules, verbatim (2026-11-23).
STARTER_RULES = [
    "The last speaker was talking to another participant, not to the assistant.",
    "The assistant was mentioned but not asked anything.",
    "The speaker has not finished their thought.",
    "Another participant has already answered the question.",
    "The group is in the middle of agreeing on something.",
]


def minimal():
    """The smallest valid set: every category twice, half speak and half silent."""
    scenarios = []
    for i, category in enumerate(c for c in CATEGORIES for _ in range(2)):
        silent = i % 2 == 0
        scenarios.append({
            "id": f"s{i}", "category": category, "description": "d", "bot_name": "Juniper",
            "participants": ["Mara", "Teo"],
            "transcript": [{"speaker": "Mara", "text": "a"}, {"speaker": "Juniper", "text": "b"},
                           {"speaker": "Teo", "text": "c"}],
            "active_rules": ["r1"], "expected": "silent" if silent else "speak",
            "expected_rule": "r1" if silent else "none", "note": "n",
        })
    return {"rules": [{"id": "r1", "instructions": "A rule."}], "scenarios": scenarios}


class TestTheCommittedSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load()

    def test_it_is_valid(self):
        self.assertEqual(validate(self.data), [])

    def test_about_forty_scenarios(self):
        self.assertTrue(36 <= len(self.data["scenarios"]) <= 50, len(self.data["scenarios"]))

    def test_the_rules_are_the_owners_starter_rules_verbatim(self):
        self.assertEqual([r["instructions"] for r in self.data["rules"]], STARTER_RULES)

    def test_every_starter_rule_is_expected_to_block_somewhere(self):
        blocking = {s["expected_rule"] for s in self.data["scenarios"] if s["expected"] == "silent"}
        self.assertEqual(blocking, {r["id"] for r in self.data["rules"]})

    def test_cases_left_to_the_owner_say_what_is_asked(self):
        asked = [s for s in self.data["scenarios"] if s.get("needs_owner_decision")]
        self.assertTrue(asked, "a human answering a question put to the bot is the owner's call")
        self.assertTrue({s["category"] for s in asked} >= {"bot_question_human_answered"})
        self.assertTrue(all(s["category"] != "bot_question_human_answered" or s.get("needs_owner_decision")
                            for s in self.data["scenarios"]))


class TestValidate(unittest.TestCase):
    def problems(self, change):
        data = minimal()
        change(data)
        return validate(data)

    def assertRejects(self, change, expect):
        problems = self.problems(change)
        self.assertTrue(any(expect in p for p in problems), f"{expect!r} not in {problems}")

    def test_the_minimal_set_is_valid(self):
        self.assertEqual(validate(minimal()), [])

    def test_validate_changes_nothing(self):
        data = minimal()
        before = copy.deepcopy(data)
        validate(data)
        self.assertEqual(data, before)

    def test_a_missing_field(self):
        for field in ("id", "category", "description", "bot_name", "participants", "transcript",
                      "active_rules", "expected", "expected_rule", "note"):
            with self.subTest(field):
                self.assertRejects(lambda d: d["scenarios"][0].pop(field), f"s0: missing {field}")

    def test_an_unknown_field_is_a_typo(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(expcted="speak"), "s0: unknown field expcted")

    def test_an_empty_text_field(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(note=" "), "s0: note")

    def test_ids_are_unique(self):
        self.assertRejects(lambda d: d["scenarios"][1].update(id="s0"), "duplicate id s0")

    def test_rule_ids_are_unique(self):
        self.assertRejects(lambda d: d["rules"].append({"id": "r1", "instructions": "Again."}), "duplicate rule r1")

    def test_an_unknown_category(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(category="other"), "s0: category")

    def test_expected_is_speak_or_silent(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(expected="maybe"), "s0: expected")

    def test_an_active_rule_must_exist(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(active_rules=["r1", "r9"]), "s0: unknown rule r9")

    def test_silent_names_the_active_rule_that_blocks(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(expected_rule="none"), "s0: expected_rule")
        self.assertRejects(lambda d: d["scenarios"][0].update(expected_rule="direct_address"), "s0: expected_rule")
        self.assertRejects(lambda d: d["scenarios"][0].update(active_rules=[]), "s0: expected_rule")

    def test_speak_names_direct_address_or_none(self):
        self.assertRejects(lambda d: d["scenarios"][1].update(expected_rule="r1"), "s1: expected_rule")
        self.assertEqual(self.problems(lambda d: d["scenarios"][1].update(expected_rule="direct_address")), [])

    def test_the_balance_is_within_the_stated_range(self):
        self.assertEqual((MIN_SPEAK_SHARE, MAX_SPEAK_SHARE), (0.4, 0.6))

        def all_silent(d):
            for s in d["scenarios"]:
                s.update(expected="silent", expected_rule="r1")
        self.assertRejects(all_silent, "speak share")

    def test_every_category_has_two_cases(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(category=d["scenarios"][2]["category"]),
                           f"category {CATEGORIES[0]}: 1 case")

    def test_the_last_speaker_is_a_participant_not_the_bot(self):
        self.assertRejects(lambda d: d["scenarios"][0]["transcript"].append({"speaker": "Juniper", "text": "x"}),
                           "s0: the last turn")

    def test_every_speaker_is_a_participant_or_the_bot(self):
        self.assertRejects(lambda d: d["scenarios"][0]["transcript"][0].update(speaker="Nobody"), "s0: turn 1 speaker")

    def test_a_participant_cannot_have_the_bots_name(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(participants=["Mara", "Juniper"]), "s0: participants")

    def test_one_to_four_participants(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(participants=["A", "B", "C", "D", "Mara", "Teo"]),
                           "s0: participants")

    def test_three_to_twelve_turns(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(transcript=d["scenarios"][0]["transcript"][1:]),
                           "s0: transcript")
        self.assertRejects(lambda d: d["scenarios"][0].update(transcript=[{"speaker": "Mara", "text": "a"}] * 13),
                           "s0: transcript")

    def test_a_turn_has_words(self):
        self.assertRejects(lambda d: d["scenarios"][0]["transcript"][0].update(text=""), "s0: turn 1 text")

    def test_optional_fields_are_typed(self):
        self.assertRejects(lambda d: d["scenarios"][0].update(silence_after_s="long"), "s0: silence_after_s")
        self.assertRejects(lambda d: d["scenarios"][0].update(needs_owner_decision="yes"), "s0: needs_owner_decision")
        self.assertRejects(lambda d: d["scenarios"][0].update(needs_owner_decision=True), "s0: owner_question")
        self.assertEqual(self.problems(lambda d: d["scenarios"][0].update(
            silence_after_s=8, needs_owner_decision=True, owner_question="Reply anyway?")), [])


class TestRenderState(unittest.TestCase):
    """The text the classifier is given. Thresholds are tuned against this exact format, so a
    change here is a change to every threshold: change this test knowingly."""

    scenario = {
        "bot_name": "Juniper",
        "transcript": [
            {"speaker": "Mara", "text": "Juniper, what's a \"p-value\"?"},
            {"speaker": "Juniper", "text": "The chance of data this extreme if nothing is going on."},
            {"speaker": "Teo", "text": "Così, okay: and…"},
        ],
    }

    def test_the_exact_format(self):
        self.assertEqual(render_state(self.scenario), (
            'The assistant is named Juniper. The conversation so far, oldest turn first, one JSON object per line:\n'
            '{"speaker": "Mara", "role": "participant", "text": "Juniper, what\'s a \\"p-value\\"?"}\n'
            '{"speaker": "Juniper", "role": "assistant", "text": "The chance of data this extreme if nothing is going on."}\n'
            '{"speaker": "Teo", "role": "participant", "text": "Così, okay: and…"}'
        ))

    def test_silence_after_the_last_turn_is_the_last_line(self):
        self.assertEqual(render_state({**self.scenario, "silence_after_s": 12}).splitlines()[-1],
                         "Nobody has spoken for 12 seconds since the last turn.")
        self.assertEqual(render_state({**self.scenario, "silence_after_s": 7.5}).splitlines()[-1],
                         "Nobody has spoken for 7.5 seconds since the last turn.")

    def test_no_silence_no_line(self):
        self.assertEqual(render_state({**self.scenario, "silence_after_s": 0}), render_state(self.scenario))

    def test_every_committed_scenario_renders(self):
        for s in load()["scenarios"]:
            self.assertEqual(len(render_state(s).splitlines()),
                             1 + len(s["transcript"]) + bool(s.get("silence_after_s")), s["id"])


if __name__ == "__main__":
    unittest.main()
