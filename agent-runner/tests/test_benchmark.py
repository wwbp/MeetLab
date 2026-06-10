"""Pipeline latency benchmark: 10 samples, 2 users + 1 bot.

Measures per-stage latencies stored in Utterance.meta["timing"] by the
instrumented bot pipeline:
  - stt_ms            : last audio frame → transcript committed
                        (endpointing wait + transcription + network roundtrip)
  - llm_ttft_ms       : transcript committed → LLM first token
  - tts_first_ms      : LLM first token → TTS first audio chunk
  - e2e_ms (latency_ms): post-STT pipeline (transcript committed → first TTS audio)
  - total_latency_ms  : stt_ms + e2e_ms (true user-perceived latency)

Run via:
    make benchmark
"""

import asyncio
import json
import os
import sys
import time
import unittest
import urllib.request
import wave
from datetime import timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

from livekit import api, rtc

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

RUNNER_URL = os.getenv("AGENT_RUNNER_URL", "http://localhost:7860")
LIVEKIT_URL_TEST = os.getenv("LIVEKIT_URL", "ws://transport-server:7880")
API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
SAMPLES = int(os.getenv("BENCHMARK_SAMPLES", "10"))
RESPONSE_TIMEOUT = float(os.getenv("BENCHMARK_TIMEOUT", "30"))

WAV_PATH = Path(__file__).parent / "fixtures" / "benchmark_prompt.wav"


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_token(room_name: str, identity: str) -> str:
    return (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(timedelta(minutes=30))
        .to_jwt()
    )


def _post(path: str, body: dict) -> dict:
    url = f"{RUNNER_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


async def _stream_wav(room: rtc.Room) -> None:
    """Stream benchmark_prompt.wav as a LiveKit audio track."""
    with wave.open(str(WAV_PATH), "rb") as wf:
        sample_rate = wf.getframerate()
        num_channels = wf.getnchannels()
        frame_ms = 60
        frame_samples = sample_rate * frame_ms // 1000

        source = rtc.AudioSource(sample_rate=sample_rate, num_channels=num_channels)
        track = rtc.LocalAudioTrack.create_audio_track("bench-audio", source)
        await room.local_participant.publish_track(track)

        while True:
            raw = wf.readframes(frame_samples)
            if not raw:
                break
            samples_per_channel = len(raw) // (2 * num_channels)
            frame = rtc.AudioFrame(
                data=raw,
                sample_rate=sample_rate,
                num_channels=num_channels,
                samples_per_channel=samples_per_channel,
            )
            await source.capture_frame(frame)
            await asyncio.sleep(frame_ms / 1000)


async def _poll_bot_utterance(session_id: str, timeout: float) -> Optional[dict]:
    """Poll the DB for a bot utterance with timing metadata for session_id."""
    from db.engine import AsyncSessionLocal, engine
    from db.models import Utterance, Conversation, Speaker
    from sqlalchemy import select

    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Utterance)
                    .join(Conversation, Utterance.conv_id == Conversation.id)
                    .join(Speaker, Utterance.speaker_id == Speaker.id)
                    .where(
                        Conversation.id == session_id,
                        Speaker.meta["role"].astext == "bot",
                    )
                    .order_by(Utterance.ts.asc())
                )
                for utt in result.scalars().all():
                    if utt.meta and "timing" in utt.meta:
                        return utt.meta
            await asyncio.sleep(1.0)
    finally:
        await engine.dispose()
    return None


# ── benchmark ─────────────────────────────────────────────────────────────────

class TestPipelineBenchmark(unittest.IsolatedAsyncioTestCase):

    async def test_pipeline_latency(self):
        if os.getenv("RUN_BENCHMARK", "").strip() != "1":
            self.skipTest("Set RUN_BENCHMARK=1 to run — use: make benchmark")

        self.assertTrue(
            WAV_PATH.exists(),
            f"Benchmark audio not found at {WAV_PATH}. Run: make benchmark-audio",
        )

        results: list[dict] = []

        for i in range(SAMPLES):
            room_name = f"bench-{uuid4().hex[:8]}"
            print(f"\n[{i+1}/{SAMPLES}] room={room_name}", flush=True)

            # Start bot
            resp = _post("/start", {"room_name": room_name})
            session_id = resp["session_id"]
            bot_identity = resp["bot_identity"]

            # Connect 2 users
            room1 = rtc.Room()
            room2 = rtc.Room()
            uid1 = f"bench_u1_{uuid4().hex[:6]}"
            uid2 = f"bench_u2_{uuid4().hex[:6]}"
            await room1.connect(LIVEKIT_URL_TEST, _make_token(room_name, uid1))
            await room2.connect(LIVEKIT_URL_TEST, _make_token(room_name, uid2))

            # Wait for bot to join and deliver greeting
            await asyncio.sleep(4)

            # User1 streams the benchmark prompt audio
            await _stream_wav(room1)

            # Poll DB for a bot utterance with timing
            meta = await _poll_bot_utterance(session_id, timeout=RESPONSE_TIMEOUT)

            await room1.disconnect()
            await room2.disconnect()

            if meta is None:
                print(f"  WARNING: no timing data within {RESPONSE_TIMEOUT}s — skipping sample")
                continue

            timing = meta.get("timing", {})
            e2e = meta.get("latency_ms")
            llm = timing.get("llm_ttft_ms")
            tts = timing.get("tts_first_ms")
            stt = timing.get("stt_ms")
            total = meta.get("total_latency_ms")

            row = {"stt_ms": stt, "llm_ttft_ms": llm, "tts_first_ms": tts, "e2e_ms": e2e, "total_latency_ms": total}
            results.append(row)
            print(
                f"  stt={stt}ms  llm_ttft={llm}ms  tts_first={tts}ms  e2e={e2e}ms  total={total}ms",
                flush=True,
            )

        _print_stats(results, SAMPLES)
        self.assertGreater(len(results), 0, "No timing samples collected — check bot logs")


# ── reporting ─────────────────────────────────────────────────────────────────

def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    idx = max(0, int(len(vals) * p / 100) - 1)
    return sorted(vals)[idx]


def _print_stats(results: list[dict], total_samples: int) -> None:
    n = len(results)
    print(f"\n{'─'*66}")
    print(f"Pipeline Latency Benchmark  (collected {n}/{total_samples} samples)")
    print(f"{'─'*66}")
    if not results:
        print("No data.")
        return

    rows = [
        ("STT (last audio → transcript)", "stt_ms"),
        ("LLM TTFT", "llm_ttft_ms"),
        ("TTS first chunk", "tts_first_ms"),
        ("Post-STT E2E", "e2e_ms"),
        ("Total (stt + post-stt e2e)", "total_latency_ms"),
    ]
    header = f"{'Stage':<30}  {'N':>3}  {'Mean':>7}  {'P50':>7}  {'P95':>7}  {'Min':>7}"
    print(header)
    print(f"{'─'*66}")
    for label, key in rows:
        vals = sorted(v for r in results if (v := r.get(key)) is not None)
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        p50 = _percentile(vals, 50)
        p95 = _percentile(vals, 95)
        vmin = vals[0]
        print(f"{label:<30}  {len(vals):>3}  {mean:>6.0f}ms  {p50:>6.0f}ms  {p95:>6.0f}ms  {vmin:>6.0f}ms")
    print(f"{'─'*66}\n")


if __name__ == "__main__":
    unittest.main()
