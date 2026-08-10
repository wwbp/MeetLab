"""Bot-over-user interruption: detection, measurement, and the yield signal.

The bot should yield when a user starts speaking — it should not talk over them.
Until 2026-08 this module only *measured* how badly it failed to (52 talk-over
events in the Jul/Aug pilot, up to 4743ms of the bot carrying on regardless).
``user_onset`` also returns whether the caller should interrupt, so the
"once per response" rule lives here rather than being re-derived by bot.py.
See docs/pilot-postmortem-2026-08.md (RC3).

Two windows, deliberately different
-----------------------------------
This tracker keeps them apart, because conflating them made interruption work
only intermittently in live testing:

**The audio window** — ``bot_started`` / ``bot_stopped``, driven by
BotStarted/StoppedSpeakingFrame. Pipecat's output transport declares the bot
stopped after ``BOT_VAD_STOP_SECS = 0.35`` of silence. With sentence-level TTS,
the pause while the next sentence is synthesised (TTS TTFB ~215ms plus
aggregation ~213ms) lands right around that threshold. This window is what
``talkover_ms`` is measured against — how long audio actually continued — so the
numbers stay comparable with the pilot.

**The response window** — opens on the first ``bot_started`` and closes on
``assistant_turn_stopped``. This is what gates *enforcement*. Using the audio
window instead meant an onset arriving in a >350ms inter-sentence gap was ignored
and the bot talked straight through the user; under 350ms it worked. That is a
coin flip, and it is what "sometimes it interrupts" felt like.

Signals in:

    bot_started(t)             BotStartedSpeakingFrame  — bot's TTS output began
    bot_stopped(t)             BotStoppedSpeakingFrame  — bot's TTS output ended
    assistant_turn_stopped(t)  the whole bot response finished
    user_onset(t, sid)         real user speech onset (per-participant VAD, via
                               MultiSpeakerSTT) — NOT the transcript-commit time

A *talk-over* is a user_onset inside an open response window. We count it once per
response and measure ``talkover_ms`` = how long the bot's audio kept going after
the user started (bot_stopped − first onset). Lower is better; a fast,
well-behaved bot yields almost immediately.

Times are monotonic seconds (the caller passes time.monotonic()). Emits two OTel
instruments unless record=False: meetlab.bot_interruptions_total and
meetlab.bot_talkover_ms.
"""
from loguru import logger


class InterruptionTracker:
    def __init__(self, *, record: bool = True, labels: dict | None = None):
        self._record = record
        self._labels = labels or {}
        # Audio is playing right now (may flicker between sentences).
        self._bot_audio_on = False
        # A bot response is in flight — spans inter-sentence gaps. Enforcement
        # is gated on this, not on the audio flag.
        self._response_open = False
        self._overlap_start: float | None = None
        # One interruption per response. Cleared only when the response ends, so a
        # sentence boundary mid-talk-over cannot re-arm it.
        self._interrupted_this_response = False
        self.interruptions = 0
        self.talkovers_ms: list[float] = []

    def bot_started(self, t: float) -> None:
        self._bot_audio_on = True
        # First audio of a response opens the response window. Later sentences in
        # the same response must not reset _overlap_start, or a talk-over already
        # in progress would be forgotten and could be counted twice.
        if not self._response_open:
            self._response_open = True
            self._overlap_start = None
            self._interrupted_this_response = False

    def bot_stopped(self, t: float) -> None:
        """Audio stopped. May be a gap between sentences, not the end of the turn."""
        if self._bot_audio_on and self._overlap_start is not None:
            ms = max(0.0, (t - self._overlap_start) * 1000.0)
            self.talkovers_ms.append(ms)
            logger.info(f"Talk-over: bot kept speaking {ms:.0f}ms after the user started")
            if self._record:
                self._emit_talkover(ms)
            # Measured once; keep the response window open so a later onset in
            # this same response is still recognised as talking over the bot.
            self._overlap_start = None
        self._bot_audio_on = False

    def assistant_turn_stopped(self, t: float) -> None:
        """The bot's whole response is finished — anything after this is normal."""
        self._response_open = False
        self._overlap_start = None
        self._interrupted_this_response = False

    def user_onset(self, t: float, sid: str | None = None) -> bool:
        """Record a user speech onset. Returns True if the bot should yield now.

        The return value is the enforcement signal. It is True once per bot
        *response* — including onsets that land in the gap between two sentences,
        which is where interruption used to be silently dropped. Later onsets in
        the same response return False: the bot is already being cancelled and
        re-interrupting would only churn the pipeline.

        False when no response is in flight, which is ordinary turn-taking.
        Interrupting there would cancel nothing and risks discarding the user's
        own in-progress turn.
        """
        if self._response_open and not self._interrupted_this_response:
            self._interrupted_this_response = True
            self._overlap_start = t
            self.interruptions += 1
            logger.info(f"Interruption: user {sid} started speaking while bot was talking")
            if self._record:
                self._emit_count(sid)
            return True
        return False

    def summary(self) -> dict:
        tk = self.talkovers_ms
        return {
            "interruptions": self.interruptions,
            "talkover_ms_max": max(tk) if tk else 0.0,
            "talkover_ms_avg": (sum(tk) / len(tk)) if tk else 0.0,
        }

    # ── metric emission (kept off the unit-test path via record=False) ────────
    def _emit_count(self, sid: str | None) -> None:
        try:
            import metrics as _prom
            _prom.bot_interruptions_total.add(1, self._labels)
        except Exception:
            pass

    def _emit_talkover(self, ms: float) -> None:
        try:
            import metrics as _prom
            _prom.bot_talkover_ms.record(ms, self._labels)
        except Exception:
            pass
