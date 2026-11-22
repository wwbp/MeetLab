"""A turn's latency the way Pipecat's voice-agent metrics count it, for rooms where each person
has their own speech-to-text: time to first byte of speech-to-text (they stopped speaking ->
their transcript) and user-bot latency (they stopped speaking -> the bot starts speaking).

"Stopped speaking" is each person's own voice-activity stop, so both include no silence wait
(`stt_endpointing_ms` comes before it). bot.py feeds the three moments and stores what it gets.
"""


class TurnLatency:
    def __init__(self):
        self._stopped: dict[str, float] = {}  # per person: stopped speaking, transcript not yet in
        self._heard_at: float | None = None   # the last person heard this turn stopped speaking here

    def speech_stopped(self, sid: str, t: float) -> None:
        self._stopped[sid] = t

    def transcript(self, sid: str, t: float) -> float | None:
        """Speech-to-text time for this transcript in ms; None for words nobody spoke (chat)."""
        stopped = self._stopped.pop(sid, None)
        if stopped is None:
            return None
        self._heard_at = stopped
        return (t - stopped) * 1000

    def bot_started(self, t: float) -> float | None:
        """User-bot latency in ms at the reply's first audio; None when nothing was heard first."""
        heard, self._heard_at = self._heard_at, None
        return None if heard is None else (t - heard) * 1000
