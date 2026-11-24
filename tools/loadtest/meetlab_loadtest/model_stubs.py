"""Stand-ins for the two paid models, for tests that need the bot to finish a turn at no cost
(tests/system/voice_loop.py). One small server speaking OpenAI's API as the bot's own clients
call it, so the bot reaches it through the settings production uses for our own models
(SELFHOSTED_LLM_URL + SELFHOSTED_LLM_MODEL, KOKORO_TTS_URL) and runs no test-only code:

    POST /v1/chat/completions   the scripted reply, as the bot's JSON answer (meetlab_core.turns)
    POST /v1/audio/speech       a tone as long as the text would take to say (24 kHz 16-bit mono PCM)

It keeps what it was asked (chats, spoken), so a test can assert on what the bot heard and said.
Standard library only.

ponytail: one fixed reply and a tone, answered at once. Enough to prove a turn completes; it says
nothing about how real models behave (their latency, their wording, a voice a person can
understand). For those: script replies per request, or point the same settings at real models.
"""
import json
import math
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "scripted-stub"  # a room's llm_model; the runner's SELFHOSTED_LLM_MODEL must name it too
REPLY = "This is the scripted reply. It never changes."
PORT = 9100
SAMPLE_RATE = 24000  # what OpenAI's speech API returns for "pcm", and what Pipecat's client expects


def tone(text: str) -> bytes:
    """A 440 Hz tone, 60 ms per character (about speaking pace), between 0.5 and 4 s."""
    n = int(SAMPLE_RATE * min(4.0, max(0.5, 0.06 * len(text))))
    return struct.pack(f"<{n}h", *(int(8000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)) for i in range(n)))


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.endswith("/chat/completions"):
            self.server.chats.append(body)
            answer = json.dumps({"reply": self.server.reply, "addressed_to": []})
            if not body.get("stream"):
                return self._send("application/json", json.dumps(
                    self._completion("chat.completion", {"message": {"role": "assistant", "content": answer}})).encode())
            pieces = [answer[i:i + 12] for i in range(0, len(answer), 12)]  # in pieces, as a model streams
            events = [self._completion("chat.completion.chunk", {"delta": {"role": "assistant", "content": p}}, None)
                      for p in pieces] + [self._completion("chat.completion.chunk", {"delta": {}})]
            return self._send("text/event-stream",
                              "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode() + b"data: [DONE]\n\n")
        if self.path.endswith("/audio/speech"):
            self.server.spoken.append(body.get("input", ""))
            return self._send("audio/pcm", tone(body.get("input", "")))
        self.send_error(404)

    def _completion(self, kind: str, choice: dict, finish: str | None = "stop") -> dict:
        return {"id": "stub", "object": kind, "created": 0, "model": MODEL,
                "choices": [{"index": 0, "finish_reason": finish, **choice}]}

    def _send(self, content_type: str, data: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # one line per request, where the test's output is
        print(f"[model stubs] {fmt % args}", flush=True)


class Stubs(ThreadingHTTPServer):
    """serve_forever() in a thread; chats and spoken are the requests so far."""
    daemon_threads = True

    def __init__(self, address=("0.0.0.0", PORT), reply: str = REPLY):
        super().__init__(address, _Handler)
        self.reply, self.chats, self.spoken = reply, [], []
