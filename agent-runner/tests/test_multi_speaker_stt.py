"""Unit tests for MultiSpeakerSTT and SpeakerLabelInjector.

No external services required. Mock STT instances are used so tests run fast
inside the container without API keys.

Run via:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_multi_speaker_stt -v
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    StartFrame,
    TranscriptionFrame,
    UserAudioRawFrame,
    UserSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup

from loguru import logger

from multi_speaker_stt import MultiSpeakerSTT, SpeakerLabelInjector, _FrameCollector


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_setup() -> FrameProcessorSetup:
    """Minimal FrameProcessorSetup backed by a mock task manager.

    The mock task_manager delegates create_task to the running asyncio event
    loop so real coroutines (pump task, per-participant STT input tasks) execute
    normally during the test. cancel_task is an AsyncMock to support awaiting.
    """
    setup = MagicMock(spec=FrameProcessorSetup)
    tm = MagicMock()
    tm.get_event_loop.side_effect = asyncio.get_event_loop
    tm.create_task.side_effect = lambda coro, name=None, context=None: asyncio.get_event_loop().create_task(coro, name=name, context=context)
    tm.cancel_task = AsyncMock()
    setup.task_manager = tm
    setup.clock = MagicMock()
    setup.clock.get_time.return_value = 0.0
    # pipecat>=1.4.0 added a required ``pipeline_worker`` field on
    # FrameProcessorSetup, read by FrameProcessor.setup(). The processors under
    # test never dereference it, so a bare mock is sufficient.
    setup.pipeline_worker = MagicMock()
    setup.observer = None
    return setup


def _make_audio_frame(user_id: str) -> UserAudioRawFrame:
    # num_frames is computed from audio length; do not pass it explicitly.
    return UserAudioRawFrame(audio=b"\x00" * 320, sample_rate=16000, num_channels=1, user_id=user_id)


def _make_transcription(user_id: str, text: str, finalized: bool = True) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id=user_id, timestamp="2025-01-01T00:00:00Z", finalized=finalized)


class _Sink(FrameProcessor):
    """Captures frames pushed to it via queue_frame — no pipeline machinery needed.

    Overrides queue_frame so frames land directly in `received` without
    requiring setup/start on this processor.
    """

    def __init__(self):
        super().__init__()
        self.received: list[Frame] = []

    async def queue_frame(
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
        callback=None,
    ) -> None:
        if direction == FrameDirection.DOWNSTREAM:
            self.received.append(frame)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self.received.append(frame)


class _PassthroughHead(FrameProcessor):
    """Fake chain head (stands in for VADProcessor): forwards every frame to _next.

    Records received frames so tests can assert audio was routed into the chain
    entry rather than directly into the STT tail.
    """

    def __init__(self):
        super().__init__()
        self.received: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self.received.append(frame)
        await self.push_frame(frame, direction)


class _ImmediateSTT(FrameProcessor):
    """Fake STT: on UserAudioRawFrame emits VAD frames + TranscriptionFrame.

    Simulates OpenAI Realtime behaviour (emits its own UserStarted/StoppedSpeakingFrames).
    """

    def __init__(self, reply_text: str = "hello"):
        super().__init__()
        self.reply_text = reply_text
        self.received_audio: list[UserAudioRawFrame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, UserAudioRawFrame):
            self.received_audio.append(frame)
            await self.push_frame(UserStartedSpeakingFrame(), direction)
            await self.push_frame(_make_transcription(frame.user_id, self.reply_text), direction)
            await self.push_frame(UserStoppedSpeakingFrame(), direction)
        else:
            await self.push_frame(frame, direction)


# ── _FrameCollector ──────────────────────────────────────────────────────────

class TestFrameCollector(unittest.IsolatedAsyncioTestCase):
    async def test_queues_non_system_downstream_frames(self):
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q)
        tf = _make_transcription("alice", "hi")

        await collector.queue_frame(tf, FrameDirection.DOWNSTREAM)

        self.assertFalse(q.empty())
        got = q.get_nowait()
        self.assertIs(got, tf)

    async def test_filters_system_frames(self):
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q)

        for sys_frame in [StartFrame(), EndFrame(), CancelFrame()]:
            await collector.queue_frame(sys_frame, FrameDirection.DOWNSTREAM)

        self.assertTrue(q.empty())

    async def test_ignores_upstream_frames(self):
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q)

        await collector.queue_frame(_make_transcription("alice", "hi"), FrameDirection.UPSTREAM)

        self.assertTrue(q.empty())

    async def test_vad_wrap_on_final_transcription(self):
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q, needs_vad_wrap=True)
        tf = _make_transcription("alice", "hi", finalized=True)

        await collector.queue_frame(tf, FrameDirection.DOWNSTREAM)

        frames = []
        while not q.empty():
            frames.append(q.get_nowait())

        # VAD frames precede the User* frames so the latency observer clock is set
        # before the LLM/TTS chain starts (fixes observer race condition).
        self.assertEqual(len(frames), 5)
        self.assertIsInstance(frames[0], VADUserStartedSpeakingFrame)
        self.assertIsInstance(frames[1], VADUserStoppedSpeakingFrame)
        self.assertIsInstance(frames[2], UserStartedSpeakingFrame)
        self.assertIsInstance(frames[3], TranscriptionFrame)
        self.assertIsInstance(frames[4], UserStoppedSpeakingFrame)

    async def test_real_vad_onset_fires_speech_onset_callback(self):
        """VADUserStartedSpeakingFrame (real onset) is surfaced to on_speech_onset,
        then dropped from the main pipeline — this is the interruption signal."""
        q: asyncio.Queue = asyncio.Queue()
        seen = []
        collector = _FrameCollector(
            q, needs_vad_wrap=True, sid="sidA",
            on_speech_onset=lambda sid: seen.append(sid),
        )

        await collector.queue_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)

        self.assertEqual(seen, ["sidA"])      # callback fired with the participant sid
        self.assertTrue(q.empty())            # frame still dropped from main pipeline

    async def test_no_vad_wrap_for_interim_transcription(self):
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q, needs_vad_wrap=True)
        tf = _make_transcription("alice", "hi", finalized=False)

        await collector.queue_frame(tf, FrameDirection.DOWNSTREAM)

        frames = []
        while not q.empty():
            frames.append(q.get_nowait())

        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], TranscriptionFrame)

    async def test_end_frame_filtered_explicitly(self):
        """EndFrame is a ControlFrame (not SystemFrame) in Pipecat 1.2.x — must still be filtered."""
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q)

        await collector.queue_frame(EndFrame(), FrameDirection.DOWNSTREAM)

        self.assertTrue(q.empty(), "EndFrame should be filtered even though it is not a SystemFrame")

    async def test_no_vad_wrap_for_finalized_when_disabled(self):
        """needs_vad_wrap=False: finalized TranscriptionFrame goes directly to queue without sandwich."""
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q, needs_vad_wrap=False)
        tf = _make_transcription("alice", "hi", finalized=True)

        await collector.queue_frame(tf, FrameDirection.DOWNSTREAM)

        frames = []
        while not q.empty():
            frames.append(q.get_nowait())

        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], TranscriptionFrame)
        self.assertNotIn(UserStartedSpeakingFrame, [type(f) for f in frames])

    async def test_vad_wrap_passthrough_for_non_transcription(self):
        """needs_vad_wrap=True: non-TranscriptionFrame is enqueued directly, no VAD sandwich."""
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q, needs_vad_wrap=True)

        await collector.queue_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)

        frames = []
        while not q.empty():
            frames.append(q.get_nowait())

        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], UserStartedSpeakingFrame)

    async def test_vad_wrap_drops_internal_vad_frames(self):
        """needs_vad_wrap=True: raw VAD frames from an in-chain VADProcessor are dropped.

        Whisper chains run a per-participant VADProcessor whose VADUser*/UserSpeaking
        frames would double-fire the latency observer on top of the synthetic sandwich.
        Only the sandwich (emitted around the finalized TranscriptionFrame) may pass.
        """
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q, needs_vad_wrap=True)

        for frame in [
            VADUserStartedSpeakingFrame(),
            VADUserStoppedSpeakingFrame(stop_secs=0.2),
            UserSpeakingFrame(),
        ]:
            await collector.queue_frame(frame, FrameDirection.DOWNSTREAM)

        self.assertTrue(q.empty(), "raw VAD frames must not leak past the collector when vad_wrap is on")

    async def test_raw_vad_frames_pass_when_vad_wrap_disabled(self):
        """needs_vad_wrap=False (server-side VAD STT): VAD frames pass through untouched."""
        q: asyncio.Queue = asyncio.Queue()
        collector = _FrameCollector(q, needs_vad_wrap=False)

        await collector.queue_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await collector.queue_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2), FrameDirection.DOWNSTREAM)

        frames = []
        while not q.empty():
            frames.append(q.get_nowait())
        self.assertEqual([type(f) for f in frames], [VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame])


# ── MultiSpeakerSTT chain factories ──────────────────────────────────────────

class TestMultiSpeakerSTTChainFactory(unittest.IsolatedAsyncioTestCase):
    """stt_factory may return a (head, tail) chain instead of a single processor.

    Used by local-Whisper STT: head is a per-participant VADProcessor, tail is the
    WhisperSTTService. Audio must enter at the head; transcripts are collected from
    the tail; vad_wrap is decided by the tail's type.
    """

    async def asyncSetUp(self):
        self.setup_params = _make_setup()
        self.heads: list[_PassthroughHead] = []
        self.tails: list[_ImmediateSTT] = []

        def chain_factory(sid=None):
            head = _PassthroughHead()
            tail = _ImmediateSTT()
            head.link(tail)
            self.heads.append(head)
            self.tails.append(tail)
            return (head, tail)

        self.multi_stt = MultiSpeakerSTT(chain_factory)
        await self.multi_stt.setup(self.setup_params)
        self.sink = _Sink()
        self.multi_stt.link(self.sink)
        await self.multi_stt.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)

    async def asyncTearDown(self):
        try:
            await self.multi_stt.process_frame(CancelFrame(), FrameDirection.DOWNSTREAM)
        except Exception:
            pass
        from db.engine import engine
        await engine.dispose()

    async def test_audio_routed_to_chain_head(self):
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)

        self.assertEqual(len(self.heads), 1)
        audio_at_head = [f for f in self.heads[0].received if isinstance(f, UserAudioRawFrame)]
        self.assertEqual(len(audio_at_head), 1, "audio must enter the chain at the head")

    async def test_tail_output_reaches_downstream(self):
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.1)

        transcripts = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(len(transcripts) >= 1, f"tail transcript should reach the pipeline, got: {self.sink.received}")
        self.assertEqual(transcripts[0].user_id, "alice_sid")

    async def test_vad_wrap_decided_by_tail(self):
        """_ImmediateSTT is not an OpenAI Realtime STT, so the tail's collector wraps."""
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)

        collector = self.tails[0]._next
        self.assertIsInstance(collector, _FrameCollector)
        self.assertTrue(collector._needs_vad_wrap)

    async def test_remove_participant_tears_down_chain(self):
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)
        self.assertIn("alice_sid", self.multi_stt._stts)

        await self.multi_stt.remove_participant("alice_sid")

        self.assertNotIn("alice_sid", self.multi_stt._stts)
        end_frames_at_head = [f for f in self.heads[0].received if isinstance(f, EndFrame)]
        self.assertEqual(len(end_frames_at_head), 1, "EndFrame must be delivered to the chain head on teardown")


# ── MultiSpeakerSTT routing ───────────────────────────────────────────────────

class TestMultiSpeakerSTTRouting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup_params = _make_setup()

        def factory(sid=None):
            return _ImmediateSTT()

        self.multi_stt = MultiSpeakerSTT(factory)
        await self.multi_stt.setup(self.setup_params)

        self.sink = _Sink()
        self.multi_stt.link(self.sink)

        await self.multi_stt.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)  # let pump task start

    async def asyncTearDown(self):
        try:
            await self.multi_stt.process_frame(CancelFrame(), FrameDirection.DOWNSTREAM)
        except Exception:
            pass
        from db.engine import engine
        await engine.dispose()

    async def test_creates_distinct_stts_for_different_sids(self):
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        await self.multi_stt.process_frame(_make_audio_frame("bob_sid"), FrameDirection.DOWNSTREAM)

        self.assertIn("alice_sid", self.multi_stt._stts)
        self.assertIn("bob_sid", self.multi_stt._stts)
        self.assertIsNot(self.multi_stt._stts["alice_sid"], self.multi_stt._stts["bob_sid"])

    async def test_reuses_stt_for_same_sid(self):
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        stt_first = self.multi_stt._stts.get("alice_sid")

        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        stt_second = self.multi_stt._stts.get("alice_sid")

        self.assertIsNotNone(stt_first)
        self.assertIs(stt_first, stt_second)

    async def test_remove_participant_clears_entry(self):
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        await self.multi_stt.process_frame(_make_audio_frame("bob_sid"), FrameDirection.DOWNSTREAM)

        await self.multi_stt.remove_participant("alice_sid")

        self.assertNotIn("alice_sid", self.multi_stt._stts)
        self.assertIn("bob_sid", self.multi_stt._stts)

    async def test_transcription_frames_forwarded_downstream(self):
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.1)  # let pump flush

        transcript_frames = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(len(transcript_frames) >= 1, f"Expected TranscriptionFrame downstream, got: {self.sink.received}")
        self.assertEqual(transcript_frames[0].user_id, "alice_sid")

    async def test_non_audio_frames_pass_through(self):
        tf = _make_transcription("alice_sid", "direct injection")
        await self.multi_stt.process_frame(tf, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0)

        passthrough = [f for f in self.sink.received if isinstance(f, TranscriptionFrame) and f.text == "direct injection"]
        self.assertTrue(len(passthrough) >= 1, "Direct TranscriptionFrame should pass through unchanged")

    async def test_audio_frame_without_user_id_not_routed(self):
        frame = UserAudioRawFrame(audio=b"\x00" * 320, sample_rate=16000, num_channels=1, user_id="")
        initial_count = len(self.multi_stt._stts)

        await self.multi_stt.process_frame(frame, FrameDirection.DOWNSTREAM)

        self.assertEqual(len(self.multi_stt._stts), initial_count, "Empty user_id should not create a new STT")

    async def test_audio_without_user_id_not_forwarded_downstream(self):
        """Audio frame with empty user_id is silently dropped — not pushed to the downstream pipeline."""
        frame = UserAudioRawFrame(audio=b"\x00" * 320, sample_rate=16000, num_channels=1, user_id="")
        await self.multi_stt.process_frame(frame, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0)

        audio_downstream = [f for f in self.sink.received if isinstance(f, UserAudioRawFrame)]
        self.assertEqual(len(audio_downstream), 0, "Audio with no user_id should be dropped, not forwarded")

    async def test_user_id_not_cross_contaminated(self):
        """Core attribution invariant: alice's audio produces transcripts with alice's user_id, never bob's."""
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        await self.multi_stt.process_frame(_make_audio_frame("bob_sid"), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.1)

        alice_tx = [f for f in self.sink.received if isinstance(f, TranscriptionFrame) and f.user_id == "alice_sid"]
        bob_tx = [f for f in self.sink.received if isinstance(f, TranscriptionFrame) and f.user_id == "bob_sid"]
        wrong = [f for f in self.sink.received if isinstance(f, TranscriptionFrame) and f.user_id not in ("alice_sid", "bob_sid")]

        self.assertTrue(len(alice_tx) >= 1, "Expected at least one TranscriptionFrame for alice_sid")
        self.assertTrue(len(bob_tx) >= 1, "Expected at least one TranscriptionFrame for bob_sid")
        self.assertEqual(len(wrong), 0, f"Cross-contaminated transcriptions: {[f.user_id for f in wrong]}")

    async def test_each_speakers_transcriber_hears_only_that_speaker(self):
        """F11, layer 1: interleaved audio from two speakers. Routing must keep the audio
        itself apart, not just copy the user_id (which the fake STT would echo anyway)."""
        for i in range(20):
            sid = ("alice_sid", "bob_sid")[i % 2]
            frame = UserAudioRawFrame(audio=(b"A" if sid == "alice_sid" else b"B") * 320,
                                      sample_rate=16000, num_channels=1, user_id=sid)
            await self.multi_stt.process_frame(frame, FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)
        heard = {sid: {bytes(f.audio[:1]) for f in stt.received_audio} for sid, stt in self.multi_stt._stts.items()}
        self.assertEqual(heard, {"alice_sid": {b"A"}, "bob_sid": {b"B"}})

    async def test_end_frame_clears_stts_and_pump(self):
        """EndFrame tears down all per-participant STTs and stops the pump task."""
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        self.assertIn("alice_sid", self.multi_stt._stts)
        self.assertIsNotNone(self.multi_stt._pump_task)

        await self.multi_stt.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0)

        self.assertEqual(len(self.multi_stt._stts), 0, "_stts should be empty after EndFrame")
        self.assertIsNone(self.multi_stt._pump_task, "_pump_task should be None after EndFrame")

    async def test_cancel_frame_clears_stts_and_pump(self):
        """CancelFrame tears down all per-participant STTs and stops the pump task."""
        await self.multi_stt.process_frame(_make_audio_frame("alice_sid"), FrameDirection.DOWNSTREAM)
        self.assertIn("alice_sid", self.multi_stt._stts)

        await self.multi_stt.process_frame(CancelFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0)

        self.assertEqual(len(self.multi_stt._stts), 0, "_stts should be empty after CancelFrame")
        self.assertIsNone(self.multi_stt._pump_task, "_pump_task should be None after CancelFrame")

    async def test_remove_nonexistent_sid_is_noop(self):
        """remove_participant with an unknown SID does not raise and leaves state unchanged."""
        await self.multi_stt.remove_participant("does_not_exist")
        self.assertEqual(len(self.multi_stt._stts), 0)


# ── SpeakerLabelInjector ──────────────────────────────────────────────────────

class TestSpeakerLabelInjector(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sid_map = {"alice_sid": "alice_123", "bob_sid": "bob_456"}
        self.injector = SpeakerLabelInjector(self.sid_map)
        self.setup_params = _make_setup()
        await self.injector.setup(self.setup_params)

        self.sink = _Sink()
        self.injector.link(self.sink)

        # Start the injector so push_frame's _check_started passes.
        await self.injector.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0)

    async def asyncTearDown(self):
        from db.engine import engine
        await engine.dispose()

    async def test_prepends_identity_for_known_sid(self):
        await self.injector.process_frame(_make_transcription("alice_sid", "what is X?"), FrameDirection.DOWNSTREAM)

        transcripts = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(
            any(f.text == "alice_123: what is X?" for f in transcripts),
            f"Expected labeled text, got: {[f.text for f in transcripts]}",
        )

    async def test_preserves_user_id_after_labeling(self):
        await self.injector.process_frame(_make_transcription("alice_sid", "hello"), FrameDirection.DOWNSTREAM)

        transcripts = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(all(f.user_id == "alice_sid" for f in transcripts))

    async def test_unknown_sid_text_unchanged(self):
        await self.injector.process_frame(_make_transcription("unknown_sid", "mystery"), FrameDirection.DOWNSTREAM)

        transcripts = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(
            any(f.text == "mystery" for f in transcripts),
            "Unknown SID should not have label prepended",
        )

    async def test_empty_text_not_labeled(self):
        await self.injector.process_frame(_make_transcription("alice_sid", ""), FrameDirection.DOWNSTREAM)

        transcripts = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(all(f.text == "" for f in transcripts))

    async def test_non_transcription_frames_pass_through(self):
        await self.injector.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)

        vad_frames = [f for f in self.sink.received if isinstance(f, UserStartedSpeakingFrame)]
        self.assertTrue(len(vad_frames) >= 1, "UserStartedSpeakingFrame should pass through")

    async def test_live_dict_update_reflected(self):
        # Unknown SID — no label
        await self.injector.process_frame(_make_transcription("charlie_sid", "new person"), FrameDirection.DOWNSTREAM)
        before = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(any(f.text == "new person" for f in before), "Before: text should be unlabeled")

        self.sink.received.clear()
        self.sid_map["charlie_sid"] = "charlie_789"  # live update — same dict reference

        await self.injector.process_frame(_make_transcription("charlie_sid", "new person"), FrameDirection.DOWNSTREAM)
        after = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(
            any(f.text == "charlie_789: new person" for f in after),
            f"After dict update: expected labeled text, got {[f.text for f in after]}",
        )

    async def test_upstream_transcription_not_labeled(self):
        """TranscriptionFrame going upstream is not modified and does not reach the downstream sink."""
        await self.injector.process_frame(
            _make_transcription("alice_sid", "upstream text"),
            FrameDirection.UPSTREAM,
        )

        transcripts = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertEqual(len(transcripts), 0, "Upstream transcription should not reach the downstream sink")

    async def test_interim_transcription_also_labeled(self):
        """Label is applied to interim (finalized=False) transcriptions as well as final ones."""
        await self.injector.process_frame(
            _make_transcription("alice_sid", "partial", finalized=False),
            FrameDirection.DOWNSTREAM,
        )

        transcripts = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(
            any(f.text == "alice_123: partial" for f in transcripts),
            f"Interim transcription should also be labeled, got: {[f.text for f in transcripts]}",
        )

    async def test_finalized_field_preserved_after_labeling(self):
        """The finalized flag on the new TranscriptionFrame matches the original."""
        await self.injector.process_frame(
            _make_transcription("alice_sid", "final", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        await self.injector.process_frame(
            _make_transcription("alice_sid", "partial", finalized=False),
            FrameDirection.DOWNSTREAM,
        )

        transcripts = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        final_tx = [f for f in transcripts if "final" in f.text]
        interim_tx = [f for f in transcripts if "partial" in f.text]

        self.assertTrue(all(f.finalized is True for f in final_tx), "finalized=True should be preserved")
        self.assertTrue(all(f.finalized is False for f in interim_tx), "finalized=False should be preserved")

    async def test_language_field_preserved_after_labeling(self):
        """The language field on the new TranscriptionFrame matches the original."""
        frame = TranscriptionFrame(
            text="bonjour",
            user_id="alice_sid",
            timestamp="2025-01-01T00:00:00Z",
            language="fr",
            finalized=True,
        )
        await self.injector.process_frame(frame, FrameDirection.DOWNSTREAM)

        transcripts = [f for f in self.sink.received if isinstance(f, TranscriptionFrame)]
        labeled = [f for f in transcripts if "bonjour" in f.text]
        self.assertTrue(len(labeled) >= 1)
        self.assertEqual(labeled[0].language, "fr", "language field should survive the label-prepend")

    async def test_postfix_stripped_when_no_name_map(self):
        """Without sid_to_name, label uses identity with __postfix stripped."""
        sid_map = {"alice_sid": "Alice__abc1"}
        injector = SpeakerLabelInjector(sid_map)
        sink = _Sink()
        injector.link(sink)
        await injector.setup(_make_setup())
        await injector.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

        await injector.process_frame(_make_transcription("alice_sid", "hi"), FrameDirection.DOWNSTREAM)
        transcripts = [f for f in sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(
            any(f.text == "Alice: hi" for f in transcripts),
            f"Expected postfix stripped label, got: {[f.text for f in transcripts]}",
        )

    async def test_sid_to_name_takes_priority_over_identity(self):
        """When sid_to_name is provided, its value is used instead of stripping the identity."""
        sid_map = {"alice_sid": "Alice__abc1"}
        name_map = {"alice_sid": "Alice"}
        injector = SpeakerLabelInjector(sid_map, name_map)
        sink = _Sink()
        injector.link(sink)
        await injector.setup(_make_setup())
        await injector.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

        await injector.process_frame(_make_transcription("alice_sid", "hello"), FrameDirection.DOWNSTREAM)
        transcripts = [f for f in sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(
            any(f.text == "Alice: hello" for f in transcripts),
            f"Expected clean name from sid_to_name, got: {[f.text for f in transcripts]}",
        )

    async def test_sid_to_name_falls_back_to_stripped_identity(self):
        """If sid not in sid_to_name, falls back to stripping __postfix from identity."""
        sid_map = {"alice_sid": "Alice__abc1"}
        name_map: dict = {}
        injector = SpeakerLabelInjector(sid_map, name_map)
        sink = _Sink()
        injector.link(sink)
        await injector.setup(_make_setup())
        await injector.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

        await injector.process_frame(_make_transcription("alice_sid", "fallback"), FrameDirection.DOWNSTREAM)
        transcripts = [f for f in sink.received if isinstance(f, TranscriptionFrame)]
        self.assertTrue(
            any(f.text == "Alice: fallback" for f in transcripts),
            f"Expected stripped-identity fallback, got: {[f.text for f in transcripts]}",
        )


class AdmissionControlTests(unittest.IsolatedAsyncioTestCase):
    """The cap that 2026-08-19 lacked: refuse visibly instead of degrading.

    That ramp accepted every participant, exhausted recognition throughput and
    quietly stopped replying (261 replies → 23, processor at 77%). These pin the
    two properties that make a cap safe rather than merely present.
    """

    def _stt(self, cap):
        made = []

        def factory(sid=None):
            p = _ImmediateSTT()
            made.append(p)
            return p

        return MultiSpeakerSTT(factory, cap=cap), made

    async def _speak(self, ms, sid):
        return await ms._ensure_stt(sid)

    async def test_new_participants_are_refused_at_the_cap(self):
        ms, made = self._stt(cap=2)
        self.assertIsNotNone(await self._speak(ms, "a"))
        self.assertIsNotNone(await self._speak(ms, "b"))
        self.assertIsNone(await self._speak(ms, "c"))
        self.assertEqual(len(made), 2, "a refused participant must not build an STT")
        self.assertIn("c", ms.refused)

    async def test_people_already_in_the_room_keep_working_at_the_cap(self):
        """The regression that matters most.

        route_audio speaks in worker names, not sids. Passing sids straight in
        makes every membership test miss, so at the cap the *existing* speakers
        get refused too — throttling a healthy conversation to protect capacity,
        which breaks the thing being protected.
        """
        ms, _ = self._stt(cap=2)
        first = await self._speak(ms, "a")
        await self._speak(ms, "b")
        await self._speak(ms, "c")           # refused, room is full
        again = await self._speak(ms, "a")   # 'a' was here first
        self.assertIsNotNone(again, "an admitted participant was refused at the cap")
        self.assertIs(again, first, "an admitted participant got a second STT")

    async def test_leaving_frees_the_slot_for_someone_new(self):
        ms, _ = self._stt(cap=2)
        await self._speak(ms, "a")
        await self._speak(ms, "b")
        self.assertIsNone(await self._speak(ms, "c"))
        await ms.remove_participant("a")
        self.assertIsNotNone(await self._speak(ms, "c"), "slot not released on leave")

    async def test_a_refusal_is_recorded_once_not_once_per_audio_frame(self):
        """Audio arrives every 20ms; an unlatched refusal would flood the log."""
        ms, _ = self._stt(cap=1)
        await self._speak(ms, "a")
        seen = []
        sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
        try:
            for _ in range(50):
                self.assertIsNone(await self._speak(ms, "b"))
        finally:
            logger.remove(sink)
        refusals = [m for m in seen if "refused" in m]
        self.assertEqual(len(refusals), 1, f"expected one refusal log, got {len(refusals)}")

if __name__ == "__main__":
    unittest.main()
