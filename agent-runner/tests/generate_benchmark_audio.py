"""Generate benchmark WAV fixtures using OpenAI TTS.

Run once before benchmarking:
    make benchmark-audio

Produces:
  benchmark_prompt.wav       — short question, ~5 words (original)
  benchmark_prompt_long.wav  — longer question, ~35 words, multi-sentence bot response
"""

import asyncio
import wave
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_RATE = 24000

PROMPTS = {
    "benchmark_prompt.wav": "What is the capital of France?",
    # 23-word question → substantive STT work, elicits a focused 3-4 sentence answer
    # (not an open-ended deep-dive that generates 500+ tokens)
    "benchmark_prompt_long.wav": (
        "Can you briefly explain how DNS translates a domain name into an IP address? "
        "Just the key steps, two or three sentences is fine."
    ),
}


async def _generate(client, api_key_cfg, name: str, text: str) -> None:
    path = FIXTURES_DIR / name
    print(f"Generating '{name}'...")
    print(f"  Text ({len(text.split())} words): {text[:80]}{'...' if len(text) > 80 else ''}")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key_cfg)

    response = await client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=text,
        response_format="wav",
    )
    path.write_bytes(response.content)

    with wave.open(str(path), "rb") as wf:
        raw = b""
        while chunk := wf.readframes(1024):
            raw += chunk
        actual_s = len(raw) / (wf.getsampwidth() * wf.getnchannels() * wf.getframerate())
        print(f"  Saved {path.name} — {path.stat().st_size} bytes, {actual_s:.2f}s")


async def main() -> None:
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from config import load_config, require

    cfg = load_config()
    api_key = require(cfg.openai_api_key, "OPENAI_API_KEY")

    FIXTURES_DIR.mkdir(exist_ok=True)

    for name, text in PROMPTS.items():
        await _generate(None, api_key, name, text)

    print("\nAll audio files generated.")


if __name__ == "__main__":
    asyncio.run(main())
