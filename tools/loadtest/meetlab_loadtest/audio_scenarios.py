"""Procedural audio generators for the meeting simulation harness.

Pure NumPy — no LiveKit, no network. These build the test signals that
simulate_meeting.py streams into a LiveKit room: speech mixed with background
noise, continuous noise beds, and pure tones (incl. infrasonic / near-Nyquist
probes for the "inaudible noise" hypothesis).

Convention: signals are float32 in roughly [-1, 1], mono, at SAMPLE_RATE.
SAMPLE_RATE matches the checked-in TTS fixtures (24 kHz) so mixing is sample-aligned.
"""
from __future__ import annotations

import io
import wave

import numpy as np

SAMPLE_RATE = 24_000


def rms(x: np.ndarray) -> float:
    """Root-mean-square level of a signal (0.0 for empty/silent input)."""
    if len(x) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


def tone(freq: float, n_samples: int, sr: int = SAMPLE_RATE, amplitude: float = 0.3) -> np.ndarray:
    """A pure sine wave of `freq` Hz, `n_samples` long, peak `amplitude`."""
    t = np.arange(n_samples, dtype=np.float64) / sr
    return (amplitude * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


def white_noise(n_samples: int, seed: int = 0, amplitude: float = 0.3) -> np.ndarray:
    """Uniform white noise in [-amplitude, amplitude]; deterministic per seed."""
    rng = np.random.default_rng(seed)
    return (rng.uniform(-amplitude, amplitude, n_samples)).astype(np.float32)


def pink_noise(n_samples: int, seed: int = 0, amplitude: float = 0.3) -> np.ndarray:
    """Pink (1/f) noise: white noise shaped by a 1/sqrt(f) spectral envelope.

    Approximates steady-state ambient noise (HVAC, fans) better than white noise,
    which is the realistic "continuous background" case for the held-open-VAD test.
    """
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n_samples)
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n_samples, 1.0)
    envelope = np.ones_like(freqs)
    envelope[1:] = 1.0 / np.sqrt(freqs[1:] * n_samples)
    shaped = np.fft.irfft(spectrum * envelope, n=n_samples)
    peak = np.max(np.abs(shaped)) or 1.0
    return (amplitude * shaped / peak).astype(np.float32)


def scale_to_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """Return `noise` rescaled so that 20·log10(rms(speech)/rms(scaled)) == snr_db."""
    speech_rms = rms(speech)
    noise_rms = rms(noise)
    if noise_rms == 0.0 or speech_rms == 0.0:
        return noise.astype(np.float32)
    desired_noise_rms = speech_rms / (10.0 ** (snr_db / 20.0))
    return (noise * (desired_noise_rms / noise_rms)).astype(np.float32)


def _fit(sig: np.ndarray, n: int) -> np.ndarray:
    """Tile/trim `sig` to exactly `n` samples."""
    if len(sig) == 0:
        return np.zeros(n, dtype=np.float32)
    if len(sig) >= n:
        return sig[:n].astype(np.float32)
    reps = int(np.ceil(n / len(sig)))
    return np.tile(sig, reps)[:n].astype(np.float32)


def mix_at_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """Mix `noise` under `speech` at the target SNR; result is bounded to [-1, 1].

    The noise is tiled/trimmed to the speech length first, so a short noise loop
    can underlay a longer utterance. If the sum clips, the whole mix is scaled
    down uniformly (preserving the SNR between the two components).
    """
    n = max(len(speech), len(noise))
    speech = _fit(speech, n)
    scaled_noise = _fit(scale_to_snr(speech, noise, snr_db), n)
    mixed = speech + scaled_noise
    peak = float(np.max(np.abs(mixed))) if len(mixed) else 0.0
    if peak > 1.0:
        mixed = mixed / peak
    return mixed.astype(np.float32)


def float_to_pcm16(x: np.ndarray) -> bytes:
    """Clip to [-1, 1] and convert to little-endian signed 16-bit PCM bytes."""
    clipped = np.clip(x, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def pcm16_to_float(raw: bytes) -> np.ndarray:
    """Inverse of float_to_pcm16: 16-bit PCM bytes → float32 in [-1, 1]."""
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0


def to_wav_bytes(x: np.ndarray, sr: int = SAMPLE_RATE) -> bytes:
    """Serialize a float signal to an in-memory mono 16-bit WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(float_to_pcm16(x))
    return buf.getvalue()


def load_wav_float(path: str) -> tuple[np.ndarray, int]:
    """Load a mono 16-bit WAV file as (float32 signal, sample_rate)."""
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    return pcm16_to_float(raw), sr
