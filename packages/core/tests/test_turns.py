"""The bot's structured input and output (meetlab_core.turns): one schema for what the model
reads (who said what) and what it answers (what to say, to whom), shared by the bot and rejoin.
    python -m unittest tests.test_turns -v   (from packages/core)
"""
import json
import unittest

from meetlab_core.turns import (
    REPLY_SCHEMA, RESPONSE_FORMAT, BotReply, Turn, assistant_message, parse_reply, parse_turns,
    turn_json, user_message,
)

ANA = Turn("ana__1a2b", "Ana", "What should we do first?")
BEN = Turn("ben__3c4d", "Ben", "I'd start with the budget.")


class TurnInTest(unittest.TestCase):
    def test_a_turn_is_one_json_object_with_every_field(self):
        self.assertEqual(json.loads(turn_json(ANA)),
                         {"speaker_id": "ana__1a2b", "name": "Ana", "text": "What should we do first?",
                          "source": "speech"})

    def test_words_are_kept_as_said_even_with_quotes_colons_and_braces(self):
        odd = Turn("ana__1a2b", "Ana", 'Ben: said "no" {twice}', "chat")
        self.assertEqual(parse_turns(turn_json(odd)), [odd])

    def test_a_merged_turn_is_split_by_speaker_never_by_its_words(self):
        # Pipecat's user aggregator joins every transcript in one end-of-turn window with spaces.
        merged = " ".join([turn_json(BEN), turn_json(ANA), turn_json(Turn("ben__3c4d", "Ben", "And staffing."))])
        self.assertEqual(parse_turns(merged), [BEN, ANA, Turn("ben__3c4d", "Ben", "And staffing.")])

    def test_one_persons_consecutive_fragments_are_one_turn(self):
        merged = turn_json(Turn("ana__1a2b", "Ana", "What should")) + " " + turn_json(Turn("ana__1a2b", "Ana", "we do first?"))
        self.assertEqual(parse_turns(merged), [ANA])

    def test_empty_words_are_dropped(self):
        self.assertEqual(parse_turns(turn_json(Turn("ana__1a2b", "Ana", "  "))), [])

    def test_text_that_is_not_a_turn_is_refused(self):
        for bad in ("Ana: hello", '{"speaker_id": "a"}', "[]", '{"speaker_id": 1, "name": "A", "text": "x", "source": "speech"}'):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    parse_turns(bad)

    def test_the_model_reads_a_turn_as_a_json_list_of_speakers(self):
        message = user_message([BEN, ANA])
        self.assertEqual(message["role"], "user")
        self.assertEqual([Turn(**t) for t in json.loads(message["content"])], [BEN, ANA])


class ReplyOutTest(unittest.TestCase):
    def test_the_schema_is_strict_and_every_field_required(self):
        self.assertEqual(REPLY_SCHEMA["required"], ["reply", "addressed_to"])
        self.assertIs(REPLY_SCHEMA["additionalProperties"], False)
        self.assertEqual(RESPONSE_FORMAT["type"], "json_schema")
        self.assertIs(RESPONSE_FORMAT["json_schema"]["strict"], True)
        self.assertIs(RESPONSE_FORMAT["json_schema"]["schema"], REPLY_SCHEMA)

    def test_a_reply_is_parsed(self):
        self.assertEqual(parse_reply('{"reply": "Start with the budget, Ben.", "addressed_to": ["ben__3c4d"]}'),
                         BotReply("Start with the budget, Ben.", ["ben__3c4d"]))

    def test_a_reply_that_breaks_the_schema_is_still_spoken_and_addressed_to_no_one(self):
        # The model's output is untrusted; a model without structured output still speaks.
        for raw, said in (("Plain words.", "Plain words."), ('{"reply": 3}', '{"reply": 3}'),
                          ('{"reply": "Hi", "addressed_to": "ben"}', "Hi")):
            with self.subTest(raw):
                self.assertEqual(parse_reply(raw), BotReply(said, []))

    def test_the_context_keeps_the_bots_reply_in_the_same_shape(self):
        reply = BotReply("Start with the budget, Ben.", ["ben__3c4d"])
        message = assistant_message(reply)
        self.assertEqual(message["role"], "assistant")
        self.assertEqual(parse_reply(message["content"]), reply)


if __name__ == "__main__":
    unittest.main()
