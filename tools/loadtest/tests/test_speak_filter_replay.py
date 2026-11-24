"""Replaying the speak filter's scenarios through a classifier (speak_filter_replay.py): what
is counted and how, against a fake classifier with scripted probabilities. Nothing here uses
the network; the paid run is made by hand."""
import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from pipecat.classifiers.base_classifier import BaseClassifier, ClassifierError, YesNoResult
from pipecat.metrics.metrics import LLMTokenUsage

from meetlab_loadtest.speak_filter_replay import ReplayStopped, key_from, main, replay, report, stats, table
from meetlab_loadtest.speak_filter_scenarios import load


def scenario(id, category, expected, expected_rule, last, **extra):
    return {"id": id, "category": category, "description": "d", "bot_name": "Juniper",
            "participants": ["Mara", "Teo"],
            "transcript": [{"speaker": "Mara", "text": "a"}, {"speaker": "Juniper", "text": "b"},
                           {"speaker": "Teo", "text": last}],
            "active_rules": ["r1", "r2"], "expected": expected, "expected_rule": expected_rule, "note": "n", **extra}


DATA = {
    "rules": [{"id": "r1", "instructions": "Rule one."}, {"id": "r2", "instructions": "Rule two."}],
    "scenarios": [
        scenario("caught", "peers", "silent", "r1", "caught"),
        scenario("spoke-over", "peers", "silent", "r1", "spoke-over"),
        scenario("wrong-rule", "peers", "silent", "r1", "wrong-rule"),
        scenario("replied", "requests", "speak", "none", "replied"),
        scenario("blocked", "requests", "speak", "none", "blocked"),
        scenario("named", "requests", "speak", "direct_address", "Juniper, what do you think?"),
        scenario("flips", "requests", "speak", "none", "flips"),
    ],
}
# What the fake answers, by the last turn's words: one answer for every run, or one per run.
SCRIPT = {
    "caught": {"r1": 0.9, "r2": 0.1},
    "spoke-over": {"r1": 0.2, "r2": 0.1},
    "wrong-rule": {"r1": 0.1, "r2": 0.7},
    "replied": {"r1": 0.1, "r2": 0.0},
    "blocked": {"r1": 0.3, "r2": 0.8},
    "flips": [{"r1": 0.6, "r2": 0.0}, {"r1": 0.4, "r2": 0.0}, {"r1": 0.55, "r2": 0.0}],
}


class FakeClassifier(BaseClassifier):
    def __init__(self, script=SCRIPT, *, error=None):
        super().__init__()
        self.script, self.error, self.calls, self.asked = script, error, 0, {}

    async def _ask(self, state, questions, **_):
        self.calls += 1
        if self.error:
            raise self.error
        last = json.loads(state.splitlines()[-1])["text"]
        self.asked[last] = self.asked.get(last, 0) + 1
        answer = self.script[last]
        answer = answer[self.asked[last] - 1] if isinstance(answer, list) else answer
        return ({name: YesNoResult(probability=answer[name]) for name in questions},
                LLMTokenUsage(prompt_tokens=100, completion_tokens=7, total_tokens=107))


def run(coroutine):
    return asyncio.run(coroutine)


def replayed(runs=3, threshold=0.5, **kwargs):
    fake = FakeClassifier(**kwargs)
    return fake, run(replay(DATA, fake, runs=runs, threshold=threshold, max_calls=100))


class TestReplay(unittest.TestCase):
    def test_every_scenario_goes_through_decide_once_per_run(self):
        fake, records = replayed(runs=3)
        self.assertEqual(len(records), 3 * 7)
        self.assertEqual([r["id"] for r in records[:7]], [s["id"] for s in DATA["scenarios"]])
        self.assertEqual({r["run"] for r in records}, {1, 2, 3})
        self.assertEqual(fake.calls, 3 * 6, "the scenario that names the bot never reaches the classifier")

    def test_a_record_is_the_decision(self):
        _, records = replayed(runs=1)
        by_id = {r["id"]: r for r in records}
        self.assertEqual({k: by_id["caught"][k] for k in ("speak", "reason", "blocked_by", "probabilities", "error")},
                         {"speak": False, "reason": "blocked", "blocked_by": ["r1"],
                          "probabilities": {"r1": 0.9, "r2": 0.1}, "error": None})
        self.assertGreaterEqual(by_id["caught"]["elapsed_ms"], 0)
        self.assertEqual((by_id["named"]["reason"], by_id["named"]["elapsed_ms"]), ("direct_address", None))

    def test_only_a_scenarios_active_rules_are_asked_and_its_batch_is_passed(self):
        data = {"rules": DATA["rules"], "scenarios": [
            scenario("off", "c", "speak", "none", "caught", active_rules=[]),
            scenario("one", "c", "speak", "none", "wrong-rule", active_rules=["r1"]),
            {**scenario("batch", "c", "speak", "direct_address", "caught", batch_size=2),
             "transcript": [{"speaker": "Mara", "text": "a"}, {"speaker": "Mara", "text": "Juniper, can you help?"},
                            {"speaker": "Teo", "text": "caught"}]},
        ]}
        fake = FakeClassifier()
        records = run(replay(data, fake, runs=1, threshold=0.5, max_calls=10))
        self.assertEqual([(r["reason"], r["probabilities"]) for r in records],
                         [("disabled", {}), ("no_rule_fired", {"r1": 0.1}), ("direct_address", {})])
        self.assertEqual(fake.calls, 1)

    def test_more_calls_than_the_cap_allows_is_refused_before_any_call(self):
        fake = FakeClassifier()
        with self.assertRaises(ValueError) as refused:
            run(replay(DATA, fake, runs=3, threshold=0.5, max_calls=20))
        self.assertIn("21", str(refused.exception))
        self.assertEqual(fake.calls, 0)
        self.assertEqual(len(run(replay(DATA, fake, runs=3, threshold=0.5, max_calls=21))), 21)

    def test_a_first_call_that_fails_stops_the_run(self):
        """An account or model-access error must not be retried across the whole set."""
        fake = FakeClassifier(error=ClassifierError("OpenAI Decisions rejected the request: HTTP 403: no access"))
        with self.assertRaises(ReplayStopped) as stopped:
            run(replay(DATA, fake, runs=3, threshold=0.5, max_calls=100))
        self.assertIn("HTTP 403: no access", str(stopped.exception))
        self.assertEqual(fake.calls, 1)

    def test_a_later_failure_is_recorded_and_the_run_goes_on(self):
        class FailsOnce(FakeClassifier):
            async def _ask(self, state, questions, **_):
                if self.calls == 2:
                    self.calls += 1
                    raise ClassifierError("busy")
                return await super()._ask(state, questions)
        records = run(replay(DATA, FailsOnce(), runs=1, threshold=0.5, max_calls=100))
        self.assertEqual([r["reason"] for r in records].count("classifier_error"), 1)
        self.assertEqual(len(records), 7)


class TestTheCommittedSetReplayed(unittest.TestCase):
    """The committed scenarios against a classifier that would block everything (1.0 for every
    rule): what is decided before the classifier is asked."""

    @classmethod
    def setUpClass(cls):
        class BlocksAll(FakeClassifier):
            async def _ask(self, state, questions, **_):
                self.calls += 1
                return {name: YesNoResult(probability=1.0) for name in questions}, None
        cls.data, cls.fake = load(), BlocksAll()
        cls.records = {r["id"]: r for r in run(replay(cls.data, cls.fake, runs=1, threshold=0.5, max_calls=100))}

    def test_asked_by_name_then_answered_by_a_person_the_bot_is_owed_a_reply(self):
        for i in ("bqh-01", "bqh-02", "bqh-03"):
            r = self.records[i]
            self.assertEqual((r["speak"], r["reason"], r["elapsed_ms"], r["probabilities"]), (True, "owed_reply", None, {}), i)

    def test_no_scenario_expecting_silence_is_owed_a_reply(self):
        expected = {s["id"]: s["expected"] for s in self.data["scenarios"]}
        self.assertEqual([i for i, r in self.records.items() if r["reason"] == "owed_reply" and expected[i] == "silent"], [])
        self.assertEqual(sorted(i for i, r in self.records.items() if r["reason"] == "owed_reply"), ["bqh-01", "bqh-02", "bqh-03"])

    def test_only_what_the_name_did_not_decide_reaches_the_classifier(self):
        asked = sum(r["elapsed_ms"] is not None for r in self.records.values())
        self.assertEqual(asked, self.fake.calls)
        self.assertEqual(asked, len(self.records) - sum(r["reason"] in ("direct_address", "owed_reply", "disabled")
                                                        for r in self.records.values()))


class TestReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.records = replayed(runs=3)
        cls.report = report(DATA, cls.records, threshold=0.5)

    def test_the_two_errors_are_counted_apart(self):
        """False interruption: expected silent, the bot spoke. Missed turn: expected speak, blocked.
        Counted over every run: 3 silent scenarios and 4 speak scenarios, 3 runs each."""
        self.assertEqual(self.report["errors"]["overall"], {
            "silent_expected": 9, "false_interruptions": 3, "false_interruption_rate": 0.3333,
            "speak_expected": 12, "missed_turns": 5, "missed_turn_rate": 0.4167,
        })

    def test_per_category(self):
        by = self.report["errors"]["by_category"]
        self.assertEqual(list(by), ["peers", "requests"])
        self.assertEqual((by["peers"]["false_interruptions"], by["peers"]["silent_expected"], by["peers"]["speak_expected"]), (3, 9, 0))
        self.assertEqual((by["peers"]["false_interruption_rate"], by["peers"]["missed_turn_rate"]), (0.3333, None))
        self.assertEqual((by["requests"]["missed_turns"], by["requests"]["speak_expected"]), (5, 12))

    def test_per_rule(self):
        """A rule's false interruptions: silent cases it should have blocked where the bot spoke.
        Its missed turns: speak cases it blocked. Blocking a silent case that was another
        rule's is right for the wrong reason, counted on its own."""
        self.assertEqual(self.report["errors"]["by_rule"], {
            "r1": {"should_block": 9, "false_interruptions": 3, "missed_turns": 2, "blocked_for_another_rule": 0},
            "r2": {"should_block": 0, "false_interruptions": 0, "missed_turns": 3, "blocked_for_another_rule": 3},
        })

    def test_every_scenarios_decisions_and_probabilities_in_every_run(self):
        rows = {s["id"]: s for s in self.report["scenarios"]}
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows["flips"], {
            "id": "flips", "category": "requests", "expected": "speak", "expected_rule": "none",
            "decisions": ["silent", "speak", "silent"], "reasons": ["blocked", "no_rule_fired", "blocked"],
            "blocked_by": [["r1"], [], ["r1"]], "flipped": True, "correct": [False, True, False],
            "probabilities": {"r1": [0.6, 0.4, 0.55], "r2": [0.0, 0.0, 0.0]}, "spread": {"r1": 0.2, "r2": 0.0},
        })
        self.assertEqual((rows["named"]["decisions"], rows["named"]["probabilities"], rows["named"]["flipped"]),
                         (["speak"] * 3, {}, False))

    def test_flips_are_listed(self):
        self.assertEqual(self.report["flips"], ["flips"])

    def test_counts_and_settings(self):
        self.assertEqual({k: self.report[k] for k in ("runs", "threshold", "scenarios_count", "calls", "classifier_errors")},
                         {"runs": 3, "threshold": 0.5, "scenarios_count": 7, "calls": 18, "classifier_errors": 0})
        self.assertEqual(self.report["latency_ms"]["n"], 18)

    def test_a_threshold_sweep_from_the_recorded_probabilities(self):
        fake, records = replayed(runs=3)
        calls = fake.calls
        sweep = report(DATA, records, threshold=0.5)["sweep"]
        self.assertEqual(fake.calls, calls, "no extra calls")
        self.assertEqual([row["threshold"] for row in sweep["all_rules"]], [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])
        at = {row["threshold"]: row for row in sweep["all_rules"]}
        self.assertEqual({k: at[0.5][k] for k in ("false_interruptions", "missed_turns")},
                         {"false_interruptions": 3, "missed_turns": 5})
        # 0.3: nothing with a probability of 0.3 or more gets through; the bot named still speaks.
        self.assertEqual((at[0.3]["false_interruptions"], at[0.3]["missed_turns"]), (3, 6))
        # 0.9: only "caught" (0.9) is still blocked.
        self.assertEqual((at[0.9]["false_interruptions"], at[0.9]["missed_turns"]), (6, 0))
        self.assertEqual((at[0.9]["false_interruption_rate"], at[0.9]["missed_turn_rate"]), (0.6667, 0.0))

    def test_the_sweep_per_rule_alone(self):
        """What one rule would do by itself at each threshold: of the cases that are its to
        block, how many it blocks; of the cases where the bot should speak, how many it blocks."""
        r1 = {row["threshold"]: row for row in report(DATA, self.records, threshold=0.5)["sweep"]["by_rule"]["r1"]}
        self.assertEqual(r1[0.5], {"threshold": 0.5, "should_block": 9, "blocks_own": 3, "blocks_speak": 2,
                                   "speak_asked": 9})
        self.assertEqual((r1[0.3]["blocks_own"], r1[0.3]["blocks_speak"]), (3, 6))
        self.assertEqual((r1[0.9]["blocks_own"], r1[0.9]["blocks_speak"]), (3, 0))

    def test_a_classifier_error_counts_as_speaking_and_is_counted(self):
        records = [dict(r) for r in self.records]
        records[0].update(speak=True, reason="classifier_error", blocked_by=[], probabilities={}, error="busy")
        changed = report(DATA, records, threshold=0.5)
        self.assertEqual(changed["classifier_errors"], 1)
        self.assertEqual(changed["errors"]["overall"]["false_interruptions"], 4)

    def test_stats(self):
        self.assertEqual(stats([400.0, 100.0, 300.0, 200.0]), {"n": 4, "min": 100.0, "median": 250.0, "p95": 400.0, "max": 400.0})
        self.assertEqual(stats([float(n) for n in range(1, 101)])["p95"], 95.0)
        self.assertEqual(stats([]), {"n": 0})

    def test_the_table_names_both_errors_and_every_category(self):
        text = table({**self.report, "model": "fake", "tokens": {"input": 1800, "output": 126}})
        for word in ("false interruptions", "missed turns", "peers", "requests", "r1", "r2", "flips", "median", "0.9", "1800"):
            self.assertIn(word, text)


class TestMain(unittest.TestCase):
    def main(self, *argv, classifier=None):
        made = []

        async def make(args):
            made.append(args)
            return classifier or FakeClassifier()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(list(argv), make_classifier=make)
        return code, out.getvalue(), made

    def test_it_refuses_without_the_flag_that_says_it_costs_money(self):
        code, said, made = self.main("--max-calls", "200")
        self.assertEqual((code, made), (2, []))
        self.assertIn("--i-understand-this-costs-money", said)

    def test_it_refuses_without_a_cap(self):
        code, said, made = self.main("--i-understand-this-costs-money")
        self.assertEqual((code, made), (2, []))
        self.assertIn("--max-calls", said)

    def test_it_refuses_a_cap_below_what_the_run_needs_before_building_a_classifier(self):
        code, said, made = self.main("--i-understand-this-costs-money", "--max-calls", "5")
        self.assertEqual((code, made), (2, []))
        self.assertIn("--max-calls", said)

    def test_a_first_call_that_fails_is_reported_as_it_came_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            code, said, _ = self.main("--i-understand-this-costs-money", "--max-calls", "400", "--out", str(out),
                                      classifier=FakeClassifier(error=ClassifierError("HTTP 403: model not available")))
            self.assertEqual(code, 1)
            self.assertIn("HTTP 403: model not available", said)
            self.assertFalse(out.exists())

    def test_a_run_writes_the_report_and_prints_the_table(self):
        class Flat(FakeClassifier):
            """0.0 for every rule: the committed scenario set, every reply let through."""
            async def _ask(self, state, questions, **_):
                self.calls += 1
                return ({name: YesNoResult(probability=0.0) for name in questions},
                        LLMTokenUsage(prompt_tokens=100, completion_tokens=7, total_tokens=107))
        fake = Flat()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            code, said, _ = self.main("--i-understand-this-costs-money", "--max-calls", "400", "--runs", "2",
                                      "--out", str(out), classifier=fake)
            written = json.loads(out.read_text())
        self.assertEqual(code, 0)
        self.assertIn("false interruptions", said)
        self.assertEqual((written["runs"], written["calls"]), (2, fake.calls))
        self.assertEqual(written["tokens"], {"input": 100 * fake.calls, "output": 7 * fake.calls})
        self.assertEqual(written["errors"]["overall"]["missed_turns"], 0)
        self.assertEqual(written["errors"]["overall"]["false_interruption_rate"], 1.0)
        self.assertNotIn("folding table", json.dumps(written), "ids and numbers only, none of the words")


class TestKey(unittest.TestCase):
    def test_only_the_one_line_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text('# comment\nOTHER_SECRET=nope\nexport OPENAI_API_KEY="sk-test-123"\nLIVEKIT_API_SECRET=no\n')
            before = dict(os.environ)
            self.assertEqual(key_from(env), "sk-test-123")
            self.assertEqual(dict(os.environ), before, "reading the file sets nothing")
            env.write_text("OPENAI_API_KEY=sk-plain # the key\n")
            self.assertEqual(key_from(env), "sk-plain")
            env.write_text("OTHER=1\n")
            self.assertIsNone(key_from(env))


if __name__ == "__main__":
    unittest.main()
