"""Measure how much earlier the bot can notice speech than it used to.

Interruption used to trigger on the VAD's SPEAKING transition, because that is the
only onset VADProcessor emits. But the analyzer decides something earlier:

    QUIET --(first frame over threshold)--> STARTING --(start_secs)--> SPEAKING

STARTING is the first 32ms frame that clears confidence and min_volume. SPEAKING is
that same evidence, confirmed. Segmentation needs the confirmation; interruption
does not — waiting for it is pure latency, and it is what "the bot keeps speaking"
felt like. bot.py now yields on the earlier edge (see speech_onset_vad.py).

This script measures the gap on real speech, using production's own analyzer with
production's parameters. The gap is deterministic by construction:

    delta = (_vad_start_frames - 1) x frame_ms
          = (round(start_secs / frame_ms) - 1) x frame_ms

so the script prints that prediction next to the measurement. They should agree
exactly; if they diverge, either the params or pipecat's state machine changed and
the interruption path deserves another look.

Result on 2026-08-19 (pipecat 1.4.0, 67 utterances across 5 real recordings):
STARTING fired **160 ms** earlier than SPEAKING, with zero variance — 5 frames of
32 ms, exactly as predicted.

Get audio with:
    aws s3 ls s3://<bucket>/recordings/ | grep audio- | grep -v audio-bot_

NOTE: those WAVs are real participants' voices. Analyse them, do not commit them.

Usage: cd agent-runner && uv run python ../scripts/measure-interruption-onset.py <wav>...
"""
import asyncio
import statistics as st
import sys
import wave

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

SR = 16000
ENDPOINTING_MS = 450  # production default: bot_config.stt_endpointing_ms


def read_pcm(path):
    """Return mono 16-bit PCM at SR, or None if the file is not that."""
    with wave.open(path) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SR, 1, 2):
            return None
        return w.readframes(w.getnframes())


async def measure(path):
    """Return (deltas_ms, frame_ms, start_frames) for one recording."""
    analyzer = SileroVADAnalyzer(params=VADParams(stop_secs=ENDPOINTING_MS / 1000))
    analyzer.set_sample_rate(SR)

    frame_samples = analyzer.num_frames_required()
    frame_bytes = frame_samples * 2
    frame_ms = frame_samples / SR * 1000.0
    start_frames = analyzer._vad_start_frames

    pcm = read_pcm(path)
    if pcm is None:
        return None, frame_ms, start_frames

    prev = VADState.QUIET
    started_at = None
    deltas = []

    for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        t_ms = (i // frame_bytes) * frame_ms
        state = await analyzer.analyze_audio(pcm[i : i + frame_bytes])

        # Any move off QUIET is the interruption trigger.
        if prev == VADState.QUIET and state != VADState.QUIET:
            started_at = t_ms
        # Promotion to SPEAKING is what we used to wait for.
        if state == VADState.SPEAKING and prev != VADState.SPEAKING and started_at is not None:
            deltas.append(t_ms - started_at)
            started_at = None
        # A start that retracted without confirming is not a measurable pair.
        if state == VADState.QUIET:
            started_at = None
        prev = state

    return deltas, frame_ms, start_frames


async def main():
    paths = sorted(sys.argv[1:])
    if not paths:
        print(__doc__.strip().splitlines()[-1])
        return 2

    pooled = []
    frame_ms = start_frames = None

    print(f"{'recording':<52} {'utterances':>10} {'median':>9} {'max':>8}")
    print("-" * 82)
    for path in paths:
        deltas, frame_ms, start_frames = await measure(path)
        name = path.split("/")[-1][:50]
        if deltas is None:
            print(f"{name:<52} {'skipped — not 16k mono 16-bit':>40}")
            continue
        if not deltas:
            print(f"{name:<52} {0:>10} {'—':>9} {'—':>8}")
            continue
        pooled.extend(deltas)
        print(f"{name:<52} {len(deltas):>10} {st.median(deltas):>8.0f}ms {max(deltas):>7.0f}ms")

    print("-" * 82)
    if not pooled:
        print("No utterances detected. Is this audio silent, or below min_volume?")
        return 1

    ordered = sorted(pooled)
    predicted = (start_frames - 1) * frame_ms
    print(f"VAD frame                  : {frame_ms:.0f} ms  ({start_frames} frames to confirm)")
    print(f"Pooled                     : {len(ordered)} utterances across {len(paths)} recordings")
    print(f"STARTING earlier by        : median {st.median(ordered):.0f} ms")
    print(f"                             p90    {ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]:.0f} ms")
    print(f"                             range  {min(ordered):.0f}–{max(ordered):.0f} ms")
    print(f"Predicted by the state machine: {predicted:.0f} ms", end="")
    print("  ✓ agrees" if abs(st.median(ordered) - predicted) < 1e-6 else "  ✗ DIVERGED — see module docstring")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
