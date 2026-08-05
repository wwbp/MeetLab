"""Regression tests against real production data from the worst pilot day.

Fixture: `fixtures/pilot_2026_07_30.json` — 72 measured turns across 13 sessions,
2026-07-30 15:05–16:30 UTC. This is not synthetic. It is what real participants
actually experienced, pulled from `utterances.meta` in the production database.

Two jobs:

1. **Pin the failure.** The pilot's numbers are now a committed artefact rather
   than a claim in a document. If someone reads the postmortem in six months and
   wonders whether it was really that bad, the data is here.

2. **Define the bar.** `ACCEPTANCE` below is what a fixed system must achieve on
   the same workload. `test_the_acceptance_bar_is_not_already_met` proves the bar
   has teeth by asserting today's data fails it.

The fixture is de-identified: structural features only, no transcript text, room
names, participant names or real conversation ids. Participants are real people
whose recordings were publicly exposed until 2026-08-05 (see
docs/distillation-audit.md, Iteration 3) — their words do not belong in the repo.

See docs/pilot-postmortem-2026-08.md for the analysis this data produced.
"""

import json
import os
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

FIXTURE = Path(__file__).parent / "fixtures" / "pilot_2026_07_30.json"

# Pipecat's user_turn_stop_timeout default, in ms — the constant that ends a
# fragmented turn when the aggregator has no VAD to tell it the user stopped.
TURN_STOP_TIMEOUT_MS = 5000

# What a fixed system must achieve on this same workload. Derived from
# docs/performance-tests.md: "around one second feels natural; beyond two seconds
# feels broken."
ACCEPTANCE = {
    "p50_total_ms": 1500,
    "p90_total_ms": 2500,
    "max_pct_over_3s": 10.0,
}


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(int(q * len(ordered)), len(ordered) - 1)]


def summarise(turns):
    """Reduce a set of turns to the numbers we hold the system to."""
    totals = [t["total_ms"] for t in turns]
    return {
        "p50_total_ms": percentile(totals, 0.50),
        "p90_total_ms": percentile(totals, 0.90),
        "max_total_ms": max(totals),
        "pct_over_3s": 100.0 * sum(1 for t in totals if t > 3000) / len(totals),
    }


def assert_meets_acceptance(summary, label):
    """Reusable gate — point this at a post-fix dataset to check the fix landed."""
    failures = []
    if summary["p50_total_ms"] > ACCEPTANCE["p50_total_ms"]:
        failures.append(f"p50 {summary['p50_total_ms']:.0f}ms > {ACCEPTANCE['p50_total_ms']}ms")
    if summary["p90_total_ms"] > ACCEPTANCE["p90_total_ms"]:
        failures.append(f"p90 {summary['p90_total_ms']:.0f}ms > {ACCEPTANCE['p90_total_ms']}ms")
    if summary["pct_over_3s"] > ACCEPTANCE["max_pct_over_3s"]:
        failures.append(
            f"{summary['pct_over_3s']:.1f}% of turns over 3s > {ACCEPTANCE['max_pct_over_3s']}%"
        )
    return failures


class PilotDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(FIXTURE.read_text())
        cls.turns = cls.data["turns"]

    def test_fixture_carries_no_participant_content(self):
        """Guard against someone regenerating this fixture with transcripts in it."""
        allowed = {
            "conversation", "speaker", "fragments", "chars", "stt_ms", "llm_ttft_ms",
            "tts_ttfb_ms", "sentence_agg_ms", "e2e_ms", "total_ms", "stt_spike",
            "queue_depth",
        }
        for turn in self.turns:
            self.assertEqual(set(turn) - allowed, set(), "unexpected field in pilot fixture")
        # Speaker/conversation labels must be re-indexed, never real identities.
        for turn in self.turns:
            self.assertRegex(turn["conversation"], r"^c\d{2}$")
            self.assertRegex(turn["speaker"], r"^s\d+$")

    def test_baseline_matches_the_recorded_production_failure(self):
        """The pilot really was this bad. 72 turns, 13 sessions, one afternoon."""
        s = summarise(self.turns)

        self.assertEqual(len(self.turns), 72)
        self.assertAlmostEqual(s["p50_total_ms"], 2876, delta=50)
        self.assertAlmostEqual(s["p90_total_ms"], 7026, delta=100)
        self.assertAlmostEqual(s["max_total_ms"], 11097, delta=100)
        self.assertAlmostEqual(s["pct_over_3s"], 48.6, delta=1.0)

    def test_the_acceptance_bar_is_not_already_met(self):
        """Proves ACCEPTANCE is a real gate and not trivially satisfied.

        When the RC1/RC2 fixes land, re-export this fixture from the next pilot and
        `assert_meets_acceptance` should return no failures.
        """
        failures = assert_meets_acceptance(summarise(self.turns), "2026-07-30")
        self.assertEqual(
            len(failures), 3,
            f"expected the broken baseline to miss all three thresholds, got: {failures}",
        )


class RootCauseSignatureTests(unittest.TestCase):
    """The RC1 diagnosis, asserted against the real data rather than argued."""

    @classmethod
    def setUpClass(cls):
        cls.turns = json.loads(FIXTURE.read_text())["turns"]
        cls.spikes = [t for t in cls.turns if t["stt_spike"]]

    def test_a_third_of_turns_hit_the_stt_spike_threshold(self):
        self.assertEqual(len(self.spikes), 22)
        self.assertGreater(len(self.spikes) / len(self.turns), 0.30)

    def test_spikes_cluster_at_the_5s_timeout_rather_than_forming_a_tail(self):
        """The signature that identified RC1.

        A slow provider or a loaded box produces a smooth tail. A fixed timeout
        produces a tight band. Every spike sits just above 5.0s, which is
        Pipecat's `user_turn_stop_timeout` default — the turn was not slow, it was
        *waiting on a timer*.
        """
        spike_stt = [t["stt_ms"] for t in self.spikes]
        self.assertGreaterEqual(min(spike_stt), TURN_STOP_TIMEOUT_MS)
        self.assertLess(max(spike_stt), TURN_STOP_TIMEOUT_MS * 1.4)

        # Tight enough that it cannot be a latency distribution.
        self.assertLess(statistics.pstdev(spike_stt), 400)

    def test_spikes_are_not_explained_by_queue_backlog(self):
        """Rules out the cause we originally suspected.

        The Phase-1 diagnostics existed to discriminate backlog from other causes.
        They did their job: the queue was essentially empty during every spike.
        """
        self.assertLessEqual(max(t["queue_depth"] for t in self.spikes), 1)
        self.assertLess(statistics.mean(t["queue_depth"] for t in self.spikes), 0.5)

    def test_latency_scales_with_how_much_the_participant_actually_said(self):
        """RC1's cruelty: the system is fastest with the people who say least.

        Fragmented turns are what a thinking-aloud human produces once
        `stt_endpointing_ms=100` splits on every pause.
        """
        short = [t for t in self.turns if t["fragments"] <= 1]
        long_ = [t for t in self.turns if t["fragments"] >= 5]
        self.assertTrue(short and long_, "fixture should contain both turn shapes")

        short_p50 = percentile([t["stt_ms"] for t in short], 0.5)
        long_p50 = percentile([t["stt_ms"] for t in long_], 0.5)

        self.assertLess(short_p50, 1000)
        self.assertGreater(long_p50, 4000)
        self.assertGreater(long_p50 / short_p50, 4.0)

        # ...and they said substantially more to earn that penalty.
        self.assertGreater(
            statistics.mean(t["chars"] for t in long_),
            3 * statistics.mean(t["chars"] for t in short),
        )

    def test_the_model_and_the_voice_were_never_the_problem(self):
        """Guards the postmortem's central claim: this was not an LLM/TTS issue.

        If a future regression *is* in the LLM or TTS, this test stops us
        reflexively blaming the turn timeout again.
        """
        llm = [t["llm_ttft_ms"] for t in self.turns if t["llm_ttft_ms"]]
        tts = [t["tts_ttfb_ms"] for t in self.turns if t["tts_ttfb_ms"]]

        self.assertLess(percentile(llm, 0.9), 2500)
        self.assertLess(percentile(tts, 0.9), 800)

        # STT dominated the total on the worst turns; the rest of the pipeline did not.
        worst = sorted(self.turns, key=lambda t: -t["total_ms"])[:10]
        for t in worst:
            self.assertGreater(
                t["stt_ms"] / t["total_ms"], 0.40,
                "a worst-case turn where STT was not the dominant term — re-check the diagnosis",
            )


if __name__ == "__main__":
    unittest.main()
