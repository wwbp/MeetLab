"""Bot Config's "clause" start (clause_aggregator.py): the voice starts at the first clause of a
reply, then speaks whole sentences. The first sentence's wait was the largest stage of the reply
time and the one that grew with load (255 → 386 ms p50 at 6 → 102 rooms, 2026-10-05)."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clause_aggregator import FirstClauseTextAggregator  # noqa: E402


def llm_tokens(reply: str) -> list[str]:
    """How an LLM streams: the space starts the next token, punctuation comes alone ("Well", ",", " that")."""
    import re
    return re.findall(r" ?[\w']+|[^\w\s]", reply)


def chunks(*replies, interrupt_after=None, tokens=llm_tokens):
    """What the voice is handed, reply by reply, as the LLM streams tokens."""
    async def run():
        agg, out = FirstClauseTextAggregator(), []
        for n, reply in enumerate(replies):
            spoken = []
            for token in tokens(reply):
                async for a in agg.aggregate(token):
                    spoken.append(a.text)
            if interrupt_after == n:
                await agg.handle_interruption()
            else:
                rest = await agg.flush()
                if rest and rest.text:
                    spoken.append(rest.text)
            out.append(spoken)
        return out
    return asyncio.run(run())


class TestFirstClause(unittest.TestCase):
    def test_the_first_clause_goes_at_once_then_whole_sentences(self):
        self.assertEqual(chunks("Well, that depends on the weather, honestly. I would bring a coat, just in case."),
                         [["Well, that depends on the weather,", "honestly.", "I would bring a coat, just in case."]])

    def test_whole_words_with_trailing_spaces_work_too(self):
        self.assertEqual(chunks("Well, that depends on the weather, honestly. Okay.", tokens=lambda r: [w + " " for w in r.split(" ")]),
                         [["Well, that depends on the weather,", "honestly.", "Okay."]])

    def test_a_short_opening_waits_for_more_words(self):
        # "Sure," alone is too short to be worth its own request: it waits for the sentence.
        self.assertEqual(chunks("Sure, I can help with that. What do you need?"),
                         [["Sure, I can help with that.", "What do you need?"]])

    def test_numbers_are_not_clauses(self):
        self.assertEqual(chunks("It costs about 1,000 dollars a year, give or take."),
                         [["It costs about 1,000 dollars a year,", "give or take."]])

    def test_every_reply_starts_fresh(self):
        self.assertEqual(chunks("Yes, of course, that works for me. Great.", "Hmm, let me think about it, okay?"),
                         [["Yes, of course, that works for me.", "Great."], ["Hmm, let me think about it,", "okay?"]])

    def test_an_interrupted_reply_does_not_carry_over(self):
        out = chunks("I think that the best way, if you ask", "Okay, so what would you like to do now?",
                     interrupt_after=0)
        self.assertEqual(out[1], ["Okay, so what would you like to do now?"])


class TestInstalled(unittest.TestCase):
    def test_a_clause_room_gets_the_clause_aggregator_on_any_voice(self):
        # Pipecat has no hook for a custom aggregator: this fails if a Pipecat upgrade moves the
        # private attribute we replace, instead of silently speaking whole sentences again.
        from pipecat.services.openai.tts import OpenAITTSService
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        from clause_aggregator import install
        for tts in (OpenAITTSService(api_key="x"), ElevenLabsTTSService(api_key="x", settings=ElevenLabsTTSService.Settings(voice="v"))):
            install(tts, "clause")
            self.assertIsInstance(tts._text_aggregator, FirstClauseTextAggregator)

    def test_other_rooms_keep_pipecats(self):
        from pipecat.services.openai.tts import OpenAITTSService
        from clause_aggregator import install
        tts = OpenAITTSService(api_key="x")
        install(tts, "sentence")
        self.assertNotIsInstance(tts._text_aggregator, FirstClauseTextAggregator)


if __name__ == "__main__":
    unittest.main()
