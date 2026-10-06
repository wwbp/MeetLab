"""The load test's conversation library (load-test readiness L6): everyday dialogues from
DailyDialog (Li et al., 2017; ConvLab's mirror; CC BY-NC-SA 4.0, research use), spoken in
varied voices, in rooms of 1 to 3 people, as studies have them.

Pure and deterministic, so every run speaks the same lines in the same voices:
tests/test_conversation_library.py. The audio is made once by
tests/build_conversation_library.py and kept in S3, not in the (public) repo.
"""
import hashlib
import re

# Kokoro's English voices (US and UK, women and men): one per dialogue side.
VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_aoede", "af_kore", "af_river",
          "am_adam", "am_michael", "am_fenrir", "am_puck", "am_echo", "am_liam",
          "bf_emma", "bf_isabella", "bf_alice", "bm_george", "bm_lewis", "bm_daniel"]
PAUSE_SHARE = 20                 # percent of lines spoken with a mid-sentence pause
JOINING = {"and", "but", "so", "because", "or", "then", "which", "though"}


def _n(dialogue_id: str) -> int:
    return int(dialogue_id.rsplit("-", 1)[-1])


def _digest(key: str) -> int:
    return int(hashlib.md5(key.encode()).hexdigest(), 16)


def select(dialogues: list[dict], n: int = 100) -> list[dict]:
    """Held-out (test split) dialogues of 6-10 turns, each 2-30 words: spoken-sized. Topics
    taken in turn, so the rarer ones are not crowded out by the commonest."""
    def fits(d):
        return (d["data_split"] == "test" and 6 <= len(d["turns"]) <= 10
                and all(2 <= len(t["utterance"].split()) <= 30 for t in d["turns"]))
    by_topic: dict[str, list[dict]] = {}
    for d in sorted(filter(fits, dialogues), key=lambda d: _n(d["dialogue_id"])):
        by_topic.setdefault(d["domains"][0], []).append(d)
    queues, out = [by_topic[t] for t in sorted(by_topic)], []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                d = q.pop(0)
                out.append({"id": d["dialogue_id"], "topic": d["domains"][0],
                            "lines": [{"side": k % 2, "text": t["utterance"].strip()} for k, t in enumerate(d["turns"])]})
    return out


MIXES = {
    "study": [1, 2, 1, 3, 1, 2, 1, 2, 1, 3],  # every 10 rooms: five of 1, three of 2, two of 3 (the user's study mix)
    "equal": [1, 2, 3],                       # as many of each (the 100-room spike, 2026-10-05)
}


def participants_for(room: int, mix: str = "study") -> int:
    """People in a room, interleaved so even a small run has every size."""
    sizes = MIXES[mix]
    return sizes[room % len(sizes)]


def room_plan(room: int, library: list[dict], per_room: int = 1, mix: str = "study") -> list[tuple[int, dict]]:
    """(participant, line) in speaking order, through the room's own dialogues back to back (no two
    rooms share one while the library lasts). One person speaks one side and the bot answers in
    place of the other, so they get twice the dialogues to fill the same time; two speak the two
    sides; three take the lines in turn."""
    def need(r):
        return per_room * (2 if participants_for(r, mix) == 1 else 1)
    start = sum(need(r) for r in range(room))
    people = participants_for(room, mix)
    plan = []
    for j in range(need(room)):
        d = library[(start + j) % len(library)]
        if people == 1:
            plan += [(0, line) for line in d["lines"] if line["side"] == 0]
        else:
            plan += [(k % people, line) for k, line in enumerate(d["lines"])]
    return plan


def select_topical(conversations: dict, n: int = 280) -> list[dict]:
    """Topical-Chat (Gopalakrishnan et al., 2019; CDLA-Sharing-1.0): real human-to-human chats,
    held-out (test) conversations of 16+ turns, each 2-40 words: long enough that a room's 10
    minutes are whole conversations, not one repeated. Fixed order, so every run is the same."""
    def fits(c):
        return len(c["content"]) >= 16 and all(2 <= len(m["message"].split()) <= 40 for m in c["content"])
    out = []
    for cid in sorted(k for k, c in conversations.items() if fits(c))[:n]:
        out.append({"id": f"tc-{cid}", "topic": "topical-chat",
                    "lines": [{"side": 0 if m["agent"] == "agent_1" else 1, "text": " ".join(m["message"].split())}
                              for m in conversations[cid]["content"]]})
    return out


def voice_for(dialogue_id: str, side: int) -> str:
    """The same voice for a side every run; the two sides of a dialogue never share one."""
    h = _digest(dialogue_id)
    first = h % len(VOICES)
    return VOICES[first if side == 0 else (first + 1 + (h >> 8) % (len(VOICES) - 1)) % len(VOICES)]


def paused(dialogue_id: str, line: int) -> bool:
    return _digest(f"{dialogue_id}:{line}") % 100 < PAUSE_SHARE


def split_for_pause(text: str) -> tuple[str, str] | None:
    """Where a speaker would pause mid-turn: after a comma, between two sentences, or before a
    joining word, nearest the middle. None for a line too short to pause in."""
    w = text.split()
    cuts = [k for k in range(1, len(w) - 1)
            if w[k - 1][-1] in ",.?!" or re.sub(r"\W", "", w[k]).lower() in JOINING]
    if not cuts:
        return None
    k = min(cuts, key=lambda c: abs(c - len(w) / 2))
    return " ".join(w[:k]), " ".join(w[k:])
