"""Load test: a standard shape (load_plan.py) run against a stack profile, measured the way a
participant hears it.

Each room is real: the console creates it, a synthetic participant joins and speaks
recorded turns (conversation_script.py), the console starts its bot, and a reply is the
bot's audio arriving (conversation_soak.BotListener). Each step of the shape is judged
by the same SLOs whatever the profile, so runs of different stacks compare directly.

    MEET_URL=https://meet-staging.wwbp.org CONSOLE_PASSWORD=... \\
    LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \\
    PROFILE=ours SHAPE=smoke TARGET=50 [HOLD_S=60] [WORKERS=8] [RESULTS=s3://bucket/key.json] \\
    uv run python tests/load_run.py

PROFILE names load_profiles/<name>.json: the bot config every room gets (stored = none:
whatever the console's global config is). Before the
first step it presses Prepare for study for the largest step, as a researcher would, and
waits for the machines, so the run measures load, not cold starts; Stop preparing after.
Rooms are spread over WORKERS processes (one interpreter cannot pace many real-time
microphones); a worker whose event loop falls behind marks the run's numbers as the
harness's, not the stack's. PREPARE=0 skips Prepare for study (local stack: no pool).
"""
import asyncio
import json
import multiprocessing as mp
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from livekit import rtc  # noqa: E402

from conversation_script import conversation_for  # noqa: E402
from load_plan import room_window, schedule, step_bounds, step_of, summarise_step, verdict  # noqa: E402

PROFILES = Path(__file__).parent.parent / "load_profiles"
SAMPLE_RATE, FRAME_MS = 24000, 20
GRACE_S = 15      # a step is judged this long after it ends: its last turns' replies arrive late
MAX_LAG_MS = 500  # a worker's event loop late by more: its microphones were not real time


CLIPS = Path("/tmp/load-clips")


class ClipRecorder:
    """The bot's audio for a sampled reply (quality.record_turn), as a participant hears it,
    saved as a WAV for the report's intelligibility and naturalness scores."""

    def __init__(self):
        self.on, self.pcm, self.rate = False, bytearray(), 48000
        self._task = None

    def watch(self, track: rtc.Track) -> None:
        async def pump():
            async for event in rtc.AudioStream(track, num_channels=1):
                if self.on:
                    self.rate = event.frame.sample_rate
                    self.pcm += bytes(event.frame.data)
        self._task = asyncio.create_task(pump())

    def start(self) -> None:
        self.on, self.pcm = True, bytearray()

    def save(self, path: Path) -> None:
        import wave
        self.on = False
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(self.rate), w.writeframes(bytes(self.pcm))


class Console:
    """meet's console API, as a researcher's browser uses it."""

    def __init__(self, base: str, password: str):
        self.base, self.web = base.rstrip("/"), urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        self.call("POST", "/api/console/login", {"password": password})

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None,
                                     {"Content-Type": "application/json"}, method=method)
        with self.web.open(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")


async def _sleep_until(t: float, abort) -> bool:
    while time.time() < t:
        if abort.is_set():
            return False
        await asyncio.sleep(min(1.0, t - time.time()))
    return not abort.is_set()


async def run_room(i: int, run_id: str, steps, t0: float, profile: dict, console: Console, q, abort):
    from conversation_soak import BotListener, load_turn_audio

    from quality import record_turn

    window = room_window(i, steps, t0)
    if not window or not await _sleep_until(window[0], abort):
        return
    leave = window[1]
    name = f"load-{run_id}-{i:03d}"
    room, listener, closing = rtc.Room(), BotListener(), {"intentional": False}
    recorder, recording = ClipRecorder(), None  # recording: where the current clip goes

    @room.on("track_subscribed")
    def _on_track(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity.startswith("bot_"):
            listener.watch(track)
            recorder.watch(track)

    @room.on("disconnected")
    def _on_disconnected(*_):
        if not closing["intentional"]:
            q.put(("disconnect", time.time(), i, name))

    try:
        from _sim_common import token
        await asyncio.to_thread(console.call, "POST", "/api/concierge/rooms", {"name": name})
        if profile:
            await asyncio.to_thread(console.call, "PUT", "/api/console/config", {"scope": name, **profile})
        await room.connect(os.environ["LIVEKIT_URL"], token(name, f"load_{i:03d}", ttl_minutes=24 * 60))
        source = rtc.AudioSource(SAMPLE_RATE, 1, queue_size_ms=120)  # paces capture_frame to real time
        mic = rtc.LocalAudioTrack.create_audio_track("mic", source)
        await room.local_participant.publish_track(mic)
        asked = time.time()
        started = await asyncio.to_thread(console.call, "POST", f"/api/concierge/rooms/{name}/bots", {})
        q.put(("session", asked, i, started["request"]["runnerSessionId"]))  # its stored turns, for quality
        while not any(p.identity.startswith("bot_") for p in room.remote_participants.values()):
            if time.time() - asked > 600 or abort.is_set():
                raise TimeoutError("the bot did not join within 10 min")
            await asyncio.sleep(0.5)
        q.put(("join", asked, i, time.time() - asked))
    except Exception as e:
        q.put(("start_error", time.time(), i, f"{name}: {e!r}"[:300]))
        closing["intentional"] = True
        await room.disconnect()
        return

    try:
        await asyncio.sleep(4)  # the greeting starts 1 s after someone joins
        await listener.wait_until_quiet()
        n = SAMPLE_RATE * FRAME_MS // 1000
        for turn_no, turn in enumerate(t for _ in iter(int, 1) for t in conversation_for(i)):
            if time.time() > leave or abort.is_set():
                break
            await listener.wait_until_quiet()
            if recording:  # the sampled reply has finished
                recorder.save(recording)
                recording = None
            audio = load_turn_audio(turn.text)
            said_at = time.time()
            q.put(("said", said_at, i, turn.text))  # quality.py: what the bot should have heard
            for k in range(0, len(audio) - n, n):
                await source.capture_frame(rtc.AudioFrame(audio[k:k + n].tobytes(), SAMPLE_RATE, 1, n))
            ended = time.time()
            listener.arm()
            if record_turn(i, turn_no):
                recorder.start()
                recording = CLIPS / run_id / f"{i:03d}-{said_at:.3f}.wav"
            while listener.first_audio_since_arm is None and time.time() - ended < turn.expect_reply_within_s:
                await asyncio.sleep(0.05)
            heard = listener.first_audio_since_arm
            q.put(("turn", ended, i, None if heard is None else (heard - ended) * 1000))
            await asyncio.sleep(turn.pause_after_s)
    finally:
        if recording:
            await listener.wait_until_quiet()
            recorder.save(recording)
        await listener.stop()
        closing["intentional"] = True
        await room.disconnect()  # the bot leaves on its own after the rejoin grace (presence.py)


async def _lag_monitor(worker: int, q, abort):
    while not abort.is_set():
        t = time.monotonic()
        await asyncio.sleep(1)
        lag_ms = (time.monotonic() - t - 1) * 1000
        if lag_ms > MAX_LAG_MS:
            q.put(("lag", time.time(), worker, lag_ms))


def _worker(worker: int, rooms: list[int], run_id, steps, t0, profile, q, abort):
    async def main():
        console = Console(os.environ["MEET_URL"], os.environ["CONSOLE_PASSWORD"])
        lag = asyncio.create_task(_lag_monitor(worker, q, abort))
        for i, outcome in zip(rooms, await asyncio.gather(
                *(run_room(i, run_id, steps, t0, profile, console, q, abort) for i in rooms), return_exceptions=True)):
            if isinstance(outcome, BaseException):  # a harness fault must never pass silently
                print(f"room {i}: harness error {outcome!r}", flush=True)
                q.put(("start_error", time.time(), i, f"harness: {outcome!r}"[:300]))
        lag.cancel()
    try:
        asyncio.run(main())
    finally:
        q.put(("done", time.time(), worker, None))


def _judge(k, step, events, bounds) -> dict:
    mine = [e for e in events if step_of(e[1], bounds) == k]
    m = summarise_step(step, turns=[e[3] for e in mine if e[0] == "turn"],
                       joins_s=[e[3] for e in mine if e[0] == "join"],
                       start_errors=sum(e[0] == "start_error" for e in mine),
                       disconnects=sum(e[0] == "disconnect" for e in mine))
    m["harness_lag_ms"] = max([e[3] for e in mine if e[0] == "lag"], default=0)
    m["errors"] = [e[3] for e in mine if e[0] == "start_error"][:5]
    m["pass"], m["why"] = verdict(m)
    return m


def _fmt(v, f="{:.0f}"):
    return "-" if v is None else f.format(v)


def main() -> int:
    profile_name, shape = os.getenv("PROFILE", "ours"), os.getenv("SHAPE", "smoke")
    profile = json.loads((PROFILES / f"{profile_name}.json").read_text())
    steps = schedule(shape, int(os.getenv("TARGET", "50")), int(os.getenv("HOLD_S", "0")))
    peak = max(s.rooms for s in steps)
    run_id = uuid.uuid4().hex[:6]
    console = Console(os.environ["MEET_URL"], os.environ["CONSOLE_PASSWORD"])
    print(f"load test {run_id}: {shape} on '{profile_name}' {profile}, peak {peak} rooms, "
          f"{sum(s.hold_s for s in steps) / 60:.0f} min", flush=True)

    until = datetime.now(timezone.utc) + timedelta(seconds=sum(s.hold_s for s in steps) + 1800)
    prepare = os.getenv("PREPARE", "1") == "1"  # 0 locally: no bot pool to prepare
    warm = console.call("POST", "/api/concierge/capacity", {"sessions": peak, "until": until.isoformat()}) if prepare else {}
    result = {"run": run_id, "profile": profile_name, "config": profile, "shape": shape, "steps": [],
              "git_sha": os.getenv("GIT_SHA"), "started": datetime.now(timezone.utc).isoformat()}
    try:
        if warm.get("capped"):
            raise SystemExit(f"the bot pool's ceiling holds fewer than {peak} sessions ({warm}): raise bot_pool_max")
        t = time.time()
        while prepare and (s := console.call("GET", "/api/concierge/capacity"))["ready_instances"] < s["min_instances"]:
            if time.time() - t > 900:
                raise SystemExit(f"bot machines not ready within 15 min: {s}")
            time.sleep(10)
        if prepare:
            print(f"bot machines ready: {s['ready_instances']} after {time.time() - t:.0f} s", flush=True)

        workers = min(peak, int(os.getenv("WORKERS", "0")) or os.cpu_count() or 2)
        t0 = time.time() + 5
        bounds = step_bounds(steps, t0)
        result["bounds"] = bounds  # load_report.py reads staging's side of each step by these
        ctx = mp.get_context("spawn")
        q, abort = ctx.Queue(), ctx.Event()
        procs = [ctx.Process(target=_worker, args=(w, list(range(w, peak, workers)), run_id, steps, t0, profile, q, abort),
                             daemon=True) for w in range(workers)]
        for p in procs:
            p.start()

        events, k = [], 0
        print(f"{'step':>4} {'rooms':>5} {'turns':>5} {'reply':>6} {'p50':>6} {'p95':>6} {'p99':>6} {'join95':>6}  verdict",
              flush=True)
        # Judged by the clock, not by the workers: the closing step must run its full
        # length, or the teardown check below runs inside the bots' rejoin grace.
        while k < len(steps) and not abort.is_set():
            try:
                events.append(q.get(timeout=1))
            except Exception:
                pass
            while k < len(steps) and time.time() > bounds[k][1] + GRACE_S:
                m = _judge(k, steps[k], events, bounds)
                result["steps"].append(m)
                print(f"{k:>4} {m['rooms']:>5} {m['turns']:>5} {_fmt(m['reply_rate'], '{:.0%}'):>6} {_fmt(m['p50_ms']):>6} "
                      f"{_fmt(m['p95_ms']):>6} {_fmt(m['p99_ms']):>6} {_fmt(m['join_p95_s']):>6}  "
                      f"{'PASS' if m['pass'] else 'FAIL ' + '; '.join(m['why'])}"
                      f"{'  (harness lagged ' + _fmt(m['harness_lag_ms']) + ' ms)' if m['harness_lag_ms'] else ''}", flush=True)
                if not m["pass"] and steps[k].stop_on_failure and not abort.is_set():
                    abort.set()  # breakpoint: found it; every room leaves now
                k += 1
        if abort.is_set():
            time.sleep(steps[-1].hold_s)  # the rooms left at the abort: give the bots the closing step's time
        for p in procs:
            p.join(timeout=60)
        still = []
        for i in range(peak):
            name = f"load-{run_id}-{i:03d}"
            try:
                if console.call("GET", f"/api/concierge/rooms/{name}/bots").get("assignedBotIdentity"):
                    still.append(name)
            except urllib.error.HTTPError:
                pass  # the room is gone, and with it the bot
        result["sessions_left_running"] = still
        # What each room's participant said, and what its conversation stored (quality.py).
        result["rooms"] = []
        for _, _, i, session in (e for e in events if e[0] == "session"):
            try:
                turns = console.call("GET", f"/api/meetings/{session}/utterances")["utterances"]
            except urllib.error.HTTPError as e:
                turns, _ = [], print(f"no turns for room {i}: {e}")
            result["rooms"].append({"room": i, "session": session, "turns": turns,
                                    "said": [[t, text] for kind, t, j, text in events if kind == "said" and j == i]})
        # How good the answers were, per step (judge.py): a sample of replies, each with the
        # conversation before it, scored by a fixed judge model. Skipped without a key.
        if os.getenv("OPENAI_API_KEY"):
            import judge
            for m, (a, b) in zip(result["steps"], bounds):
                if m["rooms"]:
                    m["answers"] = judge.summarise([judge.ask(c) for c in judge.cases(result["rooms"], a, b, limit=20)])
        result["clips"] = _upload_clips(run_id)
        passed = [m["rooms"] for m in result["steps"] if m["pass"] and m["rooms"]]
        result["capacity_rooms"] = max(passed, default=0)
        result["harness_valid"] = all(m["harness_lag_ms"] == 0 for m in result["steps"])
        result["pass"] = all(m["pass"] for m in result["steps"]) and not still
        print(f"\nsessions still running after the run: {len(still)}  {still[:5]}")
        print(f"capacity (largest passing step): {result['capacity_rooms']} rooms")
        if not result["harness_valid"]:
            print("HARNESS OVERLOADED: its microphones fell behind; add WORKERS or a bigger machine before trusting this run")
        print(f"VERDICT: {'PASS' if result['pass'] else 'FAIL'}", flush=True)
    finally:
        if prepare:
            console.call("DELETE", "/api/concierge/capacity")  # never leave the pool warm
        _save(result)
    return 0 if result.get("pass") else 1


def _upload_clips(run_id: str) -> list[dict]:
    """The sampled replies' audio next to the result (s3://…/<result>-clips/), for load_report.py."""
    out, dest = [], os.getenv("RESULTS", "")
    for path in sorted((CLIPS / run_id).glob("*.wav")):
        room, said_at = path.stem.split("-", 1)
        where = str(path)
        if dest.startswith("s3://"):
            import boto3
            bucket, key = dest[5:].split("/", 1)
            where = f"{key.removesuffix('.json')}-clips/{path.name}"
            boto3.client("s3").upload_file(str(path), bucket, where)
            where = f"s3://{bucket}/{where}"
        out.append({"room": int(room), "said_at": float(said_at), "audio": where})
    return out


def _save(result: dict):
    out = os.getenv("RESULTS", "")
    body = json.dumps(result, indent=2)
    if out.startswith("s3://"):
        import boto3
        bucket, key = out[5:].split("/", 1)
        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=body.encode(), ContentType="application/json")
    elif out:
        Path(out).write_text(body)


if __name__ == "__main__":
    sys.exit(main())
