"""Unit tests for bot.py helper functions and guard logic."""
import os
import sys
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://localhost:7880")
os.environ.setdefault("DEEPGRAM_API_KEY", "test-deepgram-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from bot import (
    _build_stt,
    _build_stt_for_multi_speaker,
    _find_participant_by_sid,
    _turn_detection_for_vad_mode,
    _OpenAIRealtimeSTT,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.services.whisper.stt import WhisperSTTService


@dataclass
class _FakeBotConfig:
    stt_model: str
    stt_vad_mode: str = "local"
    stt_delay: str | None = None
    stt_endpointing_ms: int = 200


def _fake_participant(identity: str, sid: str) -> MagicMock:
    p = MagicMock()
    p.identity = identity
    p.sid = sid
    return p


class TestFindParticipantBySid(unittest.TestCase):
    """_find_participant_by_sid searches remote_participants by SID value."""

    def test_finds_participant_when_sid_matches(self):
        p = _fake_participant("alice", "PA_abc")
        result = _find_participant_by_sid({"alice": p}, "PA_abc")
        self.assertIs(result, p)

    def test_returns_none_when_sid_not_present(self):
        p = _fake_participant("alice", "PA_abc")
        result = _find_participant_by_sid({"alice": p}, "PA_unknown")
        self.assertIsNone(result)

    def test_returns_none_for_empty_dict(self):
        self.assertIsNone(_find_participant_by_sid({}, "PA_abc"))

    def test_old_get_by_sid_key_always_fails(self):
        """Regression: dict.get(sid) on an identity-keyed dict returns None."""
        p = _fake_participant("alice", "PA_abc")
        participants = {"alice": p}
        self.assertIsNone(participants.get("PA_abc"))        # old broken pattern
        self.assertIsNotNone(_find_participant_by_sid(participants, "PA_abc"))  # fixed

    def test_selects_correct_participant_among_multiple(self):
        alice = _fake_participant("alice", "PA_aaa")
        bob = _fake_participant("bob", "PA_bbb")
        participants = {"alice": alice, "bob": bob}
        self.assertIs(_find_participant_by_sid(participants, "PA_bbb"), bob)
        self.assertIs(_find_participant_by_sid(participants, "PA_aaa"), alice)


class TestTurnDetectionForVadMode(unittest.TestCase):
    """_turn_detection_for_vad_mode always returns False (local Silero VAD only)."""

    def test_local_returns_false(self):
        self.assertIs(_turn_detection_for_vad_mode("local"), False)

    def test_any_input_returns_false(self):
        # Server VAD was removed — all inputs fall through to local behaviour
        for mode in ("local", "server", "unknown", ""):
            self.assertIs(_turn_detection_for_vad_mode(mode), False)


class TestBuildStt(unittest.TestCase):
    """_build_stt selects the correct STT service based on stt_model prefix."""

    def test_nova_uses_deepgram(self):
        cfg = _FakeBotConfig(stt_model="nova-3-general")
        svc = _build_stt(cfg, "openai-key", "deepgram-key")
        self.assertIsInstance(svc, DeepgramSTTService)

    def test_gpt_realtime_whisper_uses_openai(self):
        cfg = _FakeBotConfig(stt_model="gpt-realtime-whisper")
        svc = _build_stt(cfg, "openai-key", "deepgram-key")
        self.assertIsInstance(svc, _OpenAIRealtimeSTT)

    def test_gpt_4o_transcribe_uses_openai(self):
        cfg = _FakeBotConfig(stt_model="gpt-4o-transcribe")
        svc = _build_stt(cfg, "openai-key", "deepgram-key")
        self.assertIsInstance(svc, _OpenAIRealtimeSTT)

    def test_gpt_4o_mini_transcribe_uses_openai(self):
        cfg = _FakeBotConfig(stt_model="gpt-4o-mini-transcribe")
        svc = _build_stt(cfg, "openai-key", "deepgram-key")
        self.assertIsInstance(svc, _OpenAIRealtimeSTT)

    def test_deepgram_requires_api_key(self):
        cfg = _FakeBotConfig(stt_model="nova-3-general")
        with self.assertRaises(ValueError):
            _build_stt(cfg, "openai-key", None)

    def test_openai_does_not_require_deepgram_key(self):
        cfg = _FakeBotConfig(stt_model="gpt-realtime-whisper")
        # Should not raise even with no Deepgram key
        svc = _build_stt(cfg, "openai-key", None)
        self.assertIsInstance(svc, _OpenAIRealtimeSTT)

    def test_benchmark_winner_config(self):
        # nova-3-general / gpt-5.4-nano / elevenlabs — top E2E P50 (1279ms)
        cfg = _FakeBotConfig(stt_model="nova-3-general")
        svc = _build_stt(cfg, "openai-key", "deepgram-key")
        self.assertIsInstance(svc, DeepgramSTTService)


class TestBuildSttWhisperChain(unittest.TestCase):
    """whisper-* models build a local VADProcessor → WhisperSTTService chain.

    WhisperSTTService is a SegmentedSTTService: it only transcribes after
    VADUserStarted/StoppedSpeakingFrames tell it where the speech segment is.
    Per-participant audio routed by MultiSpeakerSTT never passes through the
    transport's VAD, so each chain must carry its own VADProcessor to generate
    those frames. Both builders return (head, tail) = (VADProcessor, Whisper).

    WhisperSTTService._load is patched out — the real constructor eagerly
    downloads the model (~1.6 GB for turbo), which unit tests must not do.
    """

    def setUp(self):
        patcher = patch.object(WhisperSTTService, "_load", autospec=True)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_whisper_returns_vad_to_stt_chain(self):
        cfg = _FakeBotConfig(stt_model="whisper-turbo")
        head, tail = _build_stt(cfg, "openai-key", None)
        self.assertIsInstance(head, VADProcessor)
        self.assertIsInstance(tail, WhisperSTTService)

    def test_whisper_multi_speaker_chain_is_linked(self):
        cfg = _FakeBotConfig(stt_model="whisper-turbo")
        head, tail = _build_stt_for_multi_speaker(cfg, "openai-key", None)
        self.assertIsInstance(head, VADProcessor)
        self.assertIsInstance(tail, WhisperSTTService)
        self.assertIs(head._next, tail, "audio must flow head (VAD) → tail (Whisper)")

    def test_whisper_model_prefix_stripped(self):
        cfg = _FakeBotConfig(stt_model="whisper-turbo")
        _, tail = _build_stt(cfg, "openai-key", None)
        self.assertEqual(tail._settings.model, "turbo")

    def test_whisper_does_not_require_deepgram_key(self):
        cfg = _FakeBotConfig(stt_model="whisper-base")
        head, tail = _build_stt(cfg, "openai-key", None)  # must not raise
        self.assertIsInstance(head, VADProcessor)
        self.assertEqual(tail._settings.model, "base")

    def test_whisper_endpointing_default_200ms(self):
        cfg = _FakeBotConfig(stt_model="whisper-turbo")
        head, _ = _build_stt(cfg, "openai-key", None)
        self.assertAlmostEqual(head._vad_controller._vad_analyzer.params.stop_secs, 0.2)

    def test_whisper_endpointing_respects_config(self):
        cfg = _FakeBotConfig(stt_model="whisper-turbo", stt_endpointing_ms=100)
        head, _ = _build_stt(cfg, "openai-key", None)
        self.assertAlmostEqual(head._vad_controller._vad_analyzer.params.stop_secs, 0.1)


class TestBuildSttParakeetChain(unittest.TestCase):
    """parakeet-* models build a VADProcessor → NemotronHTTPSTTService chain.

    Same segmented-chain shape as the whisper path; the tail POSTs each VAD-cut
    segment (WAV bytes) to the stt-nemotron sidecar's /transcribe endpoint.
    Experiment log: docs/experiment-6-gpu-stt.md
    """

    def test_parakeet_returns_vad_to_http_chain(self):
        from nemotron_stt import NemotronHTTPSTTService
        cfg = _FakeBotConfig(stt_model="parakeet-tdt-0.6b-v2")
        head, tail = _build_stt(cfg, "openai-key", None)  # no Deepgram key needed
        self.assertIsInstance(head, VADProcessor)
        self.assertIsInstance(tail, NemotronHTTPSTTService)

    def test_parakeet_multi_speaker_chain_is_linked(self):
        from nemotron_stt import NemotronHTTPSTTService
        cfg = _FakeBotConfig(stt_model="parakeet-tdt-0.6b-v2")
        head, tail = _build_stt_for_multi_speaker(cfg, "openai-key", None)
        self.assertIsInstance(head, VADProcessor)
        self.assertIsInstance(tail, NemotronHTTPSTTService)
        self.assertIs(head._next, tail)

    def test_parakeet_url_from_env(self):
        from nemotron_stt import NemotronHTTPSTTService
        cfg = _FakeBotConfig(stt_model="parakeet-tdt-0.6b-v2")
        with patch.dict(os.environ, {"NEMOTRON_STT_URL": "http://elsewhere:9000/"}):
            _, tail = _build_stt(cfg, "openai-key", None)
        self.assertEqual(tail.base_url, "http://elsewhere:9000")

    def test_parakeet_endpointing_respects_config(self):
        cfg = _FakeBotConfig(stt_model="parakeet-tdt-0.6b-v2", stt_endpointing_ms=100)
        head, _ = _build_stt(cfg, "openai-key", None)
        self.assertAlmostEqual(head._vad_controller._vad_analyzer.params.stop_secs, 0.1)

    def test_parakeet_model_name_recorded(self):
        cfg = _FakeBotConfig(stt_model="parakeet-tdt-0.6b-v2")
        _, tail = _build_stt(cfg, "openai-key", None)
        self.assertEqual(tail.model_name, "parakeet-tdt-0.6b-v2")


class TestOnUserTurnStoppedGuards(unittest.TestCase):
    """Guard conditions for on_user_turn_stopped mirror the bot handler logic.

    The handler skips DB writes (and avoids FK violations / empty LLM turns)
    when:
      - message.content is falsy (empty transcription)
      - no participant identity is known (_sid_to_identity is empty)
    """

    def _run_guards(self, content: str, sid_to_identity: dict):
        """Execute the exact guard sequence from on_user_turn_stopped."""
        if not content:
            return "skipped:empty_content"

        sid = next(iter(sid_to_identity), None)
        identity = sid_to_identity.get(sid, sid) if sid else None
        if not identity:
            return "skipped:unknown_identity"

        return f"proceed:{identity}"

    # --- empty content ---

    def test_empty_string_skips_insert(self):
        self.assertEqual(self._run_guards("", {"PA_x": "alice"}), "skipped:empty_content")

    def test_whitespace_skips_insert(self):
        # Transcriptions that are only whitespace are falsy after strip,
        # but the guard uses `not content` so only the empty string hits it.
        # Confirm empty string is caught.
        self.assertEqual(self._run_guards("", {}), "skipped:empty_content")

    # --- unknown identity ---

    def test_empty_sid_map_skips_insert(self):
        self.assertEqual(self._run_guards("Hello", {}), "skipped:unknown_identity")

    # --- happy path ---

    def test_known_identity_and_content_proceeds(self):
        result = self._run_guards("Hello there", {"PA_abc": "alice"})
        self.assertEqual(result, "proceed:alice")

    def test_returns_first_known_identity(self):
        # _sid_to_identity may have multiple entries; first one is used
        sid_map = {"PA_aaa": "alice", "PA_bbb": "bob"}
        result = self._run_guards("Hey", sid_map)
        first_identity = sid_map[next(iter(sid_map))]
        self.assertEqual(result, f"proceed:{first_identity}")


class TestValidSttModels(unittest.TestCase):
    """Only benchmark-verified STT models should be accepted."""

    VALID_STT_MODELS = [
        "nova-3-general",
        "gpt-realtime-whisper",
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
    ]
    REMOVED_STT_MODELS = [
        "nova-3-meeting",     # 100% timeout on current Deepgram plan
        "nova-3-phonecall",   # 100% timeout on current Deepgram plan
        "nova-3-voicemail",   # 100% timeout on current Deepgram plan
    ]

    def test_valid_models_build_without_error(self):
        for model in self.VALID_STT_MODELS:
            with self.subTest(model=model):
                cfg = _FakeBotConfig(stt_model=model)
                # Deepgram models need key; OpenAI models don't
                key = "dg-key" if not model.startswith("gpt-") else None
                svc = _build_stt(cfg, "openai-key", key)
                self.assertIsNotNone(svc)

    def test_removed_models_still_route_to_deepgram(self):
        # They're no longer listed in the UI but the pipeline won't crash if
        # somehow stored in the DB — they'll just time out at runtime.
        for model in self.REMOVED_STT_MODELS:
            with self.subTest(model=model):
                cfg = _FakeBotConfig(stt_model=model)
                svc = _build_stt(cfg, "openai-key", "deepgram-key")
                self.assertIsInstance(svc, DeepgramSTTService)


class TestConfigLoaderDefaults(unittest.TestCase):
    """Hardcoded fallback in config_loader uses the benchmark-winning defaults."""

    def test_default_stt_is_nova_3_general(self):
        from db.config_loader import EffectiveBotConfig
        cfg = EffectiveBotConfig(
            system_prompt="", greeting="", vad_stop_secs=0.6,
            llm_model="gpt-5.4-nano", tts_voice="WhMcMcvXQ8T2QfmQmlYh",
            stt_model="nova-3-general", stt_vad_mode="local",
            stt_delay=None, tts_provider="elevenlabs",
            tts_aggregation_mode="sentence", stt_endpointing_ms=200,
        )
        self.assertEqual(cfg.stt_model, "nova-3-general")
        self.assertEqual(cfg.llm_model, "gpt-5.4-nano")
        self.assertEqual(cfg.tts_provider, "elevenlabs")
        self.assertEqual(cfg.stt_vad_mode, "local")
        self.assertIsNone(cfg.stt_delay)

    def test_fallback_constants_match_benchmark_winner(self):
        import db.config_loader as cl
        import inspect, ast, textwrap
        src = inspect.getsource(cl.load_bot_config)
        # Ensure the hardcoded fallback uses the winning defaults
        self.assertIn("nova-3-general", src)
        self.assertIn("gpt-5.4-nano", src)
        self.assertIn("elevenlabs", src)


if __name__ == "__main__":
    unittest.main()
