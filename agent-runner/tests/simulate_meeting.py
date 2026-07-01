"""Local meeting simulation harness.

Streams synthetic audio scenarios into a real LiveKit room (the --dev
transport-server) and reports the STT/VAD diagnostics, so the failure modes
behind high STT latency can be reproduced and the VAD tuned BEFORE a live
meeting — the "we only get one shot" constraint.

Scenarios (SCENARIO env or argv[1]):
    noise      speech fixture + continuous background noise at SNR_DB
    noise-bed  continuous noise, NO speech (held-open VAD / phantom segments)
    overlap    SPEAKERS participants streaming speech concurrently (queue backlog)
    inaudible  infrasonic (~12 Hz) + near-Nyquist (~11 kHz) tone beds — does
               non-audible energy reach the VAD, or is it filtered?
    echo       capture the bot's own TTS and replay it as a participant
               (the speakerphone re-capture path → self-echo diagnostic)

Knobs (env): SNR_DB=5, NOISE=pink|white|hum|hf, DURATION=12, SPEAKERS=3,
STT_MODEL, ENDPOINTING_MS, CAPTURE_SECS=6.

Run inside the agent-runner container:
    make simulate SCENARIO=noise SNR_DB=3
"""
import asyncio
import os
import sys
import time
from uuid import uuid4

import numpy as np
from livekit import rtc

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import audio_scenarios as audio
from _sim_common import (
    LIVEKIT_URL,
    collect_bot_metas,
    env as _env,
    load_speech as _speech,
    request as _request,
    set_config,
    stream_float as _stream_float,
    token as _token,
)


SCENARIO = _env("SCENARIO", sys.argv[1] if len(sys.argv) > 1 else "noise")
SNR_DB = float(_env("SNR_DB", "5"))
NOISE = _env("NOISE", "pink")
DURATION = float(_env("DURATION", "12"))
SPEAKERS = int(_env("SPEAKERS", "3"))
STT_MODEL = _env("STT_MODEL", "")
ENDPOINTING_MS = _env("ENDPOINTING_MS", "")
CAPTURE_SECS = float(_env("CAPTURE_SECS", "6"))
SETTLE_SECS = float(_env("SETTLE_SECS", "4"))
COLLECT_TIMEOUT = float(_env("COLLECT_TIMEOUT", "30"))


# ── http + token helpers (shared with the soak harness, see _sim_common) ──────

def _maybe_set_config(scope: str) -> None:
    """Apply per-room STT_MODEL / ENDPOINTING_MS overrides before the bot starts."""
    fields = set_config(scope, stt_model=STT_MODEL, endpointing_ms=ENDPOINTING_MS)
    if len(fields) > 1:
        print(f"  config[{scope}]: "
              + ", ".join(f"{k}={v}" for k, v in fields.items() if k != "scope"))


# ── audio streaming ───────────────────────────────────────────────────────────

async def _capture_bot_audio(room: rtc.Room, seconds: float) -> tuple[np.ndarray, int]:
    """Subscribe to the bot's published audio track and record `seconds` of it."""
    chunks: list[bytes] = []
    captured_sr = [audio.SAMPLE_RATE]
    done = asyncio.Event()

    @room.on("track_subscribed")
    def _on_sub(track, publication, participant):  # noqa: ANN001
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if not (participant.identity or "").startswith("bot_"):
            return

        async def _read():
            stream = rtc.AudioStream(track)
            deadline = time.monotonic() + seconds
            async for event in stream:
                captured_sr[0] = event.frame.sample_rate
                chunks.append(bytes(event.frame.data))
                if time.monotonic() > deadline:
                    break
            await stream.aclose()
            done.set()

        asyncio.create_task(_read())

    try:
        await asyncio.wait_for(done.wait(), timeout=seconds + 10)
    except asyncio.TimeoutError:
        pass
    if not chunks:
        return np.zeros(0, dtype=np.float32), captured_sr[0]
    return audio.pcm16_to_float(b"".join(chunks)), captured_sr[0]


# ── scenario signal builders ──────────────────────────────────────────────────

def _noise_signal(n: int) -> np.ndarray:
    if NOISE == "white":
        return audio.white_noise(n, seed=11)
    if NOISE == "hum":
        return audio.tone(120.0, n, amplitude=0.3)
    if NOISE == "hf":
        return audio.tone(7500.0, n, amplitude=0.3)
    return audio.pink_noise(n, seed=11)  # default


# ── DB collection ─────────────────────────────────────────────────────────────

_collect_bot_metas = collect_bot_metas  # poll the DB for bot utterances (shared helper)


# ── report ────────────────────────────────────────────────────────────────────

def _report(metas: list[dict]) -> None:
    print(f"\n{'─'*70}")
    print(f"Simulation: {SCENARIO}   (noise={NOISE} snr={SNR_DB}dB "
          f"speakers={SPEAKERS} dur={DURATION}s)")
    print(f"{'─'*70}")
    if not metas:
        print("No bot responses with timing were produced.")
        print("  → For noise-bed / inaudible this is EXPECTED (no real speech).")
        print("  → Check phantom/held-open VAD via:  make logs SERVICE=agent-runner")
        print(f"{'─'*70}\n")
        return
    print(f"{'#':>2}  {'stt_ms':>8}  {'total_ms':>8}  {'qdepth':>6}  {'spike':>5}  {'echo':>4}  text")
    for i, m in enumerate(metas, 1):
        meta = m["meta"]
        timing = meta.get("timing", {})
        diag = meta.get("diag", {})
        print(f"{i:>2}  {str(timing.get('stt_ms','-')):>8}  "
              f"{str(meta.get('total_latency_ms','-')):>8}  "
              f"{str(diag.get('queue_depth','-')):>6}  "
              f"{('YES' if diag.get('stt_spike') else '-'):>5}  "
              f"{('YES' if diag.get('self_echo') else '-'):>4}  "
              f"{(m['text'] or '')[:40]}")
    print(f"{'─'*70}\n")


# ── main ────────────────────────────────────────────────────────────────────

async def main() -> None:
    room_name = f"sim-{SCENARIO}-{uuid4().hex[:6]}"
    print(f"[simulate] scenario={SCENARIO} room={room_name}")
    _maybe_set_config(room_name)

    resp = _request("POST", "/start", {"room_name": room_name})
    session_id = resp["session_id"]
    print(f"  bot started: session={session_id} identity={resp['bot_identity']}")

    speech, sr = _speech()
    rooms: list[rtc.Room] = []

    async def join(identity: str) -> rtc.Room:
        room = rtc.Room()
        await room.connect(LIVEKIT_URL, _token(room_name, identity))
        rooms.append(room)
        return room

    try:
        await asyncio.sleep(SETTLE_SECS)  # let the bot join + greet

        if SCENARIO == "noise":
            r = await join(f"sim_u_{uuid4().hex[:5]}")
            noisy = audio.mix_at_snr(speech, _noise_signal(len(speech)), SNR_DB)
            await _stream_float(r, noisy, sr, "noisy-speech")

        elif SCENARIO == "noise-bed":
            r = await join(f"sim_noise_{uuid4().hex[:5]}")
            n = int(sr * DURATION)
            await _stream_float(r, _noise_signal(n), sr, "noise-bed")

        elif SCENARIO == "overlap":
            async def speaker(idx: int):
                room = await join(f"sim_spk{idx}_{uuid4().hex[:4]}")
                await asyncio.sleep(0.15 * idx)  # slight stagger, still overlapping
                await _stream_float(room, speech, sr, f"spk{idx}")
            await asyncio.gather(*(speaker(i) for i in range(SPEAKERS)))

        elif SCENARIO == "inaudible":
            n = int(sr * DURATION)
            r1 = await join(f"sim_infra_{uuid4().hex[:4]}")
            r2 = await join(f"sim_hf_{uuid4().hex[:4]}")
            await asyncio.gather(
                _stream_float(r1, audio.tone(12.0, n, amplitude=0.9), sr, "infrasonic-12hz"),
                _stream_float(r2, audio.tone(11000.0, n, amplitude=0.9), sr, "near-nyquist-11khz"),
            )

        elif SCENARIO == "echo":
            listener = await join(f"sim_listen_{uuid4().hex[:4]}")
            print(f"  capturing bot audio for {CAPTURE_SECS}s ...")
            bot_audio, bot_sr = await _capture_bot_audio(listener, CAPTURE_SECS)
            if len(bot_audio) == 0:
                print("  WARNING: captured no bot audio (no greeting?). "
                      "Try raising CAPTURE_SECS or check the bot is speaking.")
            else:
                print(f"  captured {len(bot_audio)/bot_sr:.1f}s of bot audio @ {bot_sr}Hz; replaying")
                speaker_room = await join(f"sim_echo_{uuid4().hex[:4]}")
                await _stream_float(speaker_room, bot_audio, bot_sr, "bot-echo")

        else:
            raise SystemExit(f"Unknown SCENARIO={SCENARIO!r}")

        metas = await _collect_bot_metas(session_id, COLLECT_TIMEOUT)
        _report(metas)
    finally:
        for room in rooms:
            try:
                await room.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
