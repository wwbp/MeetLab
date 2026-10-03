"""Answer quality (load-test readiness L4, step 2): each bot reply, with the conversation
before it, scored 1-5 by a fixed judge model against a fixed rubric. Same for every stack
profile; a change to the rubric or the judge bumps RUBRIC_VERSION, and only results with the
same version compare. Only the load test's synthetic conversations are sent, never a study's.

Everything but the model call (ask) is pure: tests/test_judge.py.
"""
import json
import os
import urllib.request

from conversation_script import CONVERSATIONS

RUBRIC_VERSION = "2026-10-03.1"
JUDGE_MODEL = "gpt-5.4"
FOLLOW_UP = {t.text: t.follow_up for c in CONVERSATIONS for t in c}
CRITERIA = ("answers", "spoken", "overall")

RUBRIC = """You are scoring one reply from a voice assistant taking part in a spoken conversation.
The person spoke; their words were transcribed; the assistant's reply is spoken back aloud.

Score the reply from 1 (poor) to 5 (excellent) on:
- "answers": does it respond to what the person just said, correctly and usefully?
- "spoken": does it suit being heard rather than read: short enough to follow by ear (two or
  three sentences), natural, no lists or markdown?
- "overall": how good a turn in this conversation is it?
{context_rule}
Reply with JSON only, for example {example}."""


def cases(rooms: list[dict], start: float, end: float, limit: int = 40) -> list[dict]:
    """The bot's replies in [start, end) to sentences the participant spoke (not the greeting),
    each with the conversation before it; at most `limit`, spread evenly across the run."""
    out = []
    for room in rooms:
        said = sorted(room["said"])
        turns = sorted((u for u in room["turns"] if u["ts"] is not None), key=lambda u: u["ts"])
        for k, u in enumerate(turns):
            if not u["bot"] or not (start <= u["ts"] < end):
                continue
            current = [text for t, text in said if t <= u["ts"]]
            if not current:
                continue  # before anyone spoke: the greeting
            out.append({"reply": u["text"], "follow_up": FOLLOW_UP.get(current[-1], False),
                        "history": [("bot" if v["bot"] else "person", v["text"]) for v in turns[:k]]})
    if len(out) > limit:
        out = [out[round(i * len(out) / limit)] for i in range(limit)]
    return out


def prompt(case: dict) -> str:
    context_rule = ('- "context": this is a follow-up that only makes sense given what was said earlier; '
                    "does the reply use that earlier context correctly?\n") if case["follow_up"] else ""
    keys = ("answers", "context", "spoken", "overall") if case["follow_up"] else CRITERIA
    rubric = RUBRIC.format(context_rule=context_rule, example=json.dumps({k: 4 for k in keys}))
    history = "\n".join(f"{'Assistant' if who == 'bot' else 'Person'}: {text}" for who, text in case["history"])
    return f"{rubric}\n\nConversation so far:\n{history}\n\nReply to score: {case['reply']}"


def parse_scores(text: str, follow_up: bool) -> dict | None:
    """The judge's scores, or None if the verdict is unusable (it is counted as dropped)."""
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None
    keys = CRITERIA + (("context",) if follow_up else ())
    scores = {k: raw.get(k) for k in keys}
    if not all(isinstance(v, int) and 1 <= v <= 5 for v in scores.values()):
        return None
    return scores


def summarise(verdicts: list[dict | None]) -> dict:
    judged = [v for v in verdicts if v]
    out = {"rubric": RUBRIC_VERSION, "judge": JUDGE_MODEL, "judged": len(judged), "dropped": len(verdicts) - len(judged)}
    for k in CRITERIA + ("context",):
        values = [v[k] for v in judged if k in v]
        out[k] = round(sum(values) / len(values), 2) if values else None
    return out


def ask(case: dict, api_key: str = "") -> dict | None:
    """One case to the judge model (OpenAI chat completions, JSON reply)."""
    body = {"model": JUDGE_MODEL, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt(case)}]}
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", json.dumps(body).encode(), {
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key or os.environ['OPENAI_API_KEY']}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return parse_scores(json.loads(r.read())["choices"][0]["message"]["content"], case["follow_up"])
    except Exception:
        return None  # counted as dropped, never a crash in the report
