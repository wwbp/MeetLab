"""Turning many speakers' transcripts into one ordered conversation.

The responder worker holds the shared LLM context and the single bot voice, fed
by N listener workers over the bus. That introduces a problem the old
single-pipeline design did not have: **transcripts arrive in completion order,
not speaking order.**

Each participant's speech recognition finishes when it finishes. If Alice speaks
first but Bob's recogniser returns first, appending on arrival puts Bob above
Alice in the context, and the model reads a conversation that did not happen.
With one pipeline this could not occur, because everything was serialised through
it. Fanning out buys isolation and parallelism, and this is the bill.

Ordering is done at context-build time rather than by buffering arrivals, so it
costs nothing in latency — the utterances are sorted when the messages are
assembled, not held back waiting for stragglers.

Also folds in the speaker labelling currently done by SpeakerLabelInjector, so
the model can tell a group conversation apart ("Alice: what is X?").
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from responder_context import Utterance, build_messages, display_name


class TestDisplayName(unittest.TestCase):
    def test_the_token_name_is_preferred(self):
        self.assertEqual(display_name("alice__k3f9", "Alice Chen"), "Alice Chen")

    def test_otherwise_the_random_postfix_is_stripped(self):
        """connection-details appends __<random> to keep identities unique; it is
        not something to read out to the model."""
        self.assertEqual(display_name("alice__k3f9", None), "alice")

    def test_an_identity_with_no_postfix_is_used_as_is(self):
        self.assertEqual(display_name("alice", None), "alice")

    def test_a_blank_name_falls_back_rather_than_labelling_with_nothing(self):
        self.assertEqual(display_name("alice__k3f9", "   "), "alice")


class TestLabelling(unittest.TestCase):
    def test_each_utterance_is_attributed_to_its_speaker(self):
        msgs = build_messages([Utterance("Alice", "what is the deadline", 1.0)])
        self.assertEqual(
            msgs, [{"role": "user", "content": "Alice: what is the deadline"}]
        )

    def test_blank_utterances_are_dropped(self):
        """Empty transcripts are common and add nothing but noise to context."""
        msgs = build_messages(
            [Utterance("Alice", "  ", 1.0), Utterance("Bob", "friday", 2.0)]
        )
        self.assertEqual(len(msgs), 1)
        self.assertIn("Bob", msgs[0]["content"])

    def test_nothing_in_nothing_out(self):
        self.assertEqual(build_messages([]), [])


class TestOrdering(unittest.TestCase):
    """The problem fanning out introduced."""

    def test_utterances_are_ordered_by_when_they_were_spoken(self):
        out_of_order = [
            Utterance("Bob", "i think friday", 2.0),      # recogniser returned first
            Utterance("Alice", "what is the deadline", 1.0),  # but spoken first
        ]
        msgs = build_messages(out_of_order)
        self.assertEqual(
            [m["content"] for m in msgs],
            ["Alice: what is the deadline", "Bob: i think friday"],
            "arrival order would show Bob answering a question nobody had asked",
        )

    def test_a_three_way_exchange_reads_in_sequence(self):
        msgs = build_messages([
            Utterance("Carol", "can we confirm", 3.0),
            Utterance("Alice", "what is the deadline", 1.0),
            Utterance("Bob", "i think friday", 2.0),
        ])
        self.assertEqual(
            [m["content"].split(":")[0] for m in msgs], ["Alice", "Bob", "Carol"]
        )

    def test_simultaneous_utterances_order_deterministically(self):
        """Two speakers can share a timestamp. Any order is defensible, but it
        must be the same order every time — otherwise the same meeting produces
        different transcripts on replay."""
        a = build_messages([Utterance("Bob", "one", 1.0), Utterance("Alice", "two", 1.0)])
        b = build_messages([Utterance("Alice", "two", 1.0), Utterance("Bob", "one", 1.0)])
        self.assertEqual(a, b)

    def test_a_missing_timestamp_does_not_reorder_the_conversation(self):
        """Timestamps come from the transcript and are occasionally absent. An
        unknown time should keep its place rather than jump to the front."""
        msgs = build_messages([
            Utterance("Alice", "first", 1.0),
            Utterance("Bob", "second", None),
            Utterance("Carol", "third", 3.0),
        ])
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0]["content"], "Alice: first")


if __name__ == "__main__":
    unittest.main()
