"""Render the conversation scripts to speech, once, so load tests can replay them.

The old harness had exactly one fixture — a five-word question — and looped it.
That produces one-word answers, which exercise almost no language-model or
speech-synthesis work and never fill a context window, so a load test built on it
measures the transport and very little else.

These are the turns from conversation_script.py, rendered with different voices
per conversation so concurrent rooms do not all sound like the same person. Run
once; the fixtures are then reused by every run, which keeps load tests
comparable and costs nothing per run.

    make conversation-audio

Fixtures land in tests/fixtures/conversations/ and are deliberately not
committed: they are generated artefacts, and regenerating them is one command.
"""
import asyncio
import hashlib
import os
import sys
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from conversation_script import CONVERSATIONS

FIXTURES = Path(__file__).parent / "fixtures" / "conversations"

# One voice per conversation. Concurrent rooms otherwise present the recogniser
# with the same speaker over and over, which is not what a real load looks like.
VOICES = ["alloy", "echo", "fable", "onyx", "nova"]


def fixture_name(text: str) -> str:
    """Stable filename for a line of dialogue.

    Hashed rather than numbered so that editing one turn does not renumber and
    invalidate every fixture after it.
    """
    return f"{hashlib.sha256(text.encode()).hexdigest()[:16]}.wav"


async def _render(client, text: str, voice: str) -> None:
    path = FIXTURES / fixture_name(text)
    if path.exists():
        print(f"  have  {path.name}  {text[:52]}")
        return
    resp = await client.audio.speech.create(
        model="tts-1", voice=voice, input=text, response_format="wav"
    )
    path.write_bytes(resp.content)
    with wave.open(str(path), "rb") as wf:
        # Streamed WAVs carry a placeholder frame count, so size the clip from
        # the bytes actually written.
        secs = path.stat().st_size / (
            wf.getframerate() * wf.getnchannels() * wf.getsampwidth()
        )
    print(f"  made  {path.name}  {secs:4.1f}s  {text[:52]}")


async def main() -> None:
    from openai import AsyncOpenAI

    from config import load_config, require

    cfg = load_config()
    client = AsyncOpenAI(api_key=require(cfg.openai_api_key, "OPENAI_API_KEY"))

    FIXTURES.mkdir(parents=True, exist_ok=True)
    total = sum(len(c) for c in CONVERSATIONS)
    print(f"Rendering {total} turns across {len(CONVERSATIONS)} conversations")

    for i, convo in enumerate(CONVERSATIONS):
        voice = VOICES[i % len(VOICES)]
        print(f"\nConversation {i} (voice: {voice})")
        for turn in convo:
            await _render(client, turn.text, voice)

    print(f"\nFixtures in {FIXTURES}")


if __name__ == "__main__":
    asyncio.run(main())
