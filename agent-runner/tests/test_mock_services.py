"""Mock LLM/TTS services emit the right frames so the pipeline runs without paid calls."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipecat.frames.frames import TTSAudioRawFrame
from mock_services import MockTTSService


class TestMockTTS(unittest.IsolatedAsyncioTestCase):
    async def test_run_tts_yields_silence_audio_frames(self):
        svc = MockTTSService()
        frames = [f async for f in svc.run_tts("hello there", context_id="c1")]
        self.assertTrue(frames, "mock TTS produced no audio frames")
        self.assertTrue(all(isinstance(f, TTSAudioRawFrame) for f in frames))
        # All-zero PCM (silence), correct context id propagated.
        self.assertTrue(all(set(f.audio) == {0} for f in frames))
        self.assertEqual(frames[0].context_id, "c1")

    async def test_longer_text_speaks_longer(self):
        svc = MockTTSService()
        short = [f async for f in svc.run_tts("hi", context_id="c")]
        long = [f async for f in svc.run_tts("a much longer sentence " * 5, context_id="c")]
        self.assertGreater(len(long), len(short))


if __name__ == "__main__":
    unittest.main()
