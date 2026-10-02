"""livekit_input.py: one resampler per participant (diagnosis F11; upstream pipecat#6033)."""
import unittest
from unittest import mock

import numpy as np
from livekit import rtc

import livekit_input
from pipecat.transports.livekit.transport import LiveKitParams

RATE_IN, RATE_OUT, TONES = 48000, 16000, {"alice": 440.0, "bob": 1000.0}


def _frame(hz: float, index: int) -> rtc.AudioFrameEvent:
    n = RATE_IN // 100  # 10 ms
    t = (np.arange(n) + index * n) / RATE_IN
    return rtc.AudioFrameEvent(frame=rtc.AudioFrame((8000 * np.sin(2 * np.pi * hz * t)).astype(np.int16).tobytes(), RATE_IN, 1, n))


def _magnitude(pcm: bytes, hz: float) -> float:
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(spectrum[int(round(hz * len(samples) / RATE_OUT))])


class PerParticipantInputTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self):
        frames = [(_frame(hz, i), who) for i in range(100) for who, hz in TONES.items()]

        async def interleaved():  # two people talking at once, 1 s each
            for frame in frames:
                yield frame

        client = mock.MagicMock()
        client.get_next_audio_frame = interleaved
        transport = livekit_input._PerParticipantInput(mock.MagicMock(), client, LiveKitParams())
        transport._sample_rate = RATE_OUT
        heard = {who: b"" for who in TONES}

        async def collect(frame):
            heard[frame.user_id] += frame.audio

        transport.push_audio_frame = collect
        await transport._audio_in_task_handler()
        return heard

    async def test_each_participant_keeps_only_their_own_audio(self):
        heard = await self._run()
        for who, other in (("alice", "bob"), ("bob", "alice")):
            pcm = heard[who][len(heard[who]) // 4:]  # past the resampler's start-up
            self.assertLess(_magnitude(pcm, TONES[other]) / _magnitude(pcm, TONES[who]), 0.01, who)

    async def test_each_participant_gets_one_resampler_not_one_per_frame(self):
        real = livekit_input.create_stream_resampler
        with mock.patch.object(livekit_input, "create_stream_resampler", side_effect=real) as made:
            await self._run()
        self.assertEqual(made.call_count, len(TONES))


if __name__ == "__main__":
    unittest.main()
