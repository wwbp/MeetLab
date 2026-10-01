"""A bot's session always ends with a recorded status (step 4c, PR 4; design iteration 5).

Before this, three ways out of bot() left the session row 'running' forever:
  * setup failed before the pipeline started (outside the try/finally)
  * the STT backend was missing, so every transcription failed and the bot sat
    silent (staging sanity run 1, 2026-09-30)
  * the process got SIGTERM (ECS StopTask) and died at once (spike, exit 143)

Pure/offline: LiveKit, config and DB writes are stubbed. Run with:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_bot_lifecycle -v
"""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import bot
from bot_task import runner_args_for
from runner_types import LiveKitRunnerArguments

ARGS = LiveKitRunnerArguments(url="wss://lk", token="t", room_name="r1", session_id="s1", bot_identity="bot_r1")


class SetupFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_failure_before_the_pipeline_starts_still_ends_the_session(self):
        with mock.patch.object(bot, "LiveKitTransport", side_effect=RuntimeError("bad token")), \
             mock.patch.object(bot, "_finalize_conversation", new=mock.AsyncMock()) as fin:
            with self.assertRaises(RuntimeError):
                await bot.bot(ARGS)
        fin.assert_awaited_once_with("s1", "error")


class SttBackendTest(unittest.IsolatedAsyncioTestCase):
    def cfg(self, model):
        return SimpleNamespace(stt_model=model)

    def test_parakeet_needs_a_nim_url(self):
        with mock.patch.dict(os.environ, {"NEMOTRON_STT_URL": ""}):
            with self.assertRaises(ValueError) as ctx:
                bot._require_stt_backend(self.cfg("parakeet-tdt-0.6b-v2"))
        self.assertIn("NEMOTRON_STT_URL", str(ctx.exception))

    def test_parakeet_with_a_nim_url_and_other_models_are_fine(self):
        with mock.patch.dict(os.environ, {"NEMOTRON_STT_URL": "http://nim:9000"}):
            bot._require_stt_backend(self.cfg("parakeet-tdt-0.6b-v2"))
        with mock.patch.dict(os.environ, {"NEMOTRON_STT_URL": ""}):
            bot._require_stt_backend(self.cfg("nova-3-general"))

    async def test_a_bot_without_its_stt_backend_fails_instead_of_sitting_silent(self):
        env = {"NEMOTRON_STT_URL": "", "STT_MODEL_OVERRIDE": "", "OPENAI_API_KEY": "k", "ELEVENLABS_API_KEY": "k"}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(bot, "LiveKitTransport"), \
             mock.patch.object(bot, "load_bot_config", new=mock.AsyncMock(return_value=SimpleNamespace(
                 stt_model="parakeet-tdt-0.6b-v2", llm_model="m", tts_voice="v",
                 stt_endpointing_ms=0, user_speech_timeout_ms=0))), \
             mock.patch.object(bot, "_finalize_conversation", new=mock.AsyncMock()) as fin:
            with self.assertRaises(ValueError):
                await bot.bot(ARGS)
        fin.assert_awaited_once_with("s1", "error")


class SigtermTest(unittest.TestCase):
    def test_a_bot_task_shuts_down_gracefully_on_sigterm(self):
        # ECS StopTask sends SIGTERM; the runner must turn it into a pipeline cancel
        # so the existing finally flushes audio and writes the final status.
        row = SimpleNamespace(id="s1", room_name="r1", bot_identity="bot_r1", meta={})
        self.assertTrue(runner_args_for(row, url="wss://lk", token="t").handle_sigterm)

    def test_an_in_process_bot_leaves_the_api_process_signals_alone(self):
        self.assertFalse(ARGS.handle_sigterm)


if __name__ == "__main__":
    unittest.main()
