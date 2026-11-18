"""How a timed session ends (docs/session-limits.md), as a pure clock-driven state machine.

At the limit people are muted, so nobody starts a new reply or cuts one off. The reply in
progress plays to its end (at most REPLY_MAX_S), the bot waits for a pause (at most PAUSE_MAX_S),
speaks the closing line, and grace_s after that line ends the room closes for everyone: closing
the room is what shows each participant their completion code.

bot.py feeds tick() once a second and acts on what it returns: "say_closing" or "close_room".
"""

REPLY_MAX_S = 30.0   # a reply still playing this long after the limit is given up on
PAUSE_MAX_S = 10.0   # a person still talking this long after the reply gets the closing anyway
CLOSING_START_S = 20.0  # a closing line with no audio by then is counted as over
CLOSING_MAX_S = 60.0    # a closing line still playing this long is counted as over
QUIET_S = 1.0           # the bot's audio pauses between sentences: this much quiet is the end


class SessionEnd:
    def __init__(self, *, grace_s: float, has_closing: bool = True):
        self._grace_s = grace_s
        self._has_closing = has_closing
        self._limit_at = None   # when the limit was reached
        self._free_at = None    # when the bot first went quiet after it
        self._said_at = None    # when the closing line was queued
        self._played = False    # its audio has started
        self._quiet_since = None  # its audio stopped here and hasn't restarted
        self._ended_at = None   # when it ended: the buffer runs from here
        self._closed = False

    @property
    def muted(self) -> bool:
        """People's words no longer reach the LLM: no new reply, no interruption."""
        return self._limit_at is not None

    @property
    def closing_heard(self) -> bool:
        """The closing line's audio played to its end, not only queued."""
        return self._played and self._ended_at is not None

    def limit_reached(self, t: float) -> None:
        if self._limit_at is None:
            self._limit_at = t

    def tick(self, t: float, *, bot_busy: bool, someone_talking: bool) -> str | None:
        if self._limit_at is None or self._closed:
            return None
        if self._ended_at is None and self._said_at is None:
            if t - self._limit_at < REPLY_MAX_S:
                if bot_busy:
                    return None
                if self._free_at is None:
                    self._free_at = t
                if someone_talking and t - self._free_at < PAUSE_MAX_S:
                    return None
            if not self._has_closing:
                self._ended_at = t
            else:
                self._said_at = t
                return "say_closing"
        if self._ended_at is None:
            self._played |= bot_busy
            since = t - self._said_at
            if bot_busy or not self._played:
                self._quiet_since = None
            elif self._quiet_since is None:
                self._quiet_since = t
            if self._quiet_since is not None and t - self._quiet_since >= QUIET_S:
                self._ended_at = self._quiet_since
            elif since >= CLOSING_MAX_S or (not self._played and since >= CLOSING_START_S):
                self._ended_at = t
            else:
                return None
        if t - self._ended_at >= self._grace_s:
            self._closed = True
            return "close_room"
        return None


class PlaybackClock:
    """When the bot's audio is really over. Speech is synthesized faster than it plays, so
    Pipecat's "bot stopped speaking" can come seconds early; the audio sent says how long."""

    def __init__(self):
        self._until = 0.0

    def sent(self, t: float, seconds: float) -> None:
        self._until = max(self._until, t) + seconds

    def cut(self, t: float) -> None:
        """An interruption dropped whatever was still queued."""
        self._until = min(self._until, t)

    def playing(self, t: float) -> bool:
        return t < self._until
