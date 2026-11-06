"""The audio modes' live driver: real bots in a local room, simulated people, sim_modes.py's verdict.

    make sim-modes MODE=ptt SCENARIO=turn_taking PEOPLE=2 [REPEAT=0]    one run
    make sim-modes-matrix COST_OK=<usd>                                 every run; stops at the budget

PAID: every run calls the real LLM and voice. Never run it unasked (docs/audio-modes-test-plan.md).

The modes are closed to real rooms (runner.py:_AUDIO_MODES_BUILT), so this writes the room's row in
the database itself, and refuses any stack but the local one (refuse_unless_local). Normal rooms
get no row: they run on the global settings, as today.

Everything down to `# Live` is standard library and runs in `make sim-modes-check`, in virtual time.
"""
import array
import asyncio
import json
import math
import os
import sys
import time
import urllib.request
import wave
from collections import deque
from functools import lru_cache
from urllib.parse import urlsplit
from uuid import uuid4

from meetlab_loadtest.conversation_script import CONVERSATIONS
from meetlab_loadtest.generate_conversation_audio import FIXTURES, fixture_name
from meetlab_loadtest.sim_modes import LONG_HOLD_S, SCENARIOS, hold_timing, report, row, run_list

SPEAKERS = "abc"
RATE = 24000                # the fixtures'
TRIM_LEVEL, TRIM_PAD_S = 300, .05
PAUSE_LEVEL, PAUSE_S = 800, .3
LONG_LINE_S = LONG_HOLD_S + 3   # a slip can shorten a line by up to 450 ms

GAP_S = .3                  # bot audio this far apart is one span: frames, not sentences
QUIET_S = 1.5               # silence after which the bot has finished talking
GREETING_LIMIT_S = 60
REPLY_LIMIT_S = 20          # a turn with no bot audio by then is unanswered; the run moves on
WALKED_AWAY_REPLY_LIMIT_S = 30   # the bot may wait out the 15 s idle first
SPOKEN_LIMIT_S = 60
GRANT_LIMIT_S = 30          # above the 15 s a walked-away holder blocks the next person
TURNS = 6
OVERLAP_OFFSET_S = .15      # the plan's
FIRST_PRESS_S = .4          # room for the longest pre-roll before the first overlapping line
INTERRUPT_AFTER_S = 1.0     # past the first 600 ms, a hazard of its own (plan, Hazards found)
GENERATING_AFTER_S = .85    # with pre-rolls of 150-350 ms, a press in the bot's ~1.2 s of generation
LATE_RELEASE_S = 2.0
WALK_AWAY_NEXT_S = 2.0
TAIL_S = 5.0                # a last transcript or event still being written

# The plan's estimates for the matrix, about 130 attempts. Not checked against our bills.
MATRIX_USD = {"openai": (4, 10), "elevenlabs": (6, 50)}
MATRIX_ATTEMPTS = 130
ATTEMPT_MIN = 3


@lru_cache(maxsize=None)
def clip_pcm(text: str) -> bytes:
    """A fixture's speech, without the silence around it: up to 0.79 s of it trails a clip, past the
    450 ms that ends a turn, and a line's last frame should be its last word."""
    with wave.open(str(FIXTURES / fixture_name(text))) as w:
        if w.getframerate() != RATE:
            raise ValueError(f"{fixture_name(text)} is {w.getframerate()} Hz, not {RATE}")
        raw = b"".join(iter(lambda: w.readframes(65536), b""))  # a streamed WAV's header lies
    samples, pad = array.array("h", raw), int(TRIM_PAD_S * RATE)
    loud = [i for i, s in enumerate(samples) if abs(s) >= TRIM_LEVEL]
    return raw[max(loud[0] - pad, 0) * 2:(loud[-1] + pad) * 2]


def _no_pause(text: str) -> bool:
    """Nothing near the 450 ms that ends a turn: one clip has a pause of 820 ms in the middle."""
    samples, window, quiet = array.array("h", clip_pcm(text)), RATE // 50, 0
    for k in range(0, len(samples), window):
        quiet = quiet + window if max(abs(s) for s in samples[k:k + window]) < PAUSE_LEVEL else 0
        if quiet >= PAUSE_S * RATE:
            return False
    return True


def line_for(person: int, k: int, repeat: int, long: bool = False) -> dict:
    """The k-th thing a person says: each has their own conversation, so their own voice, and a
    repeat starts everyone on the next one. A long line is clips back to back, one breath."""
    own = CONVERSATIONS[(repeat + person) % len(CONVERSATIONS)]
    parts = [own[k % len(own)].text]
    if long:
        rest = [t.text for c in range(len(CONVERSATIONS)) for t in CONVERSATIONS[(repeat + person + c) % len(CONVERSATIONS)]
                if _no_pause(t.text)]
        parts = []
        while sum(len(clip_pcm(p)) for p in parts) / (2 * RATE) < LONG_LINE_S:
            parts.append(rest[len(parts) % len(rest)])
    return {"text": " ".join(parts), "parts": parts, "seconds": sum(len(clip_pcm(p)) for p in parts) / (2 * RATE)}


class Run:
    """One run: when each person presses, speaks and lets go, and what it looked like from the room.
    `room` is what acts on the room: mic(who, on), floor(who, kind), speak(who, line, skip_s, cut_s)."""

    def __init__(self, mode, scenario, people, repeat, room, now):
        self.mode, self.repeat, self.room, self.now = mode, repeat, room, now
        self.dialogue = f"{scenario}-{people}-{repeat}"
        self.who = SPEAKERS[:people]
        self.lines, self.holds, self.floor, self.bot = [], [], [], []
        self.holder = ""
        self.t0 = self.last_end = 0.0
        self.spoken = asyncio.Event()
        self._said, self._own = 0, dict.fromkeys(self.who, 0)

    # What the room says back.

    def bot_audio(self):
        t = self.now()
        if self.bot and t - self.bot[-1][1] <= GAP_S:
            self.bot[-1][1] = t
        else:
            self.bot.append([t, t])

    def floor_changed(self, holder):
        if holder == self.holder:
            return
        t = self.now()
        if self.holder:
            self.floor[-1][2] = t
        if holder:
            self.floor.append([holder, t, None])
        self.holder = holder

    # Waiting.

    async def _until(self, condition, limit) -> bool:
        deadline = self.now() + limit
        while not condition():
            if self.now() >= deadline:
                return False
            await asyncio.sleep(.02)
        return True

    async def until_time(self, t):
        if t > self.now():
            await asyncio.sleep(t - self.now())

    def _quiet(self):
        return self.now() - self.bot[-1][1] >= QUIET_S

    async def after_greeting(self):
        if not await self._until(lambda: self.bot, GREETING_LIMIT_S):
            raise RuntimeError(f"no greeting in {GREETING_LIMIT_S} s: the bot is down or its voice is out of quota")
        await self._until(self._quiet, SPOKEN_LIMIT_S)
        self.t0 = self.now()

    async def bot_heard(self, limit=REPLY_LIMIT_S) -> bool:
        return await self._until(lambda: self.bot and self.bot[-1][1] > self.last_end, limit)

    async def reply(self, limit=REPLY_LIMIT_S):
        if await self.bot_heard(limit):
            await self._until(self._quiet, SPOKEN_LIMIT_S)

    async def tail(self):
        await asyncio.sleep(TAIL_S)

    # A person.

    async def _press(self, who) -> bool:
        if self.mode == "ptt":
            self.room.mic(who, True)
            return True
        await self.room.floor(who, "claim")
        granted = await self._until(lambda: self.holder == who, GRANT_LIMIT_S)
        if granted:
            self.room.mic(who, True)
        return granted

    async def _release(self, who):
        self.room.mic(who, False)
        if self.mode == "baton":
            await self.room.floor(who, "release")

    async def line(self, who, at=None, long=False, walks_away=False, late_release=False):
        """Say their next line, its first frame at `at` (default: as soon as the pre-roll allows).
        Times are as published, after the gate: a late press loses the clip's start, an early release
        its end. A baton line waits for the grant. One never granted is said and never heard."""
        said = line_for(self.who.index(who), self._own[who], self.repeat, long)
        timing = hold_timing(self.dialogue, self._said)
        self._said += 1
        self._own[who] += 1
        press_s, release_s, late, early = (timing[k] for k in ("press_s", "release_s", "press_late", "release_early"))
        if late_release:
            release_s, early = LATE_RELEASE_S, False
        held = self.mode != "normal"
        skip, cut = (max(press_s, 0), -release_s if early else 0) if held else (0, 0)
        first = self.now() + max(-press_s, 0) if at is None else at
        granted, pressed = True, None
        if held:
            await self.until_time(first + press_s)
            pressed = self.now()
            granted = await self._press(who)
        await self.until_time(first + skip)
        start = self.now()
        if granted:
            await self.room.speak(who, said, skip, cut)
        self.last_end = self.now()
        self.lines.append({"who": who, "text": said["text"], "start": start, "end": self.last_end,
                           "press_late": late, "release_early": early})
        self.spoken.set()
        if walks_away:
            self.holds.append((who, pressed, None))
        elif held:
            await asyncio.sleep(max(release_s, 0))
            await self._release(who)
            self.holds.append((who, pressed, self.now()))

    def timeline(self, stored, sentence_ends, infra) -> dict:
        """sim_modes.py's timeline, in seconds from the end of the greeting. stored and sentence_ends
        are in the same clock as `now`."""
        t0 = self.t0
        return {"lines": [{**x, "start": x["start"] - t0, "end": x["end"] - t0} for x in self.lines],
                "bot": [(s - t0, e - t0) for s, e in self.bot if s >= t0],
                "stored": [(t - t0, who, text) for t, who, text in stored],
                "holds": [(who, p - t0, None if r is None else r - t0) for who, p, r in self.holds],
                "floor": [(who, s - t0, None if e is None else e - t0) for who, s, e in self.floor if e is None or e > t0],
                "bot_sentence_ends": [t - t0 for t in sentence_ends],
                "end": self.now() - t0, "infra": list(infra)}


# The scenarios: the same script in every mode (the plan's table).
async def _turn_taking(run):
    for k in range(TURNS):
        await run.line(run.who[k % len(run.who)])
        await run.reply()


async def _overlap(run):  # one burst: a second would muddle "queue in order"
    t = run.now() + FIRST_PRESS_S
    await asyncio.gather(*(run.line(who, at=t + OVERLAP_OFFSET_S * k) for k, who in enumerate(run.who)))
    await run.reply()


async def _interrupt(run):
    await run.line(run.who[0])
    at = run.bot[-1][0] + INTERRUPT_AFTER_S if await run.bot_heard() else None
    await run.line(run.who[-1], at=at)
    await run.reply()


async def _long_hold(run):
    await run.line(run.who[0], long=True)
    await run.reply()


async def _press_generating(run):
    first = asyncio.ensure_future(run.line(run.who[0]))
    await run.spoken.wait()
    await asyncio.gather(first, run.line(run.who[1], at=run.now() + GENERATING_AFTER_S))
    await run.reply()


async def _walked_away(run):
    await run.line(run.who[0], walks_away=True)
    await run.line(run.who[1], at=run.now() + WALK_AWAY_NEXT_S)
    await run.reply(WALKED_AWAY_REPLY_LIMIT_S)


async def _late_release(run):
    await run.line(run.who[0], late_release=True)
    await run.reply()


PLAYS = {"turn_taking": _turn_taking, "overlap": _overlap, "interrupt": _interrupt, "long_hold": _long_hold,
         "press_generating": _press_generating, "walked_away": _walked_away, "late_release": _late_release}


# Keeping it local, and the money.
def refuse_unless_local(env) -> None:
    """Writing audio_mode to the database would open a mode the runner refuses: local stack only."""
    for key in ("DATABASE_URL", "LIVEKIT_URL", "AGENT_RUNNER_URL"):
        host = urlsplit(env.get(key, "")).hostname
        if host not in ("postgres", "transport-server", "agent-runner", "localhost", "127.0.0.1"):
            raise SystemExit(f"sim-modes: refused, nothing ran. {key} isn't the local stack ({host}).")


def budget(cost_ok: str) -> float:
    try:
        usd = float(cost_ok)
    except ValueError:
        usd = 0.0
    if not (usd > 0 and math.isfinite(usd)):
        raise SystemExit(f"sim-modes-matrix: refused, nothing ran. COST_OK must be dollars, not {cost_ok!r}.")
    return usd


def attempt_usd(provider: str) -> tuple[float, float]:
    """(low, high) for one attempt. An unknown voice is priced as the dearest."""
    low, high = MATRIX_USD.get(provider, MATRIX_USD["elevenlabs"])
    return low / MATRIX_ATTEMPTS, high / MATRIX_ATTEMPTS


async def rerun_until_valid(runs, done, attempt, save, cost_ok, per_attempt) -> bool:
    """Every run until it has a valid row, rerunning invalid attempts under the same key and keeping
    them (the report counts them). Valid rows already in `done` aren't paid for twice. False: the
    next attempt could pass cost_ok."""
    def key(r):
        return r["scenario"], r["people"], r["repeat"], r["mode"]

    valid, spent = {key(r) for r in done if r["status"] != "invalid"}, 0.0
    for run in runs:
        while key(run) not in valid:
            if spent + per_attempt > cost_ok:
                return False
            result = await attempt(run)
            spent += per_attempt
            save(result)
            if result["status"] != "invalid":
                valid.add(key(run))
    return True


def stt_down(url: str, timeout: float = 3) -> bool:
    """Is stt-cpu restarting: nobody listening, or the model not loaded. Merely slow isn't: that can
    be what a mode does to it, and an outcome must not make a run invalid."""
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout) as r:
            return not json.load(r)["ready"]
    except TimeoutError:
        return False
    except OSError as e:
        return not isinstance(getattr(e, "reason", None), TimeoutError)


# Live: everything below needs the stack, livekit and the database.
LAG_S = .5
ATTEMPT_LIMIT_S = 600       # a hung run stops the matrix, loud
TOPIC = "meetlab.floor"
FRAME_BYTES = RATE // 50 * 2
BOT_LOUD = 300              # peak; the bot's silence is near zero, not zero
RESULTS = "sim-modes-results.jsonl"


class LiveRoom:
    """The Run's room: one rtc.Room per person, a mic that always sends frames as a real one does,
    published muted as the page does in ptt and baton. Python's mute() is the press (no track churn
    in livekit 1.1.7). Baton claims the way the page does; bot audio and the floor are read from
    the first person's room."""

    def __init__(self, mode, room_name):
        self.mode, self.room_name = mode, room_name
        self.rooms, self.tracks, self.out, self.tasks = {}, {}, {}, []
        self.names = {}  # identity: who

    async def join(self, run, who_all):
        from livekit import rtc

        from meetlab_loadtest._sim_common import LIVEKIT_URL, token

        async def listen(track):
            import numpy as np
            async for event in rtc.AudioStream(track, num_channels=1):
                if np.abs(np.frombuffer(event.frame.data, dtype=np.int16)).max() > BOT_LOUD:
                    run.bot_audio()

        def subscribed(track, _publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity.startswith("bot_"):
                self.tasks.append(asyncio.create_task(listen(track)))

        def attributes(_changed, participant):
            if participant.identity.startswith("bot_"):
                holder = participant.attributes.get("floor.holder", "")
                run.floor_changed(self.names.get(holder, holder))

        for k, who in enumerate(who_all):
            identity = f"sim_{who}_{uuid4().hex[:4]}"
            self.names[identity] = who
            room = rtc.Room()
            if k == 0:
                room.on("track_subscribed")(subscribed)
                room.on("participant_attributes_changed")(attributes)
            await room.connect(LIVEKIT_URL, token(self.room_name, identity))
            source = rtc.AudioSource(RATE, 1, queue_size_ms=60)  # paces capture_frame to real time
            track = rtc.LocalAudioTrack.create_audio_track("mic", source)
            if self.mode != "normal":
                track.mute()
            await room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            self.rooms[who], self.tracks[who], self.out[who] = room, track, deque()
            self.tasks.append(asyncio.create_task(self._pump(who, source)))

    async def _pump(self, who, source):
        from livekit import rtc
        silence, out = bytes(FRAME_BYTES), self.out[who]
        while True:
            item = out.popleft() if out else silence
            if isinstance(item, asyncio.Event):  # the clip's last frame has gone
                item.set()
            else:
                await source.capture_frame(rtc.AudioFrame(item, RATE, 1, len(item) // 2))

    def mic(self, who, on):
        (self.tracks[who].unmute if on else self.tracks[who].mute)()

    async def floor(self, who, kind):
        await self.rooms[who].local_participant.publish_data(json.dumps({"type": kind}), reliable=True, topic=TOPIC)

    async def speak(self, who, line, skip_s, cut_s):
        pcm = b"".join(clip_pcm(p) for p in line["parts"])
        pcm = pcm[int(skip_s * RATE) * 2:len(pcm) - int(cut_s * RATE) * 2]
        done = asyncio.Event()
        self.out[who].extend([pcm[k:k + FRAME_BYTES] for k in range(0, len(pcm), FRAME_BYTES)] + [done])
        await done.wait()

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for room in self.rooms.values():
            await room.disconnect()


async def _set_room_mode(room_name, mode):
    """The room's row: the global settings with its mode, so mode is the only variable."""
    from sqlalchemy import select

    from meetlab_core.db.engine import AsyncSessionLocal
    from meetlab_core.db.models import BotConfig
    from meetlab_loadtest._sim_common import request

    async with AsyncSessionLocal() as db, db.begin():
        base = (await db.execute(select(BotConfig).where(BotConfig.scope == "global"))).scalar_one_or_none()
        fields = {c.key: getattr(base, c.key) for c in BotConfig.__table__.columns
                  if base and c.key not in ("id", "scope", "updated_at")}
        db.add(BotConfig(**{**fields, "scope": room_name, "audio_mode": mode}))
    seen = request("GET", f"/config?room={room_name}")["audio_mode"]
    if seen != mode:
        raise RuntimeError(f"{room_name} runs {seen}, not {mode}: is the runner up to date?")


async def _delete_room_mode(room_name):
    from sqlalchemy import delete

    from meetlab_core.db.engine import AsyncSessionLocal
    from meetlab_core.db.models import BotConfig

    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(delete(BotConfig).where(BotConfig.scope == room_name))


async def _read_back(session, bot, names):
    from sqlalchemy import select

    from meetlab_core.db.engine import AsyncSessionLocal
    from meetlab_core.db.models import Event, Utterance

    async with AsyncSessionLocal() as db:
        said = (await db.execute(select(Utterance.ts, Utterance.speaker_id, Utterance.text).where(
            Utterance.conv_id == session, Utterance.speaker_id != bot).order_by(Utterance.ts))).all()
        ends = (await db.execute(select(Event.payload).where(
            Event.conv_id == session, Event.type == "floor.sentence_end"))).scalars().all()
    return [(ts, names.get(sid, sid), text) for ts, sid, text in said], sorted(e["at"] for e in ends)


async def _watch_infra(infra, stt_url):
    """The only ways a run can be invalid: this process stalling, stt-cpu restarting."""
    async def lag():
        while True:  # the timeline's clock: monotonic misses the laptop sleeping, this doesn't
            t = time.time()
            await asyncio.sleep(.1)
            if time.time() - t - .1 > LAG_S:
                infra.add("lag")

    async def stt():
        while True:
            if await asyncio.to_thread(stt_down, stt_url):
                infra.add("stt-cpu restart")
            await asyncio.sleep(1)

    await asyncio.gather(lag(), stt())


async def _attempt(run) -> dict:
    from meetlab_loadtest._sim_common import request

    mode, scenario, people = run["mode"], run["scenario"], run["people"]
    room_name = f"sim-modes-{mode}-{uuid4().hex[:6]}"
    if mode != "normal":
        await _set_room_mode(room_name, mode)
    room, infra = LiveRoom(mode, room_name), set()
    watching = asyncio.create_task(_watch_infra(infra, os.environ.get("NEMOTRON_STT_URL", "http://stt-cpu:8000")))
    try:
        started = request("POST", "/start", {"room_name": room_name})
        session, bot = started["session_id"], started["bot_identity"]
        print(f"[sim-modes] {scenario} {people}p {mode} repeat {run['repeat']}: room={room_name} session={session}", flush=True)
        live = Run(mode, scenario, people, run["repeat"], room, time.time)
        await room.join(live, live.who)
        await live.after_greeting()
        await PLAYS[scenario](live)
        await live.tail()
        stored, ends = await _read_back(session, bot, room.names)
        timeline = live.timeline(stored, ends, sorted(infra))
        return {**row(run, timeline), "room": room_name, "session": session, "timeline": timeline}
    finally:
        watching.cancel()
        await room.close()
        await asyncio.to_thread(request, "POST", "/stop", {"room_name": room_name})
        if mode != "normal":
            await _delete_room_mode(room_name)


def _print_result(result):
    print(f"[sim-modes] {result['status'].upper()} {result['scenario']} {result['people']}p {result['mode']}", flush=True)
    for problem in result["problems"]:
        print(f"  {problem}", flush=True)


def _voice() -> str:
    from meetlab_loadtest._sim_common import request
    return request("GET", "/config")["tts_provider"]


async def _one(mode, scenario, people, repeat) -> int:
    if scenario not in SCENARIOS or people not in SCENARIOS[scenario][0] or mode not in SCENARIOS[scenario][1]:
        raise SystemExit(f"sim-modes: refused, nothing ran. No such cell: {scenario} {people}p {mode}. See docs/audio-modes-test-plan.md.")
    voice = _voice()
    low, high = attempt_usd(voice)
    print(f"[sim-modes] PAID: real LLM and {voice} voice, expected ${low:.2f}-{high:.2f}, about {ATTEMPT_MIN} min.", flush=True)
    result = await asyncio.wait_for(
        _attempt({"scenario": scenario, "people": people, "repeat": repeat, "mode": mode}), ATTEMPT_LIMIT_S)
    _print_result(result)
    print(json.dumps({k: v for k, v in result.items() if k != "timeline"}, indent=1))
    return 0 if result["status"] == "pass" else 1


async def _matrix(cost_ok) -> int:
    with_rows = []
    if os.path.exists(RESULTS):
        with open(RESULTS) as f:
            with_rows = [json.loads(x) for x in f]
    voice = _voice()
    low, high = attempt_usd(voice)
    print(f"[sim-modes] PAID: real LLM and {voice} voice, expected ${low * MATRIX_ATTEMPTS:.0f}-{high * MATRIX_ATTEMPTS:.0f}, "
          f"about {MATRIX_ATTEMPTS * ATTEMPT_MIN / 60:.1f} h; stops at ${cost_ok:g}, counting ${high:.2f} an attempt. "
          f"{len(with_rows)} rows already in {RESULTS}.", flush=True)

    def save(result):
        _print_result(result)
        with_rows.append(result)
        with open(RESULTS, "a") as f:
            f.write(json.dumps(result) + "\n")

    async def attempt(run):
        return await asyncio.wait_for(_attempt(run), ATTEMPT_LIMIT_S)

    if not await rerun_until_valid(run_list(), list(with_rows), attempt, save, cost_ok, high):
        print(f"[sim-modes] STOPPED at the budget, ${cost_ok:g}: the matrix isn't finished. The same command with "
              f"a new COST_OK carries on from {RESULTS}.", flush=True)
        return 2
    print(json.dumps(report(with_rows), indent=1))
    return 0


def main() -> int:
    refuse_unless_local(os.environ)
    if os.environ.get("COST_OK") is not None:
        return asyncio.run(_matrix(budget(os.environ["COST_OK"])))
    return asyncio.run(_one(os.environ["MODE"], os.environ["SCENARIO"], int(os.environ["PEOPLE"]), int(os.environ.get("REPEAT") or 0)))


if __name__ == "__main__":
    sys.exit(main())
