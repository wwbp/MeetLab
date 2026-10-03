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


def record_turn(room: int, n: int) -> bool:
    """Whether the participant records the bot's reply to its n-th turn (0-based): every
    third turn of the first 10 rooms, 4 per room at most, so at most 40 clips a run."""
    return room < 10 and n < 12 and n % 3 == 1


def reply_for(rooms: list[dict], room: int, said_at: float) -> str | None:
    """The text the bot meant to say in answer to the sentence spoken at said_at: its first
    turn stored after it (a reply is stored when the bot finishes speaking)."""
    r = next((x for x in rooms if x["room"] == room), None)
    later = sorted((u for u in (r or {}).get("turns", []) if u["bot"] and u["ts"] and u["ts"] > said_at),
                   key=lambda u: u["ts"])
    return later[0]["text"] if later else None


def voice(clips: list[tuple[str, str, float | None]]) -> dict:
    """clips: (what the bot meant to say, what a reference transcriber heard in its audio,
    predicted listener rating 1-5). Intelligibility is that word error rate."""
    errors = sum(word_errors(meant, heard) for meant, heard, _ in clips)
    n = sum(len(words(meant)) for meant, _, _ in clips)
    mos = [m for _, _, m in clips if m is not None]
    return {"clips": len(clips), "words": n, "errors": errors, "wer": errors / n if n else None,
            "naturalness": round(sum(mos) / len(mos), 2) if mos else None}


def clip_reply(rooms: list[dict], clip: dict, settle_s: float = 3.0) -> str | None:
    """The reply a recorded clip holds: the one bot turn stored (when it finished) between the
    clip's start and shortly after its end. None when it holds none or parts of several: the
    bot answered half a paused sentence, and no single text says what the clip should be."""
    r = next((x for x in rooms if x["room"] == clip["room"]), {"turns": []})
    inside = [u["text"] for u in r["turns"] if u["bot"] and u["ts"] and clip["start"] < u["ts"] <= clip["end"] + settle_s]
    return inside[0] if len(inside) == 1 else None
