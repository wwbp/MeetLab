"""The net under audio modes: what a normal room does today, pinned (docs/audio-modes-test-plan.md).

Runs the real bot() up to its pipeline, with the real tracker, observer, recorders, MultiSpeakerSTT,
SpeakerLabelInjector and ReplyStreamer. Faked: LiveKit, LLM, TTS, the database, each person's VAD
and STT, and Pipecat's worker. So BotStarted/StoppedSpeakingFrame are scripted, not produced by a
real output transport, and Pipecat's aggregators are built but no frame passes through them.

A difference in the trace is a change to normal mode: if it is not one you meant, fix the code,
not the golden.
"""
import asyncio
import os
import struct
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    UserAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from meetlab_bot import bot
from meetlab_bot.audio_tracks import AudioTrackSink
from meetlab_bot.interruption import InterruptionTracker
from meetlab_bot.runner_types import LiveKitRunnerArguments
from meetlab_core.db.config_loader import EffectiveBotConfig
from tests.test_multi_speaker_stt import _make_setup

ARGS = LiveKitRunnerArguments(url="wss://lk", token="t", room_name="r1", session_id="s1", bot_identity="bot_r1")
CONFIG = EffectiveBotConfig(
    system_prompt="Be brief.", greeting="Welcome.", llm_model="m", tts_voice="v",
    stt_model="parakeet-tdt-0.6b-v2", stt_delay=None, tts_provider="elevenlabs",
    tts_aggregation_mode="sentence", stt_endpointing_ms=450, auto_record=True,
)
DOWN = FrameDirection.DOWNSTREAM
BOT_PCM = b"\x10\x20" * 40

PIPELINE = [
    "_Input", "_AudioTimestampRecorder", "PerSpeakerAudioRecorder", "MultiSpeakerSTT", "_SpeakerTracker",
    "SpeakerLabelInjector", "LLMUserAggregator", "_Llm", "ReplyStreamer", "_Tts", "BotAudioRecorder",
    "_TTSFirstTimer", "_Output", "LLMAssistantAggregator",
]
OBSERVERS = ["MetricsLogObserver", "_MetricsObserver", "_InterruptionObserver"]


def _turn(sid, text):
    return f'{{"speaker_id": "{sid}", "name": "{sid}", "text": "{text}", "source": "speech"}}'


def _sandwich(sid, text):
    """What one finished transcript becomes on its way to the context (smart_turn.turn_frames)."""
    return [
        ("heard", "VADUserStartedSpeakingFrame"),
        ("heard", "VADUserStoppedSpeakingFrame"),
        ("heard", "UserStartedSpeakingFrame"),
        ("heard", "TranscriptionFrame", sid, _turn(sid, text)),
        ("heard", "UserStoppedSpeakingFrame"),
    ]


def _reply_out(*chunks):
    """A reply's frames reaching the output transport: every chunk spoken, nothing held."""
    middle = [
        item
        for text in chunks
        for item in (("out", "LLMTextFrame", text), ("out", "TTSAudioRawFrame", len(BOT_PCM)))
    ]
    return [("out", "LLMFullResponseStartFrame"), *middle, ("out", "LLMFullResponseEndFrame")]


YIELD = ("worker", "InterruptionTaskFrame", "upstream")

GOLDEN = [
    # The greeting is queued unconditionally.
    ("worker", "TTSSpeakFrame", "Welcome."),

    # Turn-taking. Each utterance reports its onset twice (the VAD's STARTING edge, then the
    # VADUserStartedSpeakingFrame the collector sees) and its offset twice. Neither yields.
    ("user_onset", 1.0, "alice", False),
    ("user_onset", 1.16, "alice", False),
    ("user_offset", 2.0, "alice"),
    ("user_offset", 2.0, "alice"),
    *_sandwich("alice", "hello"),

    # The reply streams out at once; JSON around it is stripped.
    *_reply_out("Hi ", "there."),
    ("bot_started", 3.1, False),  # reported twice by Pipecat, once here (the observer's _seen)
    ("idle", 3.2, False),

    # Someone starting inside the bot's first 600 ms is not acted on until a later sentence
    # starts past the floor (plan, Hazards). Pinned as today's behaviour.
    ("user_onset", 3.3, "bob", False),
    ("bot_stopped", 3.5),
    ("bot_started", 3.6, False),
    ("bot_stopped", 3.75),
    ("bot_started", 3.8, True),
    YIELD,
    ("out", "InterruptionFrame"),
    ("user_onset", 3.9, "bob", False),  # once per response
    ("bot_stopped", 4.0),
    ("assistant_turn_stopped", 4.1),
    ("user_offset", 5.0, "bob"),
    *_sandwich("bob", "sorry"),

    # An onset past the floor yields at once.
    ("bot_started", 6.0, False),
    ("user_onset", 6.7, "alice", True),
    YIELD,
    ("bot_stopped", 6.8),
    ("assistant_turn_stopped", 6.9),
    ("user_offset", 7.0, "alice"),

    # Overlapping speakers stay apart.
    ("user_onset", 8.0, "alice", False),
    ("user_onset", 8.1, "bob", False),
    ("user_offset", 8.4, "alice"),
    ("user_offset", 8.4, "bob"),
    *_sandwich("alice", "one"),
    *_sandwich("bob", "two"),

    # A reply is not held back by someone talking: no audio yet, so nothing to yield.
    ("user_onset", 9.0, "alice", False),
    *_reply_out("Sure."),
    ("bot_started", 9.5, False),
    ("bot_stopped", 9.9),
    ("assistant_turn_stopped", 10.0),
    ("user_offset", 10.5, "alice"),

    # HAZARD, open decision 4 in the plan: bob has been talking since 20.0 with no stop, yet past
    # _SPEAKING_STALE_SECS he reads as silent, because _speaking keeps only the onset time. A fix
    # for the 3 s hazard changes these lines: idle(23.1) to False and bot_started(25.0) to True,
    # followed by a YIELD.
    ("user_onset", 20.0, "bob", False),
    ("idle", 20.1, False),
    ("idle", 23.0, False),
    ("idle", 23.1, True),  # HAZARD: bob is still talking
    ("bot_started", 24.0, False),
    ("bot_stopped", 24.8),
    ("bot_started", 25.0, False),  # HAZARD: floor spent and bob talking, no yield
    ("bot_stopped", 25.5),
    ("assistant_turn_stopped", 25.6),
    ("user_offset", 26.0, "bob"),
    ("idle", 26.1, True),

    # Typed chat interrupts and is queued at once.
    ("worker", "InterruptionFrame"),
    ("worker", "UserStartedSpeakingFrame"),
    ("worker", "TranscriptionFrame", "carol", "typed"),
    ("worker", "UserStoppedSpeakingFrame"),
]


def _describe(label, frame, direction=DOWN):
    name = type(frame).__name__
    if isinstance(frame, TranscriptionFrame):
        return (label, name, frame.user_id, frame.text)
    if isinstance(frame, LLMTextFrame):
        return (label, name, frame.text)
    if isinstance(frame, TTSSpeakFrame):
        return (label, name, frame.text)
    if isinstance(frame, TTSAudioRawFrame):
        return (label, name, len(frame.audio))
    if direction == FrameDirection.UPSTREAM:
        return (label, name, "upstream")
    return (label, name)


def _wav(pcm, rate):
    """A 16-bit mono WAV, written out by hand so the encoder is pinned too."""
    return (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(pcm)) + pcm
    )


class _Input(FrameProcessor):
    pass


class _Output(FrameProcessor):
    pass


class _Llm(FrameProcessor):
    pass


class _Tts(FrameProcessor):
    """Each piece of reply text becomes one chunk of audio."""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            await self.push_frame(TTSAudioRawFrame(BOT_PCM, 24000, 1), direction)


class _Stt(FrameProcessor):
    """A person's VAD and STT: keeps the audio it is given; the script decides what it reports."""

    def __init__(self, handlers):
        super().__init__()
        self.on_detected, self.on_cleared = handlers
        self.audio = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserAudioRawFrame):
            self.audio.append(frame.audio)


class _Capture(FrameProcessor):
    def __init__(self, label, trace):
        super().__init__()
        self._label, self._trace = label, trace

    async def queue_frame(self, frame, direction=DOWN, callback=None):
        self._trace.append(_describe(self._label, frame, direction))


class _Transport:
    def __init__(self):
        self.handlers = {}
        self._client = SimpleNamespace(room=SimpleNamespace(remote_participants={"alice": None}))
        self._input, self._output = _Input(), _Output()

    def input(self):
        return self._input

    def output(self):
        return self._output

    def event_handler(self, name):
        def register(fn):
            self.handlers[name] = fn
            return fn
        return register


class _Worker:
    def __init__(self, trace, pipeline, kwargs):
        self._trace, self.pipeline, self.kwargs = trace, pipeline, kwargs

    async def queue_frame(self, frame, direction=DOWN):
        self._trace.append(_describe("worker", frame, direction))

    async def queue_frames(self, frames):
        for frame in frames:
            await self.queue_frame(frame)


def _direct(processor):
    """No queues or tasks: when push_frame returns, the next processor has handled the frame."""
    async def queue_frame(frame, direction=DOWN, callback=None):
        await processor.process_frame(frame, direction)
    processor.queue_frame = queue_frame


class NormalRoomTest(unittest.IsolatedAsyncioTestCase):
    maxDiff = None

    async def asyncSetUp(self):
        self.trace, self.flushed, self.stts = [], [], []
        self.now = 0.0
        self.transport = _Transport()
        self.script = self.failure = None

        trace = self.trace

        class Tracker(InterruptionTracker):
            def __init__(tracker):
                super().__init__()
                self.tracker = tracker

            def user_onset(tracker, t, sid=None):
                result = super().user_onset(t, sid)
                trace.append(("user_onset", t, sid, result))
                return result

            def user_offset(tracker, t, sid=None):
                super().user_offset(t, sid)
                trace.append(("user_offset", t, sid))

            def bot_started(tracker, t):
                result = super().bot_started(t)
                trace.append(("bot_started", t, result))
                return result

            def bot_stopped(tracker, t):
                super().bot_stopped(t)
                trace.append(("bot_stopped", t))

            def idle(tracker, t):
                result = super().idle(t)
                trace.append(("idle", t, result))
                return result

            def assistant_turn_stopped(tracker, t):
                super().assistant_turn_stopped(t)
                trace.append(("assistant_turn_stopped", t))

        def new_stt(*args, vad_handlers=None):
            self.stts.append(_Stt(vad_handlers))
            return self.stts[-1]

        def new_worker(pipeline, **kwargs):
            self.worker = _Worker(trace, pipeline, kwargs)
            return self.worker

        async def on_flush(sid, wav, meta):
            self.flushed.append((sid, wav, meta))

        self.finalize = mock.AsyncMock()
        runner = SimpleNamespace(add_workers=mock.AsyncMock(), run=self._run_script)
        patches = [
            mock.patch.dict(os.environ, {
                "OPENAI_API_KEY": "k", "ELEVENLABS_API_KEY": "k", "NEMOTRON_STT_URL": "http://stt",
                "STT_MODEL_OVERRIDE": "",
            }),
            mock.patch.object(bot, "time", SimpleNamespace(monotonic=lambda: self.now, time=time.time)),
            mock.patch.object(bot, "InterruptionTracker", Tracker),
            mock.patch.object(bot, "LiveKitTransport", side_effect=lambda **_: self.transport),
            mock.patch.object(bot, "load_bot_config", new=mock.AsyncMock(return_value=CONFIG)),
            mock.patch.object(bot, "_build_stt_for_multi_speaker", side_effect=new_stt),
            mock.patch.object(bot, "_build_llm", side_effect=lambda *a, **k: _Llm()),
            mock.patch.object(bot, "_build_tts", side_effect=lambda *a, **k: _Tts()),
            mock.patch.object(bot, "build_audio_track_sink", side_effect=lambda *a, **k: AudioTrackSink(on_flush)),
            mock.patch.object(bot, "Pipeline", side_effect=list),
            mock.patch.object(bot, "PipelineWorker", side_effect=new_worker),
            mock.patch.object(bot, "WorkerRunner", return_value=runner),
            mock.patch.object(bot, "beat_forever", new=mock.AsyncMock()),
            mock.patch.object(bot, "wait_until_empty", side_effect=lambda *a, **k: asyncio.Event().wait()),
            mock.patch.object(bot, "_finalize_conversation", new=self.finalize),
            # say() guards its write, and nothing else in these scripts may touch the database.
            mock.patch.object(bot, "AsyncSessionLocal", side_effect=RuntimeError("no database here")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def _run_script(self):
        """Stands in for WorkerRunner.run: the meeting happens where the pipeline would run."""
        try:
            await self._wire()
            await self.script()
        except Exception as e:
            self.failure = e  # _bot logs a failed run and carries on; _meet raises it
            raise

    async def _wire(self):
        procs, observers = self.worker.pipeline, self.worker.kwargs["observers"]
        self.assertEqual([type(p).__name__ for p in procs], PIPELINE)
        self.assertEqual([type(o).__name__ for o in observers], OBSERVERS)
        self.observer = observers[2]
        self.assistant = procs[13]
        self.multi_stt = procs[3]
        # The two stretches of pipeline whose code is ours, from the input to the user aggregator,
        # and from the LLM's output to the output transport.
        self.front, self.back = procs[1:6], procs[8:12]
        for chain, end in ((self.front, "heard"), (self.back, "out")):
            for p, following in zip(chain, chain[1:] + [_Capture(end, self.trace)]):
                _direct(p)
                p.link(following)
                await p.setup(_make_setup())
            await chain[0].process_frame(StartFrame(), DOWN)
        self.trace.clear()

    async def _meet(self, script):
        self.script = script
        await bot.bot(ARGS)
        if self.failure:
            raise self.failure
        self.finalize.assert_awaited_once_with("s1", "completed")

    # The meeting's steps.

    async def _audio(self, sid, pcm=b"\x01\x02" * 160):
        """A frame of sid's audio; returns their STT. New person, new STT."""
        await self.front[0].process_frame(
            UserAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1, user_id=sid), DOWN)
        return self.stts[-1]

    async def _transcript(self, stt, sid, text):
        await stt.push_frame(TranscriptionFrame(
            text=text, user_id=sid, timestamp="2026-11-05T00:00:00Z", finalized=True))
        await self._settle()

    async def _settle(self):
        """Lets MultiSpeakerSTT's pump finish; with direct processors nothing else is in flight."""
        while self.multi_stt.qsize():
            await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def _reply(self, *chunks):
        for frame in (LLMFullResponseStartFrame(), *map(LLMTextFrame, chunks), LLMFullResponseEndFrame()):
            await self.back[0].process_frame(frame, DOWN)

    async def _bot_audio(self, frame_type):
        frame = frame_type()
        # Pipecat reports a frame once per hop.
        await self.observer.on_push_frame(SimpleNamespace(frame=frame))
        await self.observer.on_push_frame(SimpleNamespace(frame=frame))

    async def _reply_ended(self):
        """The real on_assistant_turn_stopped. No content: it returns before any storing."""
        handler = self.assistant._event_handlers["on_assistant_turn_stopped"].handlers[0]
        await handler(self.assistant, SimpleNamespace(content="", timestamp=None))

    async def _at(self, t, step, *args):
        self.now = t
        return await step(*args)

    async def test_normal_trace_is_unchanged(self):
        async def meeting():
            on = BotStartedSpeakingFrame
            off = BotStoppedSpeakingFrame
            tracker = self.tracker

            def idle(t):
                self.now = t
                tracker.idle(t)

            await self.transport.handlers["on_first_participant_joined"](self.transport, "alice")

            # Turn-taking.
            alice = await self._audio("alice")
            await self._at(1.0, alice.on_detected)
            await self._at(1.16, alice.push_frame, VADUserStartedSpeakingFrame())
            await self._at(2.0, alice.on_cleared)
            await self._at(2.0, alice.push_frame, VADUserStoppedSpeakingFrame())
            await self._at(2.2, self._transcript, alice, "alice", "hello")

            await self._at(3.0, self._reply, '{"reply": "Hi ', 'there.", "addressed_to": []}')
            await self._at(3.1, self._bot_audio, on)
            idle(3.2)

            # Inside the floor, then the sentence boundary that spends it.
            bob = await self._audio("bob")
            await self._at(3.3, bob.on_detected)
            await self._at(3.5, self._bot_audio, off)
            await self._at(3.6, self._bot_audio, on)
            await self._at(3.75, self._bot_audio, off)
            await self._at(3.8, self._bot_audio, on)
            await self.back[0].process_frame(InterruptionFrame(), DOWN)  # the worker injects it
            await self._at(3.9, bob.push_frame, VADUserStartedSpeakingFrame())
            await self._at(4.0, self._bot_audio, off)
            await self._at(4.1, self._reply_ended)
            await self._at(5.0, bob.push_frame, VADUserStoppedSpeakingFrame())
            await self._at(5.1, self._transcript, bob, "bob", "sorry")

            # An onset past the floor.
            await self._at(6.0, self._bot_audio, on)
            await self._at(6.7, alice.on_detected)
            await self._at(6.8, self._bot_audio, off)
            await self._at(6.9, self._reply_ended)
            await self._at(7.0, alice.on_cleared)

            # Overlap.
            await self._at(8.0, alice.on_detected)
            await self._at(8.1, bob.on_detected)
            await self._at(8.4, alice.on_cleared)
            await self._at(8.4, bob.on_cleared)
            await self._at(8.5, self._transcript, alice, "alice", "one")
            await self._at(8.5, self._transcript, bob, "bob", "two")

            # A reply while someone talks.
            await self._at(9.0, alice.on_detected)
            await self._at(9.2, self._reply, '{"reply": "Sure.", "addressed_to": []}')
            await self._at(9.5, self._bot_audio, on)
            await self._at(9.9, self._bot_audio, off)
            await self._at(10.0, self._reply_ended)
            await self._at(10.5, alice.on_cleared)

            # The 3 s hazard: see GOLDEN.
            await self._at(20.0, bob.on_detected)
            idle(20.1)
            idle(23.0)
            idle(23.1)
            await self._at(24.0, self._bot_audio, on)
            await self._at(24.8, self._bot_audio, off)
            await self._at(25.0, self._bot_audio, on)
            await self._at(25.5, self._bot_audio, off)
            await self._at(25.6, self._reply_ended)
            await self._at(26.0, bob.on_cleared)
            idle(26.1)

            # Typed chat; carol is not in the room's roster, so nothing is registered.
            await self.worker.kwargs["rtvi_processor"]._on_chat("carol", "typed")

        await self._meet(meeting)

        self.assertEqual(self.trace, GOLDEN)
        self.assertEqual(self.tracker.interruptions, 2)
        self.assertEqual([round(ms, 3) for ms in self.tracker.talkovers_ms], [200.0, 100.0])

    async def test_normal_wav_bytes_unchanged(self):
        """Each track is exactly the PCM received, in order: no padding for a gap in the frames.
        (So a gap collapses and tracks lose time alignment: the plan's biggest research risk, for
        modes that stop frames. A fix belongs to those modes; a normal room's bytes stay these.)"""
        chunks = [bytes([i]) * 320 for i in range(1, 4)]

        async def meeting():
            alice = await self._at(1.0, self._audio, "alice", chunks[0])
            await self._at(1.02, self._audio, "alice", chunks[1])
            await self._at(9.0, self._audio, "alice", chunks[2])  # 8 s with no frames
            bob = await self._at(9.0, self._audio, "bob", chunks[1])
            await self._at(9.5, self._reply, '{"reply": "Hi ', 'there.", "addressed_to": []}')
            self.assertEqual(alice.audio, chunks)  # the recorder drops nothing on its way to STT
            self.assertEqual(bob.audio, chunks[1:2])

        await self._meet(meeting)

        self.assertEqual(sorted(self.flushed, key=lambda f: f[0]), [
            ("alice", _wav(b"".join(chunks), 16000), {
                "sid": "alice", "sample_rate": 16000, "num_channels": 1,
                "duration_s": 0.03, "bytes": 44 + 960, "part": 0,
            }),
            ("bob", _wav(chunks[1], 16000), {
                "sid": "bob", "sample_rate": 16000, "num_channels": 1,
                "duration_s": 0.01, "bytes": 44 + 320, "part": 0,
            }),
            ("bot_r1", _wav(BOT_PCM * 2, 24000), {
                "sid": "bot_r1", "sample_rate": 24000, "num_channels": 1,
                "duration_s": 0.003, "bytes": 44 + 160, "part": 0,
            }),
        ])


if __name__ == "__main__":
    unittest.main()
