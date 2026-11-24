"""The speak filter's scenario set: written conversations, each ending at the moment the bot
must decide whether to reply, with the decision expected of it and the rule that should block. People talking at once reach
the bot as one batch of turns: `batch_size` says how many of the last turns that is (1 if absent).
To be replayed through the filter with a real classifier, to choose each rule's threshold.

The data is tools/loadtest/fixtures/speak_filter_scenarios.json: invented names and words only.
Here: load it, check it (validate), and turn a transcript into the filter's turns
(filter_turns). No model calls: tools/loadtest/tests/test_speak_filter_scenarios.py.
"""
import json
from collections import Counter
from pathlib import Path

from meetlab_bot.speak_filter import DEFAULT_RULES, FilterTurn

SCENARIOS_PATH = Path(__file__).parents[1] / "fixtures" / "speak_filter_scenarios.json"

# What each case tests; every category has at least two cases.
CATEGORIES = (
    "peer_to_peer",                    # two participants talking to each other
    "named_not_addressed",             # the bot named, nothing asked of it
    "unfinished_thought",              # the speaker is mid-thought
    "group_question_answered",         # a question to the group, answered by a participant
    "bot_question_human_answered",     # a question to the bot, answered first by a participant
    "group_agreeing",                  # the group converging on a decision
    "direct_request",                  # the bot asked by name
    "implicit_request",                # no name, but only the bot could answer
    "direct_address_with_block_rule",  # addressed by name while a block rule also holds
    "transcription_error",             # a misheard word or a wrong speaker label
    "overlapping_turns",               # people talking at once: one of them addresses the bot
    "unanswered_silence",              # a question nobody answers, then silence
    "control",                         # no rule applies
    "hard_silent",                     # sounds like it is for the bot, and is not
)
# A filter that always blocks, or never does, must score badly: the share of scenarios
# expecting a reply stays within this range.
MIN_SPEAK_SHARE, MAX_SPEAK_SHARE = 0.4, 0.6

_TEXT = ("id", "category", "description", "bot_name", "note")
_REQUIRED = _TEXT + ("participants", "transcript", "active_rules", "expected", "expected_rule")
_OPTIONAL = ("silence_after_s", "batch_size", "needs_owner_decision", "owner_question", "requires_setting")
# Behaviour a study can switch on, off by default: a case that needs one is reported apart, not
# scored against the default rules. answer_unanswered_group_question: owner, 2026-11-24, to be
# built with the silence trigger.
SETTINGS = ("answer_unanswered_group_question",)


def load(path=SCENARIOS_PATH) -> dict:
    """{"rules": [{id, instructions, threshold}], "scenarios": [...]}: the scenarios as written,
    the rules the filter's defaults (DEFAULT_RULES). validate() checks it."""
    return {"rules": [{"id": r.id, "instructions": r.instructions, "threshold": r.threshold} for r in DEFAULT_RULES],
            **json.loads(Path(path).read_text(encoding="utf-8"))}


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _probability(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 1


def _scenario_problems(s: dict, rule_ids: set) -> list[str]:
    out = [f"missing {f}" for f in _REQUIRED if f not in s]
    out += [f"unknown field {f}" for f in s if f not in _REQUIRED + _OPTIONAL]
    if out:
        return out
    out += [f"{f} must be text" for f in _TEXT if not _text(s[f])]
    if s["category"] not in CATEGORIES:
        out.append(f"category {s['category']!r} is not one of CATEGORIES")
    people = s["participants"]
    if not (isinstance(people, list) and 1 <= len(people) <= 4 and all(map(_text, people))
            and len(set(people)) == len(people) and s["bot_name"] not in people):
        out.append("participants must be 1 to 4 distinct names, none of them the bot's")
        people = []
    turns = s["transcript"]
    if not (isinstance(turns, list) and 3 <= len(turns) <= 12
            and all(isinstance(t, dict) and set(t) == {"speaker", "text"} for t in turns)):
        out.append("transcript must be 3 to 12 turns of {speaker, text}")
        turns = []
    for n, t in enumerate(turns, 1):
        if people and t["speaker"] not in [*people, s["bot_name"]]:
            out.append(f"turn {n} speaker {t['speaker']!r} is neither a participant nor the bot")
        if not _text(t["text"]):
            out.append(f"turn {n} text is empty")
    if turns and people and turns[-1]["speaker"] not in people:
        out.append("the last turn must be a participant's: the bot decides after a person speaks")
    active = s["active_rules"] if isinstance(s["active_rules"], list) else []
    if active != s["active_rules"]:
        out.append("active_rules must be a list of rule ids")
    out += [f"unknown rule {r}" for r in active if r not in rule_ids]
    if s["expected"] not in ("speak", "silent"):
        out.append(f"expected {s['expected']!r} is neither speak nor silent")
    elif s["expected"] == "silent" and s["expected_rule"] not in active:
        out.append("expected_rule of a silent case must be the active rule that blocks")
    elif s["expected"] == "speak" and s["expected_rule"] not in ("direct_address", "none"):
        out.append("expected_rule of a speak case must be direct_address or none")
    silence = s.get("silence_after_s", 0)
    if isinstance(silence, bool) or not isinstance(silence, (int, float)) or silence < 0:
        out.append("silence_after_s must be seconds, 0 or more")
    batch = s.get("batch_size", 1)
    if turns and people and not (type(batch) is int and 1 <= batch <= len(turns)
                                 and all(t["speaker"] in people for t in turns[-batch:])):
        out.append("batch_size must count the last turns committed together, all of them participants'")
    if not isinstance(s.get("needs_owner_decision", False), bool):
        out.append("needs_owner_decision must be true or false")
    elif s.get("needs_owner_decision") and not _text(s.get("owner_question")):
        out.append("owner_question must say what the owner is asked")
    if s.get("requires_setting", SETTINGS[0]) not in SETTINGS:
        out.append(f"requires_setting must be one of {', '.join(SETTINGS)}")
    return out


def validate(data: dict) -> list[str]:
    """Every problem with the set, as sentences; empty when it is valid."""
    rule_ids = [r.get("id") for r in data["rules"]]
    out = [f"duplicate rule {r}" for r, n in Counter(rule_ids).items() if n > 1]
    out += [f"rule {r.get('id')}: id and instructions must be text, a threshold 0 to 1" for r in data["rules"]
            if not {"id", "instructions"} <= set(r) <= {"id", "instructions", "threshold"}
            or not (_text(r.get("id")) and _text(r.get("instructions")))
            or not _probability(r.get("threshold", 0.5))]
    scenarios = data["scenarios"]
    out += [f"duplicate id {i}" for i, n in Counter(s.get("id") for s in scenarios).items() if n > 1]
    for s in scenarios:
        out += [f"{s.get('id')}: {p}" for p in _scenario_problems(s, set(rule_ids))]
    cases = Counter(s.get("category") for s in scenarios)
    out += [f"category {c}: {cases[c]} case(s), at least 2 needed" for c in CATEGORIES if cases[c] < 2]
    speak = sum(s.get("expected") == "speak" for s in scenarios) / max(len(scenarios), 1)
    if not MIN_SPEAK_SHARE <= speak <= MAX_SPEAK_SHARE:
        out.append(f"speak share {speak:.2f} is outside {MIN_SPEAK_SHARE}-{MAX_SPEAK_SHARE}")
    return out


def filter_turns(scenario: dict) -> list[FilterTurn]:
    """A scenario's transcript as the filter's turns: the bot's are those under its name. The
    classifier's text is then speak_filter.render_state, the one renderer."""
    return [FilterTurn(t["speaker"], t["text"], is_bot=t["speaker"] == scenario["bot_name"]) for t in scenario["transcript"]]
