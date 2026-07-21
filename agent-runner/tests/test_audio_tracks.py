"""Unit tests for audio_tracks.py.

Iteration 1 covers only pcm_to_wav — a pure PCM→WAV encoder with no I/O.
Later iterations add PerSpeakerAudioRecorder and AudioTrackSink.
"""

import asyncio
import io
import os
import sys
import unittest
import wave
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipecat.frames.frames import (
    Frame,
    StartFrame,
    TranscriptionFrame,
    UserAudioRawFrame,
)
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)

from unittest.mock import patch

from audio_tracks import (
    AudioTrackSink,
    PerSpeakerAudioRecorder,
    build_track_flush,
    get_sink,
    pcm_to_wav,
    register_sink,
    unregister_sink,
)


# ── FrameProcessor test harness ─────────────────────────────────────────────

def _make_setup() -> FrameProcessorSetup:
    setup = MagicMock(spec=FrameProcessorSetup)
    tm = MagicMock()
    tm.get_event_loop.side_effect = asyncio.get_event_loop
    tm.create_task.side_effect = lambda coro, name=None: asyncio.get_event_loop().create_task(coro)
    tm.cancel_task = AsyncMock()
    setup.task_manager = tm
    setup.clock = MagicMock()
    setup.clock.get_time.return_value = 0.0
    setup.pipeline_worker = MagicMock()
    setup.observer = None
    return setup


class _Sink(FrameProcessor):
    """Captures frames pushed downstream without needing pipeline machinery."""

    def __init__(self):
        super().__init__()
        self.received: list[Frame] = []

    async def queue_frame(self, frame, direction=FrameDirection.DOWNSTREAM, callback=None):
        if direction == FrameDirection.DOWNSTREAM:
            self.received.append(frame)


class _FakeSink:
    """Records offer() calls; stands in for AudioTrackSink (iteration 3)."""

    def __init__(self, raises: bool = False):
        self.offers: list[tuple] = []
        self._raises = raises

    def offer(self, sid, pcm, sample_rate, num_channels):
        self.offers.append((sid, pcm, sample_rate, num_channels))
        if self._raises:
            raise RuntimeError("sink boom")


def _audio(user_id: str, pcm: bytes = b"\x11\x22" * 8) -> UserAudioRawFrame:
    return UserAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1, user_id=user_id)


async def _run(recorder: PerSpeakerAudioRecorder, sink: _Sink, frames: list[Frame]) -> None:
    recorder.link(sink)
    await recorder.setup(_make_setup())
    await recorder.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
    for f in frames:
        await recorder.process_frame(f, FrameDirection.DOWNSTREAM)


class TestPcmToWav(unittest.TestCase):
    def test_roundtrips_through_wave(self):
        pcm = b"\x01\x02\x03\x04\x05\x06\x07\x08"  # 4 mono 16-bit samples
        data = pcm_to_wav(pcm, sample_rate=16000, num_channels=1)
        with wave.open(io.BytesIO(data), "rb") as w:
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)  # 16-bit PCM
            self.assertEqual(w.getnframes(), 4)
            self.assertEqual(w.readframes(4), pcm)

    def test_frame_count_matches_channels(self):
        # 8 bytes stereo 16-bit = 4 bytes/frame → 2 frames
        pcm = b"\x00" * 8
        data = pcm_to_wav(pcm, sample_rate=48000, num_channels=2)
        with wave.open(io.BytesIO(data), "rb") as w:
            self.assertEqual(w.getnchannels(), 2)
            self.assertEqual(w.getnframes(), 2)

    def test_empty_pcm_produces_valid_zero_frame_wav(self):
        data = pcm_to_wav(b"", sample_rate=16000, num_channels=1)
        with wave.open(io.BytesIO(data), "rb") as w:
            self.assertEqual(w.getnframes(), 0)

    def test_returns_bytes(self):
        self.assertIsInstance(pcm_to_wav(b"\x00\x00", 16000, 1), bytes)


class TestPerSpeakerAudioRecorder(unittest.IsolatedAsyncioTestCase):
    async def test_offers_audio_and_forwards_frame(self):
        sink, out = _FakeSink(), _Sink()
        recorder = PerSpeakerAudioRecorder(sink)
        frame = _audio("sid-A")
        await _run(recorder, out, [frame])
        self.assertEqual(sink.offers, [("sid-A", frame.audio, 16000, 1)])
        self.assertIn(frame, out.received)  # audio still flows to multi_stt downstream

    async def test_ignores_non_audio_frames_but_forwards_them(self):
        sink, out = _FakeSink(), _Sink()
        recorder = PerSpeakerAudioRecorder(sink)
        tx = TranscriptionFrame(text="hi", user_id="sid-A", timestamp="2025-01-01T00:00:00Z")
        await _run(recorder, out, [tx])
        self.assertEqual(sink.offers, [])
        self.assertIn(tx, out.received)

    async def test_skips_empty_user_id(self):
        sink, out = _FakeSink(), _Sink()
        recorder = PerSpeakerAudioRecorder(sink)
        frame = _audio("")
        await _run(recorder, out, [frame])
        self.assertEqual(sink.offers, [])
        self.assertIn(frame, out.received)

    async def test_sink_error_never_blocks_audio_flow(self):
        sink, out = _FakeSink(raises=True), _Sink()
        recorder = PerSpeakerAudioRecorder(sink)
        frame = _audio("sid-A")
        await _run(recorder, out, [frame])  # must not raise
        self.assertIn(frame, out.received)


class _FlushCapture:
    """Async on_flush handler that records (sid, wav_bytes, meta) calls."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def __call__(self, sid, wav_bytes, meta):
        self.calls.append((sid, wav_bytes, meta))


def _wav_frames(wav_bytes: bytes) -> int:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnframes()


class TestAudioTrackSink(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_sink_buffers_nothing(self):
        cap = _FlushCapture()
        sink = AudioTrackSink(cap)
        sink.offer("sid-A", b"\x00" * 320, 16000, 1)  # not enabled
        await sink.flush_all()
        self.assertEqual(cap.calls, [])

    async def test_flushes_one_wav_per_speaker(self):
        cap = _FlushCapture()
        sink = AudioTrackSink(cap)
        sink.enable()
        sink.offer("sid-A", b"\x01\x02" * 100, 16000, 1)
        sink.offer("sid-B", b"\x03\x04" * 50, 16000, 1)
        await sink.flush_all()

        self.assertEqual({c[0] for c in cap.calls}, {"sid-A", "sid-B"})
        for sid, wav_bytes, meta in cap.calls:
            self.assertEqual(meta["sid"], sid)
            self.assertGreater(_wav_frames(wav_bytes), 0)  # valid, non-empty WAV

    async def test_accumulates_chunks_before_flush(self):
        cap = _FlushCapture()
        sink = AudioTrackSink(cap)
        sink.enable()
        sink.offer("sid-A", b"\x00\x00" * 10, 16000, 1)
        sink.offer("sid-A", b"\x00\x00" * 10, 16000, 1)
        await sink.flush("sid-A")
        (_, wav_bytes, meta) = cap.calls[0]
        self.assertEqual(_wav_frames(wav_bytes), 20)  # both chunks in one file

    async def test_duration_seconds_in_meta(self):
        cap = _FlushCapture()
        sink = AudioTrackSink(cap)
        sink.enable()
        # 16000 mono 16-bit samples = 32000 bytes = exactly 1.0s
        sink.offer("sid-A", b"\x00" * 32000, 16000, 1)
        await sink.flush("sid-A")
        self.assertAlmostEqual(cap.calls[0][2]["duration_s"], 1.0, places=3)

    async def test_empty_buffer_flush_is_noop(self):
        cap = _FlushCapture()
        sink = AudioTrackSink(cap)
        sink.enable()
        await sink.flush("never-spoke")
        self.assertEqual(cap.calls, [])

    async def test_double_flush_is_idempotent(self):
        cap = _FlushCapture()
        sink = AudioTrackSink(cap)
        sink.enable()
        sink.offer("sid-A", b"\x01\x02" * 10, 16000, 1)
        await sink.flush("sid-A")
        await sink.flush("sid-A")  # buffer already drained → no second write
        self.assertEqual(len(cap.calls), 1)


class _FakeScheduler:
    """Collects coroutines instead of firing them on the loop, for deterministic tests."""

    def __init__(self):
        self.coros: list = []

    def __call__(self, coro):
        self.coros.append(coro)

    async def drain(self):
        for c in self.coros:
            await c
        self.coros = []


class TestAudioTrackSinkOverflowGuard(unittest.IsolatedAsyncioTestCase):
    async def test_overflow_rolls_a_part_file_without_blocking(self):
        cap, sched = _FlushCapture(), _FakeScheduler()
        sink = AudioTrackSink(cap, max_buffer_bytes=100, schedule=sched)
        sink.enable()
        sink.offer("sid-A", b"\x00" * 200, 16000, 1)  # over the cap → schedule a roll
        self.assertEqual(len(sched.coros), 1)          # scheduled, not awaited inline
        self.assertEqual(cap.calls, [])                # nothing written yet
        await sched.drain()
        self.assertEqual(len(cap.calls), 1)
        self.assertEqual(cap.calls[0][2]["part"], 0)   # first rolled part

    async def test_remaining_audio_flushes_as_next_part(self):
        cap, sched = _FlushCapture(), _FakeScheduler()
        sink = AudioTrackSink(cap, max_buffer_bytes=100, schedule=sched)
        sink.enable()
        sink.offer("sid-A", b"\x00" * 200, 16000, 1)   # rolls part 0
        await sched.drain()
        sink.offer("sid-A", b"\x11" * 40, 16000, 1)     # under cap, stays buffered
        await sink.flush("sid-A")                        # final drain → part 1
        self.assertEqual([c[2]["part"] for c in cap.calls], [0, 1])

    async def test_no_schedule_when_under_cap(self):
        cap, sched = _FlushCapture(), _FakeScheduler()
        sink = AudioTrackSink(cap, max_buffer_bytes=10_000, schedule=sched)
        sink.enable()
        sink.offer("sid-A", b"\x00" * 320, 16000, 1)
        self.assertEqual(sched.coros, [])


class TestSinkRegistry(unittest.TestCase):
    """In-process control path: /recordings/start reaches the running bot's sink."""

    def tearDown(self):
        unregister_sink("room-reg-test")

    def test_register_then_get(self):
        sink = AudioTrackSink(_FlushCapture())
        register_sink("room-reg-test", sink)
        self.assertIs(get_sink("room-reg-test"), sink)

    def test_get_unknown_room_returns_none(self):
        self.assertIsNone(get_sink("no-such-room-xyz"))

    def test_unregister_removes_sink(self):
        register_sink("room-reg-test", AudioTrackSink(_FlushCapture()))
        unregister_sink("room-reg-test")
        self.assertIsNone(get_sink("room-reg-test"))

    def test_unregister_unknown_is_noop(self):
        unregister_sink("never-registered-room")  # must not raise


class TestBuildTrackFlush(unittest.IsolatedAsyncioTestCase):
    """The on_flush handler: WAV → storage.write_file + an available MediaFile row."""

    async def test_writes_wav_and_records_media_file(self):
        persisted: list[dict] = []

        async def _persist(fields):
            persisted.append(fields)

        on_flush = build_track_flush(
            room_name="demo-room",
            resolve_speaker=lambda sid: f"identity-of-{sid}",
            persist=_persist,
        )
        wav = pcm_to_wav(b"\x00" * 320, 16000, 1)
        meta = {"sid": "PA_abc", "part": 0, "duration_s": 0.01}

        with patch("audio_tracks.storage.write_file", new=AsyncMock(return_value="/rec/x.wav")) as wf:
            await on_flush("PA_abc", wav, meta)

        # WAV bytes written under a name carrying the room, speaker sid and part.
        (filename, content), _ = wf.call_args
        self.assertEqual(content, wav)
        self.assertIn("demo-room", filename)
        self.assertIn("PA_abc", filename)
        self.assertTrue(filename.endswith(".wav"))

        # One available audio_track MediaFile row, tagged with the resolved speaker.
        self.assertEqual(len(persisted), 1)
        row = persisted[0]
        self.assertEqual(row["type"], "audio_track")
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["path"], "/rec/x.wav")
        self.assertEqual(row["meta"]["speaker_id"], "identity-of-PA_abc")
        self.assertEqual(row["meta"]["part"], 0)

    async def test_part_number_distinguishes_filenames(self):
        async def _persist(fields):
            pass

        on_flush = build_track_flush("r", lambda sid: sid, _persist)
        names: list[str] = []

        async def _capture(filename, content):
            names.append(filename)
            return filename

        with patch("audio_tracks.storage.write_file", new=_capture):
            await on_flush("PA_a", b"RIFF0", {"sid": "PA_a", "part": 0})
            await on_flush("PA_a", b"RIFF1", {"sid": "PA_a", "part": 1})

        self.assertNotEqual(names[0], names[1])


if __name__ == "__main__":
    unittest.main()
