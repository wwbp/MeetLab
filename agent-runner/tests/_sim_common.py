"""Shared helpers for the local LiveKit audio harnesses (simulate + soak).

Extracted from simulate_meeting.py so the multi-room soak driver can reuse the same
token/HTTP/audio-streaming/DB-collection machinery. Behavior is identical to the
original simulate helpers — simulate_meeting.py now imports from here.
"""
import asyncio
import json
import os
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

import numpy as np  # noqa: F401  (re-exported convenience for callers)
from livekit import api, rtc

import audio_scenarios as audio

RUNNER_URL = os.getenv("AGENT_RUNNER_URL", "http://localhost:7860")
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://transport-server:7880")
API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
BOT_RUNNER_SECRET = os.getenv("BOT_RUNNER_SECRET")
FIXTURE = Path(__file__).parent / "fixtures" / "benchmark_prompt.wav"


def env(name: str, default: str) -> str:
    """os.getenv but treat an empty value (Makefile passes `VAR=`) as the default."""
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


def token(room_name: str, identity: str, ttl_minutes: int = 30) -> str:
    return (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(api.VideoGrants(
            room_join=True, room=room_name,
            can_publish=True, can_subscribe=True, can_publish_data=True,
        ))
        .with_ttl(timedelta(minutes=ttl_minutes))
        .to_jwt()
    )


def request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if BOT_RUNNER_SECRET:
        headers["Authorization"] = f"Bearer {BOT_RUNNER_SECRET}"
    req = urllib.request.Request(f"{RUNNER_URL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def set_config(scope: str, stt_model: str = "", endpointing_ms: str = "",
               llm_model: str = "", tts_provider: str = "", tts_voice: str = "") -> dict:
    """Apply per-room overrides before the bot starts (STT, endpointing, LLM, TTS)."""
    fields: dict = {"scope": scope}
    if stt_model:
        fields["stt_model"] = stt_model
    if endpointing_ms:
        fields["stt_endpointing_ms"] = int(endpointing_ms)
    if llm_model:
        fields["llm_model"] = llm_model
    if tts_provider:
        fields["tts_provider"] = tts_provider
    # Voice must match the provider (OpenAI rejects ElevenLabs voice IDs), so pin a
    # compatible voice whenever we override the provider.
    if tts_voice:
        fields["tts_voice"] = tts_voice
    if len(fields) > 1:
        request("PUT", "/config", fields)
    return fields


def load_speech() -> tuple:
    if not FIXTURE.exists():
        raise SystemExit(f"Missing speech fixture {FIXTURE} — run: make benchmark-audio")
    return audio.load_wav_float(str(FIXTURE))


async def capture_signal(source: rtc.AudioSource, signal, sr: int) -> None:
    """Push a float signal into an already-published AudioSource in 20 ms frames."""
    raw = audio.float_to_pcm16(signal)
    frame_ms = 20
    bytes_per_frame = (sr * frame_ms // 1000) * 2
    for off in range(0, len(raw), bytes_per_frame):
        chunk = raw[off:off + bytes_per_frame]
        spc = len(chunk) // 2
        if spc == 0:
            break
        await source.capture_frame(rtc.AudioFrame(
            data=chunk, sample_rate=sr, num_channels=1, samples_per_channel=spc,
        ))
        await asyncio.sleep(frame_ms / 1000)


async def publish_audio_source(room: rtc.Room, label: str, sr: int) -> rtc.AudioSource:
    """Create + publish one persistent mono audio track; reuse it across turns."""
    source = rtc.AudioSource(sr, 1)
    track = rtc.LocalAudioTrack.create_audio_track(f"sim-{label}", source)
    await room.local_participant.publish_track(track)
    return source


async def stream_float(room: rtc.Room, signal, sr: int, label: str) -> None:
    """Publish a float signal as a real-time mono audio track (20 ms frames)."""
    source = await publish_audio_source(room, label, sr)
    await capture_signal(source, signal, sr)


async def query_bot_metas(session_id: str) -> list[dict]:
    """One-shot: all bot utterances (with timing) for a session. Does not dispose engine."""
    from db.engine import AsyncSessionLocal
    from db.models import Utterance, Conversation, Speaker
    from sqlalchemy import select

    out: list[dict] = []
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(Utterance)
            .join(Conversation, Utterance.conv_id == Conversation.id)
            .join(Speaker, Utterance.speaker_id == Speaker.id)
            .where(Conversation.id == session_id, Speaker.meta["role"].astext == "bot")
            .order_by(Utterance.ts.asc())
        )
        for utt in rows.scalars().all():
            if utt.meta and "timing" in utt.meta:
                out.append({"id": utt.id, "text": utt.text, "meta": utt.meta, "ts": utt.ts})
    return out


async def collect_bot_metas(session_id: str, timeout: float, *, dispose_engine: bool = True) -> list[dict]:
    """Poll the DB for all bot utterances (with timing) for this session."""
    from db.engine import engine

    seen: dict[str, dict] = {}
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            for m in await query_bot_metas(session_id):
                seen[m["id"]] = m
            await asyncio.sleep(1.0)
    finally:
        if dispose_engine:
            await engine.dispose()
    return list(seen.values())


async def conversation_status(session_id: str) -> tuple[str | None, object]:
    """Return (status, ended_at) for a conversation, or (None, None) if missing."""
    from db.engine import AsyncSessionLocal
    from db.models import Conversation
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        row = await db.execute(select(Conversation).where(Conversation.id == session_id))
        conv = row.scalar_one_or_none()
    if conv is None:
        return None, None
    return conv.status, conv.ended_at
