"""Unit tests for NemotronHTTPSTTService — the Parakeet NIM client.

The service POSTs WAV segment bytes (as produced by SegmentedSTTService) to the Parakeet
NIM's OpenAI-compatible /v1/audio/transcriptions endpoint and yields the transcript. These
tests run a real in-process HTTP server so the multipart request path is exercised without
the NIM.
"""
import asyncio
import io
import json
import os
import sys
import threading
import unittest
import unittest.mock
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://localhost:7880")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from nemotron_stt import NemotronHTTPSTTService


def _wav_bytes(seconds: float = 0.5, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setsampwidth(2)
    w.setnchannels(1)
    w.setframerate(rate)
    w.writeframes(b"\x00\x00" * int(rate * seconds))
    w.close()
    return buf.getvalue()


class _StubHandler(BaseHTTPRequestHandler):
    """Mimics the Parakeet NIM: POST /v1/audio/transcriptions → {"text": ...}."""

    received: list = []  # class-level capture

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _StubHandler.received.append({"path": self.path, "length": len(body), "body": body})
        if self.path != "/v1/audio/transcriptions":
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps({"text": "what is the capital of france", "timestamps": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence test output
        pass


class TestNemotronHTTPSTTService(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _StubHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _StubHandler.received.clear()

    async def test_transcribe_posts_to_v1_endpoint_with_model_and_language(self):
        """Targets /v1/audio/transcriptions with the NIM's served model id + a language
        field (both required by the NIM), no should_chunk, and parses {"text"}."""
        svc = NemotronHTTPSTTService(
            base_url=f"http://127.0.0.1:{self.port}", model="parakeet-tdt-0.6b-v2")
        text = await svc._transcribe(_wav_bytes())
        self.assertEqual(text, "what is the capital of france")
        self.assertEqual(len(_StubHandler.received), 1)
        rec = _StubHandler.received[0]
        self.assertEqual(rec["path"], "/v1/audio/transcriptions")
        self.assertIn(b'name="model"', rec["body"])
        self.assertIn(b"parakeet-tdt-0.6b-multi-asr-offline", rec["body"])  # NIM's served id, not stt_model
        self.assertIn(b'name="language"', rec["body"])
        self.assertNotIn(b'name="should_chunk"', rec["body"])
        # WAV body actually went over the wire (multipart adds overhead on top)
        self.assertGreater(rec["length"], 16000)

    async def test_served_model_and_language_env_overridable(self):
        with unittest.mock.patch.dict(
                os.environ, {"NEMOTRON_STT_MODEL": "custom-model", "NEMOTRON_STT_LANGUAGE": "en-GB"}):
            svc = NemotronHTTPSTTService(base_url=f"http://127.0.0.1:{self.port}", model="x")
        await svc._transcribe(_wav_bytes())
        body = _StubHandler.received[0]["body"]
        self.assertIn(b"custom-model", body)
        self.assertIn(b"en-GB", body)

    async def test_transcribe_raises_on_http_error(self):
        svc = NemotronHTTPSTTService(base_url=f"http://127.0.0.1:{self.port}/wrong-base", model="parakeet-tdt-0.6b-v2")
        with self.assertRaises(Exception):
            await svc._transcribe(_wav_bytes())

    async def test_base_url_trailing_slash_normalized(self):
        svc = NemotronHTTPSTTService(base_url=f"http://127.0.0.1:{self.port}/", model="x")
        self.assertEqual(svc.base_url, f"http://127.0.0.1:{self.port}")


if __name__ == "__main__":
    unittest.main()
