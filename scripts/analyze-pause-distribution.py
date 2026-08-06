"""Measure the real pause distribution in recorded speech, using production's own VAD.

Calibrates the turn-end window against reality instead of judgement. Drives
pipecat's SileroVADAnalyzer — the same component the bot pipeline uses — over
per-speaker WAVs, reports how long people actually pause mid-turn, and shows how
many segments each candidate endpointing threshold would produce.

Pair the segment counts with the real turn count from the database:

    SELECT count(*) FROM utterances u JOIN conversations c ON c.id=u.conv_id
    WHERE c.room_name = '<room>' AND u.speaker_id NOT LIKE 'bot\\_%' AND u.text <> '';

The threshold where predicted segments == real turns is the target. See
tests/test_turn_calibration.py for the values this produced on 2026-07-30.

Get audio with:
    aws s3 ls s3://<bucket>/recordings/ | grep audio- | grep -v audio-bot_

NOTE: those WAVs are real participants' voices. Analyse them, do not commit them.

Drives pipecat's SileroVADAnalyzer — the exact component in the bot pipeline —
over real participant audio from 2026-07-30, the worst pilot day. Reports the
distribution of silences *inside* a person's speech, then asks how many of those
each candidate endpointing threshold would mistake for the end of a turn.

Usage: uv run python analyze_pauses.py <wav>...
"""
import asyncio
import sys
import wave
import statistics as st

import numpy as np
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

CANDIDATES = [100, 200, 300, 450, 600, 800]
SR = 16000
CHUNK = 512  # samples per analyze_audio call at 16k = 32ms


def load_pcm16(path):
    with wave.open(path, "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if sr != SR:  # nearest-neighbour resample; adequate for VAD
        idx = (np.arange(int(len(a) * SR / sr)) * sr / SR).astype(int)
        a = a[idx[idx < len(a)]]
    return a


async def speech_runs(path):
    """Return (duration_s, [(start_s, end_s), ...]) using raw voice confidence.

    stop_secs is set very low so the analyzer reports boundaries rather than
    applying a hangover — we want the raw silences so we can evaluate candidate
    thresholds ourselves.
    """
    vad = SileroVADAnalyzer(params=VADParams(stop_secs=0.05, start_secs=0.05))
    vad.set_sample_rate(SR)
    pcm = load_pcm16(path)

    runs, cur = [], None
    for i in range(0, len(pcm) - CHUNK, CHUNK):
        t = i / SR
        state = await vad.analyze_audio(pcm[i : i + CHUNK].tobytes())
        speaking = state in (VADState.SPEAKING, VADState.STARTING)
        if speaking and cur is None:
            cur = t
        elif not speaking and cur is not None:
            runs.append((cur, t))
            cur = None
    if cur is not None:
        runs.append((cur, len(pcm) / SR))
    return len(pcm) / SR, runs



async def main():
    all_gaps = []
    print(f"{'speaker':<12} {'dur':>7} {'speech':>7} {'runs':>5} {'gaps':>5}   gap percentiles (ms)")
    for path in sys.argv[1:]:
        name = path.split("audio-")[-1].split("__")[0][:11]
        dur, runs = await speech_runs(path)
        if len(runs) < 2:
            print(f"{name:<12} (too little speech detected)")
            continue
        gaps = [(runs[i + 1][0] - runs[i][1]) * 1000.0 for i in range(len(runs) - 1)]
        gaps = [g for g in gaps if g > 0]
        all_gaps.extend(gaps)
        speech = sum(e - s for s, e in runs)
        qs = st.quantiles(gaps, n=100) if len(gaps) > 2 else None
        q = (lambda v: qs[v - 1]) if qs else (lambda v: float("nan"))
        print(f"{name:<12} {dur:6.0f}s {speech:6.0f}s {len(runs):5d} {len(gaps):5d}   "
              f"p25={q(25):5.0f} p50={q(50):5.0f} p75={q(75):5.0f} p90={q(90):5.0f} max={max(gaps):6.0f}")

    if not all_gaps:
        sys.exit("no gaps measured")

    print(f"\nPooled: {len(all_gaps)} intra-speaker silences across {len(sys.argv)-1} real participants")
    qs = st.quantiles(all_gaps, n=100)
    for pct in (10, 25, 50, 75, 90, 95, 99):
        print(f"  p{pct:<3} {qs[pct-1]:6.0f} ms")

    print("\nWhat each endpointing threshold does to real speech:")
    print(f"  {'threshold':>10}  {'pauses read as turn-end':>24}  {'share of all pauses':>21}")
    for c in CANDIDATES:
        n = sum(1 for g in all_gaps if g >= c)
        mark = "   <-- pilot" if c == 100 else ("   <-- new default" if c == 450 else "")
        print(f"  {c:>7} ms  {n:>24}  {100.0*n/len(all_gaps):>20.1f}%{mark}")


asyncio.run(main())
