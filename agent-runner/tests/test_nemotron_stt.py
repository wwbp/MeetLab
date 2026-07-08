"""Unit tests for NemotronHTTPSTTService — the parakeet sidecar client.

The service POSTs WAV segment bytes (as produced by SegmentedSTTService) to the
stt-nemotron sidecar's /transcribe endpoint and yields the transcript. These tests
run a real in-process HTTP server so the multipart request path is exercised
without the sidecar container.
"""
import asyncio
import io
import json
import os
import sys
import threading
import unittest
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
    """Mimics the parakeet sidecar: POST /transcribe → {"text": ...}."""

    received: list = []  # class-level capture

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _StubHandler.received.append({"path": self.path, "length": len(body), "body": body})
        # shadowfita → /transcribe ; NIM/OpenAI-compatible → /v1/audio/transcriptions
        if self.path not in ("/transcribe", "/v1/audio/transcriptions"):
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

    async def test_transcribe_posts_wav_and_returns_text(self):
        svc = NemotronHTTPSTTService(base_url=f"http://127.0.0.1:{self.port}", model="parakeet-tdt-0.6b-v2")
        text = await svc._transcribe(_wav_bytes())
        self.assertEqual(text, "what is the capital of france")
        self.assertEqual(len(_StubHandler.received), 1)
        self.assertEqual(_StubHandler.received[0]["path"], "/transcribe")
        # WAV body actually went over the wire (multipart adds overhead on top)
        self.assertGreater(_StubHandler.received[0]["length"], 16000)

    async def test_transcribe_disables_server_chunking(self):
        """Segments are already VAD-cut; server-side chunking is redundant AND
        upstream's should_chunk=True path crashes on tuple unpacking (issue #16)."""
        svc = NemotronHTTPSTTService(base_url=f"http://127.0.0.1:{self.port}", model="parakeet-tdt-0.6b-v2")
        await svc._transcribe(_wav_bytes())
        body = _StubHandler.received[0]["body"]
        self.assertIn(b'name="should_chunk"', body)
        self.assertIn(b"false", body)

    async def test_transcribe_raises_on_http_error(self):
        svc = NemotronHTTPSTTService(base_url=f"http://127.0.0.1:{self.port}/wrong-base", model="parakeet-tdt-0.6b-v2")
        with self.assertRaises(Exception):
            await svc._transcribe(_wav_bytes())

    async def test_base_url_trailing_slash_normalized(self):
        svc = NemotronHTTPSTTService(base_url=f"http://127.0.0.1:{self.port}/", model="x")
        self.assertEqual(svc.base_url, f"http://127.0.0.1:{self.port}")

    async def test_openai_api_posts_to_v1_endpoint_with_model(self):
        """api='openai' targets the NIM/OpenAI-compatible /v1/audio/transcriptions with a
        model field (no should_chunk), and parses the same {"text": ...} response."""
        svc = NemotronHTTPSTTService(
            base_url=f"http://127.0.0.1:{self.port}", model="parakeet-tdt-0.6b-v2", api="openai")
        text = await svc._transcribe(_wav_bytes())
        self.assertEqual(text, "what is the capital of france")
        rec = _StubHandler.received[0]
        self.assertEqual(rec["path"], "/v1/audio/transcriptions")
        self.assertIn(b'name="model"', rec["body"])
        self.assertIn(b"parakeet-tdt-0.6b-v2", rec["body"])
        self.assertNotIn(b'name="should_chunk"', rec["body"])

    async def test_default_api_is_shadowfita(self):
        svc = NemotronHTTPSTTService(base_url=f"http://127.0.0.1:{self.port}", model="x")
        await svc._transcribe(_wav_bytes())
        self.assertEqual(_StubHandler.received[0]["path"], "/transcribe")


if __name__ == "__main__":
    unittest.main()
