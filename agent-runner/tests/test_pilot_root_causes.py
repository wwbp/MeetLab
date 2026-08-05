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

    def test_production_aggregator_waits_5s_with_no_vad_to_end_a_turn_sooner(self):
        """Reproduces the exact params bot.py builds — the effective prod config.

        With ``vad_analyzer=None`` the aggregator has no voice signal to detect
        the end of a turn, so the wall-clock timeout is the *only* thing that
        commits it.

        FIX: once bot.py passes a real analyzer and an explicit timeout, assert
        ``user_turn_stop_timeout <= 1.2`` and ``vad_analyzer is not None``.
        """
        from pipecat.processors.aggregators.llm_response_universal import (
            LLMUserAggregatorParams,
        )

        params = LLMUserAggregatorParams(vad_analyzer=None)

        self.assertIsNone(params.vad_analyzer)
        self.assertEqual(params.user_turn_stop_timeout, 5.0)

    def test_bot_does_not_override_the_turn_stop_timeout(self):
        """bot.py never passes the knob, so the 5.0s default wins.

        FIX: invert to ``assertIn("user_turn_stop_timeout", kwargs)``.
        """
        sites = call_sites(BOT_PY, "LLMUserAggregatorParams")
        self.assertEqual(len(sites), 1, "expected exactly one aggregator-params call site")
        kwargs = sites[0]

        self.assertIn("vad_analyzer", kwargs)
        self.assertNotIn("user_turn_stop_timeout", kwargs)

    def test_bot_builds_the_aggregator_without_a_vad_analyzer(self):
        """FIX: invert once a real VAD analyzer is passed instead of None."""
        source = BOT_PY.read_text()
        self.assertIn("LLMUserAggregatorParams(vad_analyzer=None)", source)


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

    def test_endpointing_100ms_yields_a_100ms_silence_window(self):
        """The pilot value. 100ms is shorter than a normal conversational pause.

        FIX: once the default moves to 400-600ms, assert the new value here and
        update ``test_defaults.test_endpointing_default_is_100`` to match.
        """
        self.assertAlmostEqual(
            self._captured_stop_secs(stt_endpointing_ms=100, vad_stop_secs=0.1), 0.1
        )

    def test_endpointing_falls_back_to_200ms_when_unset(self):
        self.assertAlmostEqual(
            self._captured_stop_secs(stt_endpointing_ms=None, vad_stop_secs=0.1), 0.2
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


class RC3InterruptionNotEnforcedTests(unittest.TestCase):
    """The bot talks over users and has no mechanism to stop.

    Production signature: 52 talk-over events, p50 1375ms, observed max 4743ms.
    """

    def test_transport_is_built_without_a_vad_analyzer(self):
        """No transport VAD means Pipecat's built-in interruption path is inert.

        FIX: invert to ``assertIn("vad_analyzer", kwargs)``.
        """
        sites = call_sites(BOT_PY, "LiveKitParams")
        self.assertEqual(len(sites), 1, "expected exactly one LiveKitParams call site")

        self.assertNotIn("vad_analyzer", sites[0])

    def test_allow_interruptions_is_never_configured_anywhere(self):
        """FIX: invert once the flag is set — assert the offenders list is non-empty.

        Scans agent-runner sources rather than one call site: the point is that
        the setting is absent from the entire service, not just from bot.py.
        """
        hits = [
            path.name
            for path in AGENT_RUNNER_DIR.glob("*.py")
            if "allow_interruptions" in path.read_text()
        ]
        self.assertEqual(hits, [])

    def test_tracker_records_a_talkover_but_has_no_way_to_stop_the_bot(self):
        """The tracker is observe-only: counters in, nothing out.

        It holds no pipeline handle, so a detected talk-over cannot become a
        cancellation. This is the whole of RC3 in one assertion.

        FIX: when interruption is enforced, assert the detection path actually
        emits/pushes something (e.g. an InterruptionFrame) instead of only
        incrementing a counter.
        """
        from interruption import InterruptionTracker

        tracker = InterruptionTracker(record=False)
        tracker.bot_started(0.0)
        tracker.user_onset(0.5, "sid-A")   # user starts talking over the bot
        tracker.bot_stopped(4.7)           # ...bot carries on for another 4.2s

        # The talk-over is measured precisely.
        self.assertEqual(tracker.interruptions, 1)
        self.assertAlmostEqual(tracker.talkovers_ms[0], 4200.0, places=3)

        # ...and the tracker has no means to have prevented it.
        for mechanism in ("push_frame", "queue_frame", "cancel", "interrupt", "task"):
            self.assertFalse(
                hasattr(tracker, mechanism),
                f"tracker unexpectedly exposes {mechanism!r} — interruption may now be enforced",
            )


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
