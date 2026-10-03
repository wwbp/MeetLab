"""Answer quality (judge.py): each bot reply, with the conversation before it, scored by a fixed
judge model against a fixed rubric. These tests cover everything but the model call."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from judge import RUBRIC_VERSION, cases, parse_scores, prompt, summarise  # noqa: E402

ROOM = {
    "said": [[10.0, "What should we prioritise for the leadership role this quarter?"],
             [30.0, "Why does that matter more than the others?"]],
    "turns": [
        {"bot": True, "ts": 5.0, "text": "Hi, I'm here to help."},
        {"bot": False, "ts": 13.0, "text": "What should we prioritize for the leadership role this quarter?"},
        {"bot": True, "ts": 14.0, "text": "Hiring a deputy first."},
        {"bot": False, "ts": 33.0, "text": "Why does that matter more than the others?"},
        {"bot": True, "ts": 34.0, "text": "Because the team is stretched thin."},
    ],
}


class TestCases(unittest.TestCase):
    def test_each_reply_to_a_spoken_sentence_is_a_case_with_what_came_before(self):
        c = cases([ROOM], 0, 100)
        self.assertEqual([x["reply"] for x in c], ["Hiring a deputy first.", "Because the team is stretched thin."])
        self.assertEqual(c[1]["history"][-1], ("person", "Why does that matter more than the others?"))
        self.assertIn(("bot", "Hiring a deputy first."), c[1]["history"])

    def test_follow_ups_are_marked_from_the_script(self):
        self.assertEqual([x["follow_up"] for x in cases([ROOM], 0, 100)], [False, True])

    def test_only_replies_in_the_window(self):
        self.assertEqual(len(cases([ROOM], 20, 100)), 1)

    def test_at_most_a_sample_spread_across_the_run(self):
        rooms = [ROOM] * 30
        self.assertEqual(len(cases(rooms, 0, 100, limit=10)), 10)


class TestPrompt(unittest.TestCase):
    def test_the_judge_sees_the_conversation_and_the_reply_and_asks_for_json(self):
        p = prompt(cases([ROOM], 0, 100)[1])
        self.assertIn("Person: Why does that matter more than the others?", p)
        self.assertIn("Reply to score: Because the team is stretched thin.", p)
        self.assertIn('"context"', p)
        self.assertIn("follow-up", p.lower())


class TestParse(unittest.TestCase):
    def test_scores_are_whole_numbers_from_1_to_5(self):
        self.assertEqual(parse_scores('{"answers": 4, "context": 5, "spoken": 3, "overall": 4}', follow_up=True),
                         {"answers": 4, "context": 5, "spoken": 3, "overall": 4})

    def test_context_is_only_scored_on_a_follow_up(self):
        self.assertNotIn("context", parse_scores('{"answers": 4, "context": 5, "spoken": 3, "overall": 4}', follow_up=False))

    def test_a_malformed_or_out_of_range_verdict_is_dropped(self):
        self.assertIsNone(parse_scores("not json", follow_up=False))
        self.assertIsNone(parse_scores('{"answers": 7, "spoken": 3, "overall": 4}', follow_up=False))
        self.assertIsNone(parse_scores('{"answers": 4, "overall": 4}', follow_up=False))


class TestSummarise(unittest.TestCase):
    def test_means_per_criterion_and_how_many_were_judged(self):
        s = summarise([{"answers": 4, "spoken": 2, "overall": 3}, {"answers": 5, "context": 4, "spoken": 4, "overall": 5}, None])
        self.assertEqual((s["judged"], s["dropped"]), (2, 1))
        self.assertEqual((s["answers"], s["spoken"], s["overall"], s["context"]), (4.5, 3.0, 4.0, 4.0))
        self.assertEqual(s["rubric"], RUBRIC_VERSION)


if __name__ == "__main__":
    unittest.main()
