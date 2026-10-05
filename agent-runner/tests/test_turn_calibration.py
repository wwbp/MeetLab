"""The turn-end window, calibrated against real pilot speech.

Two settings decide when the bot thinks you have finished talking, and they
**add together**:

    stt_endpointing_ms       silence before the VAD closes a speech segment
    USER_SPEECH_TIMEOUT_SECS extra silence the aggregator waits after that

Neither number is meaningful alone, which is the trap: 450ms endpointing sounds
conservative until you notice the aggregator adds another 600ms on top and the
real window is over a second. These tests pin the *sum*.

Where the target comes from
---------------------------
Not judgement. Production's own SileroVADAnalyzer was run over real participant
audio from 2026-07-30 — three speakers, 407 intra-speaker silences — and the
predicted segment count compared against the 80 turns those speakers actually
took according to the database:

    effective silence   segments   vs 80 real turns
             300 ms        136        1.70x
             450 ms        107        1.34x
             600 ms         91        1.14x
             750 ms         78        0.97x   <- reproduces reality
             900 ms         66        0.82x
            1050 ms         61        0.76x

The asymmetry matters. Over-splitting is cheap — the aggregator rejoins
fragments into one turn. Under-splitting is not: merging two separate turns makes
the bot answer both at once, which is exactly the "chained answers" complaint
from the pilot. So the window should sit at or slightly below the crossover, not
above it.

Regenerate the measurement with scripts/analyze-pause-distribution.py against
per-speaker WAVs from S3. The audio itself is deliberately not in this repo —
it is real participants' voices.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-key")

# Measured crossover: where predicted segments == real turns taken (one VAD pause).
CALIBRATED_OPTIMUM_MS = 750
# Below this we over-split (cheap, recoverable). Above it we merge distinct
# turns (expensive — the bot answers two things at once).
LOWER_BOUND_MS = 550
UPPER_BOUND_MS = 800


def effective_turn_end_window_ms() -> float:
    """Total silence, in ms, before an unconfigured room commits a user turn."""
    import bot
    from db.models import BotConfig

    endpointing = BotConfig.__table__.c.stt_endpointing_ms.default.arg
    wait = BotConfig.__table__.c.user_speech_timeout_ms.default.arg
    return endpointing + wait


class TunabilityTests(unittest.TestCase):
    """Both halves of the window must be adjustable without a deploy.

    The window is only useful if it can be tuned against real conversations, and
    tuning must not be a code change. It also must not be possible to *regress*
    it by opening a form: the SQLAdmin dropdown for stt_endpointing_ms was left
    listing 100/200/50 after the default moved to 450, so saving any config row
    would silently snap endpointing back to the pilot value.
    """

    def test_the_aggregator_wait_is_a_config_column_not_a_constant(self):
        from db.models import BotConfig

        self.assertIn("user_speech_timeout_ms", BotConfig.__table__.c)

    def test_endpointing_dropdown_offers_the_current_default(self):
        import runner
        from db.models import BotConfig

        default = str(BotConfig.__table__.c.stt_endpointing_ms.default.arg)
        choices = dict(runner.BotConfigAdmin.form_args["stt_endpointing_ms"]["choices"])
        self.assertIn(
            default, choices,
            f"the admin form cannot represent the current default ({default}ms), "
            f"so saving a config row would change it. choices={sorted(choices)}",
        )

    def test_speech_timeout_dropdown_offers_the_current_default(self):
        import runner
        from db.models import BotConfig

        default = str(BotConfig.__table__.c.user_speech_timeout_ms.default.arg)
        choices = dict(runner.BotConfigAdmin.form_args["user_speech_timeout_ms"]["choices"])
        self.assertIn(default, choices)

    def test_every_dropdown_choice_is_accepted_by_the_api(self):
        """A value the form offers must be a value PUT /config will store."""
        import runner

        for field in ("stt_endpointing_ms", "user_speech_timeout_ms"):
            for value, _label in runner.BotConfigAdmin.form_args[field]["choices"]:
                v = int(value)
                self.assertTrue(50 <= v <= 2000, f"{field}={v} is outside the accepted range")

    def test_the_bot_reads_the_wait_from_config(self):
        """A room-scoped override must actually change the pipeline."""
        import bot
        from unittest import mock

        cfg = mock.Mock(user_speech_timeout_ms=800)
        params = bot.build_user_aggregator_params(cfg)
        strategy = next(
            s for s in params.user_turn_strategies.stop
            if type(s).__name__ == "SpeechTimeoutUserTurnStopStrategy"
        )
        self.assertAlmostEqual(strategy._user_speech_timeout, 0.8)


class TurnEndWindowTests(unittest.TestCase):
    """What B4 measured (2026-10-05, 30 rooms on Parakeet): the two settings do NOT add up.

    Each speaker's own VAD closes a segment after stt_endpointing_ms of silence; the
    transcript follows, and only then does the aggregator wait user_speech_timeout_ms. A
    speaker's next words can't arrive inside that wait (they need their own pause first),
    so the wait never joins fragments: it only delays the reply. 300 → 50 ms made replies
    0.24 s faster at p50 and p95 with fragmentation unchanged (24.5% → 24.6%). So the
    split window is the endpointing alone, and the wait stays as short as the API allows.
    (OpenAI realtime STT, whose own VAD ends a turn, is the exception; we default to Parakeet.)
    """

    def test_turns_split_on_the_pause_alone(self):
        from db.models import BotConfig
        pause = BotConfig.__table__.c.stt_endpointing_ms.default.arg
        # 450 ms over-splits real speech 1.34x (the table above): the cheap direction,
        # recovered by smart turn. Never above the crossover: that merges turns.
        self.assertEqual(pause, 450)
        self.assertLessEqual(pause, UPPER_BOUND_MS)

    def test_the_wait_after_the_transcript_is_as_short_as_allowed(self):
        from db.models import BotConfig
        self.assertEqual(BotConfig.__table__.c.user_speech_timeout_ms.default.arg, 50)

    def test_it_is_a_large_improvement_on_the_pilot(self):
        """The pilot's effective window was ~5.1s: 100ms endpointing, then the
        5.0s wall-clock fallback that every turn fell through to."""
        self.assertLess(effective_turn_end_window_ms(), 5100 / 4)


if __name__ == "__main__":
    unittest.main()
