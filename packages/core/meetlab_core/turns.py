"""The bot's structured input and output, one schema for the bot and rejoin.

In: each person's words are a Turn, sent to the model as JSON (who said it is a field, never
a "Name: " prefix to parse back out). Out: the model answers a BotReply under OpenAI's strict
JSON schema (RESPONSE_FORMAT): what to say and to whom. Standard library only (core's
dependencies stay as they are).
"""
import json
from dataclasses import asdict, dataclass, field

SOURCES = ("speech", "chat")


@dataclass(frozen=True)
class Turn:
    speaker_id: str  # the participant's identity, e.g. "ana__1a2b"
    name: str        # their display name
    text: str
    source: str = "speech"  # speech | chat (typed)


@dataclass(frozen=True)
class BotReply:
    reply: str  # what the bot says, spoken as it streams
    addressed_to: list[str] = field(default_factory=list)  # speaker_ids; empty = everyone


REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "What you say aloud, plain spoken words."},
        "addressed_to": {"type": "array", "items": {"type": "string"},
                         "description": "speaker_id of each person you answer; empty for everyone."},
    },
    "required": ["reply", "addressed_to"],
    "additionalProperties": False,
}
RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "bot_reply", "schema": REPLY_SCHEMA, "strict": True}}

# Appended to every Bot settings prompt: the researchers' prompt says how to behave, this says
# how the conversation reaches the model and how it answers.
FORMAT_INSTRUCTIONS = (
    "Each user message is a JSON list of turns, one per person who spoke: "
    '{"speaker_id", "name", "text", "source"} (source "chat" means typed). '
    'Answer with JSON {"reply": what you say aloud, "addressed_to": the speaker_id of each person '
    "you answer, or [] for everyone}."
)


def turn_json(turn: Turn) -> str:
    return json.dumps(asdict(turn), ensure_ascii=False)


def _turn(obj) -> Turn:
    if not (isinstance(obj, dict) and set(obj) == {"speaker_id", "name", "text", "source"}
            and all(isinstance(v, str) for v in obj.values()) and obj["source"] in SOURCES):
        raise ValueError(f"not a turn: {obj!r}")
    return Turn(**obj)


def parse_turns(content: str) -> list[Turn]:
    """The turns in a committed user message: JSON objects as SpeakerLabelInjector wrote them,
    joined by Pipecat's aggregator (several people when they talked over each other). One
    person's consecutive fragments become one turn; empty words are dropped."""
    decoder, i, out = json.JSONDecoder(), 0, []
    content = content.strip()
    while i < len(content):
        obj, i = decoder.raw_decode(content, i)
        t = _turn(obj)
        while i < len(content) and content[i].isspace():
            i += 1
        text = t.text.strip()
        if not text:
            continue
        if out and (out[-1].speaker_id, out[-1].source) == (t.speaker_id, t.source):
            out[-1] = Turn(t.speaker_id, t.name, f"{out[-1].text} {text}", t.source)
        else:
            out.append(Turn(t.speaker_id, t.name, text, t.source))
    return out


def user_message(turns: list[Turn]) -> dict:
    return {"role": "user", "content": json.dumps([asdict(t) for t in turns], ensure_ascii=False)}


def parse_reply(content: str) -> BotReply:
    """The model's answer. Untrusted: anything off-schema is still spoken (a model without
    structured output), addressed to no one."""
    try:
        obj = json.loads(content)
    except ValueError:
        return BotReply(content, [])
    if not isinstance(obj, dict) or not isinstance(obj.get("reply"), str):
        return BotReply(content, [])
    to = obj.get("addressed_to")
    return BotReply(obj["reply"], to if isinstance(to, list) and all(isinstance(s, str) for s in to) else [])


def assistant_message(reply: BotReply) -> dict:
    return {"role": "assistant", "content": json.dumps(asdict(reply), ensure_ascii=False)}
