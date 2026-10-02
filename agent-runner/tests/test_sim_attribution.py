"""The verdict of the three-speaker attribution run (sim_attribution.py, diagnosis F11)."""
import unittest

from tests.sim_attribution import heard_as, verdict

LINES = {
    "a": "What should we prioritise for the leadership role this quarter?",
    "b": "We are seeing higher latency on the transcription service since Tuesday.",
    "c": "Can you explain what a retrieval augmented generation system does?",
}


class VerdictTests(unittest.TestCase):
    def test_each_speaker_with_their_own_words_is_clean(self):
        stored = [("a", "what should we prioritize for the leadership role this quarter"),
                  ("b", "we are seeing higher latency on the transcription service since tuesday"),
                  ("c", "can you explain what a retrieval augmented generation system does")]
        self.assertEqual(verdict(LINES, stored), [])

    def test_words_stored_under_the_wrong_speaker(self):
        stored = [("a", "we are seeing higher latency on the transcription service since tuesday")]
        self.assertIn("a: stored b's words", verdict(LINES, stored))

    def test_two_speakers_merged_into_one_turn(self):
        stored = [("a", LINES["a"] + " " + LINES["c"])]
        self.assertIn("a: one turn holds a's and c's words", verdict(LINES, stored))

    def test_a_speaker_whose_words_never_arrived(self):
        stored = [("a", LINES["a"]), ("b", LINES["b"])]
        self.assertIn("c: never transcribed", verdict(LINES, stored))


class HeardAsTests(unittest.TestCase):
    """What the LLM is told: each labelled piece of a stored turn, by its label."""

    def test_splits_a_turn_at_each_speaker_label(self):
        who = {"sim_a_8fbb": "a", "sim_b_1153": "b"}
        text = "sim_a_8fbb: Seeing higher latency since Tuesday. sim_b_1153: Service since Tuesday."
        self.assertEqual(heard_as("b", text, who),
                         [("a", "Seeing higher latency since Tuesday."), ("b", "Service since Tuesday.")])

    def test_an_unlabelled_turn_belongs_to_its_speaker(self):
        self.assertEqual(heard_as("c", "plain words", {}), [("c", "plain words")])


if __name__ == "__main__":
    unittest.main()
