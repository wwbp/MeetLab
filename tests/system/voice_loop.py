"""The free voice loop: one whole turn through a real bot, no paid service (make test-voice-loop).

A participant joins a real room and speaks a recorded line. The bot (its own container, the
production image and code) hears it through our speech-to-text server (stt-cpu, Parakeet),
asks a scripted stand-in for the language model, and answers through a stand-in voice
(meetlab_loadtest/model_stubs.py, served from this process). The test then checks what the
participant heard, what the stand-ins were asked, and what the bot stored.

The bot reaches the stand-ins through the settings production uses for our own models: the
room's Bot Config (llm_model, tts_provider=kokoro) and the runner's SELFHOSTED_LLM_URL,
SELFHOSTED_LLM_MODEL and KOKORO_TTS_URL (.devcontainer/docker-compose.voice-loop.yml).

Not `test_*.py`: it needs the stack started that way, so discovery (make test-unit) skips it.

ponytail: one person, one line, one fixed reply; the reply time is printed, not judged. The
recorded line is a synthetic voice, which does not pause or hesitate like a person. Next: ptt and
baton rooms on this loop, then a reply-time gate, then real models behind the same settings.
"""
import asyncio
import json
import threading
import time
import unittest
from uuid import uuid4

import numpy as np
from livekit import rtc
from sqlalchemy import delete, select

from meetlab_core.db.engine import AsyncSessionLocal, engine
from meetlab_core.db.models import BotConfig, Utterance
from meetlab_loadtest import audio_scenarios as audio
from meetlab_loadtest._sim_common import (
    LIVEKIT_URL, capture_signal, conversation_status, publish_audio_source, request, token,
)
from meetlab_loadtest.conversation_soak import BotListener
from meetlab_loadtest.generate_conversation_audio import FIXTURES, fixture_name
from meetlab_loadtest.model_stubs import MODEL, REPLY, Stubs
from meetlab_loadtest.sim_attribution import verdict

LINE = "What should we prioritise for the leadership role this quarter?"


def heard(text: str) -> bool:
    """The line's words are in this text (sim_attribution's rule: 60% of them)."""
    return verdict({"me": LINE}, [("me", text)]) == []


class VoiceLoop(unittest.IsolatedAsyncioTestCase):
    async def until(self, ready, timeout_s: float, what: str):
        """Poll ready() until it returns something; fail at once if the bot's session ended."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if got := await ready() if asyncio.iscoroutinefunction(ready) else ready():
                return got
            status, _ = await conversation_status(self.session)
            if status != "running":
                self.fail(f"{what}: the bot's session ended '{status}' first")
            await asyncio.sleep(0.25)
        self.fail(f"{what}: not within {timeout_s:.0f} s (the session is still running)")

    async def test_a_spoken_line_is_heard_answered_and_stored(self):
        stubs = Stubs()
        threading.Thread(target=stubs.serve_forever, daemon=True).start()
        self.addCleanup(stubs.shutdown)
        self.addAsyncCleanup(engine.dispose)

        room_name, me = f"voice-loop-{uuid4().hex[:6]}", f"sim_{uuid4().hex[:4]}"
        request("PUT", "/config", {"scope": room_name, "llm_model": MODEL, "tts_provider": "kokoro", "tts_voice": "alloy"})

        async def forget_room_config():  # a developer's database keeps no row per run
            async with AsyncSessionLocal() as db, db.begin():
                await db.execute(delete(BotConfig).where(BotConfig.scope == room_name))
        self.addAsyncCleanup(forget_room_config)

        room, listener = rtc.Room(), BotListener()

        @room.on("track_subscribed")
        def _on_track(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity.startswith("bot_"):
                listener.watch(track)

        await room.connect(LIVEKIT_URL, token(room_name, me))
        self.addAsyncCleanup(room.disconnect)
        signal, sr = audio.load_wav_float(str(FIXTURES / fixture_name(LINE)))
        mic = await publish_audio_source(room, "mic", sr)

        started = request("POST", "/start", {"room_name": room_name})
        self.session, bot = started["session_id"], started["bot_identity"]
        self.addCleanup(request, "POST", "/stop", {"room_name": room_name})

        # The greeting: the bot joined, and its voice (the stand-in) reached a participant.
        await self.until(lambda: listener.last_audio_at, 120, "the bot's greeting was never heard")
        await listener.wait_until_quiet()

        await capture_signal(mic, signal, sr)
        ended = time.time()
        listener.arm()
        await capture_signal(mic, np.zeros(sr, dtype=signal.dtype), sr)  # a second of silence: the turn's end
        reply_at = await self.until(lambda: listener.first_audio_since_arm, 60, "no reply was heard after the line")
        print(f"[voice loop] reply heard {(reply_at - ended) * 1000:.0f} ms after the line ended", flush=True)

        async def stored():
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(Utterance.speaker_id, Utterance.text, Utterance.meta)
                                         .where(Utterance.conv_id == self.session).order_by(Utterance.ts))).all()
            mine = [text for speaker, text, _ in rows if speaker == me]
            replies = [meta for speaker, text, meta in rows if speaker == bot and " ".join(text.split()) == REPLY]
            return (mine, replies) if mine and replies else None
        # Stored when the reply's audio has finished.
        mine, replies = await self.until(stored, 60, "the line and the scripted reply were not both stored")
        print(f"[voice loop] stored: {mine}; reply timing {replies[0].get('timing')}", flush=True)

        self.assertTrue(any(heard(text) for text in mine), f"stored under the participant: {mine}")
        timing = replies[0].get("timing") or {}
        self.assertLessEqual({"stt_ms", "llm_ttft_ms", "tts_ttfb_ms"}, timing.keys(), "the reply's timing record")
        # What the stand-ins were asked: the model got the words as heard, the voice got the reply.
        asked = [turn["text"] for chat in stubs.chats for m in chat["messages"] if m["role"] == "user"
                 for turn in json.loads(m["content"])]
        self.assertTrue(any(heard(text) for text in asked), f"the model was asked: {asked}")
        self.assertEqual(" ".join(stubs.spoken[-2:]), REPLY)  # the voice speaks a sentence at a time


if __name__ == "__main__":
    unittest.main()
