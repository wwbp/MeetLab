"""Generate tests/fixtures/benchmark_prompt.wav using OpenAI TTS.

Run once before benchmarking:
    make benchmark-audio
"""

import asyncio
import wave
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
OUTPUT_PATH = FIXTURES_DIR / "benchmark_prompt.wav"
PROMPT = "What is the capital of France?"
SAMPLE_RATE = 24000


async def main() -> None:
    from openai import AsyncOpenAI

    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from config import load_config, require

    cfg = load_config()
    api_key = require(cfg.openai_api_key, "OPENAI_API_KEY")
    client = AsyncOpenAI(api_key=api_key)

    FIXTURES_DIR.mkdir(exist_ok=True)

    print(f"Generating audio for: '{PROMPT}'")
    response = await client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=PROMPT,
        response_format="wav",
    )
    content = response.content
    OUTPUT_PATH.write_bytes(content)

    with wave.open(str(OUTPUT_PATH), "rb") as wf:
        # getnframes() may return INT32_MAX sentinel; count actual data instead
        raw = b""
        while chunk := wf.readframes(1024):
            raw += chunk
        actual_s = len(raw) / (wf.getsampwidth() * wf.getnchannels() * wf.getframerate())
        print(
            f"Saved {OUTPUT_PATH} — {OUTPUT_PATH.stat().st_size} bytes, "
            f"{wf.getframerate()}Hz, {wf.getnchannels()}ch, {actual_s:.2f}s"
        )


if __name__ == "__main__":
    asyncio.run(main())
