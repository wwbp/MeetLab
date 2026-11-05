"""The bot acting on audio_mode: the plan's "Bot pipeline, fake frames" table
(docs/audio-modes-test-plan.md), plus what the page and the bot agreed on.

Two harnesses. FloorRoomTest is the real bot() as in test_normal_golden.py: LiveKit, LLM, TTS, the
database and each person's VAD and STT are faked, and BotSpeaking frames are scripted. RealVadTest
runs real Silero on recorded speech, for what depends on the VAD itself: closing a transcript, and
Pipecat's forced stop. test_wav_keeps_time_across_hold_gap needs real LiveKit, so it is not here.
"""
import asyncio
import base64
import json
import time
import types
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from livekit import rtc
from loguru import logger
from pipecat.frames.frames import (
    BotSpeakingFrame,
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
    TTSStoppedFrame,
    TTSTextFrame,
    UserAudioRawFrame,
    UserSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from meetlab_bot import bot, floor_room
from meetlab_bot.bot_token import bot_token
from meetlab_bot.floor import Floor
from meetlab_bot.floor_room import RELEASE_TAIL_SECS, FloorRoom
from meetlab_bot.multi_speaker_stt import MultiSpeakerSTT
from tests import test_normal_golden as golden
from tests.test_long_speech_real_vad import FRAME, speech
from tests.test_multi_speaker_stt import _make_setup

DOWN = golden.DOWN
ALICE, BOB, CAROL = (SimpleNamespace(identity=i, name=i, metadata="", track_publications={})
                     for i in ("alice", "bob", "carol"))
YIELD = golden.YIELD


def _mic(muted=False, source=rtc.TrackSource.SOURCE_MICROPHONE):
    return SimpleNamespace(source=source, muted=muted)


def _packet(person, payload=b'{"type":"claim"}', topic="meetlab.floor"):
    return SimpleNamespace(data=payload, participant=person, topic=topic)


class _Room:
    """The parts of rtc.Room the bot uses: sync handlers, as the real one insists."""

    def __init__(self):
        self.handlers = {}
        self.remote_participants = {}
        self.attributes = []
        self.local_participant = SimpleNamespace(set_attributes=self._set_attributes)
        self.hang_publish = False

    async def _set_attributes(self, attributes):
        if self.hang_publish:
            await asyncio.Event().wait()
        self.attributes.append(attributes)

    def on(self, name):
        def register(fn):
            self.handlers.setdefault(name, []).append(fn)
            return fn
        return register

    def emit(self, name, *args):
        for fn in self.handlers.get(name, []):
            fn(*args)


class _Tts(FrameProcessor):
    """Each piece of reply text, or a fixed line, becomes one chunk of audio."""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            await self.push_frame(TTSAudioRawFrame(golden.BOT_PCM, 24000, 1), direction)
            await self.push_frame(TTSStoppedFrame(), direction)
            return
        await self.push_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            await self.push_frame(TTSAudioRawFrame(golden.BOT_PCM, 24000, 1), direction)
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self.push_frame(TTSStoppedFrame(), direction)


class _Words(FrameProcessor):
    """What leaves the played tap, on its way to the assistant aggregator (the stored transcript)."""

    def __init__(self, words):
        super().__init__()
        self._words = words

    async def queue_frame(self, frame, direction=DOWN, callback=None):
        if isinstance(frame, TTSTextFrame):
            self._words.append(frame.text)


class FloorRoomTest(golden.NormalRoomTest):
    test_normal_trace_is_unchanged = None  # inherited harness, not inherited tests
    test_normal_wav_bytes_unchanged = None
    mode = "ptt"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.room, self.events = _Room(), []
        self.hang_events = False
        self.words = []
        self.transport._client.room = self.room

        async def record_event(event_type, severity="info", room_name=None, conv_id=None, payload=None):
            if self.hang_events:
                await asyncio.Event().wait()
            self.events.append((event_type, room_name, conv_id, payload))

        async def load(room_name):
            return replace(golden.CONFIG, audio_mode=self.mode)

        for p in (
            mock.patch.object(bot, "load_bot_config", new=load),
            mock.patch.object(bot, "_build_tts", side_effect=lambda *a, **k: _Tts()),
            mock.patch.object(bot, "_publish_avatar", new=mock.AsyncMock()),
            mock.patch.object(floor_room, "record_event", new=record_event),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def _wire(self):
        procs = self.worker.pipeline
        names = [type(p).__name__ for p in procs]
        self.observer = self.worker.kwargs["observers"][2]
        self.assistant = procs[-1]
        self.multi_stt = procs[names.index("MultiSpeakerSTT")]
        self.recorder = procs[names.index("PerSpeakerAudioRecorder")]
        self.floor = next(p.owner for p in procs if hasattr(p, "owner"))
        self.played = procs[names.index("_Output") + 1]
        self.front = procs[1:names.index("LLMUserAggregator")]
        self.back = procs[names.index("ReplyStreamer"):names.index("_Output")]
        ends = ((self.front, golden._Capture("heard", self.trace)), (self.back, golden._Capture("out", self.trace)),
                ([self.played], _Words(self.words)))
        for chain, end in ends:
            for p, following in zip(chain, chain[1:] + [end]):
                golden._direct(p)
                p.link(following)
                await p.setup(_make_setup())
            await chain[0].process_frame(StartFrame(), DOWN)
        await self.transport.handlers["on_connected"](self.transport)
        self.trace.clear()

    async def _meet_in(self, mode, script):
        self.mode = mode
        await self._meet(script)

    # The meeting's steps.

    async def _spin(self):
        """Lets the event and attribute tasks run; they share the loop with the script."""
        for _ in range(20):
            await asyncio.sleep(0)

    async def _tick(self, t):
        self.now = t
        await self.floor.tick(t)
        await self._spin()

    async def _press(self, t, person):
        self.now = t
        self.room.emit("track_unmuted", person, _mic())
        await self._spin()

    async def _let_go(self, t, person):
        self.now = t
        self.room.emit("track_muted", person, _mic(muted=True))
        await self._spin()

    async def _claim(self, t, person):
        self.now = t
        self.room.emit("data_received", _packet(person))
        await self._spin()

    async def _release(self, t, person):
        self.now = t
        self.room.emit("data_received", _packet(person, b'{"type":"release"}'))
        await self._spin()

    async def _play(self, frame):
        await self.played.process_frame(frame, DOWN)
        await self._spin()

    async def _say_words(self, *words):
        for word in words:
            await self._play(TTSTextFrame(word, "word"))

    def _out_audio(self):
        return sum(1 for item in self.trace if item[:2] == ("out", "TTSAudioRawFrame"))

    def _yields(self):
        return self.trace.count(YIELD)

    def _worker_frames(self, name):
        return [item for item in self.trace if item[:2] == ("worker", name)]

    def _event_types(self):
        return [e[0] for e in self.events]

    async def _reply_begins(self, t, *chunks):
        """A reply with more to come: its TTSStoppedFrame hasn't reached the gate."""
        self.now = t
        for frame in (LLMFullResponseStartFrame(), *map(LLMTextFrame, chunks)):
            await self.back[0].process_frame(frame, DOWN)
        await self._spin()

    async def _reply_now(self, t, text="Sure."):
        self.now = t
        await self._reply(json.dumps({"reply": text, "addressed_to": []}))
        await self._spin()

    def _holder_and_queue(self):
        last = self.room.attributes[-1]
        return last["floor.holder"], last["floor.queue"]

    # ptt: the plan's table.

    async def test_overlapping_holders_stay_attributed(self):
        """Alice's release closes Alice's transcript, not Bob's, while he still holds."""
        async def meeting():
            await self._press(1.0, ALICE)
            await self._press(1.1, BOB)
            alice = await self._at(1.2, self._audio, "alice")
            await self._at(1.2, alice.on_detected)
            bob_first = self.stts[-1]
            bob = await self._at(1.3, self._audio, "bob")
            self.assertIsNot(bob, bob_first)
            await self._at(1.3, bob.on_detected)
            await self._let_go(2.0, ALICE)
            await self._tick(2.0 + RELEASE_TAIL_SECS)

            self.assertEqual(len(alice.audio), 2, "her audio, then the silence that ends her turn")
            self.assertEqual(set(alice.audio[1]), {0})
            self.assertEqual(len(bob.audio), 1, "no silence pushed to someone still holding")

            await self._at(2.3, self._transcript, alice, "alice", "one")
            await self._at(2.4, bob.on_cleared)
            await self._at(2.5, self._transcript, bob, "bob", "two")

        await self._meet_in("ptt", meeting)

        heard = [item for item in self.trace if item[:2] == ("heard", "TranscriptionFrame")]
        self.assertEqual([item[2] for item in heard], ["alice", "bob"])

    async def test_reply_waits_until_everyone_releases(self):
        async def meeting():
            await self._press(1.0, ALICE)
            await self._press(1.1, BOB)
            await self._let_go(2.0, ALICE)
            await self._reply_now(3.0)
            self.assertEqual(self._out_audio(), 0, "Bob holds")
            self.assertEqual(self._yields(), 1, "the reply is cancelled, not generated for nothing")
            await self.back[0].process_frame(InterruptionFrame(), DOWN)  # the worker injects it

            for second in range(4, 10):  # Bob keeps talking
                self.now = float(second)
                bob = await self._audio("bob")
                await self._at(float(second), bob.push_frame, UserSpeakingFrame())
                await self._tick(float(second))
            self.assertEqual(self._out_audio(), 0)
            self.assertEqual(self._worker_frames("LLMRunFrame"), [], "still held")

            await self._let_go(10.0, BOB)
            await self._tick(10.0)
            await self._tick(10.0 + floor_room.REPLY_AGAIN_AFTER_SECS - 0.1)
            self.assertEqual(self._worker_frames("LLMRunFrame"), [], "Bob's last words may yet be a turn")
            await self._tick(10.0 + floor_room.REPLY_AGAIN_AFTER_SECS)
            self.assertEqual(len(self._worker_frames("LLMRunFrame")), 1, "the turn is still owed its reply")
            await self._tick(30.0)
            self.assertEqual(len(self._worker_frames("LLMRunFrame")), 1, "and only one")

            await self._reply_now(31.0)
            self.assertEqual(self._out_audio(), 1)

        await self._meet_in("ptt", meeting)

    async def test_a_new_turn_replaces_the_blocked_reply(self):
        async def meeting():
            await self._press(1.0, ALICE)
            await self._reply_now(2.0)
            await self.back[0].process_frame(InterruptionFrame(), DOWN)
            await self._let_go(3.0, ALICE)
            await self._reply_now(3.5)  # her turn produced a reply of its own
            await self._tick(10.0)
            await self._tick(10.0 + floor_room.REPLY_AGAIN_AFTER_SECS)
            self.assertEqual(self._out_audio(), 1)
            self.assertEqual(self._worker_frames("LLMRunFrame"), [])

        await self._meet_in("ptt", meeting)

    async def test_speech_while_holding_yields_the_bot(self):
        """A press alone doesn't touch a reply already speaking; speech does, once."""
        async def meeting():
            await self._reply_begins(1.0, '{"reply": "Hi ', "there. ")
            await self._at(1.1, self._bot_audio, BotStartedSpeakingFrame)
            self.assertEqual(self._out_audio(), 2)

            await self._press(2.0, ALICE)
            self.assertEqual(self._yields(), 0, "a press is not speech")
            await self._at(2.1, self.back[0].process_frame, LLMTextFrame('Still going.", "addressed_to": []}'), DOWN)
            self.assertEqual(self._out_audio(), 3, "the reply in progress keeps its audio")

            alice = await self._at(2.2, self._audio, "alice")
            await self._at(2.2, alice.on_detected)
            await self._at(2.3, alice.push_frame, VADUserStartedSpeakingFrame())
            self.assertEqual(self._yields(), 1)
            self.assertEqual(self.tracker.interruptions, 1)

        await self._meet_in("ptt", meeting)

    async def test_silent_hold_stops_blocking_after_15_s(self):
        """Audio frames don't count as speech: an open mic in a quiet room must time out."""
        async def meeting():
            await self._press(1.0, ALICE)
            self.now = 15.0
            await self._audio("alice")  # no VAD report: room tone
            await self._reply_now(15.9)
            self.assertEqual(self._out_audio(), 0, "14.9 s silent")
            await self.back[0].process_frame(InterruptionFrame(), DOWN)
            await self._reply_now(16.0)
            self.assertEqual(self._out_audio(), 1, "15 s silent")

        await self._meet_in("ptt", meeting)

    async def test_vad_speech_keeps_a_hold_blocking(self):
        async def meeting():
            await self._press(1.0, ALICE)
            alice = await self._at(1.0, self._audio, "alice")
            await self._at(10.0, alice.push_frame, UserSpeakingFrame())
            await self._reply_now(24.9)
            self.assertEqual(self._out_audio(), 0, "she was heard 14.9 s ago")

        await self._meet_in("ptt", meeting)

    async def _lines_and_chat_obey_the_gate(self, mode, press, let_go):
        self.room.remote_participants = {"alice": ALICE}  # the greeting waits for someone

        async def meeting():
            await press(1.0, ALICE)

            # A greeting, as say() sends it: the audio it makes is held back.
            await self.transport.handlers["on_first_participant_joined"](self.transport, "alice")
            self.assertEqual(self._worker_frames("TTSSpeakFrame"), [("worker", "TTSSpeakFrame", "Welcome.")])
            await self.back[0].process_frame(TTSSpeakFrame("Welcome."), DOWN)
            self.assertEqual(self._out_audio(), 0)
            self.assertEqual(self._yields(), 1)
            await self.back[0].process_frame(InterruptionFrame(), DOWN)

            # Typed chat, and its reply.
            await self.worker.kwargs["rtvi_processor"]._on_chat("carol", "typed")
            await self._reply_now(2.0)
            self.assertEqual(self._out_audio(), 0)
            await self.back[0].process_frame(InterruptionFrame(), DOWN)

            # Free again: the line is said, the reply run.
            self.trace.clear()
            await let_go(3.0, ALICE)
            await self._tick(3.0)
            await self._tick(3.0 + floor_room.REPLY_AGAIN_AFTER_SECS)
            self.assertEqual(self._worker_frames("TTSSpeakFrame"), [("worker", "TTSSpeakFrame", "Welcome.")])
            self.assertEqual(len(self._worker_frames("LLMRunFrame")), 1)
            await self.back[0].process_frame(TTSSpeakFrame("Welcome."), DOWN)
            self.assertEqual(self._out_audio(), 1)

        await self._meet_in(mode, meeting)

    async def test_fixed_lines_and_chat_replies_obey_the_gate(self):
        await self._lines_and_chat_obey_the_gate("ptt", self._press, self._let_go)

    async def test_fixed_lines_and_chat_replies_obey_the_baton(self):
        await self._lines_and_chat_obey_the_gate("baton", self._claim, self._release)

    # ptt: what counts as a press.

    async def test_first_press_with_the_mic_off_at_prejoin_counts(self):
        """Never published: the first enable arrives as track_published, not track_unmuted."""
        async def meeting():
            self.now = 1.0
            self.room.emit("track_published", _mic(), ALICE)
            await self._reply_now(1.5)
            self.assertEqual(self._out_audio(), 0)
            await self.back[0].process_frame(InterruptionFrame(), DOWN)
            self.now = 2.0
            self.room.emit("track_unpublished", _mic(), ALICE)
            await self._reply_now(2.5)
            self.assertEqual(self._out_audio(), 1)

        await self._meet_in("ptt", meeting)

    async def test_a_muted_publication_is_not_a_press(self):
        async def meeting():
            self.now = 1.0
            self.room.emit("track_published", _mic(muted=True), ALICE)
            await self._reply_now(1.5)
            self.assertEqual(self._out_audio(), 1)

        await self._meet_in("ptt", meeting)

    async def test_someone_already_unmuted_when_the_bot_joins_holds(self):
        async def meeting():
            await self._reply_now(1.0)
            self.assertEqual(self._out_audio(), 0)

        person = SimpleNamespace(identity="alice", name="alice", metadata="", track_publications={"m": _mic()})
        self.room.remote_participants = {"alice": person}
        await self._meet_in("ptt", meeting)

    async def test_camera_and_screen_are_not_a_press(self):
        async def meeting():
            self.now = 1.0
            self.room.emit("track_unmuted", ALICE, _mic(source=rtc.TrackSource.SOURCE_CAMERA))
            self.room.emit("track_published", _mic(source=rtc.TrackSource.SOURCE_SCREENSHARE), ALICE)
            await self._reply_now(1.5)
            self.assertEqual(self._out_audio(), 1)
            self.assertEqual(self.events, [])

        await self._meet_in("ptt", meeting)

    async def test_a_ptt_room_sends_no_attributes(self):
        async def meeting():
            await self._press(1.0, ALICE)
            await self._let_go(2.0, ALICE)
            self.assertEqual(self.room.attributes, [])

        await self._meet_in("ptt", meeting)

    # baton: the plan's table.

    async def test_non_holder_audio_never_reaches_stt(self):
        speech_ = b"\x01\x02" * 160

        async def meeting():
            await self._claim(1.0, ALICE)
            self.now = 1.5
            await self._audio("alice", speech_)
            await self._audio("bob", speech_)
            await self._audio("carol", speech_)
            self.assertEqual(len(self.stts), 1, "only the holder has a transcriber")
            self.assertEqual(self.stts[0].audio, [speech_])

        await self._meet_in("baton", meeting)

        recorded = {sid for sid, _wav, _meta in self.flushed}
        self.assertEqual(recorded, {"alice", "bob", "carol"}, "the recorder gets all of it")

    async def test_release_tail_keeps_the_last_audio(self):
        """track_muted overtakes the last audio by up to 121 ms; so may a release message."""
        last, late = b"\x03\x04" * 160, b"\x05\x06" * 160

        async def meeting():
            await self._claim(1.0, ALICE)
            alice = await self._at(1.5, self._audio, "alice")
            await self._at(1.5, alice.on_detected)
            await self._release(2.0, ALICE)
            await self._at(2.0 + RELEASE_TAIL_SECS - 0.01, self._audio, "alice", last)
            self.assertEqual(alice.audio[-1], last, "inside the tail")
            await self._tick(2.0 + RELEASE_TAIL_SECS)
            self.assertEqual(set(alice.audio[-1]), {0}, "the silence that ends the turn")
            count = len(alice.audio)
            await self._at(2.0 + RELEASE_TAIL_SECS + 0.01, self._audio, "alice", late)
            self.assertEqual(len(alice.audio), count, "past the tail")

        await self._meet_in("baton", meeting)

    async def test_silence_goes_only_to_whoever_was_mid_speech(self):
        async def meeting():
            await self._claim(1.0, ALICE)
            alice = await self._at(1.5, self._audio, "alice")
            await self._at(1.5, alice.on_detected)
            await self._at(1.8, alice.on_cleared)
            await self._release(2.0, ALICE)
            await self._tick(2.0 + RELEASE_TAIL_SECS)
            self.assertEqual(len(alice.audio), 1, "her VAD already closed the turn")

        await self._meet_in("baton", meeting)

    async def test_pressing_again_in_the_tail_cancels_the_silence(self):
        async def meeting():
            await self._press(1.0, ALICE)
            alice = await self._at(1.5, self._audio, "alice")
            await self._at(1.5, alice.on_detected)
            await self._let_go(2.0, ALICE)
            await self._press(2.1, ALICE)
            await self._tick(2.0 + RELEASE_TAIL_SECS)
            self.assertEqual(len(alice.audio), 1)

        await self._meet_in("ptt", meeting)

    async def test_press_during_generation_blocks_first_audio(self):
        async def meeting():
            await self._claim(1.0, ALICE)
            await self._reply_now(1.2)
            self.assertEqual(self._out_audio(), 0)
            self.assertEqual(self._yields(), 1)

        await self._meet_in("baton", meeting)

    async def test_bot_claims_at_first_audio_and_releases_when_it_stops(self):
        async def meeting():
            await self._reply_now(1.0)
            self.assertEqual(self._out_audio(), 1)
            self.assertEqual(self._holder_and_queue(), ("bot_r1", ""))
            await self._at(1.1, self._play, BotStoppedSpeakingFrame())
            await self._spin()
            self.assertEqual(self._holder_and_queue(), ("", ""))

        await self._meet_in("baton", meeting)

    async def test_press_while_the_bot_speaks_finishes_the_sentence_then_yields(self):
        async def meeting():
            await self._reply_begins(1.0, '{"reply": "First sentence. ')
            await self._say_words("First")

            await self._claim(2.0, ALICE)
            self.assertIn("floor.deny", self._event_types())
            self.assertEqual(self._holder_and_queue(), ("bot_r1", "alice"))
            self.assertEqual(self._yields(), 0, "not mid-sentence")

            await self._at(2.1, self._say_words, "sentence.")
            self.assertEqual(self._yields(), 0, "that word is still playing")

            await self._at(2.5, self._say_words, "Second")
            self.assertEqual(self._yields(), 1)
            self.assertEqual(self._holder_and_queue(), ("alice", ""))
            self.assertIn("floor.grant", self._event_types())

            await self._say_words("thing")  # what was already queued before the interruption lands
            self.assertEqual(self._yields(), 1)
            self.assertEqual(self.words[:2], ["First", "sentence."], "the transcript stops where the audio did")
            self.assertNotIn("Second", self.words)

            # Anything the TTS still produces for this reply stays out.
            count = self._out_audio()
            await self.back[0].process_frame(LLMTextFrame('More.", "addressed_to": []}'), DOWN)
            self.assertEqual(self._out_audio(), count)
            self.assertEqual(self._worker_frames("LLMRunFrame"), [], "it said its part")

        await self._meet_in("baton", meeting)

    async def test_press_in_a_gap_between_sentences_stops_the_next_one(self):
        async def meeting():
            await self._reply_begins(1.0, '{"reply": "One. ')
            await self._at(1.5, self._play, BotStoppedSpeakingFrame())  # a pause longer than 350 ms
            await self._spin()
            self.assertEqual(self._holder_and_queue(), ("", ""))
            await self._claim(1.6, ALICE)
            self.assertEqual(self._holder_and_queue(), ("alice", ""))
            count = self._out_audio()
            await self._at(1.7, self.back[0].process_frame, LLMTextFrame('Two.", "addressed_to": []}'), DOWN)
            self.assertEqual(self._out_audio(), count)
            self.assertEqual(self._yields(), 1)

        await self._meet_in("baton", meeting)

    async def test_long_reply_keeps_the_baton(self):
        """The bot is on the same 15 s clock: without heard() it would lose it mid-sentence."""
        async def meeting():
            await self._reply_now(1.0)
            for second in range(2, 40):
                await self._at(float(second), self._play, BotSpeakingFrame())
                await self._tick(float(second))
            self.assertEqual(self._holder_and_queue(), ("bot_r1", ""))
            self.assertNotIn("floor.timeout", self._event_types())

        await self._meet_in("baton", meeting)

    async def test_a_stuck_reply_loses_the_baton(self):
        async def meeting():
            await self._reply_now(1.0)  # audio left the gate; nothing is ever heard
            await self._claim(2.0, ALICE)
            await self._tick(1.0 + Floor.IDLE_SECS)
            self.assertEqual(self._holder_and_queue(), ("alice", ""))

        await self._meet_in("baton", meeting)

    # baton: the contract with the page.

    async def test_identity_comes_from_the_sender_never_the_payload(self):
        async def meeting():
            self.now = 1.0
            self.room.emit("data_received", _packet(ALICE, b'{"type":"claim","identity":"bob","who":"bob"}'))
            await self._spin()
            self.assertEqual(self._holder_and_queue(), ("alice", ""))

        await self._meet_in("baton", meeting)

    async def test_malformed_payloads_and_other_topics_are_ignored(self):
        async def meeting():
            self.now = 1.0
            for packet in (
                _packet(ALICE, b"not json"), _packet(ALICE, b"\xff\xfe"), _packet(ALICE, b"[1,2]"),
                _packet(ALICE, b'"claim"'), _packet(ALICE, b"null"), _packet(ALICE, b'{"type":"grab"}'),
                _packet(ALICE, b'{"type":["claim"]}'), _packet(ALICE, b"{}"),
                _packet(ALICE, topic="lk.chat"), _packet(ALICE, topic=None),
                _packet(None),  # a server SDK: no sender, no identity
            ):
                self.room.emit("data_received", packet)
            await self._spin()
            self.assertEqual(self.events, [])
            self.assertEqual(self.room.attributes, [])

        await self._meet_in("baton", meeting)

    async def test_mic_events_do_nothing_in_a_baton_room(self):
        async def meeting():
            await self._press(1.0, ALICE)
            self.assertEqual(self.events, [])
            self.assertEqual(self.room.attributes, [])

        await self._meet_in("baton", meeting)

    async def test_attributes_follow_every_change(self):
        async def meeting():
            await self._claim(1.0, ALICE)
            await self._claim(1.1, BOB)
            await self._claim(1.2, CAROL)
            await self._release(2.0, ALICE)
            await self._release(3.0, CAROL)  # leaves the line
            await self._release(4.0, BOB)
            self.assertEqual([(a["floor.holder"], a["floor.queue"]) for a in self.room.attributes], [
                ("alice", ""), ("alice", "bob"), ("alice", "bob,carol"),
                ("bob", "carol"), ("bob", ""), ("", ""),
            ])

        await self._meet_in("baton", meeting)

    async def test_a_hung_publish_delays_no_grant_and_no_later_publish(self):
        speech_ = b"\x01\x02" * 160

        async def meeting():
            await self._claim(1.0, ALICE)
            await self._claim(1.1, BOB)
            self.room.hang_publish = True
            await self._release(2.0, ALICE)
            self.now = 2.0
            await self._audio("bob", speech_)
            self.assertEqual(self.stts[-1].audio, [speech_], "granted while the publish hangs")

            self.room.hang_publish = False
            await asyncio.sleep(0.05)  # past the timeout
            await self._release(3.0, BOB)
            await asyncio.sleep(0.05)
            self.assertEqual(self._holder_and_queue(), ("", ""))

        with mock.patch.object(floor_room, "PUBLISH_TIMEOUT_SECS", 0.01):
            await self._meet_in("baton", meeting)

    async def test_disconnect_frees_the_baton_at_once(self):
        async def meeting():
            await self._claim(1.0, ALICE)
            await self._claim(1.1, BOB)
            self.now = 2.0
            await self.transport.handlers["on_participant_disconnected"](self.transport, "alice")
            await self._spin()
            self.assertEqual(self._holder_and_queue(), ("bob", ""))

        await self._meet_in("baton", meeting)

    async def test_a_new_bot_starts_with_a_free_baton(self):
        async def meeting():
            self.assertIsNone(self.floor._floor.holder)
            await self._reply_now(1.0)
            self.assertEqual(self._out_audio(), 1)

        await self._meet_in("baton", meeting)

    # Events.

    async def test_floor_events_are_logged(self):
        before = time.time()

        async def meeting():
            await self._press(1.0, ALICE)
            await self._let_go(2.0, ALICE)
            await self._press(3.0, BOB)
            await self._tick(3.0 + Floor.IDLE_SECS)
            await self._tick(4.0 + Floor.IDLE_SECS)

        await self._meet_in("ptt", meeting)

        self.assertEqual(self._event_types(), ["floor.press", "floor.release", "floor.press", "floor.timeout"])
        for _type, room_name, conv_id, payload in self.events:
            self.assertEqual((room_name, conv_id), ("r1", "s1"))
            self.assertGreaterEqual(payload["at"], before)
            self.assertLessEqual(payload["at"], time.time())
        self.assertEqual([e[3]["who"] for e in self.events], ["alice", "alice", "bob", "bob"])

    async def test_baton_events_are_logged_once_each(self):
        async def meeting():
            await self._claim(1.0, ALICE)
            await self._claim(1.5, ALICE)  # key repeat: nothing new
            await self._claim(1.6, BOB)
            await self._claim(1.7, BOB)  # nor here
            await self._release(2.0, ALICE)
            await self._tick(2.0 + Floor.IDLE_SECS)

        await self._meet_in("baton", meeting)

        self.assertEqual([(e[0], e[3]["who"]) for e in self.events], [
            ("floor.claim", "alice"), ("floor.grant", "alice"),
            ("floor.claim", "bob"), ("floor.deny", "bob"),
            ("floor.release", "alice"), ("floor.grant", "bob"),
            ("floor.timeout", "bob"),
        ])

    async def test_a_late_release_from_nobody_logs_nothing(self):
        async def meeting():
            await self._release(1.0, ALICE)
            self.assertEqual(self.events, [])

        await self._meet_in("baton", meeting)

    async def test_hung_event_log_does_not_delay_grant(self):
        speech_ = b"\x01\x02" * 160

        async def meeting():
            self.hang_events = True
            before = self.room.attributes[:]
            self.now = 1.0
            self.room.emit("data_received", _packet(ALICE))  # sync: returns with the grant made
            self.assertTrue(self.floor._floor.holds("alice"))
            self.now = 1.0
            await self._audio("alice", speech_)
            self.assertEqual(self.stts[-1].audio, [speech_])
            await self._spin()
            self.assertEqual(self.room.attributes, before + [{"floor.holder": "alice", "floor.queue": ""}])

        await self._meet_in("baton", meeting)

    async def test_the_timer_runs_tick(self):
        """Nothing else notices silence with nobody pressing."""
        async def meeting():
            await self._claim(1.0, ALICE)
            await self._claim(1.1, BOB)
            self.now = 1.0 + Floor.IDLE_SECS
            await asyncio.sleep(floor_room.TICK_SECS * 3)
            self.assertEqual(self._holder_and_queue(), ("bob", ""))

        await self._meet_in("baton", meeting)

    async def test_a_failing_tick_does_not_stop_the_timer(self):
        async def meeting():
            await self._claim(1.0, ALICE)
            await self._claim(1.1, BOB)
            real, calls = self.floor.tick, []

            async def flaky(t):
                calls.append(t)
                if len(calls) == 1:
                    raise RuntimeError("once")
                await real(t)

            self.floor.tick = flaky
            self.now = 1.0 + Floor.IDLE_SECS
            await asyncio.sleep(floor_room.TICK_SECS * 4)
            self.assertGreater(len(calls), 1)
            self.assertEqual(self._holder_and_queue(), ("bob", ""))

        await self._meet_in("baton", meeting)


class BotTokenTest(unittest.TestCase):
    def test_the_bot_may_publish_its_own_attributes(self):
        """set_attributes needs canUpdateOwnMetadata; without it the page never sees a grant."""
        token = bot_token("r1", "bot_r1", "key", "secret-long-enough-for-a-signature-0123456789", 5)
        claims = token.split(".")[1]
        grants = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))["video"]
        self.assertTrue(grants["canUpdateOwnMetadata"])


class _Seen(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)


class RealQueuesTest(unittest.IsolatedAsyncioTestCase):
    """The taps in a real PipelineWorker: its queues and tasks, and a real interruption. The fake
    harness above calls processors directly, so it can't show that a tap survives cancelling
    itself, or that a frame pushed from the timer arrives."""

    async def asyncSetUp(self):
        self.clock = [1.0]
        patch = mock.patch.object(floor_room, "record_event", new=mock.AsyncMock())
        patch.start()
        self.addCleanup(patch.stop)

        async def enqueue(frame, direction=DOWN):
            await self.worker.queue_frame(frame, direction)

        self.floor = FloorRoom("ptt", bot_identity="bot_r1", room_name="r1", conv_id="s1", enqueue=enqueue,
                               vad_stop_secs=0.45, now=lambda: self.clock[0])
        self.seen = _Seen()
        self.worker = PipelineWorker(
            Pipeline([self.floor.mic_gate, self.floor.reply_gate, self.floor.played, self.seen]),
            params=PipelineParams(), enable_rtvi=False)
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        await runner.add_workers(self.worker)
        self.running = asyncio.create_task(runner.run())
        self.addAsyncCleanup(self._stop)

    async def _stop(self):
        await self.worker.cancel()
        await self.running

    async def _until(self, done):
        for _ in range(300):
            if done():
                return True
            await asyncio.sleep(0.01)
        return False

    def _audio_out(self):
        return [f for f in self.seen.frames if isinstance(f, TTSAudioRawFrame)]

    async def test_a_blocked_reply_cancels_itself_and_the_next_one_plays(self):
        self.floor.press("alice")
        audio = TTSAudioRawFrame(golden.BOT_PCM, 24000, 1)
        await self.worker.queue_frame(audio)
        interrupted = lambda: any(isinstance(f, InterruptionFrame) for f in self.seen.frames)
        self.assertTrue(await self._until(interrupted), "the worker never turned the request into an interruption")
        self.assertEqual(self._audio_out(), [])

        self.floor.release("alice")
        await self.worker.queue_frame(TTSAudioRawFrame(golden.BOT_PCM, 24000, 1))
        self.assertTrue(await self._until(self._audio_out), "the gate stayed shut after the interruption")

    async def test_the_timers_silence_reaches_what_follows_the_gate(self):
        self.floor.press("alice")
        await self.worker.queue_frame(UserAudioRawFrame(b"\x01\x02" * 160, 16000, 1, "alice"))
        self.floor.heard("alice")
        users = lambda: [f for f in self.seen.frames if isinstance(f, UserAudioRawFrame)]
        self.assertTrue(await self._until(users))

        self.floor.release("alice")
        self.clock[0] += RELEASE_TAIL_SECS + 0.01
        await self.floor.tick(self.clock[0])
        self.assertTrue(await self._until(lambda: len(users()) == 2))
        self.assertEqual(set(users()[1].audio), {0})
        self.assertEqual(users()[1].user_id, "alice")


class _SegmentStt(FrameProcessor):
    """Transcribes like a segmented STT: the audio from the VAD's start to its stop, one turn each."""

    def __init__(self, sid, segments):
        super().__init__()
        self._sid, self._segments, self._audio = sid, segments, None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserAudioRawFrame):
            if self._audio is not None:
                self._audio.extend(frame.audio)
            return
        await self.push_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._audio = bytearray()
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and self._audio is not None:
            self._segments.append((self._sid, bytes(self._audio)))
            self._audio = None
            await self.push_frame(TranscriptionFrame(
                text="words", user_id=self._sid, timestamp="2026-11-05T00:00:00Z", finalized=True), direction)


class RealVadTest(unittest.IsolatedAsyncioTestCase):
    """Real Silero and VADProcessor on recorded speech, behind the real mic gate."""

    SPEECH = speech()

    async def _room(self, mode):
        self.clock, self.onsets, self.segments, self.warnings = [0.0], [], [], []
        sink = logger.add(lambda m: self.warnings.append(str(m)), level="WARNING")
        self.addCleanup(logger.remove, sink)
        patch = mock.patch.object(floor_room, "record_event", new=mock.AsyncMock())
        patch.start()
        self.addCleanup(patch.stop)
        self.floor = FloorRoom(
            mode, bot_identity="bot_r1", room_name="r1", conv_id="s1", enqueue=mock.AsyncMock(),
            vad_stop_secs=0.45, now=lambda: self.clock[0])

        async def onset(sid):  # the analyzer's STARTING edge: the interruption signal
            self.onsets.append(sid)
            self.floor.heard(sid)

        async def started(sid):  # the same onset again, from the VAD's frame
            self.floor.heard(sid)

        async def offset(sid):
            self.floor.quiet(sid)

        config = types.SimpleNamespace(stt_endpointing_ms=450)

        def chain(sid=None):
            vad = bot._build_vad_processor(config, (lambda: onset(sid), lambda: offset(sid)))
            stt = _SegmentStt(sid, self.segments)
            vad.link(stt)
            return (vad, stt)

        multi = MultiSpeakerSTT(chain, on_speech_onset=started, on_speech_offset=offset, on_still_speaking=self.floor.heard)
        gate = self.gate = self.floor.mic_gate
        setup = _make_setup()
        setup.audio_in_sample_rate = 16000
        start = StartFrame(audio_in_sample_rate=16000, audio_out_sample_rate=16000)
        for p, following in ((gate, multi), (multi, golden._Capture("heard", []))):
            golden._direct(p)
            p.link(following)
            await p.setup(setup)
        await gate.process_frame(start, DOWN)
        self.at = 0

    def _press(self, who="a"):
        self.floor.press(who)

    async def _feed(self, chunks, who="a"):
        for chunk in chunks:
            self.clock[0] += 0.02
            await self.gate.process_frame(UserAudioRawFrame(chunk, 16000, 1, who), DOWN)
            await asyncio.sleep(0)

    def _speech(self, seconds):
        """The next stretch of the recording, in 20 ms frames."""
        chunks = [self.SPEECH[i:i + FRAME] for i in range(self.at, self.at + int(seconds * 50) * FRAME, FRAME)]
        self.at += len(chunks) * FRAME
        return chunks

    async def _speak_until_heard(self):
        count = len(self.onsets)
        for _ in range(1000):
            await self._feed(self._speech(0.1))
            if len(self.onsets) > count:
                await self._feed(self._speech(0.5))  # into SPEAKING, well past the start
                return
        self.fail("the recording never read as speech")

    async def _until(self, done):
        for _ in range(150):
            if done():
                return True
            await asyncio.sleep(0.01)
        return False

    async def test_release_closes_transcript_keeps_last_words(self):
        for mode in ("ptt", "baton"):
            for frames_after in ("stopped", "silent"):
                with self.subTest(mode=mode, frames_after=frames_after):
                    await self._room(mode)
                    self._press()
                    await self._speak_until_heard()

                    released = self.clock[0]
                    self.floor.release("a")
                    last = self._speech(0.06)  # track_muted overtakes the last audio by 52-90 ms
                    await self._feed(last)
                    self.clock[0] = released + RELEASE_TAIL_SECS + 0.01
                    await self.floor.tick(self.clock[0])

                    if frames_after == "silent":
                        await self._feed([bytes(FRAME)] * 10)
                    self.assertTrue(await self._until(lambda: self.segments), "the transcript never closed")
                    self.assertLess(self.clock[0] - released, 1.0)
                    self.assertEqual(len(self.segments), 1, "one turn")
                    self.assertIn(b"".join(last), self.segments[0][1], "the final word is present")
                    self.assertEqual(self.warnings, [])

    async def _next_press_fires_onset(self, mode):
        """Pipecat's 1.0 s forced stop leaves the analyzer in SPEAKING, so the next person's
        onset (the interruption signal) never fires. Nothing here may let it happen."""
        await self._room(mode)
        self._press()
        await self._speak_until_heard()
        self.assertEqual(len(self.onsets), 1)

        self.floor.release("a")
        self.clock[0] += RELEASE_TAIL_SECS + 0.01
        await self.floor.tick(self.clock[0])
        await asyncio.sleep(1.2)  # no frames: past Pipecat's idle timeout

        self._press()
        await self._feed([bytes(FRAME)] * 50)
        await self._speak_until_heard()
        self.assertEqual(len(self.onsets), 2, "exactly one onset for the new press")
        self.assertEqual(self.warnings, [])

    async def test_next_press_after_forced_stop_fires_onset(self):
        await self._next_press_fires_onset("ptt")

    async def test_next_press_after_forced_stop_fires_onset_baton(self):
        await self._next_press_fires_onset("baton")

    async def test_non_holder_speech_opens_no_vad(self):
        await self._room("baton")
        self.floor.press("holder")
        await self._feed(self._speech(4))
        self.assertEqual(self.onsets, [])
        self.assertEqual(self.segments, [])


if __name__ == "__main__":
    unittest.main()
