"""Smart turn, per participant: each person's own Smart Turn v3 (Pipecat's LocalSmartTurnAnalyzerV3,
a small CPU model bundled with Pipecat) judges at every pause whether they have finished.

Pipecat's usual setup puts the analyser in the user aggregator, which must then see the audio.
Ours never does: MultiSpeakerSTT gives each person their own VAD → STT chain and consumes the
audio there, so speakers stay apart. So the analyser goes into each person's chain (SmartTurnGate,
between their VAD and STT) and leaves a verdict the chain's collector reads when that person's
transcript arrives: finished → the turn ends as before; unfinished → the turn stays open for their
next words, closed by TurnCloser if they never continue.

Chosen per room in Bot Config (turn_detection = "smart_turn"); the default ends a turn after a
fixed silence, which split ~19% of sentences at a mid-sentence pause (2026-10-04).
"""
import asyncio
from dataclasses import dataclass

from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
from pipecat.frames.frames import (
    InputAudioRawFrame, StartFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# How long an unfinished turn waits for more words: Pipecat's Smart Turn default (stop_secs).
OPEN_TURN_SECS = 3.0


def effective_turn_detection(turn_detection: str, stt_model: str) -> str:
    """The rule a room really gets: smart turn needs a per-speaker segmenting recogniser
    (Parakeet, Whisper); Deepgram and OpenAI segment for themselves, so those rooms end
    turns on silence (the bot says so in its log)."""
    segmented = stt_model.startswith(("parakeet-", "whisper-"))
    return "smart_turn" if turn_detection == "smart_turn" and segmented else "silence"


@dataclass
class TurnVerdict:
    """A person's latest smart-turn verdict, written by their gate, read by their collector."""
    complete: bool = True


def turn_frames(transcript, complete: bool) -> list:
    """The frames a person's finished transcript becomes. Finished: the turn ends (the VAD stop
    first, so latency is measured from it). Unfinished: no stop, the turn stays open."""
    if complete:
        return [VADUserStartedSpeakingFrame(), VADUserStoppedSpeakingFrame(stop_secs=0.0),
                UserStartedSpeakingFrame(), transcript, UserStoppedSpeakingFrame()]
    return [VADUserStartedSpeakingFrame(), UserStartedSpeakingFrame(), transcript]


def closing_frames() -> list:
    return [VADUserStoppedSpeakingFrame(stop_secs=0.0), UserStoppedSpeakingFrame()]


class SmartTurnListener:
    """Feeds one person's audio to their analyser and records its verdict at each pause."""

    def __init__(self, verdict: TurnVerdict, analyzer=None):
        if analyzer is None:
            from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
            analyzer = LocalSmartTurnAnalyzerV3()
        self.verdict, self._analyzer, self._speaking = verdict, analyzer, False

    async def on_frame(self, frame) -> None:
        if isinstance(frame, StartFrame):
            self._analyzer.set_sample_rate(frame.audio_in_sample_rate)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._speaking = True
        elif isinstance(frame, InputAudioRawFrame):
            self._analyzer.append_audio(frame.audio, self._speaking)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speaking = False
            state, _ = await self._analyzer.analyze_end_of_turn()
            self.verdict.complete = state == EndOfTurnState.COMPLETE
            if self.verdict.complete:
                self._analyzer.clear()


class SmartTurnGate(FrameProcessor):
    """SmartTurnListener in a person's chain, between their VAD and their STT; frames pass on."""

    def __init__(self, verdict: TurnVerdict, analyzer=None):
        super().__init__()
        self._listener = SmartTurnListener(verdict, analyzer)

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            await self._listener.on_frame(frame)
        await self.push_frame(frame, direction)


class TurnCloser:
    """Closes a person's open turn if they say nothing more within OPEN_TURN_SECS."""

    def __init__(self, queue: asyncio.Queue, wait_secs: float = OPEN_TURN_SECS):
        self._queue, self._wait, self._task = queue, wait_secs, None

    def open(self) -> None:
        self.cancel()
        self._task = asyncio.ensure_future(self._close_later())

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _close_later(self) -> None:
        await asyncio.sleep(self._wait)
        for f in closing_frames():
            await self._queue.put(f)
