"""The LLM and voice stand-ins (model_stubs.py), asked the way the bot asks its models:
through the OpenAI client Pipecat's OpenAILLMService and OpenAITTSService use."""
import asyncio
import json
import threading
import unittest

from openai import AsyncOpenAI

from meetlab_loadtest.model_stubs import MODEL, REPLY, SAMPLE_RATE, Stubs


class ModelStubsTests(unittest.TestCase):
    def setUp(self):
        self.stubs = Stubs(("127.0.0.1", 0))
        threading.Thread(target=self.stubs.serve_forever, daemon=True).start()
        self.addCleanup(self.stubs.server_close)
        self.addCleanup(self.stubs.shutdown)
        self.url = f"http://127.0.0.1:{self.stubs.server_port}/v1"

    def test_a_streamed_chat_answers_the_scripted_reply_as_the_bots_json(self):
        async def ask():
            async with AsyncOpenAI(api_key="none", base_url=self.url) as client:
                stream = await client.chat.completions.create(
                    model=MODEL, stream=True, stream_options={"include_usage": True},
                    messages=[{"role": "user", "content": "hello there"}])
                return [c async for c in stream]
        chunks = asyncio.run(ask())
        text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
        self.assertEqual(json.loads(text), {"reply": REPLY, "addressed_to": []})
        self.assertGreater(len(chunks), 2)  # in pieces, as a model streams
        self.assertEqual(self.stubs.chats[0]["messages"][-1]["content"], "hello there")

    def test_an_unstreamed_chat_answers_too(self):  # the context summariser's kind of request
        async def ask():
            async with AsyncOpenAI(api_key="none", base_url=self.url) as client:
                return await client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(json.loads(asyncio.run(ask()).choices[0].message.content)["reply"], REPLY)

    def test_speech_is_audible_pcm_as_long_as_the_text(self):
        async def speak(text):
            async with AsyncOpenAI(api_key="none", base_url=self.url) as client:
                async with client.audio.speech.with_streaming_response.create(
                        input=text, model="gpt-4o-mini-tts", voice="alloy", response_format="pcm") as r:
                    self.assertEqual(r.status_code, 200)
                    return b"".join([chunk async for chunk in r.iter_bytes(1024)])
        short, long = asyncio.run(speak("Hi.")), asyncio.run(speak(REPLY))
        self.assertGreater(len(long), len(short))
        self.assertGreaterEqual(len(short), SAMPLE_RATE)  # at least half a second of 16-bit samples
        self.assertEqual(len(long) % 2, 0)
        self.assertGreater(max(abs(int.from_bytes(long[i:i + 2], "little", signed=True)) for i in range(0, 2000, 2)), 1000)
        self.assertEqual(self.stubs.spoken, ["Hi.", REPLY])


if __name__ == "__main__":
    unittest.main()
