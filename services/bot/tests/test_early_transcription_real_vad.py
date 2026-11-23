"""Early transcription through real Silero and the real Parakeet chain, on recorded speech:
the check that an early answer is only reused when it heard everything the person said.
Fed about 4x real time, with the early-request delay shortened by the same factor."""
import asyncio
import os
import types
import unittest
import unittest.mock

from pipecat.frames.frames import StartFrame, UserAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection

from meetlab_bot import bot
from meetlab_bot.multi_speaker_stt import MultiSpeakerSTT
from meetlab_bot.nemotron_stt import NemotronHTTPSTTService
from tests.test_long_speech_real_vad import FRAME, speech
from tests.test_multi_speaker_stt import _make_setup

WAIT_MS = 450


class TestEarlyTranscriptionRealVad(unittest.IsolatedAsyncioTestCase):
    async def test_reused_answers_missed_nothing_but_the_silence_wait(self):
        segments = []  # (bytes in the closed segment, bytes the reused answer heard or None)

        async def heard(self, wav):
            return str(len(wav))

        early_text = NemotronHTTPSTTService._early_text

        async def recording(self, wav):
            text = await early_text(self, wav)
            segments.append((len(wav), text and int(text)))
            return text

        def chain(sid=None):
            parts = bot._build_parakeet_chain(types.SimpleNamespace(
                stt_endpointing_ms=WAIT_MS, stt_model="parakeet-tdt-0.6b-v2", turn_detection="silence"))
            parts[1]._speculate_after_secs = 0.15 / 4
            return parts

        with unittest.mock.patch.dict(os.environ, {"NEMOTRON_STT_URL": "http://unused"}), \
                unittest.mock.patch.object(NemotronHTTPSTTService, "_transcribe", heard), \
                unittest.mock.patch.object(NemotronHTTPSTTService, "_early_text", recording):
            multi = MultiSpeakerSTT(chain)
            setup = _make_setup()
            setup.audio_in_sample_rate = 16000
            await multi.setup(setup)
            await multi.process_frame(
                StartFrame(audio_in_sample_rate=16000, audio_out_sample_rate=16000), FrameDirection.DOWNSTREAM)
            audio = speech()
            for i in range(0, len(audio), FRAME):
                await multi.process_frame(
                    UserAudioRawFrame(audio=audio[i:i + FRAME], sample_rate=16000, num_channels=1, user_id="a"),
                    FrameDirection.DOWNSTREAM)
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.2)

        reused = [(whole, early) for whole, early in segments if early is not None]
        self.assertGreater(len(segments), 2, "the clip must have several segments")
        self.assertGreaterEqual(len(reused), len(segments) - 1, f"early answers went unused: {segments}")
        for whole, early in reused:
            missed_ms = (whole - early) / 32
            self.assertTrue(0 <= missed_ms <= WAIT_MS + 100, f"an early answer missed {missed_ms:.0f} ms: {segments}")


if __name__ == "__main__":
    unittest.main()
