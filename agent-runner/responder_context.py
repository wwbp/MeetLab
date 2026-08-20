"""Builds one ordered, attributed conversation out of many speakers.

The responder worker owns the shared LLM context and the single bot voice, fed by
per-participant listener workers over the bus. Fanning out that way buys
isolation and parallelism, and this module pays the bill it creates.

**Transcripts arrive in completion order, not speaking order.** Each
participant's recogniser finishes when it finishes; if Alice speaks first but
Bob's returns first, appending on arrival puts Bob above Alice and the model
reads a conversation that never happened — Bob answering a question nobody had
asked. The old single pipeline could not produce this, because everything was
serialised through it.

Ordering happens when messages are assembled rather than by holding arrivals in a
buffer, so it costs nothing in latency. Nothing is waited for; the utterances
already in hand are simply sorted before they become context.

Also does the speaker labelling that SpeakerLabelInjector does today, so the
model can follow a group conversation rather than a single merged voice.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Utterance:
    """One thing one participant said.

    Attributes:
        speaker: Display name, already resolved via :func:`display_name`.
        text: What they said.
        spoken_at: When they said it, monotonic seconds. May be None — the
            transcript does not always carry a usable timestamp.
    """

    speaker: str
    text: str
    spoken_at: float | None


def display_name(identity: str, name: str | None) -> str:
    """A name worth putting in front of the model.

    Prefers the name from the LiveKit token. Falls back to the identity with the
    ``__<random>`` postfix stripped — connection-details appends that to keep
    identities unique, and it is noise to a language model.
    """
    if name and name.strip():
        return name.strip()
    return identity.split("__")[0]


def build_messages(utterances: list[Utterance]) -> list[dict]:
    """Ordered, labelled context messages for the shared LLM context.

    Sorted by when each thing was actually said, not when its transcript turned
    up.

    A missing timestamp inherits the last known one, which keeps it where it
    already was instead of sorting it to the front of the conversation. Absent
    timestamps are occasional and local; letting one reorder everything around
    it would be a much worse failure than placing it approximately.

    Ties break on speaker name, not arrival order. Two speakers can genuinely
    share a timestamp, and the same meeting replayed must produce the same
    transcript — arrival order is a property of recogniser timing, not of the
    conversation, so ordering by it makes the output irreproducible.
    """
    filled: list[tuple[float, str, Utterance]] = []
    last_seen = float("-inf")
    for u in utterances:
        if u.spoken_at is not None:
            last_seen = u.spoken_at
        filled.append((last_seen, u.speaker, u))

    return [
        {"role": "user", "content": f"{u.speaker}: {u.text.strip()}"}
        for _, _, u in sorted(filled, key=lambda t: (t[0], t[1]))
        if u.text and u.text.strip()
    ]
