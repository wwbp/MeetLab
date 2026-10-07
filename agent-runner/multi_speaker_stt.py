"""Per-participant STT routing and speaker label injection for multi-speaker rooms.

The LiveKit transport already delivers UserAudioRawFrame(user_id=participant_sid)
for each participant's individual audio track. MultiSpeakerSTT exploits this by
routing each participant's frames to a dedicated STT instance, so TranscriptionFrames
are emitted with the exact LiveKit SID in user_id — no last-write-wins race.

SpeakerLabelInjector sits downstream and prepends "DisplayName: " to each
TranscriptionFrame's text before it reaches the LLM context aggregator, giving
the model group-conversation awareness ("Alice: what is X?").
Display names use the LiveKit token name field and strip the __randomPostfix
added by connection-details for uniqueness.

Usage in bot.py:
    from multi_speaker_stt import MultiSpeakerSTT, SpeakerLabelInjector

    def _stt_factory():
        return _build_stt(bot_config, openai_api_key, deepgram_api_key)

    multi_stt = MultiSpeakerSTT(_stt_factory)

    pipeline = Pipeline([
        transport.input(),
        multi_stt,
        _SpeakerTracker(),
        SpeakerLabelInjector(_sid_to_identity, _sid_to_name),
        context_aggregator.user(),
        ...
    ])

    # In on_participant_disconnected:
    await multi_stt.remove_participant(participant_id)
"""

import asyncio
import inspect
from collections.abc import Callable

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    SpeechControlParamsFrame,
    StartFrame,
    TranscriptionFrame,
    UserAudioRawFrame,
    UserSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup

from participant_workers import route_audio, worker_name


class _FrameCollector(FrameProcessor):
    """Captures all downstream frames from a per-participant STT into a shared queue.

    Overrides queue_frame to bypass Pipecat's internal priority-queue machinery
    so setup/start lifecycle is not needed — frames land directly in the shared
    asyncio.Queue consumed by MultiSpeakerSTT._pump_output.

    Pipeline lifecycle frames (StartFrame, EndFrame, CancelFrame) are filtered out
    since the main pipeline handles those independently. All other frames — including
    VAD frames such as UserStartedSpeakingFrame / UserStoppedSpeakingFrame — pass
    through so the context aggregator receives them.

    For STT backends without server-side VAD (e.g. Deepgram with endpointing),
    set needs_vad_wrap=True to emit UserStartedSpeakingFrame / UserStoppedSpeakingFrame
    around each final TranscriptionFrame so the context aggregator commits the turn.
    """

    def __init__(self, queue: asyncio.Queue, *, needs_vad_wrap: bool = False,
                 sid: str | None = None, on_speech_onset: Callable[[str], None] | None = None,
                 on_speech_offset: Callable[[str], None] | None = None,
                 verdict=None, open_turn_secs: float | None = None):
        super().__init__()
        self._queue = queue
        # Smart turn (smart_turn.py): this person's latest verdict, None when the room ends
        # turns on silence; and the closer for a turn they left unfinished.
        self._verdict = verdict
        self._closer = None
        if verdict is not None:
            from smart_turn import OPEN_TURN_SECS, TurnCloser
            self._closer = TurnCloser(queue, open_turn_secs or getattr(verdict, "wait_secs", OPEN_TURN_SECS))
        self._needs_vad_wrap = needs_vad_wrap
        self._sid = sid
        self._on_speech_onset = on_speech_onset
        self._on_speech_offset = on_speech_offset

    async def queue_frame(
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
        callback=None,
    ) -> None:
        if direction != FrameDirection.DOWNSTREAM:
            return
        if isinstance(frame, (StartFrame, EndFrame, CancelFrame)):
            return
        if self._needs_vad_wrap and isinstance(
            frame,
            (VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame, UserSpeakingFrame, SpeechControlParamsFrame),
        ):
            # Raw VAD frames from an in-chain VADProcessor (local Whisper path).
            # The synthetic sandwich below is the single source of VAD events for
            # the main pipeline — letting these through would double-fire the
            # latency observer and reconfigure the aggregator's speech params.
            # But VADUserStartedSpeakingFrame is the REAL user speech onset, so
            # surface it to the interruption tracker (talk-over detection) before
            # dropping it from the main pipeline.
            if isinstance(frame, VADUserStoppedSpeakingFrame) and self._on_speech_offset:
                # Clears this speaker from the interruption tracker's
                # currently-talking set. Bookkeeping only — it never interrupts.
                try:
                    result = self._on_speech_offset(self._sid)
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:
                    logger.warning(f"_FrameCollector: on_speech_offset error: {e}")
            if isinstance(frame, VADUserStartedSpeakingFrame) and self._on_speech_onset:
                try:
                    # Awaited rather than fire-and-forget: the handler pushes the
                    # InterruptionFrame that cancels bot output, and that should
                    # happen before this onset's transcript work continues.
                    # Sync callbacks stay supported.
                    result = self._on_speech_onset(self._sid)
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:
                    logger.warning(f"_FrameCollector: on_speech_onset error: {e}")
            return
        if self._needs_vad_wrap and isinstance(frame, TranscriptionFrame) and frame.finalized:
            # VADUser* frames must arrive at the observer BEFORE UserStoppedSpeakingFrame
            # triggers the context aggregator commit (which starts the LLM/TTS chain).
            # Placing VADUserStoppedSpeakingFrame here ensures UserBotLatencyObserver
            # has _user_stopped_time set before BotStartedSpeakingFrame fires.
            # stop_secs=0.0: clock starts now (post-endpointing), so e2e_ms ≈ LLM+TTS.
            from smart_turn import turn_frames
            complete = self._verdict is None or self._verdict.complete
            if self._closer:
                self._closer.cancel()  # they spoke again: the open turn continues
            for f in turn_frames(frame, complete):
                await self._queue.put(f)
            if not complete:
                self._closer.open()
        else:
            await self._queue.put(frame)


class MultiSpeakerSTT(FrameProcessor):
    """Routes per-participant audio to dedicated STT instances.

    The LiveKit transport delivers UserAudioRawFrame(user_id=participant_sid) for
    each participant's audio track. This processor routes each such frame to a
    dedicated STT service for that participant, so TranscriptionFrames carry the
    correct user_id without the last-write-wins race in a shared STT instance.

    Per-participant STT instances are created lazily on the first audio frame from
    a new participant and torn down via remove_participant() when they disconnect.

    All output frames from per-participant STTs are merged into a single shared
    asyncio.Queue and pumped back into the main pipeline downstream.
    """

    def __init__(self, stt_factory: Callable[[], FrameProcessor],
                 on_speech_onset: Callable[[str], None] | None = None,
                 on_speech_offset: Callable[[str], None] | None = None,
                 cap: int | None = None):
        super().__init__()
        self._stt_factory = stt_factory
        self._on_speech_onset = on_speech_onset
        self._on_speech_offset = on_speech_offset
        self._cap = cap
        self._stts: dict[str, FrameProcessor] = {}
        # Participants turned away at the cap. Held so the refusal is logged and
        # counted once per person rather than once per 20ms audio frame.
        self.refused: set[str] = set()
        self._output_queue: asyncio.Queue = asyncio.Queue()
        self._pump_task: asyncio.Task | None = None
        self._setup_params: FrameProcessorSetup | None = None
        self._start_frame: StartFrame | None = None

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def setup(self, setup: FrameProcessorSetup) -> None:
        await super().setup(setup)
        self._setup_params = setup

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._start_frame = frame
            self._pump_task = self.create_task(self._pump_output(), name="pump_output")
            await self.push_frame(frame, direction)

        elif isinstance(frame, (EndFrame, CancelFrame)):
            if self._pump_task:
                await self.cancel_task(self._pump_task)
                self._pump_task = None
            for sid, stt in list(self._stts.items()):
                try:
                    await stt.process_frame(frame, direction)
                except Exception as e:
                    logger.warning(f"MultiSpeakerSTT: error stopping STT for {sid}: {e}")
            self._stts.clear()
            await self.push_frame(frame, direction)

        elif direction == FrameDirection.DOWNSTREAM and isinstance(frame, UserAudioRawFrame):
            if frame.user_id:
                stt = await self._ensure_stt(frame.user_id)
                if stt is not None:
                    await stt.process_frame(frame, direction)
            # Audio consumed by per-participant STT; do not push downstream directly.
            # That includes refused audio: forwarding it would feed unattributed
            # speech into the shared context, which is the failure the cap exists
            # to avoid — worse than not hearing that person at all.

        else:
            await self.push_frame(frame, direction)

    def qsize(self) -> int:
        """Current depth of the merged output queue.

        Read by bot.py's spike logging to attribute extreme stt_ms to backlog
        (a stalled per-participant STT lets frames pile up here unboundedly).
        """
        return self._output_queue.qsize()

    async def remove_participant(self, sid: str) -> None:
        """Tear down the STT instance for a participant who left the room."""
        stt = self._stts.pop(sid, None)
        # Popped before anything can fail below, and the refusal latch cleared
        # regardless: a teardown that raises must not hold a slot forever, or one
        # bad participant shrinks the room for the rest of its life.
        self.refused.discard(sid)
        if stt is None:
            return
        try:
            await stt.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)
        except Exception as e:
            logger.warning(f"MultiSpeakerSTT: error removing participant {sid}: {e}")
        logger.info(f"MultiSpeakerSTT: removed STT for participant {sid}")

    # ── internal ────────────────────────────────────────────────────────────

    async def _ensure_stt(self, sid: str) -> FrameProcessor | None:
        """Return the per-participant STT entry point for sid, creating it if needed.

        The factory is called with the participant's sid so it can bind
        per-participant hooks (the VAD's speech-onset handlers, which drive
        interruption). It may return a single processor or a (head, tail) chain
        (e.g. VADProcessor → WhisperSTTService). Frames enter at the head;
        the collector is linked after the tail; lifecycle frames sent to the
        head propagate through the chain via the normal push machinery.

        Returns None when the room is at capacity and this participant is new.
        On 2026-08-19 an eight-room ramp accepted every participant, exhausted
        recognition throughput and quietly stopped replying — replies fell from
        261 to 23 while the processor sat at 77%. Nothing said no. Refusing is
        visible; degradation is not.
        """
        # Fast path: already admitted. A participant's audio arrives every 20ms, so
        # the admission policy is reserved for genuine new arrivals rather than
        # rebuilt and re-consulted on every frame of every speaker.
        if sid in self._stts:
            return self._stts[sid]

        # Admission is decided by pure policy in participant_workers so it can be
        # tested without a transport, a factory or a second of audio. That policy
        # speaks in worker names, not raw sids — passing sids here would make every
        # membership test miss, and the cap would then refuse people already in the
        # meeting instead of only new arrivals.
        decision = route_audio(
            sid, {worker_name(s) for s in self._stts}, cap=self._cap
        )
        if decision is None:
            if sid not in self.refused:
                self.refused.add(sid)
                logger.warning(
                    f"MultiSpeakerSTT: participant {sid} refused — at capacity "
                    f"({len(self._stts)} active). Their audio is not being transcribed."
                )
            return None

        # Admitted and new: build the chain. The membership guard that used to wrap
        # this is now the fast path above.
        chain = self._stt_factory(sid)
        # (head, tail), or (head, tail, verdict) when this person's turns end by smart turn.
        head, tail, verdict = (chain + (None,))[:3] if isinstance(chain, tuple) else (chain, chain, None)
        needs_vad_wrap = not _stt_emits_vad_frames(tail)
        collector = _FrameCollector(
            self._output_queue, needs_vad_wrap=needs_vad_wrap,
            sid=sid, on_speech_onset=self._on_speech_onset,
            on_speech_offset=self._on_speech_offset, verdict=verdict,
        )
        tail.link(collector)
        if self._setup_params is not None:
            # Every part, head to collector: a middle one (the smart-turn gate) left out raised
            # "TaskManager is not initialized" on its first frame (2026-10-07).
            p = head
            while p is not None:
                await p.setup(self._setup_params)
                p = p.next
        if self._start_frame is not None:
            await head.process_frame(self._start_frame, FrameDirection.DOWNSTREAM)
        self._stts[sid] = head
        logger.info(f"MultiSpeakerSTT: created STT for participant {sid} (vad_wrap={needs_vad_wrap})")
        return head

    async def _pump_output(self) -> None:
        """Forward frames from per-participant STT outputs to the main pipeline."""
        while True:
            try:
                frame = await self._output_queue.get()
                await self.push_frame(frame, FrameDirection.DOWNSTREAM)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"MultiSpeakerSTT: pump error: {e}")


def _stt_emits_vad_frames(stt: FrameProcessor) -> bool:
    """Return True if this STT service emits its own UserStarted/StoppedSpeakingFrames.

    OpenAI Realtime STT (server-side VAD) broadcasts these frames itself.
    Deepgram and most other STTs rely on external VAD input and do NOT emit them.
    """
    try:
        from pipecat.services.openai.stt import OpenAIRealtimeSTTService
        return isinstance(stt, OpenAIRealtimeSTTService)
    except ImportError:
        return False


class SpeakerLabelInjector(FrameProcessor):
    """Prepends "DisplayName: " to TranscriptionFrame text before the LLM sees it.

    Sits between MultiSpeakerSTT (or _SpeakerTracker) and context_aggregator.user()
    so the LLM context accumulates group-conversation-aware messages:
        [user] "Alice: what is the speed of light?"
        [assistant] "..."
        [user] "Bob: what is the boiling point of water?"

    Takes live references to the bot's _sid_to_identity and _sid_to_name dicts —
    no copy, so they always reflect the latest participant map.

    Display name resolution order:
      1. sid_to_name[sid]          — LiveKit token name field (clean, user-entered)
      2. identity.split('__')[0]   — strip the __randomPostfix added for uniqueness
    """

    def __init__(self, sid_to_identity: dict, sid_to_name: dict | None = None):
        super().__init__()
        self.sid_to_identity = sid_to_identity
        self.sid_to_name = sid_to_name

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame):
            identity = self.sid_to_identity.get(frame.user_id)
            if identity and frame.text:
                if self.sid_to_name is not None:
                    display = self.sid_to_name.get(frame.user_id) or identity.split("__")[0]
                else:
                    display = identity.split("__")[0]
                frame = TranscriptionFrame(
                    text=f"{display}: {frame.text}",
                    user_id=frame.user_id,
                    timestamp=frame.timestamp,
                    language=frame.language,
                    result=frame.result,
                    finalized=frame.finalized,
                )
                logger.debug(f"SpeakerLabelInjector: labeled → {frame.text[:80]}")
        await self.push_frame(frame, direction)
