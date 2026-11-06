"""Audio modes (normal, ptt, baton) compared on one script: the pure parts of the simulation.

Plan: docs/audio-modes-test-plan.md, Simulations. Everything here is offline and stdlib, tested
by tools/loadtest/tests/test_sim_modes.py (`make sim-modes-check`). A simulation can eliminate a
mode, not choose one.

A timeline is one run, times in seconds from just after the bot's greeting:

    lines   [{"who", "text", "start", "end", "press_late"?, "release_early"?}]  speech as published
            to the room, after any gate; the flags come from hold_timing, in every mode, normal too
    bot     [(start, end)]            bot audible
    stored  [(time, who, text)]       stored user turns
    end     time the run ended
    holds   [(who, press, release)]   ptt and baton; release None if never let go before the end
    floor   [(who, start, end)]       baton: who the bot says holds it (bot participant attributes)
    bot_sentence_ends  [time]         baton: where the bot's sentences end
    infra   ["lag" | "stt-cpu restart"]  infrastructure signals: the only way a run is invalid

The live driver is sim_modes_live.py (`make sim-modes`, `make sim-modes-matrix`).
"""
import math
import random
from statistics import fmean

from meetlab_loadtest.conversation_library import _digest
from meetlab_loadtest.load_plan import _pct
from meetlab_loadtest.quality import hearing, words
from meetlab_loadtest.sim_attribution import PRESENT

MODES = ("normal", "ptt", "baton")
INFRA_SIGNALS = ("lag", "stt-cpu restart")

# Scenario: (people, modes). The plan's table, 28 cells.
SCENARIOS = {
    "turn_taking": ((1, 2, 3), MODES),
    "overlap": ((3,), MODES),
    "interrupt": ((1, 3), MODES),
    "long_hold": ((2,), MODES),
    "press_generating": ((2,), MODES),
    "walked_away": ((2,), ("ptt", "baton")),
    "late_release": ((1,), ("ptt", "baton")),
}

# Guesses with no human data behind them (plan), so they stay settings: (centre, spread) in ms.
# The plan doesn't say how late a late press is, or how early an early release: same guess for both.
TIMING = {"pre_roll_ms": (250, 100), "post_roll_ms": (300, 150), "late_share": .10, "early_share": .10,
          "late_ms": (300, 150), "early_ms": (300, 150)}

IDLE_S = 15.0        # silence that ends a hold's claim (meetlab_bot/floor.py:Floor.IDLE_SECS)
STUCK_S = 20.0       # held with nobody audible; above IDLE_S so a walked-away run can pass
YIELD_S = 1.0        # bot audio stopping after someone's speech
HANDOVER_S = .2      # the release tail (plan, settle by running 3): audio this close isn't overlap
SENTENCE_S = .5      # bot audio ending this close to its sentence's end finished that sentence
LONG_HOLD_S = 45.0
LATE_RELEASE_S = 1.75    # the script's +2 s, less clock noise
GENERATING_S = 2.0   # a press this soon after speech lands in the bot's generation (~1.2 s)
SLO = {"reply_p95_s": 2.0, "answered": .95, "wer": .05}  # docs/testing.md

# What the report pairs, with the plan's proposed margin in the worse direction; None: shown only.
MARGINS = {"judged": -.2, "wer": .01, "reply_gate_s": .2, "clipped_first": .02, "clipped_last": .02,
           "reply_p50_s": None, "turns_answered": None, "attribution": None, "talk_over_s": None,
           "bot_share": None, "overlap_s": None}


def cells() -> list[tuple[str, int, str]]:
    return [(s, p, m) for s, (people, modes) in SCENARIOS.items() for p in people for m in modes]


def hold_timing(dialogue: str, line: int, settings: dict = TIMING) -> dict:
    """Seconds from a line's first frame to the press (negative: before it) and from its last frame
    to the release (negative: before it). From a digest of dialogue and line only, so identical
    across modes. A press late or a release early is one person's slip: it clips them, not the system."""
    def spread(name):
        centre, width = settings[name]
        return (centre + _digest(f"{dialogue}:{line}:{name}") % (2 * width + 1) - width) / 1000

    def chance(name):
        return _digest(f"{dialogue}:{line}:{name}") % 1000 < settings[name] * 1000

    late, early = chance("late_share"), chance("early_share")
    return {"press_s": spread("late_ms") if late else -spread("pre_roll_ms"),
            "release_s": -spread("early_ms") if early else spread("post_roll_ms"),
            "press_late": late, "release_early": early}


def run_list() -> list[dict]:
    """Every run, block by block; a block is one scenario, size and repeat, in every mode of that
    cell, so its runs pair. The mode order rotates with cell and repeat. 108 runs."""
    runs, cell = [], 0
    for scenario, (sizes, modes) in SCENARIOS.items():
        for people in sizes:
            five = (scenario == "turn_taking" and people > 1) or scenario == "overlap" or (scenario, people) == ("interrupt", 3)
            for repeat in range(5 if five else 3):
                k = (cell + repeat) % len(modes)
                runs += [{"scenario": scenario, "people": people, "repeat": repeat, "mode": m} for m in modes[k:] + modes[:k]]
            cell += 1
    return runs


# Intervals: lists of (start, end).
def _merge(spans) -> list[tuple[float, float]]:
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _total(merged) -> float:
    return sum(e - s for s, e in merged)


def _shared(a, b) -> float:
    return sum(max(0, min(e1, e2) - max(s1, s2)) for s1, e1 in a for s2, e2 in b)


def _overlap_s(spans) -> float:
    """Time with two or more spans running."""
    events = sorted((t, d) for s, e in spans for t, d in ((s, 1), (e, -1)))
    total, running, last = 0.0, 0, None
    for t, d in events:
        if running >= 2:
            total += t - last
        running, last = running + d, t
    return total


def _closed(tl, key) -> list[tuple[str, float, float]]:
    """holds or floor, with a hold nobody let go ending when the run did."""
    return [(who, s, tl["end"] if e is None else e) for who, s, e in tl.get(key, ())]


def _speech(tl) -> list[tuple[float, float]]:
    return [(x["start"], x["end"]) for x in tl["lines"]]


# Who said what.
def _windows(tl) -> list[tuple[dict, float]]:
    """(line, its speaker's next line's start): the span quality.hearing reads that line's turns from."""
    out = []
    for who in {x["who"] for x in tl["lines"]}:
        own = sorted((x for x in tl["lines"] if x["who"] == who), key=lambda x: x["start"])
        out += zip(own, [x["start"] for x in own[1:]] + [math.inf])
    return out


def _turns_of(tl, line, nxt) -> list[str]:
    return [text for t, who, text in tl["stored"] if who == line["who"] and line["start"] <= t < nxt]


def _heard(tl, windows) -> dict:
    """quality.hearing, one result per speaker."""
    return {who: hearing([(x["start"], nxt, x["text"]) for x, nxt in windows if x["who"] == who],
                         [(t, text) for t, w, text in tl["stored"] if w == who])
            for who in sorted({x["who"] for x, _ in windows})}


def _totals(by_speaker: dict) -> dict:
    n = sum(h["words"] for h in by_speaker.values())
    return {"words": n, "wer": sum(h["errors"] for h in by_speaker.values()) / n if n else None,
            "split": sum(h["fragmented"] for h in by_speaker.values()),
            "missed": sum(h["missed"] for h in by_speaker.values())}


def _clean(windows) -> list[tuple[dict, float]]:
    """Lines nobody slipped on: the same lines in every mode, since the slips come from hold_timing."""
    return [(x, nxt) for x, nxt in windows if not x.get("press_late") and not x.get("release_early")]


def _subsequence(wanted, within) -> bool:
    it = iter(within)
    return all(w in it for w in wanted)


def _clipped(line, turns) -> tuple[bool, bool]:
    """(first, last): the line's first or last 2 words are absent from where they belong in the
    stored text. One word's slack each end, as speech-to-text adds the odd word ("um")."""
    said, stored = words(line["text"]), words(" ".join(turns))
    return not _subsequence(said[:2], stored[:3]), not _subsequence(said[-2:], stored[-3:])


def _holds_line(text, line) -> bool:  # the share sim_attribution.py holds a line to
    want = set(words(line["text"]))
    return len(set(words(text)) & want) >= PRESENT * len(want)


def _attribution(tl, windows) -> tuple[list[dict], list[str], list[str]]:
    """(lines under the right speaker, misattribution problems, merged-turn problems)."""
    right, wrong, merged = [], [], []
    for line, nxt in windows:
        if any(_holds_line(t, line) for t in _turns_of(tl, line, nxt)):
            right.append(line)
        else:
            wrong += sorted({f"{who}: stored {line['who']}'s words" for t, who, text in tl["stored"]
                             if who != line["who"] and line["start"] <= t < nxt and _holds_line(text, line)})
    for t, who, text in tl["stored"]:  # only lines current then: dialogue repeats short phrases
        owners = sorted({x["who"] for x, nxt in windows if x["start"] <= t < nxt and _holds_line(text, x)})
        if len(owners) > 1:
            merged.append(f"{who}: one turn holds " + " and ".join(o + "'s" for o in owners) + " words")
    return right, wrong, merged


# Turns and replies.
def _turns(tl) -> list[dict]:
    """Lines that overlap, counting the holds around them (a baton queue waits holding), are one
    turn: one reply is due. A turn's reply is the first bot audio after its last speech frame and
    before the next turn's first line."""
    holds, items = _closed(tl, "holds"), []
    for x in tl["lines"]:
        own = [(s, e) for who, s, e in holds if who == x["who"] and s <= x["end"] and e >= x["start"]]
        items.append((min([x["start"]] + [s for s, _ in own]), max([x["end"]] + [e for _, e in own]), x, [e for _, e in own]))
    groups = []
    for start, end, x, releases in sorted(items, key=lambda i: i[0]):
        if groups and start <= groups[-1]["until"]:
            g = groups[-1]
            g["until"] = max(g["until"], end)
            g["first"] = min(g["first"], x["start"])
            g["last"] = max(g["last"], x["end"])
            g["releases"] += releases
        else:
            groups.append({"until": end, "first": x["start"], "last": x["end"], "releases": list(releases)})
    out = []
    for g, nxt in zip(groups, [g["first"] for g in groups[1:]] + [math.inf]):
        reply = min((s for s, _ in tl["bot"] if g["last"] <= s < nxt), default=None)
        release = max(g["releases"], default=None)
        out.append({"reply_s": None if reply is None else reply - g["last"],
                    "from_release_s": reply - release if reply is not None and release is not None and release <= reply else None})
    return out


def _gate_reply_s(turn) -> float:
    """The bot can't answer before the later of the last frame and the release: the SLO runs from it."""
    return turn["reply_s"] if turn["from_release_s"] is None else min(turn["reply_s"], turn["from_release_s"])


def _stuck_gaps(mode, tl) -> list[float]:
    """Stretches over STUCK_S with the floor held and nobody audible. Baton's floor is what the bot
    says, not the button, which a walked-away holder leaves down; ptt has only the button."""
    held = _closed(tl, "floor" if mode == "baton" else "holds")
    audible = _merge(_speech(tl) + list(tl["bot"]))
    gaps = []
    for start, end in _merge((s, e) for _, s, e in held):
        cursor = start
        for s, e in audible:
            if s >= end:
                break
            if s > cursor:
                gaps.append(s - cursor)
            cursor = max(cursor, e)
        if end > cursor:
            gaps.append(end - cursor)
    return [g for g in gaps if g > STUCK_S]


def _last_heard(tl, who, since, t) -> float:
    return max([since] + [min(x["end"], t) for x in tl["lines"] if x["who"] == who and x["start"] < t])


def _dead_times(tl) -> list[float]:
    """Baton, press to grant, for presses granted before they let go."""
    floor = _closed(tl, "floor")
    out = []
    for who, press, release in _closed(tl, "holds"):
        grant = min((s for w, s, _ in floor if w == who and press <= s < release), default=None)
        if grant is not None:
            out.append(grant - press)
    return out


def metrics(mode: str, timeline: dict) -> dict:
    """One run's row, the plan's Metrics table. wer and clipped_* leave out lines someone slipped on
    (the same lines in every mode); wer_all and slips_clipped show what those slips cost."""
    tl = timeline
    windows = _windows(tl)
    by_speaker = _heard(tl, windows)
    clean = _totals(_heard(tl, _clean(windows)))
    turns = _turns(tl)
    replies = [t["reply_s"] for t in turns if t["reply_s"] is not None]
    from_release = [t["from_release_s"] for t in turns if t["from_release_s"] is not None]
    clips = [(x, *_clipped(x, _turns_of(tl, x, nxt))) for x, nxt in windows]
    not_late = [first for x, first, _ in clips if not x.get("press_late")]
    not_early = [last for x, _, last in clips if not x.get("release_early")]
    speech, bot = _merge(_speech(tl)), _merge(tl["bot"])
    audible = _total(_merge(speech + bot))
    onsets = [(x["start"], e) for x in tl["lines"] for s, e in tl["bot"] if s <= x["start"] < e]
    right, _, merged = _attribution(tl, windows)
    dead = _dead_times(tl) if mode == "baton" else []
    return {"reply_p50_s": _pct(replies, .5), "reply_p95_s": _pct(replies, .95),
            "reply_release_p50_s": _pct(from_release, .5), "reply_release_p95_s": _pct(from_release, .95),
            "turns": len(turns), "turns_answered": len(replies) / len(turns) if turns else None,
            "by_speaker": by_speaker, "wer": clean["wer"], "wer_all": _totals(by_speaker)["wer"],
            "split": _totals(by_speaker)["split"], "missed": _totals(by_speaker)["missed"],
            "clipped_first": sum(not_late) / len(not_late) if not_late else None,
            "clipped_last": sum(not_early) / len(not_early) if not_early else None,
            "slips_clipped": sum((first and bool(x.get("press_late"))) or (last and bool(x.get("release_early")))
                                 for x, first, last in clips),
            "attribution": len(right) / len(tl["lines"]) if tl["lines"] else None, "merged_turns": len(merged),
            "talk_over_s": _shared(bot, speech), "interruptions": len(onsets),
            "yields": sum(e - onset <= YIELD_S for onset, e in onsets),
            "bot_share": _total(bot) / audible if audible else None, "overlap_s": _overlap_s(_speech(tl)),
            "dead_p50_s": _pct(dead, .5), "dead_p95_s": _pct(dead, .95),
            "stuck_floors": len(_stuck_gaps(mode, tl))}


# The verdict.
def _common(mode, tl) -> list[str]:
    """What holds in every scenario of a mode."""
    problems = []
    windows = _windows(tl)
    for line, nxt in windows:
        turns = _turns_of(tl, line, nxt)
        if not turns:
            problems.append(f"{line['who']}: never transcribed: {line['text']!r}")
            continue
        first, last = _clipped(line, turns)
        if first and not line.get("press_late"):
            problems.append(f"{line['who']}: clipped first words: {line['text']!r}")
        if last and not line.get("release_early"):
            problems.append(f"{line['who']}: clipped last words: {line['text']!r}")
    _, wrong, merged = _attribution(tl, windows)
    problems += wrong + merged
    holds = _closed(tl, "holds")
    if mode == "normal" and (holds or tl.get("floor")):
        problems.append("normal room has floor events")
    if mode != "normal":
        for x in tl["lines"]:
            if not any(w == x["who"] and press - HANDOVER_S <= x["start"] and x["end"] <= release + HANDOVER_S
                       for w, press, release in holds):
                problems.append(f"{x['who']}: audible at {x['start']} with no press")
        for start, _ in tl["bot"]:
            for who, press, release in holds:
                if press <= start < release and start - _last_heard(tl, who, press, start) < IDLE_S:
                    problems.append(f"bot started at {start} while {who} held")
        problems += [f"stuck floor: held {g:.1f} s with nobody audible" for g in _stuck_gaps(mode, tl)]
    if mode == "baton":
        for who, s, e in _closed(tl, "floor"):
            if not who.startswith("bot_") and _shared(_merge(tl["bot"]), [(s, e)]) > HANDOVER_S:
                problems.append(f"bot audible while {who} held the floor")
    else:  # decision 3: speech yields the bot, not the press
        for x in tl["lines"]:
            for s, e in tl["bot"]:
                if s <= x["start"] < e and e - x["start"] > YIELD_S:
                    problems.append(f"bot audible {e - x['start']:.1f} s after {x['who']}'s speech began")
                elif x["start"] < s < x["end"] and min(x["end"], e) - s > YIELD_S:
                    problems.append(f"bot started at {s} over {x['who']}'s speech")
    return problems


def _turn_taking(mode, tl):
    turns = _turns(tl)
    answered = sum(t["reply_s"] is not None for t in turns) / len(turns)
    problems = [f"{answered:.0%} of turns answered"] if answered < SLO["answered"] else []
    p95 = _pct([_gate_reply_s(t) for t in turns if t["reply_s"] is not None], .95)
    if p95 is not None and p95 > SLO["reply_p95_s"]:
        problems.append(f"reply p95 {p95:.2f} s")
    wer = _totals(_heard(tl, _clean(_windows(tl))))["wer"]
    if wer is not None and wer > SLO["wer"]:
        problems.append(f"word error rate {wer:.0%}")
    return problems


def _overlap(mode, tl):
    heard = _overlap_s(_speech(tl))
    if len({x["who"] for x in tl["lines"]}) < 3:
        return ["overlap did not happen: fewer than 3 people heard"]
    if mode != "baton":
        return [] if heard > 0 else ["overlap did not happen: nobody spoke over anyone"]
    problems = [f"overlap heard: {heard:.2f} s"] if heard > HANDOVER_S else []
    pressed = [who for who, press, _ in sorted(_closed(tl, "holds"), key=lambda h: h[1])]
    granted = sorted({who: min(s for w, s, _ in _closed(tl, "floor") if w == who)
                      for who, _, _ in _closed(tl, "floor")}.items(), key=lambda kv: kv[1])
    if [who for who, _ in granted if not who.startswith("bot_")] != pressed:
        problems.append(f"queue out of order: pressed {pressed}, granted {[w for w, _ in granted]}")
    return problems


def _interrupt(mode, tl):
    if mode != "baton":
        hit = any(s <= x["start"] < e for x in tl["lines"] for s, e in tl["bot"])
        return [] if hit else ["interruption did not happen: nobody spoke over the bot"]
    # Decision 2: the bot finishes its sentence, then yields.
    pressed = [(press, e) for _, press, _ in _closed(tl, "holds") for s, e in tl["bot"] if s <= press < e]
    if not pressed:
        return ["interruption did not happen: nobody pressed while the bot spoke"]
    problems = []
    for press, end in pressed:
        due = min((t for t in tl.get("bot_sentence_ends", ()) if t >= press), default=None)
        if due is None or abs(end - due) > SENTENCE_S:
            problems.append(f"bot audio ended at {end}, its sentence at {due}")
    return problems


def _long_hold(mode, tl):
    longest = [(x, nxt) for x, nxt in _windows(tl) if x["end"] - x["start"] >= LONG_HOLD_S]
    if not longest:
        return [f"long hold did not happen: no {LONG_HOLD_S:.0f} s turn"]
    problems = []
    for x, nxt in longest:
        n = len(_turns_of(tl, x, nxt))
        if n > 1:
            problems.append(f"{x['who']}: long turn stored as {n} turns")
        if any(x["start"] <= s < x["end"] for s, _ in tl["bot"]):
            problems.append("bot started during the long turn")
        if mode == "baton" and not any(w == x["who"] and s <= x["start"] and x["end"] <= e
                                       for w, s, e in _closed(tl, "floor")):
            problems.append(f"{x['who']} lost the baton during a long turn")
    return problems


def _press_generating(mode, tl):
    presses = [(who, press) for who, press, _ in _closed(tl, "holds")] if mode != "normal" else \
        [(x["who"], x["start"]) for x in tl["lines"]]
    for x in tl["lines"]:
        for who, press in presses:
            if who != x["who"] and x["end"] <= press <= x["end"] + GENERATING_S \
                    and not any(x["end"] <= s <= press for s, _ in tl["bot"]):
                return []  # what the bot then does is _common's business
    return ["press during generation did not happen"]


def _walked_away(mode, tl):
    holds = _closed(tl, "holds")
    for who, press, release in holds:
        last = _last_heard(tl, who, press, release)
        if release - last >= IDLE_S:
            break
    else:
        return ["walking away did not happen: no hold silent for 15 s"]
    if mode == "ptt":
        return []  # the bot may speak, not must: _common holds it to the idle rule and the stuck floor
    waiting = [(w, p) for w, p, _ in holds if w != who and p <= last + IDLE_S]
    if not waiting:
        return ["walking away did not happen: nobody waiting for the baton"]
    next_who, next_press = min(waiting, key=lambda h: h[1])
    grant = min((s for w, s, _ in _closed(tl, "floor") if w == next_who and s >= next_press), default=None)
    if grant is None or not last + IDLE_S <= grant <= last + STUCK_S:
        return [f"{next_who} granted at {grant}; {who} last heard at {last}"]
    return []


def _late_release(mode, tl):
    late = [release for who, _, release in _closed(tl, "holds")
            for x in tl["lines"] if x["who"] == who and release - x["end"] >= LATE_RELEASE_S]
    if not late:
        return ["late release did not happen"]
    if not any(s >= min(late) for s, _ in tl["bot"]):
        return ["bot never answered after the release"]
    return []  # the bot waiting is _common's business


CHECKS = {"turn_taking": _turn_taking, "overlap": _overlap, "interrupt": _interrupt, "long_hold": _long_hold,
          "press_generating": _press_generating, "walked_away": _walked_away, "late_release": _late_release}


def verdict(mode: str, scenario: str, timeline: dict) -> dict:
    """{"status": "pass" | "fail" | "invalid", "problems": [...]}. Invalid only on infrastructure
    signals, never outcomes (plan), whatever else went wrong: the problems stay on the record."""
    if scenario not in SCENARIOS or mode not in SCENARIOS[scenario][1]:
        raise ValueError(f"no cell for {mode} {scenario}")
    infra = timeline.get("infra", ())
    if set(infra) - set(INFRA_SIGNALS):
        raise ValueError(f"not infrastructure signals: {sorted(set(infra) - set(INFRA_SIGNALS))}")
    if not timeline["lines"]:
        problems = ["nobody spoke"]
    else:
        problems = _common(mode, timeline) + CHECKS[scenario](mode, timeline)
    return {"status": "invalid" if infra else "fail" if problems else "pass", "problems": problems}


def row(run: dict, timeline: dict) -> dict:
    """A run_list() entry with its verdict and metrics: the report's input."""
    return {**run, **verdict(run["mode"], run["scenario"], timeline), **metrics(run["mode"], timeline)}


# The report.
def _fail_upper_95(failed: int, n: int) -> float:
    """Exact (Clopper-Pearson) one-sided bound: 0 of 5 gives the plan's 45%. Bootstrap can't say this:
    resampling 5 clean runs only ever finds clean runs."""
    if failed == n:
        return 1.0

    def at_most(p):
        return sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(failed + 1))
    low, high = failed / n, 1.0
    for _ in range(50):
        mid = (low + high) / 2
        low, high = (mid, high) if at_most(mid) > .05 else (low, mid)
    return low


def _bootstrap(diffs, seed, n) -> tuple[float, float]:
    # ponytail: resamples pairs, ignoring that repeats of a cell may cluster; stratify by cell if they do.
    rng = random.Random(seed)
    means = sorted(fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return means[int(.025 * n)], means[int(.975 * n) - 1]


def _outcome(low, high, margin):
    """The whole interval against the margin: "within", "worse" or, overlapping it, "unclear"."""
    if margin is None:
        return None
    sign = 1 if margin > 0 else -1  # judged quality gates downward
    return "worse" if min(sign * low, sign * high) > sign * margin else \
        "within" if max(sign * low, sign * high) <= sign * margin else "unclear"


def _with_gate(r: dict) -> dict:
    """Reply time the gate compares: from the release where there is one. From the last frame a
    held mode carries its 300 ms post-roll, which alone would break the +200 ms margin."""
    release = r.get("reply_release_p50_s")
    return {**r, "reply_gate_s": r.get("reply_p50_s") if release is None else release}


def report(rows: list[dict], seed: int = 0, boot: int = 2000) -> dict:
    """Pure and offline, from row()s. Per mode, invalid attempts and the failure rate with its
    bound; per held mode, differences from normal over matched scenario, people and repeat, with
    bootstrap intervals. Cells without normal count towards rates only. Decides nothing."""
    valid = {}
    for r in rows:
        if r["status"] != "invalid":
            valid.setdefault((r["scenario"], r["people"], r["repeat"], r["mode"]), _with_gate(r))
    out = {"invalid": {m: sum(r["mode"] == m and r["status"] == "invalid" for r in rows) for m in MODES},
           "modes": {}, "paired": {}}
    for m in MODES:
        got = [r for key, r in valid.items() if key[3] == m]
        failed = sum(r["status"] == "fail" for r in got)
        out["modes"][m] = {"valid": len(got), "failed": failed, "fail_rate": failed / len(got) if got else None,
                           "fail_upper_95": _fail_upper_95(failed, len(got)) if got else None,
                           "stuck_floors": sum(r.get("stuck_floors", 0) for r in got)}
    for m in MODES[1:]:
        out["paired"][m] = {}
        for metric, margin in MARGINS.items():
            diffs = []
            for (s, p, rep, mode), r in valid.items():
                base = valid.get((s, p, rep, "normal"))
                if mode == m and base and r.get(metric) is not None and base.get(metric) is not None:
                    diffs.append(r[metric] - base[metric])
            if diffs:
                low, high = _bootstrap(diffs, seed, boot)
                out["paired"][m][metric] = {"pairs": len(diffs), "diff": fmean(diffs), "low": low, "high": high,
                                            "margin": margin, "outcome": _outcome(low, high, margin)}
    return out
