"""A long talker through real Silero and VADProcessor, on recorded speech: the check that the
still-speaking signal really arrives often enough. Fed about 4x real time on the audio's clock;
the VAD's 0.2 s signal period is wall time, so that spaces signals about 0.8 s apart, inside 3 s."""
import asyncio
import glob
import pathlib
import types
import unittest

import numpy as np
from pipecat.frames.frames import StartFrame, UserAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection

from meetlab_bot.bot import _build_vad_processor
from meetlab_bot.interruption import InterruptionTracker
from meetlab_bot.multi_speaker_stt import MultiSpeakerSTT
from tests.test_multi_speaker_stt import _make_setup

CLIPS = pathlib.Path(__file__).parents[3] / "tools/loadtest/fixtures/conversations"
FRAME = 640  # 20 ms at 16 kHz


def speech() -> bytes:
    """About 24 s of recorded speech with natural pauses, then 2 s of silence."""
    pcm = np.concatenate([
        np.frombuffer(open(f, "rb").read()[44:], dtype=np.int16)
        for f in sorted(glob.glob(str(CLIPS / "*.wav")))[:8]
    ])
    return np.interp(np.arange(0, len(pcm), 1.5), np.arange(len(pcm)), pcm).astype(np.int16).tobytes() + bytes(64000)


class TestLongSpeechRealVad(unittest.IsolatedAsyncioTestCase):
    async def test_idle_stays_false_for_all_of_every_utterance(self):
        tracker, clock, wrong, longest = InterruptionTracker(), [0.0], [], [0.0]
        started = [None]

        async def onset(sid="a"):
            started[0] = started[0] if started[0] is not None else clock[0]
            tracker.user_onset(clock[0], sid)

        async def offset(sid="a"):
            if started[0] is not None:
                longest[0] = max(longest[0], clock[0] - started[0])
            started[0] = None
            tracker.user_offset(clock[0], sid)

        config = types.SimpleNamespace(stt_endpointing_ms=750)
        multi = MultiSpeakerSTT(
            lambda sid=None: _build_vad_processor(config, (lambda: onset(sid), lambda: offset(sid))),
            on_speech_onset=onset, on_speech_offset=offset,
            on_still_speaking=lambda sid: tracker.user_speaking(clock[0], sid),
        )
        setup = _make_setup()
        setup.audio_in_sample_rate = 16000
        await multi.setup(setup)
        await multi.process_frame(
            StartFrame(audio_in_sample_rate=16000, audio_out_sample_rate=16000), FrameDirection.DOWNSTREAM)
        audio = speech()
        for i in range(0, len(audio), FRAME):
            clock[0] = i / 32000
            await multi.process_frame(
                UserAudioRawFrame(audio=audio[i:i + FRAME], sample_rate=16000, num_channels=1, user_id="a"),
                FrameDirection.DOWNSTREAM)
            if started[0] is not None and tracker.idle(clock[0]):
                wrong.append(round(clock[0] - started[0], 2))
            await asyncio.sleep(0.005)  # 20 ms of audio every 5 ms: about 4x real time
        self.assertGreater(longest[0], 3.2, "the clip must be long enough to have shown the bug")
        self.assertEqual(wrong, [], "idle() read someone talking as silent")
        self.assertTrue(tracker.idle(clock[0]), "silence at the end clears the speaker")


if __name__ == "__main__":
    unittest.main()
