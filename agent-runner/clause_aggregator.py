"""Bot Config's "clause" start: the voice starts at the first clause of each reply, then speaks
whole sentences.

Only the first chunk decides when the bot starts talking, so this cuts the wait for the first
whole sentence (the largest stage of the reply time, 255 → 386 ms p50 at 6 → 102 rooms,
2026-10-05) while adding just one extra break per reply. Pipecat 1.12 offers only sentence and
token aggregation; this is its sentence aggregator plus the first-clause rule.

ponytail: installed on the TTS service's private `_text_aggregator` (Pipecat has no hook for a
custom one); a constructor parameter upstream would replace that.
"""
from pipecat.utils.text.base_text_aggregator import Aggregation, AggregationType
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

CLAUSE_ENDINGS = ",;:"
MIN_WORDS = 4  # "Sure," alone isn't worth its own request: it waits for more


class FirstClauseTextAggregator(SimpleTextAggregator):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._first = True

    async def aggregate(self, text: str):
        for char in text:  # by character, like Pipecat's: an LLM's space starts the next token
            async for sentence in super().aggregate(char):
                self._first = False
                yield sentence
            # A clause ends at its punctuation *and the space after it* ("1,000" is no clause).
            if self._first and char == " " and self._text[-2:-1] in tuple(CLAUSE_ENDINGS) \
                    and len(self._text.split()) >= MIN_WORDS:
                clause, self._text, self._first = self._text.strip(), "", False
                yield Aggregation(text=clause, type=AggregationType.SENTENCE)

    async def flush(self):
        self._first = True  # the reply is over: the next one starts with a clause again
        return await super().flush()

    async def handle_interruption(self):
        self._first = True
        await super().handle_interruption()


def install(tts, mode: str) -> None:
    """Give a "clause" room's voice the first-clause aggregator; any provider (the aggregation
    runs in Pipecat before the provider is called)."""
    if mode == "clause":
        tts._text_aggregator = FirstClauseTextAggregator(aggregation_type=AggregationType.SENTENCE,
                                                         language=tts._text_aggregator.language)
