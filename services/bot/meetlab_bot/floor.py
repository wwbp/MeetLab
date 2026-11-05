"""Who may speak in push-to-talk and baton rooms: pure rules, times passed in.

Normal rooms have no Floor. Plan: docs/audio-modes-test-plan.md.

ptt: anyone may hold, at once; the bot's claim holds nothing, it only asks whether a person
is active. baton: one holder, a person or Floor.BOT; denied presses wait first-come, and
the next is granted when the holder releases, disconnects or goes silent.

The caller reports speech with heard(): VAD speech, not audio frames, or an open mic never
goes silent. That includes Floor.BOT's own audio, so a stuck reply cannot hold the room.
"""


class Floor:
    # Silence that ends a hold's claim on the room: the baton is taken, a ptt hold stops
    # blocking the bot. Speech in a hold has no limit.
    IDLE_SECS = 15.0

    # bot_ identities are bots everywhere (CLAUDE.md), so no person can collide with this.
    BOT = "bot_"

    def __init__(self, mode):
        if mode not in ("ptt", "baton"):
            raise ValueError(f"no Floor for audio mode {mode!r}")
        self._baton = mode == "baton"
        self._active = {}  # who: time last heard. ptt: everyone holding. baton: the holder.
        self._queue = []  # baton only; not empty only while someone holds
        self._granted = []  # taken from the queue, until tick() reports them

    @property
    def holder(self):
        """The baton's holder as of the last call with a time; always None in ptt."""
        return next(iter(self._active), None) if self._baton else None

    @property
    def waiting(self):
        """Someone was denied and still holds: the bot's cue to release after its sentence."""
        return bool(self._queue)

    def press(self, t, who):
        """True if who may speak now. False means who waits in line; tick() reports the grant."""
        self._expire(t)
        if who in self._active:
            return True
        if self._baton and self._active:
            if who not in self._queue:
                self._queue.append(who)
            return False
        self._active[who] = t
        return True

    def release(self, t, who):
        """who let go or left: out of the line, or off the floor. Grants the next."""
        if who in self._queue:
            self._queue.remove(who)
        elif self._active.pop(who, None) is not None:
            self._grant_next(t)

    disconnected = release

    def heard(self, t, who):
        """who spoke at t. Ignored unless who holds: only a hold has a silence clock."""
        self._expire(t)
        if who in self._active:
            self._active[who] = t

    def idle(self, t):
        """Nobody holds, or every hold has been silent for IDLE_SECS. Changes nothing."""
        return all(t - last >= self.IDLE_SECS for last in self._active.values())

    def bot_claim(self, t):
        """The check at the bot's first audio: False means drop the reply. People win ties."""
        if not self._baton:
            return self.idle(t)
        self._expire(t)
        if self._queue or self.holder not in (None, self.BOT):
            return False
        self._active[self.BOT] = t
        return True

    def tick(self, t):
        """Take the baton from a silent holder; return who was granted since the last tick.

        Call it after any event and on a timer: nothing else notices silence with nobody
        pressing. A press answered True is not listed here: its return value is the grant.
        """
        self._expire(t)
        granted, self._granted = self._granted, []
        return granted

    def _expire(self, t):
        if self._baton and self._active and self.idle(t):
            self._active.clear()
            self._grant_next(t)

    def _grant_next(self, t):
        if self._queue:
            who = self._queue.pop(0)
            self._active[who] = t
            self._granted.append(who)
