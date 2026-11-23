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

from meetlab_bot.nemotron_stt import NemotronHTTPSTTService


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


class TestTranscribingDuringThePause(unittest.IsolatedAsyncioTestCase):
    """The silence wait (450 ms) and the transcription used to run one after the other.
    Now transcription starts when the person pauses; if they stay silent, the segment that
    the VAD closes reuses that answer instead of asking again."""

    def _svc(self, texts=("early",)):
        svc = NemotronHTTPSTTService(base_url="http://unused", model="parakeet-tdt-0.6b-v2",
                                     sample_rate=16000, speculate_after_secs=0)
        svc._sample_rate = 16000
        svc.calls = []
        answers = iter(texts)

        async def fake(wav):
            svc.calls.append(wav)
            return next(answers)
        svc._transcribe = fake
        for name in ("start_processing_metrics", "start_ttfb_metrics",
                     "stop_ttfb_metrics", "stop_processing_metrics"):
            setattr(svc, name, unittest.mock.AsyncMock())
        return svc

    def _segment(self, svc, extra=b""):
        """What SegmentedSTTService hands run_stt when the VAD closes the segment."""
        from pipecat.audio.utils import pcm_to_wav
        return pcm_to_wav(bytes(svc._audio_buffer) + extra + svc._trailing_silence(), 16000)

    async def _texts(self, svc, wav):
        return [f.text async for f in svc.run_stt(wav)]

    async def test_a_pause_that_ends_the_turn_is_transcribed_once(self):
        svc = self._svc()
        svc._audio_buffer += b"\x01\x00" * 8000
        svc.speech_paused()
        await asyncio.sleep(0)                       # the early request goes out
        self.assertEqual(len(svc.calls), 1)
        wav = self._segment(svc, extra=bytes(14400))  # 450 ms more silence, then the VAD closes
        svc.segment_closed()
        self.assertEqual(await self._texts(svc, wav), ["early"])
        self.assertEqual(len(svc.calls), 1)

    async def test_speaking_again_throws_the_early_answer_away(self):
        svc = self._svc(texts=("early", "whole sentence"))
        svc._audio_buffer += b"\x01\x00" * 8000
        svc.speech_paused()
        await asyncio.sleep(0)
        svc.speech_resumed()
        wav = self._segment(svc, extra=b"\x02\x00" * 8000)
        svc.segment_closed()
        self.assertEqual(await self._texts(svc, wav), ["whole sentence"])

    async def test_a_resume_inside_the_delay_sends_nothing(self):
        svc = self._svc()
        svc._speculate_after_secs = 0.05
        svc._audio_buffer += b"\x01\x00" * 8000
        svc.speech_paused()
        svc.speech_resumed()
        await asyncio.sleep(0.1)
        self.assertEqual(svc.calls, [])

    async def test_an_early_answer_for_other_audio_is_never_used(self):
        """Belt and braces: the early answer only counts if the closed segment starts
        with exactly the audio it was made from."""
        svc = self._svc(texts=("early", "the real one"))
        svc._audio_buffer += b"\x01\x00" * 8000
        svc.speech_paused()
        await asyncio.sleep(0)
        svc.segment_closed()
        from pipecat.audio.utils import pcm_to_wav
        other = pcm_to_wav(b"\x03\x00" * 8000, 16000)
        self.assertEqual(await self._texts(svc, other), ["the real one"])

    async def test_a_failed_early_request_falls_back_to_the_normal_one(self):
        svc = self._svc()
        n = {"i": 0}

        async def flaky(wav):
            n["i"] += 1
            if n["i"] == 1:
                raise RuntimeError("server busy")
            return "second try"
        svc._transcribe = flaky
        svc._audio_buffer += b"\x01\x00" * 8000
        svc.speech_paused()
        await asyncio.sleep(0)
        wav = self._segment(svc)
        svc.segment_closed()
        self.assertEqual(await self._texts(svc, wav), ["second try"])

    async def test_a_segment_closed_before_the_early_request_went_out_is_transcribed_normally(self):
        """A silence wait shorter than the delay: the buffer is already cleared, so an
        early request now would send the wrong audio."""
        svc = self._svc(texts=("the segment",))
        svc._speculate_after_secs = 0.05
        svc._audio_buffer += b"\x01\x00" * 8000
        svc.speech_paused()
        wav = self._segment(svc)
        svc.segment_closed()
        svc._audio_buffer.clear()
        await asyncio.sleep(0.1)
        self.assertEqual(await self._texts(svc, wav), ["the segment"])
        self.assertEqual(svc.calls, [wav], "only the segment itself was sent")

    async def test_no_pause_seen_works_as_before(self):
        svc = self._svc(texts=("plain",))
        svc._audio_buffer += b"\x01\x00" * 8000
        self.assertEqual(await self._texts(svc, self._segment(svc)), ["plain"])


if __name__ == "__main__":
    unittest.main()
