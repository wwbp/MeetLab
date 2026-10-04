"""Unit tests for bot.py helper functions and guard logic."""
import os
import sys
import unittest
from unittest import mock
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
    _apply_stt_model_override,
    _build_stt,
    _build_stt_for_multi_speaker,
    _find_participant,
    _normalize_words,
    _text_similarity,
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


class TestFindParticipant(unittest.TestCase):
    """Pipecat >= 1.8 passes the participant's identity, the roster's own key (Pipecat
    1.4 passed the session id, which needed a scan and changed on every reconnect)."""

    def test_finds_the_participant_by_the_id_pipecat_passes(self):
        alice, bob = _fake_participant("alice", "PA_aaa"), _fake_participant("bob", "PA_bbb")
        self.assertIs(_find_participant({"alice": alice, "bob": bob}, "bob"), bob)

    def test_a_session_id_is_not_an_identity(self):
        self.assertIsNone(_find_participant({"alice": _fake_participant("alice", "PA_aaa")}, "PA_aaa"))

    def test_returns_none_for_an_unknown_participant(self):
        self.assertIsNone(_find_participant({}, "alice"))


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
    segment (WAV bytes) to the Parakeet NIM's /v1/audio/transcriptions endpoint.
    Experiment log: docs/latency-experiments.md
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

    def test_parakeet_unified_routes_to_sidecar_chain(self):
        # parakeet-unified-en-0.6b shares the parakeet- prefix → same offline sidecar
        # chain (streaming ~160ms mode is a separate, future integration).
        from nemotron_stt import NemotronHTTPSTTService
        cfg = _FakeBotConfig(stt_model="parakeet-unified-en-0.6b")
        head, tail = _build_stt(cfg, "openai-key", None)
        self.assertIsInstance(head, VADProcessor)
        self.assertIsInstance(tail, NemotronHTTPSTTService)
        self.assertEqual(tail.model_name, "parakeet-unified-en-0.6b")


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

    def test_default_stt_is_parakeet(self):
        from db.config_loader import EffectiveBotConfig
        cfg = EffectiveBotConfig(
            system_prompt="", greeting="",
            llm_model="gpt-5.4-nano", tts_voice="WhMcMcvXQ8T2QfmQmlYh",
            stt_model="parakeet-tdt-0.6b-v2", stt_vad_mode="local",
            stt_delay=None, tts_provider="elevenlabs",
            tts_aggregation_mode="sentence", stt_endpointing_ms=450,
            auto_record=False,
        )
        # A caller that predates the session-limit field gets no limit, not a
        # crash and not an accidental cap.
        self.assertEqual(cfg.session_limit_minutes, 0)
        self.assertEqual(cfg.stt_model, "parakeet-tdt-0.6b-v2")
        self.assertEqual(cfg.llm_model, "gpt-5.4-nano")
        self.assertEqual(cfg.tts_provider, "elevenlabs")
        self.assertEqual(cfg.stt_vad_mode, "local")
        self.assertIsNone(cfg.stt_delay)

    def test_fallback_constants_match_benchmark_winner(self):
        import db.config_loader as cl
        import inspect
        src = inspect.getsource(cl.load_bot_config)
        # Model/provider choices only. This test used to also assert
        # "stt_endpointing_ms=100", which pinned the pilot value that cut
        # participants off mid-sentence — a test actively holding a bug in place.
        # Numeric turn-taking defaults are now owned by
        # test_config_contract.FallbackDefaultsTests, which derives them from the
        # column defaults so the fallback cannot drift from the schema again.
        self.assertIn("parakeet-tdt-0.6b-v2", src)
        self.assertIn("gpt-5.4-nano", src)
        self.assertIn("elevenlabs", src)


class TestSTTModelOverride(unittest.TestCase):
    """STT_MODEL_OVERRIDE forces the bot's STT model (local dev → in-process whisper-base,
    since there's no local GPU for the Parakeet NIM). Prod leaves it unset. Applied to the
    running bot only — never to the /config store/API, which reflects what's persisted."""

    def _cfg(self, stt_model="parakeet-tdt-0.6b-v2"):
        return _FakeBotConfig(stt_model=stt_model)

    def test_unset_returns_config_unchanged(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STT_MODEL_OVERRIDE", None)
            cfg = self._cfg()
            self.assertIs(_apply_stt_model_override(cfg), cfg)

    def test_empty_string_returns_config_unchanged(self):
        with patch.dict(os.environ, {"STT_MODEL_OVERRIDE": "  "}):
            cfg = self._cfg()
            self.assertIs(_apply_stt_model_override(cfg), cfg)

    def test_set_value_overrides_stt_model_stripped(self):
        with patch.dict(os.environ, {"STT_MODEL_OVERRIDE": " whisper-base "}):
            out = _apply_stt_model_override(self._cfg("parakeet-tdt-0.6b-v2"))
            self.assertEqual(out.stt_model, "whisper-base")

    def test_override_matching_current_is_noop(self):
        with patch.dict(os.environ, {"STT_MODEL_OVERRIDE": "whisper-base"}):
            cfg = self._cfg("whisper-base")
            self.assertIs(_apply_stt_model_override(cfg), cfg)


class TestSelfEchoHeuristic(unittest.TestCase):
    """_text_similarity / _normalize_words back the bot self-echo detector."""

    def test_identical_text_is_max_similarity(self):
        self.assertEqual(_text_similarity("what is the speed of light", "what is the speed of light"), 1.0)

    def test_disjoint_text_is_zero(self):
        self.assertEqual(_text_similarity("hello there friend", "completely unrelated words"), 0.0)

    def test_empty_either_side_is_zero(self):
        self.assertEqual(_text_similarity("", "anything at all"), 0.0)
        self.assertEqual(_text_similarity("anything at all", ""), 0.0)

    def test_speaker_prefix_is_stripped_before_compare(self):
        # SpeakerLabelInjector prepends "Alice: "; the bot text has no prefix.
        # The name must not dilute the overlap, so this should read as a strong echo.
        bot = "the boiling point of water is one hundred degrees"
        user = "Alice: the boiling point of water is one hundred degrees"
        self.assertGreaterEqual(_text_similarity(user, bot), 0.65)

    def test_partial_overlap_between_zero_and_one(self):
        sim = _text_similarity("the quick brown fox", "the quick red hound")
        self.assertGreater(sim, 0.0)
        self.assertLess(sim, 1.0)

    def test_normalize_drops_punctuation_and_case(self):
        self.assertEqual(_normalize_words("Hello, World!"), ["hello", "world"])

    def test_normalize_strips_short_name_prefix_only(self):
        # A colon early in the string is treated as a speaker prefix...
        self.assertEqual(_normalize_words("Bob: hi"), ["hi"])
        # ...but a colon deep in the text (no prefix) is not stripped.
        self.assertIn("ratio", _normalize_words("the result was a strange ratio: forty two to one"))


if __name__ == "__main__":
    unittest.main()


class TestBuildLLM(unittest.TestCase):
    """Pipecat 1.9+: the model and the system prompt are LLM settings, not an initial
    "system" message in the context (deprecated, removed in 2.0)."""

    def test_the_model_and_system_prompt_are_settings(self):
        from types import SimpleNamespace

        from bot import _build_llm

        llm = _build_llm("sk-test", SimpleNamespace(llm_model="gpt-4o-mini", system_prompt="Facilitate."))
        self.assertEqual((llm._settings.model, llm._settings.system_instruction), ("gpt-4o-mini", "Facilitate."))


class TestSpokenText(unittest.TestCase):
    """What a person said is stored without the speaker label the LLM sees (SpeakerLabelInjector):
    the speaker is its own column, and the label turned up in researchers' transcripts and in
    the load test's hearing score as words nobody said (2026-10-03)."""

    def test_the_label_is_removed(self):
        from bot import _spoken_text
        self.assertEqual(_spoken_text("load_000: Why does that matter?", {"load_000"}), "Why does that matter?")

    def test_every_fragment_of_a_merged_turn_loses_its_label(self):
        from bot import _spoken_text
        self.assertEqual(_spoken_text("Alice: Candidate A is strong. Alice: But B is popular.", {"Alice", "alice_x"}),
                         "Candidate A is strong. But B is popular.")

    def test_text_without_a_label_is_unchanged(self):
        from bot import _spoken_text
        self.assertEqual(_spoken_text("What should we prioritise?", {"Alice"}), "What should we prioritise?")

    def test_a_name_is_matched_literally(self):
        from bot import _spoken_text
        self.assertEqual(_spoken_text("Dr. A+B: Hello.", {"Dr. A+B", None}), "Hello.")


class TestSmartTurnChain(unittest.TestCase):
    """turn_detection picks the end-of-turn rule per room; smart turn sits in each person's chain."""

    def chain(self, turn_detection):
        from types import SimpleNamespace
        from bot import _build_parakeet_chain
        cfg = SimpleNamespace(stt_model="parakeet-tdt-0.6b-v2", turn_detection=turn_detection, stt_vad_mode="local",
                              smart_turn_wait_ms=1500)
        with mock.patch.dict(os.environ, {"NEMOTRON_STT_URL": "http://nim.internal:9000"}):
            return _build_parakeet_chain(cfg)

    def test_silence_keeps_the_chain_as_it_was(self):
        head, tail = self.chain("silence")
        self.assertIs(head._next, tail)  # VAD straight into STT

    def test_smart_turn_puts_a_gate_between_the_persons_vad_and_stt_and_hands_over_its_verdict(self):
        from smart_turn import SmartTurnGate, TurnVerdict
        head, tail, verdict = self.chain("smart_turn")
        self.assertIsInstance(head._next, SmartTurnGate)
        self.assertIs(head._next._next, tail)
        self.assertIsInstance(verdict, TurnVerdict)
        self.assertIs(head._next._listener.verdict, verdict)
        self.assertEqual(verdict.wait_secs, 1.5)  # the room's smart_turn_wait_ms


class TestLongConversations(unittest.TestCase):
    """A conversation must outlive the LLM's context: the L6 breakpoint run (2026-10-03) went
    silent in rooms ~40 min old, because the history passed Qwen's 8,192 tokens, vLLM answered
    400, and Pipecat marked the LLM unusable while the bot stayed in the room, mute."""

    def test_old_turns_are_summarised_well_before_the_models_limit(self):
        from bot import _assistant_params
        p = _assistant_params()
        self.assertTrue(p.enable_auto_context_summarization)
        # room for the system prompt's growth and a reply under vLLM's --max-model-len 8192
        self.assertLessEqual(p.auto_context_summarization_config.max_context_tokens, 6000)

    def test_a_bot_whose_llm_can_no_longer_work_ends_its_session_instead_of_sitting_silent(self):
        from pipecat.pipeline.worker import ProcessorUnusablePolicy
        from bot import UNUSABLE_POLICY
        self.assertIs(UNUSABLE_POLICY, ProcessorUnusablePolicy.END)


class TestStageOfMetric(unittest.TestCase):
    """Which stage a time-to-first-byte belongs to, by the processor that reported it."""

    def test_each_service_reports_its_own_stage(self):
        from bot import _ttfb_stage
        self.assertEqual(_ttfb_stage("OpenAILLMService#0"), "llm_ttft_ms")
        self.assertEqual(_ttfb_stage("ElevenLabsTTSService#0"), "tts_ttfb_ms")

    def test_openais_voice_is_speech_not_the_llm(self):
        # Kokoro is driven through OpenAITTSService: its first audio overwrote the
        # LLM's first token when "openai" in the name meant LLM (load test 2026-10-03).
        from bot import _ttfb_stage
        self.assertEqual(_ttfb_stage("OpenAITTSService#0"), "tts_ttfb_ms")

    def test_speech_to_text_is_neither(self):
        from bot import _ttfb_stage
        self.assertIsNone(_ttfb_stage("OpenAISTTService#0"))
        self.assertIsNone(_ttfb_stage("NemotronHTTPSTTService#0"))


class TestSelfHostedModels(unittest.TestCase):
    """Load-test readiness L3: our own LLM (vLLM) and voice (Kokoro), chosen by config."""

    def _config(self, **kw):
        from types import SimpleNamespace

        return SimpleNamespace(**{"llm_model": "gpt-5.4-nano", "system_prompt": "Facilitate.",
                                  "tts_provider": "elevenlabs", "tts_voice": "", **kw})

    def test_the_model_our_vllm_serves_goes_to_our_server(self):
        from bot import _build_llm

        env = {"SELFHOSTED_LLM_URL": "http://models.internal:8000/v1", "SELFHOSTED_LLM_MODEL": "Qwen/Qwen2.5-7B-Instruct"}
        with mock.patch.dict(os.environ, env):
            llm = _build_llm("sk-test", self._config(llm_model="Qwen/Qwen2.5-7B-Instruct"))
        self.assertEqual(str(llm._client.base_url), "http://models.internal:8000/v1/")

    def test_any_other_model_still_goes_to_openai(self):
        from bot import _build_llm

        with mock.patch.dict(os.environ, {"SELFHOSTED_LLM_URL": "http://models.internal:8000/v1",
                                          "SELFHOSTED_LLM_MODEL": "Qwen/Qwen2.5-7B-Instruct"}):
            llm = _build_llm("sk-test", self._config(llm_model="gpt-5.4-nano"))
        self.assertIn("api.openai.com", str(llm._client.base_url))

    def test_kokoro_speaks_through_our_server(self):
        from bot import _build_tts

        with mock.patch.dict(os.environ, {"KOKORO_TTS_URL": "http://models.internal:8880/v1"}):
            tts = _build_tts(self._config(tts_provider="kokoro", tts_voice="alloy"), "sk-test", "el-test", None)
        self.assertEqual(str(tts._client.base_url), "http://models.internal:8880/v1/")

    def test_kokoro_without_its_server_fails_at_setup(self):
        from bot import _build_tts

        env = {k: v for k, v in os.environ.items() if k != "KOKORO_TTS_URL"}
        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(ValueError):
            _build_tts(self._config(tts_provider="kokoro"), "sk-test", "el-test", None)
