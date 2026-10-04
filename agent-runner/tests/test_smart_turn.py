"""Smart turn per participant (smart_turn.py): each person's own Smart Turn v3 decides whether
they have finished, and their transcript only ends the turn when they have. A mid-sentence
pause used to end the turn after a fixed silence: ~19% of sentences were split (2026-10-04)."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    InputAudioRawFrame, StartFrame, TranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402

from smart_turn import SmartTurnListener, TurnVerdict, closing_frames, turn_frames  # noqa: E402


def kinds(frames):
    return [type(f).__name__ for f in frames]


def transcript(text="hello"):
    return TranscriptionFrame(text=text, user_id="ana", timestamp="t", finalized=True)


class TestTurnFrames(unittest.TestCase):
    def test_a_finished_speaker_ends_the_turn_as_before(self):
        self.assertEqual(kinds(turn_frames(transcript(), complete=True)),
                         ["VADUserStartedSpeakingFrame", "VADUserStoppedSpeakingFrame", "UserStartedSpeakingFrame",
                          "TranscriptionFrame", "UserStoppedSpeakingFrame"])

    def test_an_unfinished_speaker_keeps_the_turn_open(self):
        frames = turn_frames(transcript(), complete=False)
        self.assertNotIn("UserStoppedSpeakingFrame", kinds(frames))
        self.assertNotIn("VADUserStoppedSpeakingFrame", kinds(frames))
        self.assertIn("TranscriptionFrame", kinds(frames))

    def test_closing_an_open_turn(self):
        self.assertEqual(kinds(closing_frames()), ["VADUserStoppedSpeakingFrame", "UserStoppedSpeakingFrame"])


class FakeAnalyzer:
    def __init__(self, verdicts):
        self.verdicts, self.audio, self.cleared, self.rate = list(verdicts), [], 0, None

    def set_sample_rate(self, rate):
        self.rate = rate

    def append_audio(self, buffer, is_speech):
        self.audio.append(is_speech)
        return EndOfTurnState.INCOMPLETE

    async def analyze_end_of_turn(self):
        return self.verdicts.pop(0), None

    def clear(self):
        self.cleared += 1


class TestListener(unittest.IsolatedAsyncioTestCase):
    def audio(self):
        return InputAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1)

    async def test_it_hears_only_this_person_and_judges_at_each_pause(self):
        analyzer, verdict = FakeAnalyzer([EndOfTurnState.INCOMPLETE, EndOfTurnState.COMPLETE]), TurnVerdict()
        listener = SmartTurnListener(verdict, analyzer)
        for f in [StartFrame(audio_in_sample_rate=16000), self.audio(), VADUserStartedSpeakingFrame(), self.audio(),
                  VADUserStoppedSpeakingFrame()]:
            await listener.on_frame(f)
        self.assertEqual(analyzer.rate, 16000)
        self.assertEqual(analyzer.audio, [False, True])  # the speech flag follows this person's VAD
        self.assertFalse(verdict.complete)  # "…follow through, but": not done
        for f in [VADUserStartedSpeakingFrame(), self.audio(), VADUserStoppedSpeakingFrame()]:
            await listener.on_frame(f)
        self.assertTrue(verdict.complete)
        self.assertEqual(analyzer.cleared, 1)  # a finished turn starts the next fresh


class TestOpenTurnBackstop(unittest.IsolatedAsyncioTestCase):
    async def test_an_open_turn_closes_if_the_speaker_never_continues(self):
        from smart_turn import TurnCloser
        queue = asyncio.Queue()
        closer = TurnCloser(queue, wait_secs=0.05)
        closer.open()
        await asyncio.sleep(0.1)
        self.assertEqual(kinds([queue.get_nowait(), queue.get_nowait()]), kinds(closing_frames()))

    async def test_speaking_again_in_time_keeps_it_open(self):
        from smart_turn import TurnCloser
        queue = asyncio.Queue()
        closer = TurnCloser(queue, wait_secs=0.05)
        closer.open()
        closer.cancel()
        await asyncio.sleep(0.1)
        self.assertTrue(queue.empty())


if __name__ == "__main__":
    unittest.main()


class TestCollector(unittest.IsolatedAsyncioTestCase):
    """A person's chain collector reads their verdict when their transcript arrives."""

    async def collect(self, verdict, wait=0.05):
        from multi_speaker_stt import _FrameCollector
        queue = asyncio.Queue()
        collector = _FrameCollector(queue, needs_vad_wrap=True, sid="ana", verdict=verdict, open_turn_secs=wait)
        await collector.queue_frame(transcript())
        return queue

    async def drain(self, queue):
        out = []
        while not queue.empty():
            out.append(queue.get_nowait())
        return out

    async def test_without_smart_turn_nothing_changes(self):
        from multi_speaker_stt import _FrameCollector
        queue = asyncio.Queue()
        await _FrameCollector(queue, needs_vad_wrap=True, sid="ana").queue_frame(transcript())
        self.assertEqual(kinds(await self.drain(queue)), kinds(turn_frames(transcript(), complete=True)))

    async def test_finished_ends_the_turn(self):
        queue = await self.collect(TurnVerdict(complete=True))
        self.assertEqual(kinds(await self.drain(queue))[-1], "UserStoppedSpeakingFrame")

    async def test_the_rooms_wait_decides_how_long_an_unfinished_turn_stays_open(self):
        from multi_speaker_stt import _FrameCollector
        queue = asyncio.Queue()
        await _FrameCollector(queue, needs_vad_wrap=True, sid="ana", verdict=TurnVerdict(complete=False, wait_secs=0.05)).queue_frame(transcript())
        await self.drain(queue)
        await asyncio.sleep(0.1)
        self.assertEqual(kinds(await self.drain(queue)), kinds(closing_frames()))

    async def test_unfinished_stays_open_then_closes_if_nothing_follows(self):
        queue = await self.collect(TurnVerdict(complete=False))
        self.assertNotIn("UserStoppedSpeakingFrame", kinds(await self.drain(queue)))
        await asyncio.sleep(0.1)
        self.assertEqual(kinds(await self.drain(queue)), kinds(closing_frames()))


class TestEffectiveRule(unittest.TestCase):
    def test_smart_turn_needs_a_segmenting_recogniser(self):
        from smart_turn import effective_turn_detection
        self.assertEqual(effective_turn_detection("smart_turn", "parakeet-tdt-0.6b-v2"), "smart_turn")
        self.assertEqual(effective_turn_detection("smart_turn", "whisper-base"), "smart_turn")
        # Deepgram and OpenAI decide their own segments: the room falls back to silence, said in the log.
        self.assertEqual(effective_turn_detection("smart_turn", "nova-3-general"), "silence")
        self.assertEqual(effective_turn_detection("smart_turn", "gpt-4o-transcribe"), "silence")
        self.assertEqual(effective_turn_detection("silence", "parakeet-tdt-0.6b-v2"), "silence")
