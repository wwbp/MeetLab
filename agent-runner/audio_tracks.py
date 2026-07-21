"""Per-speaker audio track capture: buffer each participant's PCM and write WAV.

Source separation already exists at the frame level — the LiveKit transport
delivers UserAudioRawFrame(user_id=participant_sid) per participant track (see
multi_speaker_stt.py). This module taps that stream to persist one WAV file per
speaker, without doing any I/O on the pipeline event loop:

  - PerSpeakerAudioRecorder (a FrameProcessor) only enqueues PCM and forwards
    frames unchanged — never awaits, never touches storage.
  - AudioTrackSink drains the queue on a background task, buffers per-SID PCM in
    memory, and encodes + writes WAV on flush (participant leave / session end).

Iteration 1 implements only pcm_to_wav; the processor and sink follow.
"""

import asyncio
import io
import os
import wave
from collections.abc import Awaitable, Callable

from loguru import logger

from pipecat.frames.frames import Frame, TTSAudioRawFrame, UserAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import storage

_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM


def pcm_to_wav(pcm: bytes, sample_rate: int, num_channels: int) -> bytes:
    """Wrap raw 16-bit PCM bytes in a WAV container. Pure — no I/O."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(num_channels)
        w.setsampwidth(_SAMPLE_WIDTH_BYTES)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class PerSpeakerAudioRecorder(FrameProcessor):
    """Pipeline tap that offers each participant's PCM to an AudioTrackSink.

    Sits BEFORE MultiSpeakerSTT in the pipeline, so it must forward every frame
    unchanged (multi_stt still needs the audio). The only work done on the event
    loop is a single non-blocking sink.offer() call — no encoding, no I/O. A sink
    that raises is logged and swallowed so audio never stalls.
    """

    def __init__(self, sink):
        super().__init__()
        self._sink = sink

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if (
            direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, UserAudioRawFrame)
            and frame.user_id
        ):
            try:
                self._sink.offer(frame.user_id, frame.audio, frame.sample_rate, frame.num_channels)
            except Exception as e:
                logger.warning(f"PerSpeakerAudioRecorder: sink.offer error: {e}")
        await self.push_frame(frame, direction)


class BotAudioRecorder(FrameProcessor):
    """Taps the bot's own TTS output into the sink as one more speaker track.

    Sits after the TTS service so it sees TTSAudioRawFrame; offers each chunk under
    a fixed sid (the bot identity) so the bot gets its own source-separated WAV
    alongside the human participants. Same non-blocking contract as
    PerSpeakerAudioRecorder — one offer(), then forward unchanged.
    """

    def __init__(self, sink, bot_sid: str):
        super().__init__()
        self._sink = sink
        self._bot_sid = bot_sid

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TTSAudioRawFrame):
            try:
                self._sink.offer(self._bot_sid, frame.audio, frame.sample_rate, frame.num_channels)
            except Exception as e:
                logger.warning(f"BotAudioRecorder: sink.offer error: {e}")
        await self.push_frame(frame, direction)


# on_flush(sid, wav_bytes, meta) — where a finished speaker track goes (storage + DB).
OnFlush = Callable[[str, bytes, dict], Awaitable[None]]

# Force a rolling part-file when a single speaker's buffer passes this many bytes,
# so a runaway recording can't grow the runner's memory without bound. 128 MiB of
# 16 kHz mono 16-bit PCM ≈ 70 min of continuous speech before the first roll.
_DEFAULT_MAX_BUFFER_BYTES = int(os.getenv("AUDIO_TRACK_MAX_BUFFER_BYTES", str(128 * 1024 * 1024)))


class AudioTrackSink:
    """Buffers per-speaker PCM in memory and emits one WAV per speaker on flush.

    offer() is the only method touched by the pipeline event loop: it appends raw
    bytes to an in-memory bytearray (O(1), no I/O) and returns immediately, and
    only when the sink is enabled. All encoding and persistence happen in flush()
    (participant leave / session end) or in a rolled part-file when a buffer passes
    max_buffer_bytes — both routed through the injected async on_flush handler.

    Overflow rolling detaches the buffer synchronously and hands the bytes to
    ``schedule`` (asyncio.create_task by default), so even the guard path never
    encodes or does I/O on the calling coroutine.
    """

    def __init__(
        self,
        on_flush: OnFlush,
        *,
        max_buffer_bytes: int = _DEFAULT_MAX_BUFFER_BYTES,
        schedule: Callable[[Awaitable[None]], object] = asyncio.ensure_future,
    ):
        self._on_flush = on_flush
        self._max_buffer_bytes = max_buffer_bytes
        self._schedule = schedule
        self._enabled = False
        self._buffers: dict[str, bytearray] = {}
        # sid → (sample_rate, num_channels), latched from the first chunk seen.
        self._fmt: dict[str, tuple[int, int]] = {}
        # sid → next part index; increments per emitted file so parts don't collide.
        self._part: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def offer(self, sid: str, pcm: bytes, sample_rate: int, num_channels: int) -> None:
        if not self._enabled:
            return
        buf = self._buffers.get(sid)
        if buf is None:
            buf = self._buffers[sid] = bytearray()
            self._fmt[sid] = (sample_rate, num_channels)
        buf.extend(pcm)
        if len(buf) >= self._max_buffer_bytes:
            self._roll(sid)

    def _roll(self, sid: str) -> None:
        """Detach an oversized buffer and schedule its write; keep buffering fresh audio."""
        buf = self._buffers.get(sid)
        if not buf:
            return
        data = bytes(buf)
        buf.clear()
        sample_rate, num_channels = self._fmt[sid]
        part = self._next_part(sid)
        logger.warning(
            f"AudioTrackSink: {sid} buffer ≥ {self._max_buffer_bytes} bytes; rolling part {part}"
        )
        self._schedule(self._emit(sid, data, sample_rate, num_channels, part))

    async def flush(self, sid: str) -> None:
        """Encode + hand off this speaker's remaining buffered audio. No-op if empty."""
        buf = self._buffers.pop(sid, None)
        sample_rate, num_channels = self._fmt.pop(sid, (16000, 1))
        if not buf:
            return
        await self._emit(sid, bytes(buf), sample_rate, num_channels, self._next_part(sid))

    async def flush_all(self) -> None:
        for sid in list(self._buffers.keys()):
            await self.flush(sid)

    def _next_part(self, sid: str) -> int:
        part = self._part.get(sid, 0)
        self._part[sid] = part + 1
        return part

    async def _emit(self, sid: str, data: bytes, sample_rate: int, num_channels: int, part: int) -> None:
        wav = pcm_to_wav(data, sample_rate, num_channels)
        duration_s = len(data) / (num_channels * _SAMPLE_WIDTH_BYTES * sample_rate)
        meta = {
            "sid": sid,
            "sample_rate": sample_rate,
            "num_channels": num_channels,
            "duration_s": round(duration_s, 3),
            "bytes": len(wav),
            "part": part,
        }
        await self._on_flush(sid, wav, meta)


# ── in-process sink registry ────────────────────────────────────────────────
# bot() and the FastAPI recording endpoints share one process (bot runs as a
# BackgroundTask), so a plain module-level dict is the control path: the bot
# registers its sink by room name at startup; /recordings/start looks it up and
# calls sink.enable(). Reset on process restart, like the concierge stores.

_registry: dict[str, AudioTrackSink] = {}


def register_sink(room_name: str, sink: AudioTrackSink) -> None:
    _registry[room_name] = sink


def get_sink(room_name: str) -> AudioTrackSink | None:
    return _registry.get(room_name)


def unregister_sink(room_name: str) -> None:
    _registry.pop(room_name, None)


# ── persistence handler (the real on_flush) ─────────────────────────────────

def _track_filename(room_name: str, speaker: str, part: int) -> str:
    """Human-readable WAV name: <ts>-<room>-audio-<speaker>[-<part>].wav."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in speaker)
    label = f"audio-{safe}" if part == 0 else f"audio-{safe}-{part}"
    return storage.build_filename(room_name, label, "wav")


def build_track_flush(
    room_name: str,
    resolve_speaker: Callable[[str], str | None],
    persist: Callable[[dict], Awaitable[None]],
) -> OnFlush:
    """Build an on_flush that writes each speaker WAV to storage and records a MediaFile.

    Kept dependency-injected so it unit-tests without storage or a DB:
      resolve_speaker(sid) → the LiveKit identity for a SID (or None if unknown).
      persist(fields)      → insert one MediaFile row (fields is a plain dict).

    The file is written available in one shot — unlike composite egress there is no
    pending→available webhook round-trip; the bot owns the bytes end to end.
    """
    async def _on_flush(sid: str, wav_bytes: bytes, meta: dict) -> None:
        part = meta.get("part", 0)
        speaker = resolve_speaker(sid)
        # Name the file by speaker identity (unique per participant, so no two
        # tracks in a session collide on disk); fall back to the SID if unknown.
        filename = _track_filename(room_name, speaker or sid, part)
        path = await storage.write_file(filename, wav_bytes)
        await persist({
            "type": "audio_track",
            "status": "available",
            "path": path,
            "meta": {**meta, "speaker_id": speaker},
        })
        logger.info(f"audio_track: wrote speaker={speaker or sid} part={part} → {path}")

    return _on_flush
