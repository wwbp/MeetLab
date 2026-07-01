"""Unit tests for tests/audio_scenarios.py — the procedural audio generators
used by the meeting simulation harness (simulate_meeting.py).

Pure/offline: no network, no LiveKit, no container services. Run with:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_audio_scenarios -v
"""
import io
import math
import os
import sys
import unittest
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from audio_scenarios import (
    SAMPLE_RATE,
    mix_at_snr,
    pcm16_to_float,
    float_to_pcm16,
    pink_noise,
    rms,
    scale_to_snr,
    to_wav_bytes,
    tone,
    white_noise,
)


def _dominant_freq(x: np.ndarray, sr: int) -> float:
    """Frequency (Hz) of the largest FFT magnitude bin (ignoring DC)."""
    spectrum = np.abs(np.fft.rfft(x))
    spectrum[0] = 0.0  # ignore DC offset
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    return float(freqs[int(np.argmax(spectrum))])


class TestTone(unittest.TestCase):
    def test_tone_has_requested_length(self):
        x = tone(1000.0, SAMPLE_RATE)  # 1 second
        self.assertEqual(len(x), SAMPLE_RATE)

    def test_tone_dominant_frequency_matches(self):
        x = tone(1000.0, SAMPLE_RATE)
        self.assertAlmostEqual(_dominant_freq(x, SAMPLE_RATE), 1000.0, delta=5.0)

    def test_infrasonic_tone_frequency(self):
        x = tone(12.0, SAMPLE_RATE)
        self.assertAlmostEqual(_dominant_freq(x, SAMPLE_RATE), 12.0, delta=2.0)

    def test_near_nyquist_tone_frequency(self):
        x = tone(11000.0, SAMPLE_RATE)
        self.assertAlmostEqual(_dominant_freq(x, SAMPLE_RATE), 11000.0, delta=5.0)

    def test_tone_is_bounded(self):
        x = tone(440.0, SAMPLE_RATE, amplitude=0.5)
        self.assertLessEqual(float(np.max(np.abs(x))), 0.5 + 1e-6)


class TestNoise(unittest.TestCase):
    def test_white_noise_length_and_range(self):
        x = white_noise(SAMPLE_RATE, seed=1)
        self.assertEqual(len(x), SAMPLE_RATE)
        self.assertLessEqual(float(np.max(np.abs(x))), 1.0 + 1e-6)

    def test_white_noise_is_deterministic_with_seed(self):
        a = white_noise(1000, seed=7)
        b = white_noise(1000, seed=7)
        self.assertTrue(np.array_equal(a, b))

    def test_pink_noise_is_finite_and_nonzero(self):
        x = pink_noise(SAMPLE_RATE, seed=3)
        self.assertEqual(len(x), SAMPLE_RATE)
        self.assertTrue(np.all(np.isfinite(x)))
        self.assertGreater(rms(x), 0.0)

    def test_pink_noise_has_more_low_freq_energy_than_white(self):
        n = SAMPLE_RATE
        pink = pink_noise(n, seed=5)
        white = white_noise(n, seed=5)

        def low_high_ratio(sig):
            spec = np.abs(np.fft.rfft(sig))
            freqs = np.fft.rfftfreq(n, 1.0 / SAMPLE_RATE)
            low = spec[(freqs > 20) & (freqs < 500)].sum()
            high = spec[(freqs > 4000) & (freqs < 8000)].sum()
            return low / max(high, 1e-9)

        self.assertGreater(low_high_ratio(pink), low_high_ratio(white))


class TestSnrMixing(unittest.TestCase):
    def test_scale_to_snr_produces_exact_ratio(self):
        speech = tone(300.0, SAMPLE_RATE, amplitude=0.5)
        noise = white_noise(SAMPLE_RATE, seed=2)
        for snr_db in (-6.0, 0.0, 6.0, 20.0):
            scaled = scale_to_snr(speech, noise, snr_db)
            measured = 20.0 * math.log10(rms(speech) / rms(scaled))
            self.assertAlmostEqual(measured, snr_db, delta=0.5)

    def test_mix_at_snr_length_is_max_of_inputs(self):
        speech = tone(300.0, SAMPLE_RATE)
        noise = white_noise(SAMPLE_RATE // 2, seed=2)
        mixed = mix_at_snr(speech, noise, 10.0)
        self.assertEqual(len(mixed), SAMPLE_RATE)

    def test_mix_at_snr_is_bounded(self):
        speech = tone(300.0, SAMPLE_RATE, amplitude=0.9)
        noise = white_noise(SAMPLE_RATE, seed=2)
        mixed = mix_at_snr(speech, noise, -10.0)  # very noisy
        self.assertLessEqual(float(np.max(np.abs(mixed))), 1.0 + 1e-6)


class TestPcmRoundTrip(unittest.TestCase):
    def test_float_pcm_round_trip_is_near_identity(self):
        x = tone(440.0, 4800, amplitude=0.8)
        restored = pcm16_to_float(float_to_pcm16(x))
        self.assertLess(float(np.max(np.abs(restored - x))), 1e-3)

    def test_float_to_pcm16_is_int16_bytes(self):
        x = tone(440.0, 100)
        raw = float_to_pcm16(x)
        self.assertIsInstance(raw, (bytes, bytearray))
        self.assertEqual(len(raw), 100 * 2)  # 16-bit mono


class TestWavExport(unittest.TestCase):
    def test_to_wav_bytes_is_readable_mono_16bit(self):
        x = tone(440.0, SAMPLE_RATE)
        data = to_wav_bytes(x, SAMPLE_RATE)
        with wave.open(io.BytesIO(data), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), SAMPLE_RATE)
            self.assertEqual(wf.getnframes(), SAMPLE_RATE)


if __name__ == "__main__":
    unittest.main()
