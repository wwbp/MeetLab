"""The voice hears only the "reply" of the model's JSON answer, as it streams (reply_stream.py).
    python -m unittest tests.test_reply_stream -v   (from services/bot)
"""
import json
import unittest

from pipecat.frames.frames import (
    InterruptionFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame, TTSSpeakFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from meetlab_bot.reply_stream import ReplyStreamer, ReplyText
from meetlab_core.turns import BotReply

REPLY = 'Well, Ben said "no" \\ twice.\nCafé, naïve 😀 — {ok} [x], "reply": no.'
SAMPLE = json.dumps({"reply": REPLY, "addressed_to": ["ben__3c4d", "ana__1a2b"]})  # ASCII escapes: é, 😀
SAMPLE_RAW = json.dumps({"reply": REPLY, "addressed_to": ["ben__3c4d"]}, ensure_ascii=False)


def spoken(chunks) -> str:
    r = ReplyText()
    return "".join(r.feed(c) for c in chunks)


class ReplyTextTest(unittest.TestCase):
    def test_every_split_point_yields_exactly_the_reply(self):
        for sample in (SAMPLE, SAMPLE_RAW):
            for cut in range(len(sample) + 1):
                with self.subTest(sample=sample[:10], cut=cut):
                    self.assertEqual(spoken([sample[:cut], sample[cut:]]), REPLY)

    def test_one_character_chunks(self):
        for sample in (SAMPLE, SAMPLE_RAW):
            self.assertEqual(spoken(list(sample)), REPLY)

    def test_every_pair_of_split_points(self):
        for i in range(len(SAMPLE) + 1):
            for j in range(i, len(SAMPLE) + 1, 7):
                self.assertEqual(spoken([SAMPLE[:i], SAMPLE[i:j], SAMPLE[j:]]), REPLY)

    def test_text_comes_out_as_it_streams(self):
        r = ReplyText()
        self.assertEqual(r.feed('{"reply": "Well, that'), "Well, that")
        self.assertEqual(r.feed(' depends.", "addressed_to": []}'), " depends.")

    def test_escapes_split_across_chunks(self):
        for chunks, want in [
            (['{"reply": "a\\', '"b"'], 'a"b'),
            (['{"reply": "a\\', '\\b"'], "a\\b"),
            (['{"reply": "a\\', 'nb"'], "a\nb"),
            (['{"reply": "caf\\u00', 'e9"'], "café"),
            (['{"reply": "\\ud83d', '\\ude00!"'], "😀!"),
            (['{"reply": "\\ud83d\\', 'ude00!"'], "😀!"),
            (['{"reply": "\\ud8', '3d\\ud', 'e00"'], "😀"),
        ]:
            with self.subTest(chunks):
                self.assertEqual(spoken(chunks), want)

    def test_addressed_to_before_reply_and_whitespace_between_tokens(self):
        text = ' \n { "addressed_to" : [ "reply", "x\\"]" ] ,\n "reply"\t:  "Hi, Ana." } '
        for cut in range(len(text) + 1):
            self.assertEqual(spoken([text[:cut], text[cut:]]), "Hi, Ana.")

    def test_a_reply_key_inside_another_value_is_not_the_reply(self):
        self.assertEqual(spoken(['{"addressed_to": ["reply"], "reply": "yes"}']), "yes")

    def test_text_that_is_not_json_passes_through_unchanged(self):
        r = ReplyText()
        self.assertEqual(r.feed("  "), "")
        self.assertEqual(r.feed('Sure, "reply": {x}'), '  Sure, "reply": {x}')
        self.assertEqual(r.feed(" ok"), " ok")

    def test_text_is_the_raw_output(self):
        r = ReplyText()
        r.feed(SAMPLE[:20])
        r.feed(SAMPLE[20:])
        self.assertEqual(r.text, SAMPLE)


class ReplyStreamerTest(unittest.IsolatedAsyncioTestCase):
    async def run_streamer(self, frames, on_reply=None):
        down, _ = await run_test(ReplyStreamer(on_reply), frames_to_send=frames)
        return down

    async def test_only_the_reply_reaches_the_voice_and_on_reply_gets_the_answer(self):
        replies = []

        async def on_reply(reply):
            replies.append(reply)

        chunks = [SAMPLE[i:i + 5] for i in range(0, len(SAMPLE), 5)]
        down = await self.run_streamer([LLMFullResponseStartFrame(), *map(LLMTextFrame, chunks),
                                        LLMFullResponseEndFrame()], on_reply)
        self.assertIsInstance(down[0], LLMFullResponseStartFrame)
        self.assertIsInstance(down[-1], LLMFullResponseEndFrame)
        texts = down[1:-1]
        self.assertTrue(all(isinstance(f, LLMTextFrame) and f.text for f in texts))
        self.assertEqual("".join(f.text for f in texts), REPLY)
        self.assertEqual(replies, [BotReply(REPLY, ["ben__3c4d", "ana__1a2b"])])

    async def test_other_frames_pass_through_in_order(self):
        down = await self.run_streamer([TTSSpeakFrame("hello"), LLMFullResponseStartFrame(),
                                        LLMTextFrame('{"reply": "Hi'), TTSSpeakFrame("mid"),
                                        LLMTextFrame('", "addressed_to": []}'), LLMFullResponseEndFrame(),
                                        TTSSpeakFrame("after")])
        self.assertEqual([(type(f).__name__, getattr(f, "text", None)) for f in down],
                         [("TTSSpeakFrame", "hello"), ("LLMFullResponseStartFrame", None), ("LLMTextFrame", "Hi"),
                          ("TTSSpeakFrame", "mid"), ("LLMFullResponseEndFrame", None), ("TTSSpeakFrame", "after")])

    async def test_without_on_reply_it_still_streams(self):
        down = await self.run_streamer([LLMFullResponseStartFrame(), LLMTextFrame(SAMPLE), LLMFullResponseEndFrame()])
        self.assertEqual([f.text for f in down if isinstance(f, LLMTextFrame)], [REPLY])

    async def test_an_interruption_drops_the_reply_in_progress(self):
        replies = []

        async def on_reply(reply):
            replies.append(reply)

        await self.run_streamer([LLMFullResponseStartFrame(), LLMTextFrame('{"reply": "I was say'), SleepFrame(0.05),
                                 InterruptionFrame(), SleepFrame(0.05), LLMFullResponseEndFrame()], on_reply)
        self.assertEqual(replies, [])

    async def test_each_response_starts_fresh(self):
        replies = []

        async def on_reply(reply):
            replies.append(reply)

        down = await self.run_streamer([LLMFullResponseStartFrame(), LLMTextFrame('{"reply": "I was say'),
                                        LLMFullResponseStartFrame(), LLMTextFrame('{"reply": "New.", "addressed_to": []}'),
                                        LLMFullResponseEndFrame()], on_reply)
        self.assertEqual([f.text for f in down if isinstance(f, LLMTextFrame)], ["I was say", "New."])
        self.assertEqual(replies, [BotReply("New.", [])])


if __name__ == "__main__":
    unittest.main()
