"""The CPU speech-to-text server: Parakeet (int8, ONNX) behind the endpoint the bots already
call for the GPU NIM (agent-runner/nemotron_stt.py), so a profile can point bots at either.
    make test-stt-cpu
"""
import io
import pathlib
import unittest

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

import app

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "agent-runner/tests/fixtures/conversations"


def clip(text: str) -> bytes:
    """A recorded fixture whose words we know: named by its text (generate_conversation_audio.fixture_name)."""
    import hashlib
    return (FIXTURES / f"{hashlib.sha256(text.encode()).hexdigest()[:16]}.wav").read_bytes()


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app.app).__enter__()  # runs startup: the model loads once

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def post(self, wav: bytes, **fields):
        return self.client.post("/v1/audio/transcriptions", files={"file": ("a.wav", wav, "audio/wav")},
                                data={"model": "parakeet-tdt-0.6b-multi-asr-offline", "language": "multi", **fields})

    def test_health_says_ready_only_once_the_model_is_loaded(self):
        self.assertEqual(self.client.get("/health").json(), {"ready": True})

    def test_a_spoken_question_comes_back_as_its_words(self):
        r = self.post(clip("How many days would you give Kyoto?"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["text"].lower().strip(" ?."), "how many days would you give kyoto")

    def test_the_bots_fields_are_accepted_as_the_nim_takes_them(self):
        self.assertEqual(self.post(clip("Thanks, that helps.")).status_code, 200)

    def test_silence_is_empty_text_not_an_error(self):
        buf = io.BytesIO()
        sf.write(buf, np.zeros(16000, dtype="float32"), 16000, format="WAV")
        r = self.post(buf.getvalue())
        self.assertEqual((r.status_code, r.json()["text"].strip()), (200, ""))

    def test_any_sample_rate_is_resampled(self):
        a, sr = sf.read(io.BytesIO(clip("Thanks, that helps.")), dtype="float32")
        buf = io.BytesIO()
        sf.write(buf, np.interp(np.arange(0, len(a), sr / 24000), np.arange(len(a)), a).astype("float32"), 24000, format="WAV")
        self.assertIn("thanks", self.post(buf.getvalue()).json()["text"].lower())

    def test_not_audio_is_a_400(self):
        self.assertEqual(self.post(b"not a wav").status_code, 400)


if __name__ == "__main__":
    unittest.main()
