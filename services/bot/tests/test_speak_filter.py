"""Whether the bot replies at all (speak_filter.py): pure rules around one classifier call,
tested with a fake classifier that returns scripted probabilities. No model is called."""
import asyncio
import itertools
import unittest

from pipecat.classifiers.base_classifier import BaseClassifier, ClassifierError, YesNoQuestion, YesNoResult

from meetlab_bot.speak_filter import DEFAULT_RULES, MAX_TURNS, OWED_WITHIN, Decision, FilterTurn, Rule, decide, is_direct_address, render_state

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

    async def test_an_address_the_bot_has_answered_is_not_an_address_now(self):
        """TURNS opens with 'Juniper, how long…', which the bot answers, and ends with one
        person asking another."""
        fake = FakeClassifier({"to_other": 0.9})
        self.assertEqual((await self.decide(fake)).reason, "blocked")

    async def test_people_talking_at_once_any_turn_of_the_batch_may_address_the_bot(self):
        """Several people's words can be committed together; batch_size says how many of the
        last turns that is."""
        fake = FakeClassifier({"to_other": 1.0})
        batch = [FilterTurn("Teo", "Okay, everyone's here."),
                 FilterTurn("Mara", "Juniper, can you suggest an icebreaker?"),
                 FilterTurn("Teo", "Hang on, is my mic working?")]
        self.assertEqual(await self.decide(fake, turns=batch, batch_size=2), Decision(speak=True, reason="direct_address"))
        self.assertEqual(await self.decide(fake, turns=batch, batch_size=3), Decision(speak=True, reason="direct_address"))
        self.assertEqual(fake.calls, [])

    async def test_a_batch_where_nobody_addresses_the_bot_goes_to_the_classifier(self):
        fake = FakeClassifier({"to_other": 0.9})
        d = await self.decide(fake, batch_size=2)  # TURNS: the bot's own turn, then Teo to Mara
        self.assertEqual((d.reason, len(fake.calls)), ("blocked", 1))
        self.assertEqual(len(fake.calls[0][0].splitlines()), 1 + len(TURNS), "the batch changes nothing that is sent")

    async def test_the_batch_is_the_last_turn_unless_said(self):
        """Which reason is given: named in the batch is direct_address; named before it, owed_reply."""
        fake = FakeClassifier({"to_other": 0.9})
        batch = [FilterTurn("Mara", "Juniper, can you suggest an icebreaker?"), FilterTurn("Teo", "Is my mic working?")]
        self.assertEqual((await self.decide(fake, turns=batch)).reason, "owed_reply")
        self.assertEqual((await self.decide(fake, turns=batch, batch_size=1)).reason, "owed_reply")
        self.assertEqual((await self.decide(fake, turns=batch, batch_size=0)).reason, "owed_reply", "never fewer than one")
        self.assertEqual((await self.decide(fake, turns=batch, batch_size=9)).reason, "direct_address", "no more than there are")
        self.assertEqual(fake.calls, [])

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

    async def test_silence_is_told_to_the_classifier(self):
        fake = FakeClassifier()
        await self.decide(fake, silence_s=12)
        await self.decide(fake)
        self.assertEqual(fake.calls[0][0], render_state(TURNS, "Juniper", silence_s=12))
        self.assertEqual(fake.calls[1][0], render_state(TURNS, "Juniper"))

    async def test_silence_does_not_make_the_bot_speak_by_itself(self):
        """Only a rule can act on it: with every rule firing, a long silence still blocks."""
        d = await self.decide(FakeClassifier({"to_other": 1.0}), silence_s=60)
        self.assertEqual(d.reason, "blocked")

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


BLOCK_ALL = {"to_other": 1.0, "unfinished": 1.0, "agreeing": 1.0}
ASKED = FilterTurn("Lena", "Juniper, what's the deadline for the abstract?")


class TestOwedReply(unittest.IsolatedAsyncioTestCase):
    """The bot is owed a reply when one of the last OWED_WITHIN turns before the newest is a
    participant's, calls the bot by name (is_direct_address) with more than a courtesy, and the
    bot has not spoken since. Then it speaks, and the classifier is not asked."""

    async def reason(self, *turns, **kwargs):
        fake = FakeClassifier(BLOCK_ALL)
        d = await decide(turns, ("Juniper",), RULES, fake, timeout_s=1.0, **kwargs)
        self.assertEqual(d.speak, d.reason != "blocked")
        self.assertEqual(len(fake.calls), d.reason == "blocked", "asked only when nothing else decided")
        return d.reason

    async def test_asked_by_name_then_a_person_answers(self):
        fake = FakeClassifier(BLOCK_ALL)
        turns = [ASKED, FilterTurn("Kofi", "It's the fifteenth, I checked this morning."),
                 FilterTurn("Lena", "Oh great, thanks Kofi.")]
        self.assertEqual(await decide(turns, ("Juniper",), RULES, fake, timeout_s=1.0),
                         Decision(speak=True, reason="owed_reply"))
        self.assertEqual(fake.calls, [])

    async def test_the_bot_spoke_after_the_question_nothing_is_owed(self):
        self.assertEqual(await self.reason(ASKED, FilterTurn("Juniper", "The fifteenth.", is_bot=True),
                                           FilterTurn("Kofi", "Told you, Lena.")), "blocked")

    async def test_two_questions_by_name(self):
        second = FilterTurn("Kofi", "Juniper, and how long can it be?")
        bot = FilterTurn("Juniper", "The fifteenth.", is_bot=True)
        other = FilterTurn("Lena", "Kofi, are you writing it?")
        self.assertEqual(await self.reason(ASKED, second, other), "owed_reply", "both unanswered: one reply is owed")
        self.assertEqual(await self.reason(ASKED, bot, second, other), "owed_reply", "the second came after the bot spoke")
        self.assertEqual(await self.reason(ASKED, second, bot, other), "blocked",
                         "the bot has spoken since both: whatever it said, the debt is cleared")

    async def test_a_request_with_no_question_mark_or_request_word_is_owed(self):
        self.assertEqual(await self.reason(FilterTurn("Lena", "Juniper, give us a fun fact"),
                                           FilterTurn("Kofi", "Ha, good one.")), "owed_reply")

    async def test_thanks_or_a_greeting_by_name_is_answered_at_once_or_not_at_all(self):
        """NEEDS THE OWNER'S RULING. His rule: any address by name gets a reply, thanks included;
        that holds in the turns just committed. A turn later the moment for "you're welcome"
        has passed, and saying it then would interrupt: a courtesy does not stay owed."""
        later = FilterTurn("Kofi", "Lena, shall we go on?")
        for words in ("Thanks, Juniper.", "Okay, fine. Thanks, Juniper.", "Thank you Juniper", "Hi Juniper!",
                      "Juniper?", "Oh, hey, Juniper!", "Bye, Juniper."):
            self.assertEqual(await self.reason(FilterTurn("Lena", words)), "direct_address", words)
            self.assertEqual(await self.reason(FilterTurn("Lena", words), later), "blocked", words)
        self.assertEqual(await self.reason(FilterTurn("Lena", "Thanks, Juniper. What about Friday?"), later), "owed_reply")

    async def test_a_name_that_is_mentioned_owes_nothing(self):
        for words in ("Let's ask Juniper later.", "Juniper said the library is free on Fridays.", "Should we ask Juniper?"):
            self.assertEqual(await self.reason(FilterTurn("Lena", words), FilterTurn("Kofi", "Sure.")), "blocked", words)

    async def test_the_bots_own_words_owe_nothing(self):
        self.assertEqual(await self.reason(FilterTurn("Juniper", "Juniper, what do you think?", is_bot=True),
                                           FilterTurn("Kofi", "Sure.")), "blocked")

    async def test_it_stays_owed_for_four_turns_one_each_in_a_full_room(self):
        self.assertEqual(OWED_WITHIN, 4)
        chat = [FilterTurn("Kofi", f"Lena, line {n}.") for n in range(5)]
        self.assertEqual(await self.reason(ASKED, *chat[:4]), "owed_reply")
        self.assertEqual(await self.reason(ASKED, *chat[:5]), "blocked", "five turns on, the talk has moved on")

    async def test_the_bound_is_a_parameter_and_zero_turns_it_off(self):
        chat = [FilterTurn("Kofi", f"Lena, line {n}.") for n in range(5)]
        self.assertEqual(await self.reason(ASKED, *chat[:2], owed_within=1), "blocked")
        self.assertEqual(await self.reason(ASKED, *chat[:1], owed_within=1), "owed_reply")
        self.assertEqual(await self.reason(ASKED, *chat[:5], owed_within=5), "owed_reply")
        self.assertEqual(await self.reason(ASKED, *chat[:1], owed_within=0), "blocked")
        self.assertEqual(await self.reason(ASKED, owed_within=0), "direct_address", "the newest turns are not the look-back's")

    async def test_the_bound_counts_turns_not_batches(self):
        """Turns committed together count one each: a big batch does not stretch the look-back."""
        chat = [FilterTurn("Kofi", f"Lena, line {n}.") for n in range(5)]
        self.assertEqual(await self.reason(ASKED, *chat[:4], batch_size=3), "owed_reply")
        self.assertEqual(await self.reason(ASKED, *chat[:5], batch_size=3), "blocked")
        self.assertEqual(await self.reason(ASKED, *chat[:2], batch_size=3), "direct_address", "in the batch itself")

    async def test_the_filter_off_still_asks_nothing_and_says_disabled(self):
        self.assertEqual(await self.reason(ASKED, FilterTurn("Kofi", "The fifteenth."), enabled=False), "disabled")


class TestDefaultRules(unittest.TestCase):
    def test_the_owners_choice_of_2026_11_24_verbatim(self):
        """Measured in data/speak-filter/wordings-2026-11-23.md: unfinished_a2 and mentioned_b1 in
        place of the two wordings that misfired, the other three as the owner wrote them, all at 0.9."""
        self.assertEqual(DEFAULT_RULES, (
            Rule("to_other_participant", "The last speaker was talking to another participant, not to the assistant.", 0.9),
            Rule("mentioned_not_asked", "The last speaker refers to the assistant while talking to other people, "
                                        "and asks the assistant nothing.", 0.9),
            Rule("unfinished_thought", "The last turn stops partway (for example on 'and', 'um', 'the' or a comma) and "
                                       "the speaker has more to say. A short question or request that is complete "
                                       "counts as finished.", 0.9),
            Rule("already_answered", "Another participant has already answered the question.", 0.9),
            Rule("group_agreeing", "The group is in the middle of agreeing on something.", 0.9),
        ))


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

    def test_seconds_of_silence_since_the_last_turn_are_the_last_line(self):
        turns = [FilterTurn("Mara", "Does anybody know which planet has the most moons?")]
        self.assertEqual(render_state(turns, "Juniper", silence_s=9), (
            'The assistant is named Juniper. The conversation so far, oldest turn first, one JSON object per line:\n'
            '{"speaker": "Mara", "role": "participant", "text": "Does anybody know which planet has the most moons?"}\n'
            'Nobody has spoken for 9 seconds since the last turn.'
        ))
        for seconds, said in ((7.5, "7.5"), (12.0, "12"), (3.14159, "3.1")):
            self.assertEqual(render_state(turns, "Juniper", silence_s=seconds).splitlines()[-1],
                             f"Nobody has spoken for {said} seconds since the last turn.")

    def test_no_silence_or_too_little_to_say_no_line(self):
        turns = [FilterTurn("Mara", "Hello.")]
        for seconds in (None, 0, 0.04):
            self.assertEqual(render_state(turns, "Juniper", silence_s=seconds), render_state(turns, "Juniper"), seconds)

    def test_a_person_with_the_bots_name_is_still_a_participant(self):
        self.assertIn('"role": "participant"', render_state([FilterTurn("Juniper", "Hello.")], "Juniper"))


if __name__ == "__main__":
    unittest.main()
