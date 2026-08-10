"""Generate a benchmark WAV that speaks with natural mid-sentence pauses.

Why this exists
---------------
Every fixture in ``generate_benchmark_audio.py`` is a single continuous phrase
with clean trailing silence — "What is the capital of France?". That shape always
produces a **one-fragment** user turn, which is the fast path: ~390ms STT.

The Jul/Aug 2026 pilot showed the opposite shape is what real people produce. A
participant thinking aloud pauses mid-sentence, and with ``stt_endpointing_ms``
at 100ms every one of those pauses closes a segment. Those fragments accumulate
into a single turn that is only committed by Pipecat's ``user_turn_stop_timeout``
— 5.0s by default (RC1). Median latency for a 5+ fragment turn in production was
**5198ms** versus **390ms** for a one-fragment turn.

So our benchmark has never been able to see the bug: it only ever measured the
one case that isn't affected. This fixture closes that blind spot.

Usage
-----
    make benchmark-audio-paused                       # generate (needs OPENAI_API_KEY)
    PAUSE_MS=400 python tests/generate_paused_speech_audio.py

Then benchmark against it:

    make benchmark-full BENCHMARK_SAMPLES=10 \
        BENCHMARK_WAV=tests/fixtures/benchmark_prompt_paused.wav \
        BENCHMARK_CONFIGS="<label>"

``PAUSE_MS`` must exceed ``stt_endpointing_ms`` for a pause to split a segment.
The 400ms default is a natural thinking pause: four times the pilot's 100ms
endpointing, and far below any gap a human would read as "I've finished".

See ``docs/pilot-postmortem-2026-08.md`` for the production evidence.
"""

import asyncio
import io
import os
import sys
import wave
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
OUTPUT_NAME = "benchmark_prompt_paused.wav"

# Silence inserted between clauses. Above stt_endpointing_ms → each gap closes a
# segment, so this one question arrives as len(CLAUSES) fragments.
PAUSE_MS = int(os.getenv("PAUSE_MS", "400"))

# One question, delivered the way a person actually delivers it — in clauses,
# with thinking pauses. Deliberately mirrors the pilot transcripts, e.g.
#   'rahul: Yeah. rahul: And uh rahul: Uh we have not uh rahul: Any rahul: concrete e…'
CLAUSES = [
    "So I think candidate B is the stronger one here",
    "um",
    "mainly because of the events they organized on campus",
    "and",
    "I'd want to know what the committee thinks about that",
]


async def _synthesize(api_key: str, text: str) -> bytes:
    """TTS one clause and return its raw WAV bytes."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    response = await client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input=text,
        response_format="wav",
    )
    return response.content


def _read_pcm(wav_bytes: bytes) -> tuple[bytes, int, int, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        params = (wf.getframerate(), wf.getnchannels(), wf.getsampwidth())
        frames = wf.readframes(wf.getnframes())
    return frames, *params


async def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from config import load_config, require

    api_key = require(load_config().openai_api_key, "OPENAI_API_KEY")
    FIXTURES_DIR.mkdir(exist_ok=True)

    print(f"Generating '{OUTPUT_NAME}' — {len(CLAUSES)} clauses, {PAUSE_MS}ms pauses")

    segments = []
    fmt = None
    for i, clause in enumerate(CLAUSES, 1):
        print(f"  [{i}/{len(CLAUSES)}] {clause!r}")
        pcm, rate, channels, width = _read_pcm(await _synthesize(api_key, clause))
        if fmt is None:
            fmt = (rate, channels, width)
        elif fmt != (rate, channels, width):
            raise RuntimeError(f"clause {i} format {(rate, channels, width)} != {fmt}")
        segments.append(pcm)

    rate, channels, width = fmt
    silence = b"\x00" * int(rate * channels * width * PAUSE_MS / 1000)

    # Trailing silence too: the turn has to actually end for the bot to reply.
    body = silence.join(segments) + silence * 2

    out = FIXTURES_DIR / OUTPUT_NAME
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(body)

    duration_s = len(body) / (rate * channels * width)
    print(
        f"\nSaved {out.name} — {out.stat().st_size} bytes, {duration_s:.2f}s, "
        f"{len(CLAUSES)} fragments @ {rate}Hz"
    )
    print(
        "Expect this to land in the '5+ fragments' bucket from the postmortem "
        "(p50 5198ms) rather than the '1 fragment' bucket (p50 390ms)."
    )


if __name__ == "__main__":
    asyncio.run(main())
