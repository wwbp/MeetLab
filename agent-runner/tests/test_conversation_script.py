"""A conversation worth simulating, and a way to tell whether the bot answered.

The old soak harness was not a test. It published one five-word question —
"What is the capital of France?" — on a loop, forever, from a microphone that
never stopped transmitting, and then reported success based on whether the HTTP
call that started the bot returned 200.

It never subscribed to the bot's audio track. It could not hear the bot, so it
could not know whether the bot replied. On 2026-08-20 it reported "40/40 bots
started, 0 errored rooms" for a run in which the bot produced nothing but its
opening greeting. A human joined the room, heard silence, and knew more in
thirty seconds than the harness knew in six minutes.

Two things follow, and both are tested here.

**Replies are the measurement.** Not turns streamed, not sessions started. A run
where the bot never speaks is a failed run however much audio was pushed at it,
and the harness has to fail it.

**The conversation has to resemble one.** A single repeated question produces
one-word answers, exercises almost no language-model or speech-synthesis work,
and never fills a context window. Real meetings change topic, ask follow-ups,
and pause between turns.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from conversation_script import (
    CONVERSATIONS,
    Turn,
    conversation_for,
    reply_verdict,
    summarise_replies,
)


class TestTheConversationsAreRealistic(unittest.TestCase):
    def test_there_is_more_than_one_conversation(self):
        """Every room asking the same thing measures one code path repeatedly."""
        self.assertGreater(len(CONVERSATIONS), 1)

    def test_rooms_get_different_conversations(self):
        a = conversation_for(0)
        b = conversation_for(1)
        self.assertNotEqual([t.text for t in a], [t.text for t in b])

    def test_the_same_room_is_reproducible(self):
        """A run that cannot be repeated cannot be compared with the last one."""
        self.assertEqual(
            [t.text for t in conversation_for(7)],
            [t.text for t in conversation_for(7)],
        )

    def test_conversations_have_several_turns(self):
        for i in range(len(CONVERSATIONS)):
            self.assertGreaterEqual(
                len(conversation_for(i)), 4,
                "a two-turn exchange never fills a context window or exercises "
                "the model's memory of what was said earlier",
            )

    def test_turns_vary_in_length(self):
        """Uniform-length prompts produce uniform work. Real speech does not."""
        for i in range(len(CONVERSATIONS)):
            lengths = {len(t.text.split()) for t in conversation_for(i)}
            self.assertGreater(len(lengths), 2)

    def test_some_turns_are_follow_ups(self):
        """A follow-up depends on the previous answer, so it tests that context
        survived — something a repeated standalone question never does."""
        followups = sum(
            1 for i in range(len(CONVERSATIONS)) for t in conversation_for(i) if t.follow_up
        )
        self.assertGreater(followups, 0)

    def test_every_turn_expects_a_spoken_reply(self):
        """If a turn does not warrant an answer, silence is not evidence of a
        fault, and the reply-rate metric becomes meaningless."""
        for i in range(len(CONVERSATIONS)):
            for t in conversation_for(i):
                self.assertTrue(t.text.strip())
                self.assertGreater(t.expect_reply_within_s, 0)


class TestReplyDetection(unittest.TestCase):
    """Ground truth is bot audio arriving, not a log line."""

    def test_a_turn_answered_in_time_counts_as_a_reply(self):
        s = summarise_replies([(1.0, 1.6)], expected=1)
        self.assertEqual(s.replied, 1)
        self.assertEqual(s.reply_rate, 1.0)

    def test_a_turn_with_no_audio_back_is_a_miss(self):
        s = summarise_replies([(1.0, None)], expected=1)
        self.assertEqual(s.replied, 0)
        self.assertEqual(s.reply_rate, 0.0)

    def test_latency_is_measured_from_audio_not_logs(self):
        s = summarise_replies([(1.0, 1.6), (10.0, 10.2)], expected=2)
        got = sorted(s.latencies_ms)
        self.assertAlmostEqual(got[0], 200.0, places=6)
        self.assertAlmostEqual(got[1], 600.0, places=6)

    def test_missed_turns_do_not_flatter_the_latency(self):
        """Dropping unanswered turns from the average is how a broken run
        reports excellent latency — exactly what happened at 40 rooms."""
        s = summarise_replies([(1.0, 1.2), (2.0, None), (3.0, None)], expected=3)
        self.assertEqual(len(s.latencies_ms), 1)
        self.assertAlmostEqual(s.reply_rate, 1 / 3)

    def test_turns_that_never_got_asked_still_count_against_the_rate(self):
        """expected is the number of turns the script intended to speak, so a
        room that died halfway is not scored on what it managed."""
        s = summarise_replies([(1.0, 1.2)], expected=4)
        self.assertAlmostEqual(s.reply_rate, 0.25)


class TestTheVerdict(unittest.TestCase):
    """A run that the bot slept through has to fail, loudly."""

    def test_a_healthy_run_passes(self):
        ok, why = reply_verdict(reply_rate=0.98, mean_latency_ms=420)
        self.assertTrue(ok, why)

    def test_silence_fails_however_much_audio_was_streamed(self):
        ok, why = reply_verdict(reply_rate=0.02, mean_latency_ms=0)
        self.assertFalse(ok)
        self.assertIn("replied", why.lower())

    def test_slow_but_answering_fails_on_latency(self):
        ok, why = reply_verdict(reply_rate=1.0, mean_latency_ms=2400)
        self.assertFalse(ok)
        self.assertIn("latency", why.lower())

    def test_the_target_band_is_the_one_we_publish(self):
        """800ms average is the number quoted to the team, so it is the number
        the harness enforces."""
        ok, _ = reply_verdict(reply_rate=1.0, mean_latency_ms=799)
        self.assertTrue(ok)
        ok, _ = reply_verdict(reply_rate=1.0, mean_latency_ms=801)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
