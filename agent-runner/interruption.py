"""Bot-over-user interruption tracking.

The bot should yield when a user starts speaking — it should not talk over them.
This tracker turns three pipeline signals into numbers:

    bot_started(t)        BotStartedSpeakingFrame  — bot's TTS output began
    bot_stopped(t)        BotStoppedSpeakingFrame  — bot's TTS output ended
    user_onset(t, sid)    real user speech onset (per-participant VAD, via
                          MultiSpeakerSTT) — NOT the transcript-commit time

A *talk-over* is a user_onset that lands inside an open bot-speaking window. We
count it once per window and measure `talkover_ms` = how long the bot kept going
after the user started (bot_stopped − first onset). Lower is better; a fast,
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
        self._bot_speaking = False
        self._overlap_start: float | None = None
        self.interruptions = 0
        self.talkovers_ms: list[float] = []

    def bot_started(self, t: float) -> None:
        self._bot_speaking = True
        self._overlap_start = None

    def user_onset(self, t: float, sid: str | None = None) -> None:
        # Count once per bot-speaking window: the first onset opens the talk-over.
        if self._bot_speaking and self._overlap_start is None:
            self._overlap_start = t
            self.interruptions += 1
            logger.info(f"Interruption: user {sid} started speaking while bot was talking")
            if self._record:
                self._emit_count(sid)

    def bot_stopped(self, t: float) -> None:
        if self._bot_speaking and self._overlap_start is not None:
            ms = max(0.0, (t - self._overlap_start) * 1000.0)
            self.talkovers_ms.append(ms)
            logger.info(f"Talk-over: bot kept speaking {ms:.0f}ms after the user started")
            if self._record:
                self._emit_talkover(ms)
        self._bot_speaking = False
        self._overlap_start = None

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
