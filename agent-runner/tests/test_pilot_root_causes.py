"""Reproductions of the four root causes found in the Jul/Aug 2026 pilot.

Full analysis and the production evidence behind each one:
``docs/pilot-postmortem-2026-08.md``.

    RC1  Pipecat's ``user_turn_stop_timeout`` left at its 5.0s default while the
         aggregator is built with ``vad_analyzer=None``  → lag + chained answers
    RC2  ``stt_endpointing_ms=100`` ends a turn after 100ms of silence, and the
         ``vad_stop_secs`` knob that operators actually turned does nothing
    RC3  Interruption is measured but never enforced — the bot cannot yield
    RC4  Egress quota (429) kills a recording with no retry and leaves no trace

These tests PIN the defective behaviour so the mechanism is reproduced in CI
rather than argued about, and so every fix has an exact assertion to invert.
Each pinning test carries a ``FIX:`` line naming what to change when the fix
lands. If one starts failing without a deliberate fix, a dependency default
moved underneath us — which is itself worth knowing.

Run in the container (RC4 needs the DB):

    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_pilot_root_causes -v
"""

import ast
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-key")
os.environ.setdefault("DEEPGRAM_API_KEY", "test-deepgram-key")

AGENT_RUNNER_DIR = Path(__file__).resolve().parent.parent
BOT_PY = AGENT_RUNNER_DIR / "bot.py"


def _prod_user_aggregator_params():
    """The user-aggregator params bot.py actually builds in production.

    Goes through the real builder rather than reconstructing it, so the test
    cannot drift from what ships.
    """
    import bot

    return bot.build_user_aggregator_params()


def call_sites(source_path: Path, callee: str) -> list[set[str]]:
    """Keyword-argument names used at every call site of ``callee`` in a file.

    AST rather than grep so that formatting, line wrapping and comments can move
    without breaking the test.
    """
    tree = ast.parse(source_path.read_text())
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == callee:
                sites.append({kw.arg for kw in node.keywords if kw.arg})
    return sites


# ── RC1 — the 5-second turn-commit timeout ────────────────────────────────────


class RC1TurnCommitTimeoutTests(unittest.TestCase):
    """The bot waits 5s of wall-clock before deciding a user's turn ended.

    Production signature: STT spikes cluster in a 5.2-6.1s band at queue_depth=0
    — a constant, not a latency tail. 49% of turns took >3s on 2026-07-30.
    """

    def test_pipecat_default_turn_stop_timeout_is_5_seconds(self):
        """The upstream default we inherited. This is the 5s in the spike band.

        FIX: leave this test as-is — it guards the upstream default. It is the
        *next* test that must change.
        """
        from pipecat.processors.aggregators.llm_response_universal import (
            LLMUserAggregatorParams,
        )

        self.assertEqual(LLMUserAggregatorParams().user_turn_stop_timeout, 5.0)

    def test_pipecat_default_stop_strategy_needs_audio_we_never_deliver(self):
        """The precise mechanism, and why the fix is not just a smaller timeout.

        Pipecat's default stop strategy is TurnAnalyzerUserTurnStopStrategy — an
        ONNX smart-turn model that decides the user has finished by analysing
        *audio*. But `multi_speaker_stt.py` consumes every UserAudioRawFrame to
        route it to a per-participant STT and explicitly does not forward it
        ("Audio consumed by per-participant STT; do not push downstream
        directly"). So the analyzer is starved and never fires, and every turn
        falls through to the wall-clock fallback.

        This is a standing property of upstream + our pipeline shape, so it stays
        as a guard: if the default ever changes, our reasoning needs revisiting.
        """
        from pipecat.turns.user_turn_strategies import UserTurnStrategies

        defaults = UserTurnStrategies()
        self.assertEqual(
            [type(s).__name__ for s in defaults.stop],
            ["TurnAnalyzerUserTurnStopStrategy"],
        )

    def test_bot_uses_a_stop_strategy_that_works_without_audio(self):
        """The fix: a strategy driven by VAD-stop frames and transcript inactivity.

        SpeechTimeoutUserTurnStopStrategy ends the turn a short pause after the
        user stops, and falls back to inactivity-since-last-transcript when no
        VAD stop frame arrives — both signals our pipeline actually delivers.
        """
        params = _prod_user_aggregator_params()

        names = [type(s).__name__ for s in params.user_turn_strategies.stop]
        self.assertIn("SpeechTimeoutUserTurnStopStrategy", names)
        self.assertNotIn(
            "TurnAnalyzerUserTurnStopStrategy", names,
            "the audio-based analyzer cannot work here — the aggregator gets no audio",
        )

    def test_a_pause_ends_the_turn_in_well_under_a_second(self):
        """What the user actually feels. The pilot's median was 5198ms."""
        params = _prod_user_aggregator_params()
        strategy = next(
            s for s in params.user_turn_strategies.stop
            if type(s).__name__ == "SpeechTimeoutUserTurnStopStrategy"
        )
        self.assertLessEqual(strategy._user_speech_timeout, 0.8)

    def test_the_wall_clock_fallback_is_no_longer_five_seconds(self):
        """Belt and braces: even if every strategy fails, the ceiling is sane.

        5.0s is upstream's default and was the entire pilot failure. The fallback
        should be a backstop, not the primary path.
        """
        params = _prod_user_aggregator_params()
        self.assertLessEqual(params.user_turn_stop_timeout, 2.0)


# ── RC2 — 100ms endpointing, and a knob that does nothing ─────────────────────


class RC2EndpointingTests(unittest.TestCase):
    """Turn ends after 100ms of silence, shredding natural speech into fragments.

    Production signature: avg 2.1 fragments per turn, worst turn split 31 ways,
    participants saying "Sorry?" mid-turn in the transcripts.
    """

    def _captured_stop_secs(self, **config_fields) -> float:
        """Build the production VAD processor and report the stop_secs it used.

        SileroVADAnalyzer is patched so no model is loaded; bot.py imports it
        inside the function, so patching the source module is what takes effect.
        """
        import bot

        cfg = mock.Mock(**config_fields)
        captured = {}

        class _FakeAnalyzer:
            def __init__(self, params=None):
                captured["stop_secs"] = params.stop_secs

        with mock.patch(
            "pipecat.audio.vad.silero.SileroVADAnalyzer", _FakeAnalyzer
        ), mock.patch(
            "pipecat.processors.audio.vad_processor.VADProcessor",
            lambda **kw: mock.Mock(),
        ):
            bot._build_vad_processor(cfg)

        return captured["stop_secs"]

    def test_configured_endpointing_still_drives_the_vad_window(self):
        """The mapping itself is correct and must stay that way."""
        self.assertAlmostEqual(
            self._captured_stop_secs(stt_endpointing_ms=450, vad_stop_secs=0.1), 0.45
        )

    def test_the_default_endpointing_tolerates_a_thinking_pause(self):
        """100ms was the pilot value and it shredded natural speech.

        A person pausing mid-sentence to think is silent for far longer than
        100ms; treating that as "finished" is what split one participant's
        sentence 31 ways. The default must be long enough to survive an ordinary
        pause and short enough to stay responsive.
        """
        from db.models import BotConfig

        default_ms = BotConfig.__table__.c.stt_endpointing_ms.default.arg
        self.assertGreaterEqual(default_ms, 300, "too eager — will cut people off mid-thought")
        self.assertLessEqual(default_ms, 700, "too slow — the bot will feel sluggish")

    def test_endpointing_falls_back_safely_when_unset(self):
        """An unset value must not silently revert to the aggressive old default."""
        self.assertGreaterEqual(
            self._captured_stop_secs(stt_endpointing_ms=None, vad_stop_secs=0.1), 0.3
        )

    def test_vad_stop_secs_has_no_effect_on_the_pipeline(self):
        """``vad_stop_secs`` is a fully-plumbed knob that changes nothing.

        DB column -> API validation in runner.py -> a slider in the console at
        meet/app/(shell)/config/page.tsx. All 42 pilot config rows had it set to
        0.1 by someone trying to fix responsiveness. Its only appearance in the
        pipeline is inside a log string (bot.py, "vad={...}s").

        Two wildly different values must produce an identical VAD window — that
        identity *is* the bug.

        FIX: either wire this field to the aggregator (then these two must
        differ) or delete it end-to-end — column, API, and console slider.
        """
        tiny = self._captured_stop_secs(stt_endpointing_ms=100, vad_stop_secs=0.1)
        huge = self._captured_stop_secs(stt_endpointing_ms=100, vad_stop_secs=5.0)

        self.assertEqual(tiny, huge)

    def test_vad_stop_secs_is_only_ever_logged_never_applied(self):
        """FIX: delete this test when the field is wired up or removed."""
        tree = ast.parse(BOT_PY.read_text())
        uses = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "vad_stop_secs"
        ]
        self.assertEqual(len(uses), 1, "vad_stop_secs should appear exactly once in bot.py")

        # ...and that one use is inside an f-string being logged, not a call argument.
        in_fstring = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            and any(
                isinstance(v, ast.Attribute) and v.attr == "vad_stop_secs"
                for f in node.values
                if isinstance(f, ast.FormattedValue)
                for v in ast.walk(f)
            )
        ]
        self.assertEqual(len(in_fstring), 1)


# ── RC3 — interruption measured, never enforced ───────────────────────────────


class RC3InterruptionEnforcementTests(unittest.TestCase):
    """The bot must yield when someone talks over it.

    Production signature before the fix: 52 talk-over events, p50 1375ms,
    observed max 4743ms. Detection was fully built; enforcement was not.

    Why the built-in path could not be used
    ---------------------------------------
    Pipecat's usual route is transport VAD -> UserStartedSpeakingFrame ->
    interruption. Two reasons that is not the mechanism here:

    * The 0.0.x-era ``allow_interruptions`` / ``interruption_strategies`` knobs on
      PipelineParams do not exist in 1.4.0 — see
      ``test_pipeline_params_has_no_allow_interruptions_in_this_version``.
    * We already compute a better signal. MultiSpeakerSTT gives per-participant
      VAD onset (``on_speech_onset``), attributed to a specific speaker, from
      audio it is already analysing. Adding a transport-level analyzer would
      re-run VAD over the mixed stream for a worse, unattributed signal.

    So enforcement rides the signal we have: onset during a bot-speaking window
    pushes an InterruptionFrame, which every FrameProcessor handles by cancelling
    in-flight work (frame_processor.py: InterruptionFrame -> _start_interruption).
    """

    def test_pipeline_params_has_no_allow_interruptions_in_this_version(self):
        """Guards the reasoning above against a dependency bump.

        Much of the public Pipecat documentation still describes
        ``PipelineParams(allow_interruptions=..., interruption_strategies=[...])``.
        Those fields are gone in 1.4.0. If they come back, revisit whether the
        built-in path is now the better mechanism.
        """
        from pipecat.pipeline.task import PipelineParams

        fields = set(PipelineParams.model_fields)
        self.assertNotIn("allow_interruptions", fields)
        self.assertNotIn("interruption_strategies", fields)

    def test_the_tracker_tells_the_caller_to_yield(self):
        """A detected talk-over now produces an actionable signal, not just a count."""
        from interruption import InterruptionTracker

        tracker = InterruptionTracker(record=False)
        tracker.bot_started(0.0)

        self.assertTrue(
            tracker.user_onset(0.5, "sid-A"),
            "a user speaking over the bot must signal an interruption",
        )

        tracker.bot_stopped(4.7)
        # ...and the measurement is unchanged, so the metric stays comparable
        # with the pilot numbers in docs/pilot-postmortem-2026-08.md.
        self.assertEqual(tracker.interruptions, 1)
        self.assertAlmostEqual(tracker.talkovers_ms[0], 4200.0, places=3)

    def test_the_speech_onset_handler_pushes_an_interruption_frame(self):
        """End of the chain: the signal reaches the pipeline.

        Exercises the real handler bot.py installs, with a fake enqueue, so the
        wiring is covered without standing up a pipeline.
        """
        import asyncio

        import bot
        from interruption import InterruptionTracker
        from pipecat.frames.frames import InterruptionFrame

        tracker = InterruptionTracker(record=False)
        pushed = []

        async def fake_enqueue(frame):
            pushed.append(frame)

        handler = bot.make_speech_onset_handler(tracker, fake_enqueue)

        async def scenario():
            # Nobody is speaking over anyone — no interruption.
            await handler("sid-A")
            self.assertEqual(pushed, [])

            tracker.bot_started(1.0)
            await handler("sid-A")
            await handler("sid-A")  # flicker: must not re-interrupt

        asyncio.run(scenario())

        self.assertEqual(len(pushed), 1, "expected exactly one interruption per window")
        self.assertIsInstance(pushed[0], InterruptionFrame)

    def test_a_missing_pipeline_handle_does_not_crash_the_audio_path(self):
        """on_speech_onset runs inside frame processing; it must never raise.

        The handler is created before the PipelineTask exists, so it can be
        invoked with nothing to push to. Losing an interruption is bad; killing
        the audio path is worse.
        """
        import asyncio

        import bot
        from interruption import InterruptionTracker

        tracker = InterruptionTracker(record=False)
        handler = bot.make_speech_onset_handler(tracker, None)
        tracker.bot_started(0.0)

        asyncio.run(handler("sid-A"))  # must not raise
        self.assertEqual(tracker.interruptions, 1, "the talk-over is still measured")


# ── RC4 — recordings lost to the egress quota, with no trace ──────────────────


class _Quota429(Exception):
    """Shape-accurate stand-in for the TwirpError seen in production."""

    def __init__(self):
        super().__init__(
            "TwirpError(code=resource_exhausted, "
            "message=concurrent egress sessions limit exceeded, status=429)"
        )


class RC4EgressQuotaClassificationTests(unittest.TestCase):
    """Quota errors take the no-retry path. Pure classification, no DB needed."""

    def test_quota_429_is_not_treated_as_a_retryable_output_error(self):
        import runner

        self.assertFalse(runner._is_missing_egress_output_error(_Quota429()))


class RC4EgressQuotaRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_429_is_attempted_exactly_once_and_never_retried(self):
        """15 rooms lost their video to this in the pilot — one attempt each.

        FIX: when quota retry/backoff lands, assert more than one attempt and a
        successful egress id.
        """
        import runner
        from livekit.protocol.egress import EncodedFileOutput

        attempts = []

        class _Egress:
            async def start_room_composite_egress(self, request):
                attempts.append(request)
                raise _Quota429()

        lk = mock.Mock(egress=_Egress())

        with self.assertRaisesRegex(Exception, "concurrent egress sessions limit exceeded"):
            await runner._start_room_composite_egress(
                lk, "room-quota", EncodedFileOutput(filepath="/recordings/x.mp4")
            )

        self.assertEqual(len(attempts), 1)


class RC4SilentRecordingLossTests(unittest.IsolatedAsyncioTestCase):
    """A quota failure leaves no MediaFile row at all — so nothing can recover it.

    This is why 12 of 24 real pilot sessions have no video *and* no failed-record
    to show for it: the reconciler only ever looks at rows with status='pending',
    and a 429 means no row was ever written.
    """

    async def asyncSetUp(self):
        from db.engine import engine

        # Shared asyncpg pool binds to the loop of first use; each test gets a
        # fresh loop. close=False abandons old-loop connections rather than
        # closing them across loops (see test_recording_autostart for the why).
        await engine.dispose(close=False)
        self.addAsyncCleanup(engine.dispose, close=False)

    async def test_quota_failure_records_nothing_and_leaves_nothing_to_reconcile(self):
        """FIX: assert a MediaFile row exists with status 'failed' (or similar) so
        the loss is visible in the console and recoverable by the reconciler.
        """
        import runner
        from sqlalchemy import select

        from db.engine import AsyncSessionLocal
        from db.models import Conversation, MediaFile

        room = f"rc4-quota-{uuid.uuid4().hex[:8]}"
        conv_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as db:
            async with db.begin():
                db.add(Conversation(id=conv_id, room_name=room, status="running"))

        async def _raise_quota(lk, room_name, file_output):
            raise _Quota429()

        with mock.patch.object(runner, "_start_room_composite_egress", _raise_quota):
            status, payload = await runner.start_recording_for_room(room)

        # The caller is told, with a retryable-looking 502...
        self.assertEqual(status, 502)
        self.assertIn("concurrent egress sessions limit exceeded", payload["error"])
        self.assertNotIn("media_file_id", payload)

        # ...but nothing is persisted, so the loss is invisible after the fact.
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(MediaFile).where(
                        MediaFile.conv_id == conv_id, MediaFile.type == "recording"
                    )
                )
            ).scalars().all()

        self.assertEqual(rows, [])


class RC4ReconcilerTerminalStateTests(unittest.TestCase):
    """The reconciler resolves real egress states but loops forever on 404.

    Two 2026-07-30 recordings are still 'pending' today because LiveKit 404s on
    their egress ids and the handler only logs a warning and moves on — there is
    no attempt cap and no give-up path, so they show "still recording" forever.
    """

    def test_reconciler_has_no_attempt_cap_or_give_up_path(self):
        """FIX: introduce an attempt/age cap that marks abandoned rows 'failed',
        then assert that cap here.
        """
        import inspect

        import runner

        source = inspect.getsource(runner._reconcile_pending_recordings)

        # A not-found egress hits the generic handler, which only warns.
        self.assertIn("reconcile: error checking egress", source)
        for giving_up in ("max_attempts", "attempts", "give_up", "abandon", "too_old"):
            self.assertNotIn(giving_up, source)

    def test_livekit_limit_reached_would_be_terminal_if_an_egress_row_existed(self):
        """Status 6 (LIMIT_REACHED) is handled — but only for egress that started.

        This is the trap: quota rejection happens *before* an egress exists, so
        this branch never sees the pilot's failures. Documents why the reconciler
        looked healthy while half the recordings were missing.
        """
        import inspect

        import runner

        source = inspect.getsource(runner._reconcile_pending_recordings)
        self.assertIn("TERMINAL_FAILED = (4, 5, 6)", source)


if __name__ == "__main__":
    unittest.main()
