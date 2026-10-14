"""Bot-over-user interruption: detection, measurement, and the yield signal.

The bot should yield when a user starts speaking — it should not talk over them.
Until 2026-08 this module only *measured* how badly it failed to (52 talk-over
events in the Jul/Aug pilot, up to 4743ms of the bot carrying on regardless).
``user_onset`` also returns whether the caller should interrupt, so the
"once per response" rule lives here rather than being re-derived by bot.py.
See v1.0.0:docs/pilot-postmortem-2026-08.md (RC3).

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

Times are monotonic seconds (the caller passes time.monotonic()).
"""
import os

from loguru import logger

# Freshness bound on a speech onset that has no matching stop yet.
#
# user_offset is the primary mechanism — a speaker is normally cleared the moment
# their VAD stop arrives. This bound only decides how long a *lost* stop keeps
# someone marked as talking, and the asymmetry is stark: too short and we miss an
# interruption the next onset would catch anyway; too long and one dropped frame
# makes the bot yield on every response, silencing it for the session.
#
# 3s covers the case this exists for — the LLM-generation and TTS-synthesis window
# between a user's onset and the bot's first audio. An onset older than that is
# not something the bot is about to talk over; it is a stop frame we never saw.
_SPEAKING_STALE_SECS = 3.0

# Audio the bot is guaranteed to get out before anything may cancel it.
#
# Added after the 2026-08-19 09:00 multi-person sync, where the bot managed 12
# audible responses against 22 yields and its greeting was cancelled 244ms in. In a
# group meeting people talk to each other continuously, so an unconditional yield
# means the bot is never audible at all — and because a cancelled response never
# lands in the LLM context, the model kept regenerating the same question.
#
# The floor is deliberately short: long enough for a phrase, short enough that
# talking over someone is brief. Each sentence boundary re-checks whether they are
# still going (see bot_started), so this buys the bot a phrase, not a monologue.
# Tunable without a deploy — set INTERRUPT_MIN_BOT_SPEECH_MS as an EB env property.
_MIN_BOT_SPEECH_MS = int(os.getenv("INTERRUPT_MIN_BOT_SPEECH_MS", "600"))


class InterruptionTracker:
    def __init__(
        self,
        *,
        min_bot_speech_ms: int | None = None,
    ):
        self._min_bot_speech_ms = (
            _MIN_BOT_SPEECH_MS if min_bot_speech_ms is None else min_bot_speech_ms
        )
        # When this response's audio first started, or None if it has not. Doubles
        # as "is there anything to interrupt yet".
        self._response_audio_start: float | None = None
        # Audio is playing right now (may flicker between sentences).
        self._bot_audio_on = False
        # A bot response is in flight — spans inter-sentence gaps. Enforcement
        # is gated on this, not on the audio flag.
        self._response_open = False
        self._overlap_start: float | None = None
        # One interruption per response. Cleared only when the response ends, so a
        # sentence boundary mid-talk-over cannot re-arm it.
        self._interrupted_this_response = False
        # sid -> onset time for everyone currently mid-utterance. Needed because
        # onset is edge-triggered: without it the bot cannot tell, at the moment
        # it starts speaking, that someone is already talking.
        self._speaking: dict[str, float] = {}
        self.interruptions = 0
        self.talkovers_ms: list[float] = []

    def _open_response(self) -> None:
        """Open the response window, if it isn't already.

        Later sentences in the same response must not reset _overlap_start, or a
        talk-over already in progress would be forgotten and could be counted twice.
        """
        if not self._response_open:
            self._response_open = True
            self._overlap_start = None
            self._interrupted_this_response = False

    def _arm(self, t: float, sid: str | None) -> bool:
        """Mark this response as interrupted. True the first time only.

        Both enforcement paths funnel through here so "once per response" is one
        rule in one place, whether the trigger was a user starting to speak or the
        bot starting to speak over someone.
        """
        if not self._response_open or self._interrupted_this_response:
            return False
        self._interrupted_this_response = True
        self._overlap_start = t
        self.interruptions += 1
        return True

    def _current_speaker(self, t: float) -> str | None:
        """Someone who is talking right now, or None.

        Onsets older than _SPEAKING_STALE_SECS are treated as a lost stop frame
        rather than a very long turn. Without that guard a single dropped
        VADUserStoppedSpeakingFrame would make the bot yield on every subsequent
        response — silence for the rest of the session, which is a worse failure
        than the talk-over this is here to prevent.
        """
        for sid, started in self._speaking.items():
            # Lower bound as well as upper: an onset stamped *after* t is not
            # someone talking early, it is a clock the caller did not share with
            # us, and treating it as live would burn this response's single
            # interruption on a speaker who may not be talking at all.
            if 0.0 <= t - started <= _SPEAKING_STALE_SECS:
                return sid
        return None

    def _floor_spent(self, t: float) -> bool:
        """Has the bot earned the right to be interrupted yet?

        False while no audio has played (nothing to cancel, and cancelling would
        throw away a whole response for free) and during the first
        _min_bot_speech_ms of it.
        """
        if self._response_audio_start is None:
            return False
        return (t - self._response_audio_start) * 1000.0 >= self._min_bot_speech_ms

    def bot_started(self, t: float) -> bool:
        """Audio started — for this response or just the next sentence.

        Returns True if the bot must yield immediately: someone is still talking
        and the floor is spent. Fires per sentence, which is what makes the floor
        safe — the bot gets a phrase out, then checks again rather than ploughing
        through the whole response.
        """
        self._bot_audio_on = True
        self._open_response()
        if self._response_audio_start is None:
            self._response_audio_start = t
        if not self._floor_spent(t):
            return False
        speaker = self._current_speaker(t)
        if speaker is not None and self._arm(t, speaker):
            logger.info(
                f"Interruption: user {speaker} still speaking at a sentence "
                f"boundary — yielding"
            )
            return True
        return False

    def bot_stopped(self, t: float) -> None:
        """Audio stopped. May be a gap between sentences, not the end of the turn."""
        if self._bot_audio_on and self._overlap_start is not None:
            ms = max(0.0, (t - self._overlap_start) * 1000.0)
            self.talkovers_ms.append(ms)
            logger.info(f"Talk-over: bot kept speaking {ms:.0f}ms after the user started")
            # Measured once; keep the response window open so a later onset in
            # this same response is still recognised as talking over the bot.
            self._overlap_start = None
        self._bot_audio_on = False

    def idle(self, t: float) -> bool:
        """Nobody is talking — not the bot, not a participant.

        The one moment it is safe to say something unprompted, since anything the
        bot starts while a participant is mid-sentence gets interrupted away.
        """
        return not self._bot_audio_on and self._current_speaker(t) is None

    def assistant_turn_stopped(self, t: float) -> None:
        """The bot's whole response is finished — anything after this is normal."""
        self._response_open = False
        self._overlap_start = None
        self._interrupted_this_response = False
        self._response_audio_start = None

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
        if sid is not None:
            self._speaking[sid] = t
        if not self._floor_spent(t):
            # Either the bot has not made a sound yet, or it is inside the floor.
            # Recorded as speaking above, so bot_started can still act on it.
            return False
        if self._arm(t, sid):
            logger.info(f"Interruption: user {sid} started speaking while bot was talking")
            return True
        return False

    def user_offset(self, t: float, sid: str | None = None) -> None:
        """A user stopped speaking. Clears them from the currently-talking set.

        Only bookkeeping for bot_started's level-triggered check — it never
        interrupts anything itself.
        """
        if sid is not None:
            self._speaking.pop(sid, None)

    def summary(self) -> dict:
        tk = self.talkovers_ms
        return {
            "interruptions": self.interruptions,
            "talkover_ms_max": max(tk) if tk else 0.0,
            "talkover_ms_avg": (sum(tk) / len(tk)) if tk else 0.0,
        }
