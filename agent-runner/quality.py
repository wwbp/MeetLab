"""Quality scores from a load test's own rooms (load-test readiness L4), the same for every
stack profile, so quality and speed of a configuration come from one run.

Hearing: each sentence the synthetic participant spoke is compared with the user turns the
bot stored while it was current: word error rate, fragmentation (a sentence stored as 2+
turns: the pilot's "chained answers") and missed sentences. Answering, first part: how long
the bot talks for, since a voice reply is heard, not skimmed. Pure; tests/test_quality.py.
"""
import math
import re


# Spellings that differ only between British and American English, for the words our scripts
# use (conversation_script.py): speech-to-text writes American. Real recordings with a wider
# vocabulary need a full normaliser (e.g. Whisper's English one).
SPELLING = {"prioritise": "prioritize", "summarise": "summarize"}


def words(text: str) -> list[str]:
    return [SPELLING.get(w, w) for w in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())]


def word_errors(reference: str, heard: str) -> int:
    """Substitutions, deletions and insertions (word-level edit distance)."""
    ref, hyp = words(reference), words(heard)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1]


def hearing(said: list[tuple[float, float, str]], heard: list[tuple[float, str]]) -> dict:
    """said: (time a sentence started playing, time that room's next one started, its text).
    heard: (time a user turn was stored, its text). A sentence's turns are those stored
    while it was current."""
    errors = n_words = fragmented = missed = 0
    for start, end, text in said:
        turns = [h for t, h in heard if start <= t < end]
        errors += word_errors(text, " ".join(turns))
        n_words += len(words(text))
        fragmented += len(turns) > 1
        missed += not turns
    return {"sentences": len(said), "words": n_words, "errors": errors,
            "wer": errors / n_words if n_words else None, "fragmented": fragmented, "missed": missed}


def reply_lengths(replies: list[str]) -> dict:
    n = sorted(len(words(r)) for r in replies)
    pct = lambda q: n[max(0, math.ceil(q * len(n)) - 1)] if n else None  # noqa: E731
    return {"replies": len(n), "p50_words": pct(0.5), "p95_words": pct(0.95),
            "max_words": n[-1] if n else None, "over_40_words": sum(w > 40 for w in n)}


def score_rooms(rooms: list[dict], start: float, end: float) -> dict:
    """A step's quality across its rooms. Each room: "said" [[time, text]] (the load test's
    record of what it spoke) and "turns" (the conversation's stored turns, from
    /api/meetings/{id}/utterances). Sentences spoken in [start, end) are scored; replies are
    the bot's turns in the window, not counting anything before the first sentence (greeting)."""
    totals = {"sentences": 0, "words": 0, "errors": 0, "fragmented": 0, "missed": 0}
    replies = []
    for room in rooms:
        said = sorted(room["said"])
        spans = [(t, nxt, text) for (t, text), nxt in zip(said, [t for t, _ in said[1:]] + [math.inf]) if start <= t < end]
        h = hearing(spans, [(u["ts"], u["text"]) for u in room["turns"] if not u["bot"] and u["ts"] is not None])
        for k in totals:
            totals[k] += h[k]
        first = said[0][0] if said else math.inf
        replies += [u["text"] for u in room["turns"] if u["bot"] and u["ts"] is not None and start <= u["ts"] < end and u["ts"] >= first]
    totals["wer"] = totals["errors"] / totals["words"] if totals["words"] else None
    return {"hearing": totals, "replies": reply_lengths(replies)}
