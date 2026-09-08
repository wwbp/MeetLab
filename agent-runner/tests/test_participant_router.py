"""Fanning one room's audio out to per-participant workers.

LiveKitTransport hardcodes RoomOptions(auto_subscribe=True), so a worker cannot
subscribe to a single participant: one transport holds the room and emits
UserAudioRawFrame(user_id=...) for everyone in it. The fan-out therefore happens
one hop later, and this is that hop — the replacement for the demultiplexing
currently buried inside multi_speaker_stt.py.

Two rules matter more than the routing itself, and both are pinned below.

**Audio is consumed, not forwarded.** Once a frame has been handed to a
participant's worker it must not also continue down the main pipeline, or every
sample is processed twice.

**Everything else passes through untouched.** The router sits on the live audio
path in front of the rest of the pipeline; swallowing a lifecycle frame would
stall the session, and raising would end it.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from participant_router import ParticipantAudioRouter


class FakeWorker:
    def __init__(self, name):
        self.name = name
        self.got = []
        self.ended = False

    async def queue_frame(self, frame, direction=None):
        self.got.append(frame)

    async def end(self, reason=None):
        self.ended = True


class FakePool:
    """Stands in for ParticipantWorkerPool, which has its own tests."""

    def __init__(self, refuse=()):
        self.workers = {}
        self.refuse = set(refuse)
        self.left = []

    async def handle_audio(self, sid):
        if not sid or sid in self.refuse:
            return None
        return self.workers.setdefault(sid, FakeWorker(f"listener-{sid}"))

    async def handle_leave(self, sid):
        self.left.append(sid)
        w = self.workers.pop(sid, None)
        if w:
            await w.end()


def audio(sid, n=320):
    return UserAudioRawFrame(
        audio=b"\x00" * n, sample_rate=16000, num_channels=1, user_id=sid
    )


class Harness:
    def __init__(self, pool=None):
        self.pool = pool or FakePool()
        self.router = ParticipantAudioRouter(self.pool)
        self.downstream = []
        self.router.push_frame = self._capture

    async def _capture(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.downstream.append(frame)

    async def send(self, *frames):
        for f in frames:
            await self.router.process_frame(f, FrameDirection.DOWNSTREAM)


class TestRouting(unittest.TestCase):
    def test_audio_reaches_that_participants_worker(self):
        h = Harness()
        f = audio("PA_1")
        asyncio.run(h.send(f))
        self.assertEqual(h.pool.workers["PA_1"].got, [f])

    def test_each_participant_gets_only_their_own_audio(self):
        h = Harness()
        a, b = audio("PA_1"), audio("PA_2")
        asyncio.run(h.send(a, b))
        self.assertEqual(h.pool.workers["PA_1"].got, [a])
        self.assertEqual(h.pool.workers["PA_2"].got, [b])

    def test_routed_audio_is_not_also_pushed_downstream(self):
        """Forwarding as well as routing would process every sample twice."""
        h = Harness()
        asyncio.run(h.send(audio("PA_1")))
        self.assertEqual(h.downstream, [])


class TestPassThrough(unittest.TestCase):
    def test_non_audio_frames_continue_down_the_pipeline(self):
        h = Harness()
        t = TranscriptionFrame(text="hello", user_id="PA_1", timestamp="t")
        asyncio.run(h.send(t))
        self.assertEqual(h.downstream, [t])

    def test_bot_audio_is_not_mistaken_for_participant_audio(self):
        """TTSAudioRawFrame is the bot's own voice heading out. Routing it to a
        listener worker would feed the bot its own speech."""
        h = Harness()
        f = TTSAudioRawFrame(audio=b"\x00" * 320, sample_rate=16000, num_channels=1)
        asyncio.run(h.send(f))
        self.assertEqual(h.downstream, [f])
        self.assertEqual(h.pool.workers, {})

    def test_upstream_audio_is_passed_through_not_routed(self):
        """Only downstream frames are participant input. Upstream audio belongs
        to something else and must keep travelling."""
        h = Harness()
        f = audio("PA_1")

        async def run():
            await h.router.process_frame(f, FrameDirection.UPSTREAM)

        asyncio.run(run())
        self.assertEqual(h.pool.workers, {}, "upstream audio is not participant input")
        self.assertEqual(h.downstream, [f], "and it must not be swallowed")


class TestRefusalAndUnknowns(unittest.TestCase):
    def test_a_refused_participants_audio_is_dropped(self):
        h = Harness(FakePool(refuse={"PA_9"}))
        asyncio.run(h.send(audio("PA_9")))
        self.assertEqual(h.downstream, [], "refused audio must not leak downstream")

    def test_audio_without_identity_is_dropped(self):
        h = Harness()
        asyncio.run(h.send(audio("")))
        self.assertEqual(h.pool.workers, {})
        self.assertEqual(h.downstream, [])


class TestDeparture(unittest.TestCase):
    def test_leaving_is_delegated_to_the_pool(self):
        h = Harness()

        async def run():
            await h.send(audio("PA_1"))
            await h.router.participant_left("PA_1")

        asyncio.run(run())
        self.assertEqual(h.pool.left, ["PA_1"])


class TestTheAudioPathSurvivesFailure(unittest.TestCase):
    def test_a_worker_that_raises_does_not_kill_the_session(self):
        """One participant's broken worker must cost that participant, not the
        meeting."""

        class Exploding(FakeWorker):
            async def queue_frame(self, frame, direction=None):
                raise RuntimeError("worker blew up")

        pool = FakePool()
        pool.workers["PA_1"] = Exploding("listener-PA_1")
        h = Harness(pool)
        asyncio.run(h.send(audio("PA_1")))  # must not raise

    def test_a_pool_that_raises_does_not_kill_the_session(self):
        class ExplodingPool(FakePool):
            async def handle_audio(self, sid):
                raise RuntimeError("pool blew up")

        h = Harness(ExplodingPool())
        asyncio.run(h.send(audio("PA_1")))  # must not raise


if __name__ == "__main__":
    unittest.main()
