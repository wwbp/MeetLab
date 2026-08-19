"""Reporting speech onset from the VAD we already run, instead of a second one.

A VADAnalyzer's state machine is:

    QUIET --(first frame over threshold)--> STARTING --(start_secs more)--> SPEAKING

VADProcessor only emits frames on SPEAKING and QUIET, so STARTING — the earliest
evidence the analyzer has that someone is talking — is computed and thrown away.

That discarded transition is exactly the interruption signal. Reading it costs one
extra state comparison per audio frame and needs no second analyzer, no separate
thresholds, and no second Silero session per participant. Segmentation keeps using
SPEAKING/QUIET as before; interruption uses the earlier edge. One analyzer, two
consumers, no duplicated source of truth.

`speech_edge` is pure and carries the whole decision, so the interesting cases are
testable without Silero, audio, or a pipeline.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipecat.audio.vad.vad_analyzer import VADState

from speech_onset_vad import SpeechOnsetMixin, speech_edge


class TestSpeechEdge(unittest.TestCase):
    """The pure decision: which edge, if any, did this state change represent?"""

    def test_quiet_to_starting_is_detection(self):
        self.assertEqual(speech_edge(VADState.QUIET, VADState.STARTING), "detected")

    def test_quiet_to_speaking_in_one_step_is_still_one_detection(self):
        """analyze_audio can consume several internal frames per call, so a single
        call may jump straight past STARTING. It is still one onset."""
        self.assertEqual(speech_edge(VADState.QUIET, VADState.SPEAKING), "detected")

    def test_starting_to_speaking_is_not_a_new_detection(self):
        """Promotion to SPEAKING is the STT's signal, not a second interruption."""
        self.assertIsNone(speech_edge(VADState.STARTING, VADState.SPEAKING))

    def test_speaking_to_stopping_is_not_yet_cleared(self):
        """STOPPING is the analyzer hedging; a mid-sentence breath is not a turn end."""
        self.assertIsNone(speech_edge(VADState.SPEAKING, VADState.STOPPING))

    def test_returning_to_quiet_clears(self):
        self.assertEqual(speech_edge(VADState.STOPPING, VADState.QUIET), "cleared")
        self.assertEqual(speech_edge(VADState.SPEAKING, VADState.QUIET), "cleared")

    def test_a_false_start_that_never_confirms_still_clears(self):
        """STARTING -> QUIET is the analyzer retracting. Whoever was told about the
        onset must be told it ended, or they will believe someone is still talking."""
        self.assertEqual(speech_edge(VADState.STARTING, VADState.QUIET), "cleared")

    def test_no_change_is_no_edge(self):
        for s in VADState:
            self.assertIsNone(speech_edge(s, s))


class _FakeBase:
    """Stands in for VADAnalyzer so these tests need no Silero and no audio."""

    def __init__(self, states):
        self._states = list(states)
        self._vad_state = VADState.QUIET

    async def analyze_audio(self, buffer):
        if self._states:
            self._vad_state = self._states.pop(0)
        return self._vad_state


class _OnsetVAD(SpeechOnsetMixin, _FakeBase):
    pass


class TestTheAnalyzerShell(unittest.TestCase):
    def _vad(self, states):
        vad = _OnsetVAD(states)
        seen = []
        vad.set_onset_handlers(
            on_detected=lambda: seen.append("detected"),
            on_cleared=lambda: seen.append("cleared"),
        )
        return vad, seen

    def test_detection_fires_at_starting_not_at_speaking(self):
        vad, seen = self._vad([VADState.STARTING, VADState.SPEAKING])

        async def run():
            await vad.analyze_audio(b"")
            self.assertEqual(seen, ["detected"], "must fire on the earlier edge")
            await vad.analyze_audio(b"")
            self.assertEqual(seen, ["detected"], "and not again on promotion")

        asyncio.run(run())

    def test_the_state_is_returned_unchanged(self):
        """The STT chain reads this return value; observing must not alter it."""
        vad, _ = self._vad([VADState.STARTING])
        self.assertEqual(asyncio.run(vad.analyze_audio(b"")), VADState.STARTING)

    def test_clearing_fires_on_quiet(self):
        vad, seen = self._vad([VADState.SPEAKING, VADState.QUIET])

        async def run():
            await vad.analyze_audio(b"")
            await vad.analyze_audio(b"")

        asyncio.run(run())
        self.assertEqual(seen, ["detected", "cleared"])

    def test_an_async_handler_is_awaited(self):
        """The detected handler pushes the interruption; it must finish first."""
        vad = _OnsetVAD([VADState.STARTING])
        order = []

        async def slow():
            await asyncio.sleep(0)
            order.append("handler")

        vad.set_onset_handlers(on_detected=slow)

        async def run():
            await vad.analyze_audio(b"")
            order.append("returned")

        asyncio.run(run())
        self.assertEqual(order, ["handler", "returned"])

    def test_a_failing_handler_does_not_break_analysis(self):
        """This runs on the live audio path. A bad callback must not stop STT."""
        vad = _OnsetVAD([VADState.STARTING])

        def boom():
            raise RuntimeError("handler blew up")

        vad.set_onset_handlers(on_detected=boom)
        self.assertEqual(asyncio.run(vad.analyze_audio(b"")), VADState.STARTING)

    def test_unconfigured_analyzer_is_inert(self):
        """No handlers set — behaves exactly like the base analyzer."""
        vad = _OnsetVAD([VADState.STARTING])
        self.assertEqual(asyncio.run(vad.analyze_audio(b"")), VADState.STARTING)


if __name__ == "__main__":
    unittest.main()
