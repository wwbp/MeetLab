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

# Measured crossover: where predicted segments == real turns taken.
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
    return endpointing + bot.USER_SPEECH_TIMEOUT_SECS * 1000.0


class TurnEndWindowTests(unittest.TestCase):
    def test_the_two_settings_add_up_to_the_calibrated_window(self):
        window = effective_turn_end_window_ms()

        self.assertGreaterEqual(
            window, LOWER_BOUND_MS,
            f"{window:.0f}ms over-splits real speech; participants get chopped mid-sentence",
        )
        self.assertLessEqual(
            window, UPPER_BOUND_MS,
            f"{window:.0f}ms merges separate turns — the bot will answer two things at once",
        )

    def test_the_window_lands_near_the_measured_crossover(self):
        """Within 100ms of where predicted segments matched real turns (0.97x)."""
        self.assertAlmostEqual(
            effective_turn_end_window_ms(), CALIBRATED_OPTIMUM_MS, delta=100
        )

    def test_it_is_a_large_improvement_on_the_pilot(self):
        """The pilot's effective window was ~5.1s: 100ms endpointing, then the
        5.0s wall-clock fallback that every turn fell through to."""
        self.assertLess(effective_turn_end_window_ms(), 5100 / 4)

    def test_neither_setting_alone_is_mistaken_for_the_window(self):
        """Guards the trap that produced the wrong first attempt at this fix.

        Endpointing alone looks fine at 450ms. The aggregator timeout alone looks
        fine at 300ms. Only the sum tells you what a participant experiences, so
        a future change to either must be judged against the total.
        """
        import bot
        from db.models import BotConfig

        endpointing = BotConfig.__table__.c.stt_endpointing_ms.default.arg
        aggregator_ms = bot.USER_SPEECH_TIMEOUT_SECS * 1000.0

        self.assertGreater(endpointing, 0)
        self.assertGreater(aggregator_ms, 0)
        self.assertAlmostEqual(
            effective_turn_end_window_ms(), endpointing + aggregator_ms, places=6
        )


if __name__ == "__main__":
    unittest.main()
