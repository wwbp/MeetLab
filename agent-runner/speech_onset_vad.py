"""Speech onset reported from the VAD the STT chain already runs.

A VADAnalyzer moves through:

    QUIET --(first frame over threshold)--> STARTING --(start_secs more)--> SPEAKING

VADProcessor only emits frames on SPEAKING and QUIET, so STARTING — the earliest
evidence the analyzer has that someone started talking — is computed and then
discarded. That discarded edge is precisely the interruption signal.

Reading it costs one state comparison per audio frame and, measured over real
participant speech, fires **160 ms** earlier than the SPEAKING edge — 5 VAD frames
of 32 ms, deterministic rather than distributed. Reproduce with
scripts/measure-interruption-onset.py. The alternative considered
first was a second, more sensitive analyzer per participant; that worked but ran two
Silero sessions per speaker and duplicated the thresholds, which is a second source
of truth for "is this person talking". This keeps one analyzer with two consumers:

    interruption   the STARTING edge — as early as the analyzer will commit
    segmentation   SPEAKING / QUIET, exactly as before, thresholds untouched

Interrupting on raw audio energy instead was rejected deliberately: participants'
mics re-capture the bot's own TTS when browser AEC fails (see _SELF_ECHO_SIMILARITY
in bot.py), so an energy trigger would make the bot interrupt itself on every
response for anyone not wearing headphones. The confidence and min_volume gates are
what keep the bot's own voice, keyboards and room tone from counting as speech.
"""
import inspect
from collections.abc import Callable
from typing import Any

from loguru import logger

from pipecat.audio.vad.vad_analyzer import VADState


def speech_edge(prev: VADState, cur: VADState) -> str | None:
    """Classify a VAD state change. Returns "detected", "cleared", or None.

    "detected" is any move off QUIET, including a single call that jumps straight
    to SPEAKING — analyze_audio consumes several internal frames per call, so that
    happens, and it is still one onset.

    "cleared" is any return to QUIET, including from STARTING. A false start that
    never confirms still has to be retracted, or whoever was told about the onset
    goes on believing that person is talking.

    STARTING -> SPEAKING is not a new detection: it is the STT's segmentation
    signal, and the interruption already fired one edge earlier.
    """
    if prev == cur:
        return None
    if prev == VADState.QUIET:
        return "detected"
    if cur == VADState.QUIET:
        return "cleared"
    return None


class SpeechOnsetMixin:
    """Adds onset/offset reporting to a VADAnalyzer without changing its behaviour.

    Mixed in ahead of the analyzer base class so ``analyze_audio`` can observe the
    state on either side of the base call. The return value is passed through
    untouched — the STT chain reads it, and observing must not perturb it.

    Handlers take no arguments; the caller binds whatever identity it needs, which
    keeps this class ignorant of participants and therefore trivially testable.
    """

    _on_detected: Callable[[], Any] | None = None
    _on_cleared: Callable[[], Any] | None = None

    def set_onset_handlers(
        self,
        *,
        on_detected: Callable[[], Any] | None = None,
        on_cleared: Callable[[], Any] | None = None,
    ) -> None:
        self._on_detected = on_detected
        self._on_cleared = on_cleared

    async def analyze_audio(self, buffer: bytes) -> VADState:
        prev = self._vad_state
        state = await super().analyze_audio(buffer)
        edge = speech_edge(prev, state)
        if edge == "detected":
            await self._fire(self._on_detected, "detected")
        elif edge == "cleared":
            await self._fire(self._on_cleared, "cleared")
        return state

    async def _fire(self, cb: Callable[[], Any] | None, label: str) -> None:
        """Run a handler, awaiting it if it is async, and never let it escape.

        Awaited rather than fired-and-forgotten: the detected handler is what
        pushes the interruption, and that should happen before this audio frame's
        work continues. Guarded because this runs on the live audio path — a bad
        callback must cost an interruption, not the session.
        """
        if cb is None:
            return
        try:
            result = cb()
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.warning(f"SpeechOnsetVAD: {label} handler error: {e}")


def build_speech_onset_silero(params) -> Any:
    """A SileroVADAnalyzer that also reports the STARTING edge.

    Imported lazily so this module stays importable — and unit-testable — without
    pulling in onnxruntime.
    """
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    class SpeechOnsetSileroVAD(SpeechOnsetMixin, SileroVADAnalyzer):
        pass

    return SpeechOnsetSileroVAD(params=params)
