"""Whether the bot replies at all: decided before the model is called.

Today every committed user turn goes to the model, so in a group the bot talks too much
(interruption.py, _MIN_BOT_SPEECH_MS: 12 replies against 22 yields in one session). Here a
researcher's rules, written in plain English, each with a threshold, can BLOCK a reply:

- The default is to speak. No enabled rule, or the filter off: speak, nothing is asked.
- Called by name in the turns just committed (is_direct_address): speak, nothing is asked.
- Owed a reply (owed_reply): asked something by name in one of the OWED_WITHIN turns before,
  and the bot has not spoken since (someone else spoke first): speak, nothing is asked.
- Otherwise ONE classifier call: every enabled rule as a yes/no question about the recent
  conversation (render_state). A rule blocks when its probability is at or above its threshold;
  the comparison is ours, the classifier's own is_yes (0.5) is not used.
- The classifier fails, answers badly or takes longer than the timeout: speak, as today
  (fail open), with the error in the record.

Pure: no frames, no network of its own, no logging; times come from a clock that is passed in.
decide() returns a Decision for the caller to act on and store. Not wired into bot.py yet.
Tests: services/bot/tests/test_speak_filter.py.
"""
import asyncio
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pipecat.classifiers.base_classifier import BaseClassifier, ClassifierError, YesNoQuestion

# How many of the most recent turns the classifier reads. The rules are about the last
# exchange (who was asked, was it answered, is the thought finished), so a dozen turns hold
# what they need, and every turn sent is paid in tokens and in time before each reply. Also
# the longest transcript in the scenario set the thresholds are chosen on.
MAX_TURNS = 12
# How many turns after a request by name the bot still owes its reply. Four lets each of the
# four people a room can hold speak once (a person answering for the bot, the asker thanking
# them, ...); past that the talk has moved on and a late answer would interrupt it.
OWED_WITHIN = 4


@dataclass(frozen=True)
class Rule:
    id: str
    instructions: str  # the researcher's English, asked as a yes/no question
    threshold: float   # blocks when the probability of yes is at or above this
    enabled: bool = True


@dataclass(frozen=True)
class FilterTurn:
    speaker: str  # display name
    text: str
    is_bot: bool = False


@dataclass(frozen=True)
class Decision:
    speak: bool
    reason: str  # direct_address | owed_reply | blocked | no_rule_fired | classifier_error | disabled
    blocked_by: tuple[str, ...] = ()  # ids of the rules at or above their threshold, in the rules' order
    probabilities: dict[str, float] = field(default_factory=dict)  # every enabled rule's, when asked
    elapsed_ms: float | None = None  # the classifier call; None when it was not made
    error: str | None = None


# ponytail: a text heuristic over one turn's words, blind to tone, to who is looking at whom
# and to a misheard name ("Stage" for Sage). Its misses only cost a classifier call (the rules
# then decide); its false alarms make the bot reply as it does today. What replaces it: an
# addressee question to the classifier, once measured against the scenario set, or addressee
# detection from the audio.
_BEFORE, _AFTER = r"(?<![\w'’-])", r"(?![\w'’-])"  # a whole word, and not a possessive (Juniper's)
_START = r"(?:^|[.?!…]\s+)[\"“‘(]*"                # the start of the turn or of a sentence
_FILLER = r"(?:(?:ok|okay|so|and|well|oh|um|uh|alright)[,!]?\s+)*"
_GREETING = r"(?:(?:hey|hi|hello|thanks|thank\s+you|excuse\s+me)[,!]?\s+)"
_REQUEST = r"(?:please|(?:can|could|would|will|do|did|are|were|have|should)\s+you|what|how|why|when|where|which)\b"


def _address_pattern(name: str) -> re.Pattern:
    n = _BEFORE + r"\s+".join(map(re.escape, name.split())) + _AFTER
    return re.compile("|".join((
        rf"{_START}{_FILLER}{n}\s*[,:]",           # "Juniper, what do you think?"
        rf"{_START}{_FILLER}{n}\s+{_REQUEST}",     # "Juniper can you repeat that" (no comma transcribed)
        rf"{_START}{_FILLER}{_GREETING}{n}",       # "Hey Juniper can you…", "Thank you Juniper"
        rf",\s*{n}\s*(?:[.?!…]|$)",                # "What do you think, Juniper?"
        rf"^[\"“‘(]*{_FILLER}{n}[\s.?!…\"”’)]*$",  # "Juniper?"
    )), re.IGNORECASE)


def is_direct_address(text: str, bot_names: Sequence[str]) -> bool:
    """The bot is called by one of its names in these words: the name is a term of address,
    not merely said. A name counts as a whole word, in any case, when it is

    1. at the start of the turn or of a sentence (after fillers such as "okay, so"), followed
       by a comma or colon: "Juniper, what do you think?";
    2. in the same place, followed by a request word ("can you", "would you", "please",
       "what", "how", …), for speech transcribed without the comma: "Juniper can you repeat that";
    3. in the same place, right after a greeting or thanks: "Hey Juniper …", "Thank you Juniper";
    4. after a comma, at the end of a sentence: "What do you think, Juniper?";
    5. the whole turn: "Juniper?".

    So "let's ask Juniper later", "Juniper said…", "Juniper's idea" and "should we ask Juniper?"
    are mentions. Known misses and false alarms: test_known_misses_and_false_alarms.
    """
    return any(_address_pattern(name).search(text.strip()) for name in bot_names if name.strip())


# ponytail: a word list. A turn made only of these and the bot's name is a courtesy ("Thanks,
# Juniper.", "Hi Juniper!", "Juniper?"): answered when it is new, never owed later. Anything
# else said with the name ("Juniper, give us a fun fact") is a request that stays owed.
_COURTESY = frozenset("""thanks thank you so much very a lot ok okay fine oh um uh well alright and hi hey hello
    bye goodbye cheers great good morning afternoon evening night""".split())


def owed_reply(turns: Sequence[FilterTurn], bot_names: Sequence[str], within: int = OWED_WITHIN) -> bool:
    """Was the bot asked something by name (is_direct_address, more than a courtesy) in the last
    `within` turns before the newest, with no word from the bot since?"""
    names = {w for n in bot_names for w in n.lower().split()}
    for t in reversed(turns[-(within + 1):] if within > 0 else []):
        if t.is_bot:
            return False
        words = set(re.findall(r"[\w'’]+", t.text.lower()))
        if is_direct_address(t.text, bot_names) and words - names - _COURTESY:
            return True
    return False


def render_state(turns: Sequence[FilterTurn], bot_name: str, silence_s: float | None = None) -> str:
    """The classifier's `state`: who the assistant is, then one JSON object per turn (who said
    it is a field, never a prefix to parse out of the words: meetlab_core/turns.py), then, when
    `silence_s` rounds to more than 0.0, how long nobody has spoken. Thresholds hold for this
    format only; the scenario set is rendered by this function too."""
    lines = [f"The assistant is named {bot_name}. The conversation so far, oldest turn first, one JSON object per line:"]
    lines += [json.dumps({"speaker": t.speaker, "role": "assistant" if t.is_bot else "participant", "text": t.text},
                         ensure_ascii=False) for t in turns]
    if silence_s and round(silence_s, 1) > 0:
        lines.append(f"Nobody has spoken for {round(silence_s, 1):g} seconds since the last turn.")
    return "\n".join(lines)


async def decide(
    turns: Sequence[FilterTurn],
    bot_names: Sequence[str],
    rules: Sequence[Rule],
    classifier: BaseClassifier,
    *,
    timeout_s: float,
    enabled: bool = True,
    max_turns: int = MAX_TURNS,
    batch_size: int = 1,
    owed_within: int = OWED_WITHIN,
    silence_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Decision:
    """Should the bot reply to the last of `turns`? Never raises (but can be cancelled): any
    failure is a Decision to speak. `bot_names[0]` is the name the classifier is told.
    `batch_size`: how many of the last turns were committed together (people talking at once);
    the bot is addressed if any of them calls it. `owed_within`: see owed_reply (0 turns it off).
    `silence_s`: seconds since the last turn ended, told to the classifier; only a rule can act on it."""
    active = [r for r in rules if r.enabled]
    if not enabled or not active:
        return Decision(True, "disabled")
    if any(not t.is_bot and is_direct_address(t.text, bot_names) for t in turns[-max(batch_size, 1):]):
        return Decision(True, "direct_address")
    if owed_reply(turns, bot_names, owed_within):
        return Decision(True, "owed_reply")
    state = render_state(turns[-max_turns:], next((n for n in bot_names if n.strip()), "Assistant"), silence_s)
    questions = {r.id: YesNoQuestion(instructions=r.instructions) for r in active}
    started = clock()

    def failed(error: str) -> Decision:
        return Decision(True, "classifier_error", elapsed_ms=(clock() - started) * 1000, error=error)

    try:
        results = await asyncio.wait_for(classifier.yes_no(state, questions), timeout_s)
        probabilities = {r.id: float(results[r.id].probability) for r in active}
        if not all(0.0 <= p <= 1.0 for p in probabilities.values()):
            raise ClassifierError(f"a probability outside 0 to 1: {probabilities}")
    except TimeoutError:
        return failed(f"no answer within {timeout_s:g} s")
    except Exception as e:  # fail open: a broken classifier must never silence the bot
        return failed(f"{type(e).__name__}: {e}")
    blocked_by = tuple(r.id for r in active if probabilities[r.id] >= r.threshold)
    return Decision(not blocked_by, "blocked" if blocked_by else "no_rule_fired", blocked_by, probabilities,
                    (clock() - started) * 1000)
