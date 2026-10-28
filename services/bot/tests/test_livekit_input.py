"""Pipecat's LiveKit input keeps overlapping speakers apart (diagnosis F11).

Pipecat 1.4 through 1.12 resampled every participant through one shared stream resampler, so
each speaker's audio carried the other's voice; our `livekit_input.py` subclass fixed it until
Pipecat 1.13 shipped the same fix (pipecat-ai/pipecat#6033). This pins the upstream behaviour.
"""
import unittest
from unittest import mock

import numpy as np
from livekit import rtc

from pipecat.transports.livekit.transport import LiveKitInputTransport, LiveKitParams

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
    async def test_each_participant_keeps_only_their_own_audio(self):
        frames = [(_frame(hz, i), who) for i in range(100) for who, hz in TONES.items()]

        async def interleaved():  # two people talking at once, 1 s each
            for frame in frames:
                yield frame

        client = mock.MagicMock()
        client.get_next_audio_frame = interleaved
        transport = LiveKitInputTransport(mock.MagicMock(), client, LiveKitParams())
        transport._sample_rate = RATE_OUT
        heard = {who: b"" for who in TONES}

        async def collect(frame):
            heard[frame.user_id] += frame.audio

        transport.push_audio_frame = collect
        await transport._audio_in_task_handler()
        for who, other in (("alice", "bob"), ("bob", "alice")):
            pcm = heard[who][len(heard[who]) // 4:]  # past the resampler's start-up
            self.assertLess(_magnitude(pcm, TONES[other]) / _magnitude(pcm, TONES[who]), 0.01, who)


if __name__ == "__main__":
    unittest.main()
