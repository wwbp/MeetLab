"""Whether the bot replies at all (speak_filter.py): pure rules around one classifier call,
tested with a fake classifier that returns scripted probabilities. No model is called."""
import asyncio
import itertools
import unittest

from pipecat.classifiers.base_classifier import BaseClassifier, ClassifierError, YesNoQuestion, YesNoResult

from meetlab_bot.speak_filter import MAX_TURNS, Decision, FilterTurn, Rule, decide, is_direct_address, render_state

TO_OTHER = Rule("to_other", "The last speaker was talking to another participant, not to the assistant.", 0.7)
UNFINISHED = Rule("unfinished", "The speaker has not finished their thought.", 0.6)
AGREEING = Rule("agreeing", "The group is in the middle of agreeing on something.", 0.8)
RULES = [TO_OTHER, UNFINISHED, AGREEING]
TURNS = [
    FilterTurn("Mara", "Juniper, how long should the survey take?"),
    FilterTurn("Juniper", "About ten minutes.", is_bot=True),
    FilterTurn("Teo", "Mara, did you send the draft yet?"),
]


class FakeClassifier(BaseClassifier):
    """Answers with the probabilities it was given (0.0 for a question it has none for), after
    `delay` seconds, or raises `error`. Keeps every (state, questions) it was asked."""

    def __init__(self, probabilities=None, *, error=None, delay=0.0):
        super().__init__()
        self.probabilities, self.error, self.delay, self.calls = probabilities or {}, error, delay, []

    async def _ask(self, state, questions, **_):
        self.calls.append((state, dict(questions)))
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return {name: YesNoResult(probability=self.probabilities.get(name, 0.0)) for name in questions}, None


def clock(*times):
    """A clock that reads these times in turn: 10.0 then 10.25 makes the call 250 ms."""
    return itertools.chain(times, itertools.repeat(times[-1])).__next__


class TestDecide(unittest.IsolatedAsyncioTestCase):
    async def decide(self, classifier, turns=TURNS, names=("Juniper",), rules=RULES, **kwargs):
        kwargs.setdefault("timeout_s", 1.0)
        kwargs.setdefault("clock", clock(10.0, 10.25))
        return await decide(turns, names, rules, classifier, **kwargs)

    async def test_no_rule_fired_speaks_with_every_probability_and_the_time_taken(self):
        fake = FakeClassifier({"to_other": 0.2, "unfinished": 0.1, "agreeing": 0.3})
        d = await self.decide(fake)
        self.assertEqual(d, Decision(speak=True, reason="no_rule_fired", blocked_by=(),
                                     probabilities={"to_other": 0.2, "unfinished": 0.1, "agreeing": 0.3},
                                     elapsed_ms=250.0, error=None))
        self.assertEqual(len(fake.calls), 1, "all rules go in one request")

    async def test_a_rule_at_its_threshold_blocks(self):
        d = await self.decide(FakeClassifier({"to_other": 0.7}))
        self.assertEqual((d.speak, d.reason, d.blocked_by), (False, "blocked", ("to_other",)))
        self.assertEqual(d.probabilities, {"to_other": 0.7, "unfinished": 0.0, "agreeing": 0.0})
        self.assertEqual(d.elapsed_ms, 250.0)

    async def test_just_below_its_threshold_does_not(self):
        d = await self.decide(FakeClassifier({"to_other": 0.6999, "unfinished": 0.5999, "agreeing": 0.7999}))
        self.assertEqual((d.speak, d.reason, d.blocked_by), (True, "no_rule_fired", ()))

    async def test_each_rule_has_its_own_threshold(self):
        d = await self.decide(FakeClassifier({"to_other": 0.65, "unfinished": 0.65, "agreeing": 0.65}))
        self.assertEqual(d.blocked_by, ("unfinished",))

    async def test_several_rules_firing_are_all_named_in_the_rules_order(self):
        d = await self.decide(FakeClassifier({"agreeing": 0.9, "to_other": 0.95, "unfinished": 0.1}))
        self.assertEqual((d.speak, d.blocked_by), (False, ("to_other", "agreeing")))

    async def test_the_filter_is_off_speaks_without_asking(self):
        fake = FakeClassifier({"to_other": 1.0})
        d = await self.decide(fake, enabled=False)
        self.assertEqual(d, Decision(speak=True, reason="disabled"))
        self.assertEqual(fake.calls, [])

    async def test_no_enabled_rules_speaks_without_asking(self):
        fake = FakeClassifier({"to_other": 1.0})
        for rules in ([], [Rule("to_other", "x", 0.5, enabled=False)]):
            self.assertEqual(await self.decide(fake, rules=rules), Decision(speak=True, reason="disabled"))
        self.assertEqual(fake.calls, [])

    async def test_a_disabled_rule_is_neither_asked_nor_counted(self):
        fake = FakeClassifier({"to_other": 1.0, "unfinished": 0.1})
        off = Rule("to_other", TO_OTHER.instructions, 0.7, enabled=False)
        d = await self.decide(fake, rules=[off, UNFINISHED])
        self.assertEqual(d, Decision(speak=True, reason="no_rule_fired", probabilities={"unfinished": 0.1}, elapsed_ms=250.0))
        self.assertEqual(list(fake.calls[0][1]), ["unfinished"])

    async def test_addressed_by_name_speaks_without_asking_whatever_the_rules_say(self):
        fake = FakeClassifier({"to_other": 1.0, "unfinished": 1.0, "agreeing": 1.0})
        d = await self.decide(fake, turns=[*TURNS, FilterTurn("Mara", "Juniper, what do you think, um,")])
        self.assertEqual(d, Decision(speak=True, reason="direct_address"))
        self.assertEqual(fake.calls, [])

    async def test_only_the_last_turn_counts_as_an_address(self):
        """TURNS opens with 'Juniper, how long…' and ends with one person asking another."""
        fake = FakeClassifier({"to_other": 0.9})
        self.assertEqual((await self.decide(fake)).reason, "blocked")

    async def test_any_of_the_bots_names_is_an_address(self):
        d = await self.decide(FakeClassifier(), names=("Juniper", "Assistant"),
                              turns=[FilterTurn("Teo", "Assistant, what time is it?")])
        self.assertEqual(d.reason, "direct_address")

    async def test_the_bots_own_words_are_not_an_address(self):
        d = await self.decide(FakeClassifier(), turns=[FilterTurn("Juniper", "Juniper, here.", is_bot=True)])
        self.assertEqual(d.reason, "no_rule_fired")

    async def test_the_exact_questions_and_state_the_classifier_gets(self):
        fake = FakeClassifier()
        await self.decide(fake)
        state, questions = fake.calls[0]
        self.assertEqual(questions, {r.id: YesNoQuestion(instructions=r.instructions) for r in RULES})
        self.assertEqual(list(questions), ["to_other", "unfinished", "agreeing"])
        self.assertEqual(state, (
            'The assistant is named Juniper. The conversation so far, oldest turn first, one JSON object per line:\n'
            '{"speaker": "Mara", "role": "participant", "text": "Juniper, how long should the survey take?"}\n'
            '{"speaker": "Juniper", "role": "assistant", "text": "About ten minutes."}\n'
            '{"speaker": "Teo", "role": "participant", "text": "Mara, did you send the draft yet?"}'
        ))

    async def test_only_the_most_recent_turns_are_sent(self):
        self.assertEqual(MAX_TURNS, 12)
        turns = [FilterTurn("Mara", f"line {n}") for n in range(30)]
        fake = FakeClassifier()
        await self.decide(fake)
        await self.decide(fake, turns=turns)
        await self.decide(fake, turns=turns, max_turns=2)
        default, two = fake.calls[1][0].splitlines(), fake.calls[2][0].splitlines()
        self.assertEqual((len(default), len(two)), (1 + 12, 1 + 2))
        self.assertIn("line 18", default[1])
        self.assertIn("line 29", default[-1])
        self.assertIn("line 28", two[1])

    async def test_a_classifier_that_raises_fails_open_and_says_why(self):
        d = await self.decide(FakeClassifier({"to_other": 1.0}, error=ClassifierError("no answer")))
        self.assertEqual(d, Decision(speak=True, reason="classifier_error", elapsed_ms=250.0,
                                     error="ClassifierError: no answer"))

    async def test_any_other_exception_fails_open_too(self):
        d = await self.decide(FakeClassifier(error=RuntimeError("boom")))
        self.assertEqual((d.speak, d.reason, d.error), (True, "classifier_error", "RuntimeError: boom"))

    async def test_a_slow_classifier_hits_the_timeout_and_fails_open(self):
        fake = FakeClassifier({"to_other": 1.0}, delay=5.0)
        d = await asyncio.wait_for(self.decide(fake, timeout_s=0.01), 2.0)
        self.assertEqual((d.speak, d.reason, d.blocked_by, d.probabilities), (True, "classifier_error", (), {}))
        self.assertEqual(d.error, "no answer within 0.01 s")
        self.assertEqual(d.elapsed_ms, 250.0)

    async def test_an_answer_missing_a_rule_or_out_of_range_fails_open(self):
        class Partial(FakeClassifier):
            async def yes_no(self, state, questions, **_):
                return {"to_other": YesNoResult(probability=1.0)}
        d = await self.decide(Partial())
        self.assertEqual((d.speak, d.reason, d.probabilities), (True, "classifier_error", {}))
        self.assertIn("unfinished", d.error)
        for p in (1.5, -0.1, float("nan")):
            d = await self.decide(FakeClassifier({"to_other": p}))
            self.assertEqual((d.speak, d.reason), (True, "classifier_error"), p)

    async def test_cancelling_the_bot_is_not_swallowed(self):
        task = asyncio.create_task(self.decide(FakeClassifier(delay=5.0), timeout_s=10.0))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_the_same_inputs_and_answers_give_the_same_record(self):
        answers = {"to_other": 0.71, "unfinished": 0.3, "agreeing": 0.85}
        first = await self.decide(FakeClassifier(answers))
        self.assertEqual(await self.decide(FakeClassifier(answers)), first)
        self.assertEqual(first.blocked_by, ("to_other", "agreeing"))


class TestDirectAddress(unittest.TestCase):
    """The bot's name used to call it, in the words of one turn: not merely said."""

    def yes(self, text, *names):
        self.assertTrue(is_direct_address(text, names or ("Juniper",)), text)

    def no(self, text, *names):
        self.assertFalse(is_direct_address(text, names or ("Juniper",)), text)

    def test_the_name_then_a_comma_at_the_start_of_a_sentence(self):
        self.yes("Juniper, what do you think?")
        self.yes("Great. Juniper, does Friday leave enough time?")
        self.yes("Okay, so, Juniper, can you sum up?")
        self.yes("Juniper: what's next?")

    def test_the_name_after_a_comma_at_the_end_of_a_sentence(self):
        self.yes("What do you think, Juniper?")
        self.yes("what do you think, juniper")
        self.yes("Thanks, Juniper.")
        self.yes("Is that right, Juniper? I always forget.")

    def test_a_greeting_then_the_name(self):
        self.yes("Hey Juniper can you summarize what we decided so far")
        self.yes("hi juniper")
        self.yes("Oh, hey, Juniper!")
        self.yes("Thank you Juniper")

    def test_the_name_then_a_request_without_a_comma(self):
        self.yes("Juniper can you repeat that")
        self.yes("Juniper what do you think")
        self.yes("So Juniper would you start us off?")
        self.yes("Juniper please go on")

    def test_the_name_alone(self):
        self.yes("Juniper?")
        self.yes("Juniper")
        self.yes("  Um, Juniper...  ")

    def test_a_mention_is_not_an_address(self):
        self.no("Let's ask Juniper later.")
        self.no("Let's ask Juniper later, once we've finished the list.")
        self.no("Juniper said earlier that the library is free on Fridays.")
        self.no("I think Juniper is pretty good at summaries.")
        self.no("Should we ask Juniper?")
        self.no("Did you hear what Juniper said, Teo?")
        self.no("Who said that? Juniper?")
        self.no("Juniper's answer was fine, I thought.")
        self.no("That was Juniper’s idea, right?")

    def test_a_name_inside_another_word_is_not_the_name(self):
        self.no("Al, what do you think?", "Alma")
        self.no("Also, what do you think?", "Al")
        self.no("What do you think, Alma?", "Al")
        self.no("Juniperus, what do you think?")
        self.no("Stage, can you suggest three names?", "Sage")
        self.yes("Al, what do you think?", "Al")

    def test_case_and_punctuation(self):
        self.yes("JUNIPER, WHAT DO YOU THINK?")
        self.yes("juniper,what do you think")
        self.yes("What do you think,  Juniper ?!")
        self.yes("“Juniper, what do you think?”")
        self.yes("Hmm… Juniper, any ideas?")

    def test_a_name_of_several_words(self):
        self.yes("Doctor  Sage, what do you think?", "Doctor Sage")
        self.no("Sage, what do you think?", "Doctor Sage")

    def test_a_name_that_is_also_a_common_noun(self):
        self.yes("Assistant, how long does a focus group run?", "Assistant")
        self.yes("Thanks, Assistant.", "Assistant")
        self.no("No, the assistant already gave us the dates, we can skip that part.", "Assistant")
        self.no("Moss of us can make Thursday, right Kofi?", "Moss")

    def test_no_name_no_address(self):
        self.no("Juniper, what do you think?", "")
        self.assertFalse(is_direct_address("Juniper, what do you think?", ()))
        self.no("", "Juniper")

    def test_known_misses_and_false_alarms(self):
        """What the text rules get wrong, kept here so a change that fixes one shows up.
        Misses go on to the classifier; false alarms make the bot reply as it does today."""
        self.no("I think, Juniper, that we should start.")       # called mid-sentence
        self.no("Juniper tell us a joke")                        # a command with no comma
        self.no("What do you think Juniper?")                    # no comma before the name
        self.yes("Juniper, Mara and I already talked about it.")  # first of a list of people
        self.yes("Who should we ask? Juniper, I guess.")         # an answer that starts with the name
        self.yes("I only asked Mara, Juniper.")                  # last of a list of people


class TestRenderState(unittest.TestCase):
    """The classifier's text. Thresholds are tuned against this exact format (and the scenario
    set renders the same): change it knowingly."""

    def test_the_exact_format(self):
        turns = [FilterTurn("Mara", "Juniper, what's a \"p-value\"?"),
                 FilterTurn("Juniper", "The chance of data this extreme if nothing is going on.", is_bot=True),
                 FilterTurn("Teo", "Così, okay: and…")]
        self.assertEqual(render_state(turns, "Juniper"), (
            'The assistant is named Juniper. The conversation so far, oldest turn first, one JSON object per line:\n'
            '{"speaker": "Mara", "role": "participant", "text": "Juniper, what\'s a \\"p-value\\"?"}\n'
            '{"speaker": "Juniper", "role": "assistant", "text": "The chance of data this extreme if nothing is going on."}\n'
            '{"speaker": "Teo", "role": "participant", "text": "Così, okay: and…"}'
        ))

    def test_a_person_with_the_bots_name_is_still_a_participant(self):
        self.assertIn('"role": "participant"', render_state([FilterTurn("Juniper", "Hello.")], "Juniper"))


if __name__ == "__main__":
    unittest.main()
