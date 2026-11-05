"""What a ptt or baton room does with floor.py's rules: LiveKit signals in, gates on the audio,
the event log and the bot's attributes out. Normal rooms build none of it.

In from the room:
    ptt     a person's mic unmuted or muted (or first published) is a press or a release.
    baton   reliable data on topic meetlab.floor, {"type":"claim"} or {"type":"release"}. Identity
            comes from the LiveKit sender. Anything else is ignored.
    both    heard() and quiet() from each person's VAD, never from audio frames: an open mic in
            a quiet room must go silent.

Three taps in the pipeline:
    mic_gate    after the recorder, before STT. Baton: only the holder's audio goes on, plus a
                release tail. Both modes: whoever is mid-speech when their hold ends then gets a
                burst of silence, so their VAD closes the turn at once. With no frames reaching it,
                Pipecat's 1.0 s forced stop would close it late, warn, and leave the analyzer in
                SPEAKING, so their next onset would never fire.
    reply_gate  after TTS. The bot's press: a check at its first audio. Blocked, the reply is
                cancelled and runs again once the room is free, so a turn isn't left unanswered.
    played      after the output transport, where words arrive as they are heard. Baton: someone
                waiting takes the floor at the first word after a sentence end.

Out to the room (baton): bot attributes floor.holder and floor.queue. Nothing here waits on them,
or on the event log. The bot never touches anyone's publish permissions.

heard() needs the VAD in a person's own chain: Parakeet's, production's. Other STT models report no
speech, so a hold in those rooms times out after 15 s however much is said.
"""
import asyncio
import json
import time

from livekit import rtc
from loguru import logger
from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    InterruptionTaskFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from meetlab_bot.floor import Floor
from meetlab_core.event_log import record_event

TOPIC = "meetlab.floor"
# track_muted arrives 52-90 ms before the last audio (last seen at 121 ms; local LiveKit, n=60).
RELEASE_TAIL_SECS = 0.2
# A blocked reply waits this long after the room frees: the last holder's words may yet be a turn.
REPLY_AGAIN_AFTER_SECS = 1.5
# Past a VAD's stop_secs, in the silence that closes someone's turn.
_SILENCE_MARGIN_SECS = 0.1
PUBLISH_TIMEOUT_SECS = 2.0
TICK_SECS = 0.05
_SENTENCE_ENDS = (".", "!", "?")


class _Tap(FrameProcessor):
    """Passes each downstream frame to keep(); a frame it refuses stops here."""

    def __init__(self, owner, keep, name):
        super().__init__(name=name)
        self.owner, self._keep = owner, keep

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM or await self._keep(frame):
            await self.push_frame(frame, direction)


class FloorRoom:
    def __init__(self, mode, *, bot_identity, room_name, conv_id, enqueue, vad_stop_secs, now):
        self._floor = Floor(mode)
        self._baton = mode == "baton"
        self._bot_identity, self._room_name, self._conv_id = bot_identity, room_name, conv_id
        self._enqueue, self._silence_secs, self._now = enqueue, vad_stop_secs + _SILENCE_MARGIN_SECS, now
        self.mic_gate = _Tap(self, self._mic_frame, "FloorMicGate")
        self.reply_gate = _Tap(self, self._reply_frame, "FloorReplyGate")
        self.played = _Tap(self, self._played_frame, "FloorPlayed")

        self._room = None
        self._tasks = set()  # event writes, held so they aren't collected mid-flight
        self._changed = asyncio.Event()
        self._published = (None, [])

        self._released = {}  # who: when their hold ended, until the release tail is over
        self._formats = {}  # who: (sample_rate, num_channels) of their audio
        self._voiced = set()  # who the VAD has heard start and not yet stop

        self._reply_ok = None  # this reply's first-audio check: None until it has been made
        self._line = None  # the fixed line last queued, until its audio plays
        self._line_again = None  # a blocked line, to say once the room is free
        self._reply_again = False  # a blocked reply, to run once the room is free
        self._free_since = None
        self._sentence_ended = False

    # In from the room.

    def attach(self, room):
        """rtc.Room's handlers must be sync: everything they reach only schedules its waiting."""
        self._room = room
        if self._baton:
            room.on("data_received")(self._on_data)
            return
        room.on("track_unmuted")(lambda person, publication: self._on_mic(person, publication, True))
        room.on("track_muted")(lambda person, publication: self._on_mic(person, publication, False))
        # A mic first enabled after PreJoin arrives published and unmuted: no track_unmuted comes.
        room.on("track_published")(lambda publication, person: self._on_mic(person, publication, not publication.muted))
        room.on("track_unpublished")(lambda publication, person: self._on_mic(person, publication, False))
        for person in room.remote_participants.values():
            for publication in person.track_publications.values():
                if not publication.muted:
                    self._on_mic(person, publication, True)

    def _on_mic(self, person, publication, pressed):
        if publication.source != rtc.TrackSource.SOURCE_MICROPHONE:
            return
        (self.press if pressed else self.release)(person.identity)

    def _on_data(self, packet):
        if packet.topic != TOPIC or packet.participant is None:
            return
        try:
            kind = json.loads(packet.data).get("type")
        except (ValueError, AttributeError):
            return
        if kind == "claim":
            self.press(packet.participant.identity)
        elif kind == "release":
            self.release(packet.participant.identity)

    def press(self, who):
        if self._floor.holds(who) or who in self._floor.queue:
            return  # key repeat
        t = self._now()
        self._released.pop(who, None)
        granted = self._floor.press(t, who)
        if self._baton:
            self._log("claim", who)
            self._log("grant" if granted else "deny", who)
        else:
            self._log("press", who)
        self._sync(t)

    def release(self, who):
        held = self._floor.holds(who)
        if not held and who not in self._floor.queue:
            return
        t = self._now()
        self._floor.release(t, who)
        if held:
            self._released[who] = t
        self._log("release", who)
        self._sync(t)

    def disconnected(self, who):
        self.release(who)
        self._released.pop(who, None)
        self._formats.pop(who, None)
        self._voiced.discard(who)

    def heard(self, who):
        self._voiced.add(who)
        self._floor.heard(self._now(), who)

    def quiet(self, who):
        self._voiced.discard(who)

    def saying(self, line):
        """say() is queueing a fixed line: if it gets blocked, it is this that runs again."""
        self._line = line

    # The taps.

    async def _mic_frame(self, frame):
        if not isinstance(frame, UserAudioRawFrame):
            return True
        who = frame.user_id
        self._formats[who] = (frame.sample_rate, frame.num_channels)
        if not self._baton or self._floor.holder == who:
            return True
        released = self._released.get(who)
        return released is not None and self._now() - released < RELEASE_TAIL_SECS

    async def _reply_frame(self, frame):
        if isinstance(frame, TTSAudioRawFrame):
            return await self._may_play(frame)
        if isinstance(frame, (TTSStoppedFrame, InterruptionFrame)):
            self._reply_ok = None
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._reply_again = False  # a newer reply answers the turn
        return True

    async def _may_play(self, frame):
        if self._reply_ok is False:
            return False
        # Only the first audio is checked, or a press would cut a sentence in two. Baton's next
        # sentence checks again only if the bot let go in a gap between sentences.
        if self._reply_ok and (not self._baton or self._floor.holder == Floor.BOT):
            return True
        t = self._now()
        first = self._reply_ok is None
        self._reply_ok = self._floor.bot_claim(t)
        if self._reply_ok:
            self._line = None
        else:
            if first and self._line:
                self._line_again, self._line = self._line, None
            elif first:
                self._reply_again = True
        self._sync(t)
        if not self._reply_ok:
            await self._cancel_reply()  # last: the interruption it causes cancels this very call
        return self._reply_ok

    async def _played_frame(self, frame):
        t = self._now()
        if isinstance(frame, BotSpeakingFrame):
            self._floor.heard(t, Floor.BOT)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._sentence_ended = False
            self._floor.release(t, Floor.BOT)
            self._sync(t)
        elif isinstance(frame, TTSTextFrame):
            if self._sentence_ended and self._floor.waiting and self._floor.holder == Floor.BOT:
                self._sentence_ended = False
                self._floor.release(t, Floor.BOT)
                self._sync(t)
                await self._cancel_reply()
                return False  # this word's audio is what gets cut, so it wasn't said
            self._sentence_ended = frame.text.rstrip().endswith(_SENTENCE_ENDS)
        return True

    async def _cancel_reply(self):
        await self._enqueue(InterruptionTaskFrame(), FrameDirection.UPSTREAM)

    # The timer.

    async def run(self):
        await asyncio.gather(self._tick_forever(), self._publish_forever())

    async def _tick_forever(self):
        while True:
            await asyncio.sleep(TICK_SECS)
            try:
                await self.tick(self._now())
            except Exception as e:
                logger.warning(f"floor tick failed: {e}")

    async def tick(self, t):
        self._sync(t)
        for who, released in list(self._released.items()):
            if t - released >= RELEASE_TAIL_SECS:
                del self._released[who]
                await self._close_turn(who)
        await self._run_again(t)

    async def _close_turn(self, who):
        if who not in self._voiced or who not in self._formats:
            return
        rate, channels = self._formats[who]
        silence = bytes(int(rate * self._silence_secs) * channels * 2)
        await self.mic_gate.push_frame(UserAudioRawFrame(silence, rate, channels, who))

    async def _run_again(self, t):
        if not (self._line_again or self._reply_again):
            return
        if not self._floor.idle(t) or self._floor.waiting:
            self._free_since = None
            return
        if self._free_since is None:
            self._free_since = t
        if t - self._free_since < REPLY_AGAIN_AFTER_SECS:
            return
        self._free_since = None
        if self._line_again:
            self._line, self._line_again = self._line_again, None
            await self._enqueue(TTSSpeakFrame(self._line))
        if self._reply_again:
            self._reply_again = False
            await self._enqueue(LLMRunFrame())

    # Out to the room.

    def _sync(self, t):
        """After any change: report what it led to, without waiting on anyone."""
        for who in self._floor.timeouts(t):
            self._log("timeout", who)
        for who in self._floor.tick(t):
            self._log("grant", who)
        state = (self._floor.holder, self._floor.queue)
        if state != self._published:  # never in ptt: no holder, no line
            self._published = state
            self._changed.set()

    def _log(self, event, who):
        task = asyncio.create_task(record_event(
            f"floor.{event}", room_name=self._room_name, conv_id=self._conv_id,
            payload={"who": self._identity(who), "at": time.time()}))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _identity(self, who):
        return self._bot_identity if who == Floor.BOT else who

    async def _publish_forever(self):
        """One publish at a time, always the latest state, so a slow one can't overwrite a newer."""
        while True:
            await self._changed.wait()
            self._changed.clear()
            holder, queue = self._published
            try:
                await asyncio.wait_for(self._room.local_participant.set_attributes({
                    "floor.holder": self._identity(holder) if holder else "",
                    "floor.queue": ",".join(queue),
                }), PUBLISH_TIMEOUT_SECS)
            except Exception as e:
                # The next change publishes the whole state again.
                logger.warning(f"could not publish the floor: {e!r}")
