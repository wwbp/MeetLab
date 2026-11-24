"""The speak filter's scenario set (speak_filter_scenarios.py): written conversations ending
where the bot must decide whether to reply, each with the decision expected of it. Data only;
no model is called here."""
import copy
import unittest
from collections import Counter

from meetlab_bot.speak_filter import FilterTurn, is_direct_address, owed_reply, render_state
from meetlab_loadtest.speak_filter_scenarios import (
    CATEGORIES, MAX_SPEAK_SHARE, MIN_SPEAK_SHARE, filter_turns, load, validate,
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

    def test_small_enough_to_replay_by_hand(self):
        """About forty, then eleven hard silent cases (2026-11-23): every paid run is a call per
        scenario per candidate wording, so the set stays this size."""
        self.assertTrue(36 <= len(self.data["scenarios"]) <= 64, len(self.data["scenarios"]))

    def test_hard_cases_where_the_bot_must_stay_silent(self):
        """Talk between people that sounds like a request to the bot, a question to another person
        by name, rhetorical questions, a question read aloud: at least ten, all silent, and none of
        them decided by the bot's name, so each one tests the classifier."""
        hard = [s for s in self.data["scenarios"] if s["category"] == "hard_silent"]
        self.assertGreaterEqual(len(hard), 10)
        for s in hard:
            self.assertEqual(s["expected"], "silent", s["id"])
            turns, names = filter_turns(s), [s["bot_name"]]
            self.assertFalse(is_direct_address(turns[-1].text, names), s["id"])
            self.assertFalse(owed_reply(turns, names), s["id"])

    def test_the_rules_are_the_owners_starter_rules_verbatim(self):
        self.assertEqual([r["instructions"] for r in self.data["rules"]], STARTER_RULES)

    def test_every_starter_rule_is_expected_to_block_somewhere(self):
        blocking = {s["expected_rule"] for s in self.data["scenarios"] if s["expected"] == "silent"}
        self.assertEqual(blocking, {r["id"] for r in self.data["rules"]})

    def test_no_case_is_waiting_for_the_owner(self):
        """Decided 2026-11-23: asked by name, the bot replies, even after a person answered or
        when it is only thanked."""
        self.assertEqual([s["id"] for s in self.data["scenarios"] if s.get("needs_owner_decision")], [])
        by_id = {s["id"]: s for s in self.data["scenarios"]}
        for i in ("bqh-01", "bqh-02", "bqh-03", "dab-04"):
            self.assertEqual((by_id[i]["expected"], by_id[i]["expected_rule"]), ("speak", "direct_address"), i)

    def test_people_talking_at_once_are_a_batch_and_any_turn_in_it_may_address_the_bot(self):
        batches = [s for s in self.data["scenarios"] if s["category"] == "overlapping_turns"]
        self.assertGreaterEqual(len(batches), 2)
        for s in batches:
            self.assertGreaterEqual(s["batch_size"], 2, s["id"])
            self.assertEqual((s["expected"], s["expected_rule"]), ("speak", "direct_address"), s["id"])
            earlier, last = s["transcript"][-s["batch_size"]:-1], s["transcript"][-1]
            self.assertTrue(any(s["bot_name"].lower() in t["text"].lower() for t in earlier), s["id"])
            self.assertNotIn(s["bot_name"].lower(), last["text"].lower(), s["id"])


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
                self.assertRejects(lambda d: d["scenarios"][0].pop(field), f": missing {field}")

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

    def test_a_batch_is_the_last_turns_and_none_of_them_the_bots(self):
        """batch_size: how many of the last turns were committed together (people talking at once)."""
        for bad in (0, 4, "2", True):
            self.assertRejects(lambda d: d["scenarios"][0].update(batch_size=bad), "s0: batch_size")
        self.assertRejects(lambda d: d["scenarios"][0].update(batch_size=2), "s0: batch_size")  # turn 2 is the bot's
        self.assertEqual(self.problems(lambda d: d["scenarios"][0].update(batch_size=1)), [])

        def two_people_at_once(d):
            d["scenarios"][0]["transcript"][1]["speaker"] = "Mara"
            d["scenarios"][0]["batch_size"] = 2
        self.assertEqual(self.problems(two_people_at_once), [])


class TestTheClassifiersText(unittest.TestCase):
    """A scenario is sent as the filter renders it (speak_filter.render_state, its exact format
    pinned in services/bot/tests/test_speak_filter.py): one renderer, so thresholds chosen on the
    scenarios hold for the bot."""

    scenario = {
        "bot_name": "Juniper",
        "transcript": [
            {"speaker": "Mara", "text": "Juniper, what's a \"p-value\"?"},
            {"speaker": "Juniper", "text": "The chance of data this extreme if nothing is going on."},
            {"speaker": "Teo", "text": "Così, okay: and…"},
        ],
    }

    def test_the_bots_turns_are_the_bots_name_the_rest_participants(self):
        self.assertEqual(filter_turns(self.scenario), [
            FilterTurn("Mara", "Juniper, what's a \"p-value\"?"),
            FilterTurn("Juniper", "The chance of data this extreme if nothing is going on.", is_bot=True),
            FilterTurn("Teo", "Così, okay: and…"),
        ])

    def test_every_committed_scenario_renders_with_its_silence(self):
        for s in load()["scenarios"]:
            state = render_state(filter_turns(s), s["bot_name"], silence_s=s.get("silence_after_s"))
            self.assertEqual(len(state.splitlines()), 1 + len(s["transcript"]) + bool(s.get("silence_after_s")), s["id"])


if __name__ == "__main__":
    unittest.main()
