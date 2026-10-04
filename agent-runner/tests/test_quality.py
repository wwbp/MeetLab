"""Quality scores from a load test's own rooms (quality.py): what the bot heard against what
was said, and how long its answers ran."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quality import hearing, reply_lengths, score_rooms, word_errors, words  # noqa: E402


class TestWords(unittest.TestCase):
    def test_case_and_punctuation_do_not_count(self):
        self.assertEqual(words("Why does THAT matter, more than the others?"),
                         ["why", "does", "that", "matter", "more", "than", "the", "others"])

    def test_british_and_american_spelling_are_the_same_word(self):
        # The scripts say "prioritise"; speech-to-text writes "prioritize" (local rehearsal 2026-10-03).
        self.assertEqual(word_errors("prioritise and summarise", "prioritize and summarize"), 0)

    def test_apostrophes_stay_inside_words(self):
        self.assertEqual(words("Don't stop."), ["don't", "stop"])


class TestWordErrors(unittest.TestCase):
    def test_identical_is_no_error(self):
        self.assertEqual(word_errors("the cat sat", "The cat sat."), 0)

    def test_a_substitution_a_deletion_and_an_insertion_each_count_one(self):
        self.assertEqual(word_errors("the cat sat down", "a cat sat"), 2)        # the→a, down missing
        self.assertEqual(word_errors("the cat sat", "the cat sat sat"), 1)

    def test_nothing_heard_is_every_word_missed(self):
        self.assertEqual(word_errors("the cat sat", ""), 3)


class TestHearing(unittest.TestCase):
    # Two sentences, current from t=0 and t=20 (the next one in that room starts at 40);
    # turns are stamped when they complete.
    said = [(0.0, 20.0, "What should we prioritise this quarter?"), (20.0, 40.0, "Why does that matter?")]

    def test_each_sentence_is_scored_against_the_turns_stored_while_it_was_current(self):
        h = hearing(self.said, [(3.0, "What should we prioritize this order?"), (23.0, "Why does that matter?")])
        self.assertEqual((h["sentences"], h["words"], h["errors"]), (2, 10, 1))  # quarter → order
        self.assertAlmostEqual(h["wer"], 0.1)
        self.assertEqual((h["fragmented"], h["missed"]), (0, 0))

    def test_a_sentence_stored_as_two_turns_is_fragmented_but_its_words_still_count(self):
        h = hearing(self.said, [(2.0, "What should we"), (4.0, "prioritise this quarter?"), (23.0, "Why does that matter?")])
        self.assertEqual((h["fragmented"], h["errors"]), (1, 0))

    def test_a_sentence_with_no_turn_is_missed(self):
        h = hearing(self.said, [(23.0, "Why does that matter?")])
        self.assertEqual((h["missed"], h["errors"]), (1, 6))

    def test_a_turn_after_the_sentences_end_is_not_its_own(self):
        h = hearing(self.said, [(3.0, "What should we prioritise this quarter?"), (23.0, "Why does that matter?"), (45.0, "Next topic")])
        self.assertEqual(h["errors"], 0)

    def test_nothing_said_is_nothing_to_score(self):
        self.assertEqual(hearing([], [])["wer"], None)


class TestReplyLengths(unittest.TestCase):
    def test_how_long_the_bot_talks_for(self):
        r = reply_lengths(["Sure.", "One two three four five.", " ".join(["word"] * 60)])
        self.assertEqual((r["replies"], r["p50_words"], r["max_words"], r["over_40_words"]), (3, 5, 60, 1))


class TestScoreRooms(unittest.TestCase):
    """A step's quality, across its rooms, from what the load test saved for each room."""
    rooms = [
        {"said": [[10.0, "What should we prioritise?"], [30.0, "Why?"]],
         "turns": [{"bot": True, "ts": 5.0, "text": "Hello, I am the greeter."},     # before anyone spoke: not a reply
                   {"bot": False, "ts": 13.0, "text": "What should we prioritise?"},
                   {"bot": True, "ts": 14.0, "text": "Hiring first."},
                   {"bot": False, "ts": 33.0, "text": "Why?"},
                   {"bot": True, "ts": 34.0, "text": "Because the team is stretched thin."}]},
        {"said": [[12.0, "Thanks."]],
         "turns": [{"bot": False, "ts": 14.0, "text": "Tanks."}, {"bot": True, "ts": 15.0, "text": "Welcome."}]},
    ]

    def test_hearing_and_replies_across_rooms_within_a_window(self):
        q = score_rooms(self.rooms, 0, 100)
        self.assertEqual((q["hearing"]["sentences"], q["hearing"]["errors"], q["hearing"]["words"]), (3, 1, 6))
        self.assertEqual(q["replies"]["replies"], 3)  # the greeting is not a reply

    def test_only_what_was_said_in_the_window_counts(self):
        q = score_rooms(self.rooms, 20, 100)  # the step from t=20: room 0's second sentence only
        self.assertEqual((q["hearing"]["sentences"], q["hearing"]["errors"], q["replies"]["replies"]), (1, 0, 1))


if __name__ == "__main__":
    unittest.main()


class TestVoice(unittest.TestCase):
    """How the bot sounds: each recorded reply matched to the text the bot meant to say."""

    rooms = [{"room": 0, "said": [[10.0, "Hello?"], [30.0, "Why?"]],
              "turns": [{"bot": False, "ts": 12.0, "text": "Hello?"},
                        {"bot": True, "ts": 18.0, "text": "Hi there, how can I help?"},
                        {"bot": False, "ts": 32.0, "text": "Why?"},
                        {"bot": True, "ts": 40.0, "text": "Because it matters."}]}]

    def test_a_clip_is_the_reply_stored_after_the_sentence_it_answered(self):
        from quality import reply_for
        self.assertEqual(reply_for(self.rooms, room=0, said_at=30.0), "Because it matters.")
        self.assertIsNone(reply_for(self.rooms, room=1, said_at=30.0))

    def test_intelligibility_is_the_word_error_rate_of_what_a_listener_hears(self):
        from quality import voice
        v = voice([("Hi there, how can I help?", "Hi there how can I help", 4.1),
                   ("Because it matters.", "Because it scatters.", 3.5)])
        self.assertEqual((v["clips"], v["errors"], v["words"]), (2, 1, 9))
        self.assertAlmostEqual(v["wer"], 1 / 9)
        self.assertAlmostEqual(v["naturalness"], 3.8)

    def test_no_clips_is_nothing_to_score(self):
        from quality import voice
        self.assertEqual(voice([])["wer"], None)

    def test_which_turns_are_recorded(self):
        from quality import record_turn
        # A few per room, from the first rooms only: enough to score, little to upload.
        self.assertEqual([n for n in range(12) if record_turn(room=0, n=n)], [1, 4, 7, 10])
        self.assertFalse(record_turn(room=10, n=1))


class TestCleanClips(unittest.TestCase):
    """A clip is scored against the one reply it holds; a clip holding parts of several (the bot
    answered half a paused sentence) is skipped, not scored as unintelligible (L6 run 2026-10-03)."""

    rooms = [{"room": 0, "said": [], "turns": [
        {"bot": True, "ts": 100.0, "text": "Earlier reply."},
        {"bot": True, "ts": 112.0, "text": "The reply in the clip."},
        {"bot": True, "ts": 130.0, "text": "A later reply."}]}]

    def test_the_one_reply_that_finished_inside_the_clip(self):
        from quality import clip_reply
        self.assertEqual(clip_reply(self.rooms, {"room": 0, "start": 105.0, "end": 111.0}), "The reply in the clip.")

    def test_a_clip_holding_two_replies_is_not_scored(self):
        from quality import clip_reply
        self.assertIsNone(clip_reply(self.rooms, {"room": 0, "start": 99.0, "end": 111.0}))

    def test_a_clip_with_no_finished_reply_is_not_scored(self):
        from quality import clip_reply
        self.assertIsNone(clip_reply(self.rooms, {"room": 0, "start": 113.0, "end": 120.0}))
