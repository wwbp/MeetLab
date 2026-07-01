"""Zero-cost mock TTS service for load/soak testing.

TTS is the dominant per-call cost in a soak (per-character billing); a 10-room ×
20-min run synthesizes a huge amount of speech. MockTTSService emits synthetic silence
instead of calling a TTS API, so the soak still exercises the parts that matter for a
load test — STT under concurrency, the bot-speaking window (interruption handling),
session teardown / DB consistency — without paying for TTS. The LLM is pinned to the
cheapest model (gpt-5.4-nano) rather than mocked: a correct LLM mock needs careful
assistant-turn/frame handling (deferred), and nano cost is small.

Enabled per agent-runner process via BOT_MOCK_TTS (see bot.py); OFF by default so
production is never affected.

Fidelity note: with mock TTS, `total_latency_ms` understates real response time (no
real synthesis), but `stt_ms` is still REAL — STT runs unchanged — so STT-under-load
numbers stay valid.
"""
import asyncio

from loguru import logger
from pipecat.frames.frames import TTSAudioRawFrame
from pipecat.services.tts_service import TTSService


class MockTTSService(TTSService):
    """Emits synthetic silence instead of calling a TTS API.

    Produces roughly len(text)-proportional audio so the bot's speaking window has a
    realistic wall-clock duration — important for interruption/talk-over testing. The
    base class wraps this with TTSStarted/Stopped, and the transport drives
    BotStarted/StoppedSpeaking from the played audio, so the rest of the pipeline
    behaves normally.
    """

    SECS_PER_CHAR = 0.05
    MIN_SECS = 0.4
    MAX_SECS = 6.0
    FRAME_MS = 20

    async def run_tts(self, text: str, context_id: str):
        secs = min(self.MAX_SECS, max(self.MIN_SECS, len(text) * self.SECS_PER_CHAR))
        sr = self.sample_rate or 24000
        samples_per_frame = sr * self.FRAME_MS // 1000
        silence = b"\x00\x00" * samples_per_frame  # 16-bit mono silence
        n_frames = max(1, int(secs * 1000 // self.FRAME_MS))
        logger.debug(f"MockTTS: {n_frames} silent frames (~{secs:.1f}s) for [{text[:40]}]")
        for _ in range(n_frames):
            yield TTSAudioRawFrame(silence, sr, 1, context_id=context_id)
            # Real-time pacing so the bot-speaking window is realistic and a user can
            # actually overlap it (otherwise all frames flush instantly).
            await asyncio.sleep(self.FRAME_MS / 1000)
