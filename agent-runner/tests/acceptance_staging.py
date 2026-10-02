"""Live acceptance tests for v2 staging: what a bot session must do, end to end.

Runs from a laptop and from CI (the `acceptance` job in .github/workflows/infra-v2.yml)
the same way. Every scenario keeps a stand-in participant in the room, so the
room-gone cleanup can never be what closes a session and hide a failure.

  start      a bot started through meet's console joins the room
  stoptask   ECS StopTask (SIGTERM): exit 0, the bot leaves, it records 'completed'   (4c PR 4)
  removed    the bot removed from the room: its task stops by itself, exit 0          (4c PR 4)
  kill9      kill -9 inside the task (ECS Exec): exit 137, and the heartbeat check
             fails the session within 60 s while the participant is still there, and
             is able to stop its task (a hung bot must not keep running)              (4c PR 5)
  stop_early       Stop before the bot joins: it never joins, and its task stops
  audio_recording  per-speaker audio reaches the media bucket through the bot's role  (8a)
  video_recording  LiveKit egress uploads the room's mp4 with the egress key          (8b)
  transcript       a participant's speech becomes a stored turn; with staging's NIM on,
                   transcribed by the NIM (waits for a cold NIM first)
  prewarm          Prepare for study (console) brings up a warm bot machine; Stop
                   preparing resets the pool; no bot machine is stuck unhealthy
  two_humans       two humans in before the bot, one leaves: the bot stays     (F1)
  refresh          the only human refreshes: the bot is still there after the grace
  chat             a malformed packet is ignored; a chat message becomes a turn   (F13)

A scenario that cannot reach its situation (e.g. the participant drops before the
kill) is a FAIL, not a skip: an acceptance test that didn't test anything passed nothing.

    CONSOLE_PASSWORD=... LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \\
    uv run --no-project --with livekit --with livekit-api --with boto3 \\
        python agent-runner/tests/acceptance_staging.py [scenario ...]

Needs AWS credentials that can read the staging logs and tasks, stop and exec into
bot tasks, and the session-manager-plugin on PATH (for kill9).
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import timedelta

import boto3
from livekit import api, rtc

CLUSTER = os.getenv("ECS_CLUSTER", "meetlab-v2-staging")
MEET = os.getenv("MEET_URL", "https://meet-staging.wwbp.org")
REGION = "us-east-1"
ecs = boto3.client("ecs", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
_web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())


class Fail(Exception):
    pass


def _post(path, body):
    req = urllib.request.Request(MEET + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"}, method="POST")
    return json.loads(_web.open(req, timeout=30).read())


def _console_stop(room, bot_identity):
    """The console's Stop button."""
    req = urllib.request.Request(f"{MEET}/api/concierge/rooms/{room}/bots/{bot_identity}", method="DELETE")
    try:
        return _web.open(req, timeout=30).status
    except urllib.error.HTTPError as e:
        return e.code


def _token(room, identity):
    return (api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
            .with_identity(identity).with_grants(api.VideoGrants(room_join=True, room=room))
            .with_ttl(timedelta(minutes=30)).to_jwt())


def _task(session_id):
    for status in ("RUNNING", "STOPPED"):
        arns = ecs.list_tasks(cluster=CLUSTER, startedBy=session_id, desiredStatus=status)["taskArns"]
        if arns:
            return arns[0]
    return None


def _describe(arn):
    return ecs.describe_tasks(cluster=CLUSTER, tasks=[arn])["tasks"][0]


def _log_lines(group, session_id, since):
    ev = logs.filter_log_events(logGroupName=group, startTime=int(since * 1000), filterPattern=f'"{session_id}"')
    return [e["message"] for e in ev["events"]]


async def _until(check, timeout, what):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if await check() if asyncio.iscoroutinefunction(check) else check():
            return time.time() - t0
        await asyncio.sleep(2)
    raise Fail(f"{what} not within {timeout} s")


class Meeting:
    """A room with a stand-in participant and a bot started through meet."""

    def __init__(self, wait_for_bot=True, second_human=False):
        self.wait_for_bot, self.second_human = wait_for_bot, second_human

    async def __aenter__(self):
        self.room = f"accept-{uuid.uuid4().hex[:6]}"
        self.human = rtc.Room()
        await self.human.connect(os.environ["LIVEKIT_URL"], _token(self.room, "human_standin"))
        self.second = rtc.Room()
        if self.second_human:  # in the room before the bot, as in diagnosis F1
            await self.second.connect(os.environ["LIVEKIT_URL"], _token(self.room, "human_second"))
        _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
        _post("/api/concierge/rooms", {"name": self.room})
        started = _post(f"/api/concierge/rooms/{self.room}/bots", {})["request"]
        self.session, self.bot_identity = started["runnerSessionId"], started["botIdentity"]
        self.started = time.time()
        if not self.wait_for_bot:
            return self
        self.join_s = await _until(self.bot_present, 420, "bot in the room")
        self.task = _task(self.session)
        if not self.task:
            raise Fail("no task for the session")
        return self

    def bot_present(self):
        if not self.human.isconnected():
            raise Fail("the stand-in participant dropped out of the room")
        return any(p.identity.startswith("bot_") for p in self.human.remote_participants.values())

    def require_participant(self):
        if not self.human.isconnected():
            raise Fail("the stand-in participant dropped out; the room-gone cleanup could have closed the session")

    async def __aexit__(self, *exc):
        await self.human.disconnect()
        await self.second.disconnect()


async def scenario_start():
    async with Meeting() as m:
        return f"bot joined after {m.join_s:.0f}s"


async def scenario_stoptask():
    async with Meeting() as m:
        ecs.stop_task(cluster=CLUSTER, task=m.task, reason="acceptance: stoptask")
        await _until(lambda: _describe(m.task)["lastStatus"] == "STOPPED", 150, "task stopped")
        m.require_participant()
        code = _describe(m.task)["containers"][0].get("exitCode")
        if code != 0:
            raise Fail(f"exit code {code}, expected 0 (graceful)")
        await _until(lambda: not m.bot_present(), 30, "bot gone from the room")
        await _until(lambda: any("ended (completed)" in l for l in _log_lines("/meetlab-v2/staging/bot", m.session, m.started)),
                     60, "bot logged ended (completed)")
        return "exit 0, left the room, recorded completed"


async def scenario_removed():
    async with Meeting() as m:
        lk = api.LiveKitAPI(os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        try:
            for p in (await lk.room.list_participants(api.ListParticipantsRequest(room=m.room))).participants:
                if p.identity.startswith("bot_"):
                    await lk.room.remove_participant(api.RoomParticipantIdentity(room=m.room, identity=p.identity))
        finally:
            await lk.aclose()
        await _until(lambda: _describe(m.task)["lastStatus"] == "STOPPED", 150, "task stopped by itself")
        m.require_participant()
        code = _describe(m.task)["containers"][0].get("exitCode")
        if code != 0:
            raise Fail(f"exit code {code}, expected 0")
        return "task stopped by itself, exit 0"


# No single quotes: the whole thing runs inside sh -c '...'. Skips PID 1 (the launcher;
# Linux ignores in-namespace signals to PID 1) and kills the bot's python process.
_KILL9 = ('for p in /proc/[0-9]*; do n=${p#/proc/}; [ "$n" = 1 ] && continue; '
          'c=$(tr "\\000" " " < $p/cmdline 2>/dev/null); '
          'case "$c" in *python*bot_task*) echo killing $n; kill -9 $n;; esac; done')


async def scenario_kill9():
    async with Meeting() as m:
        await _until(lambda: (_describe(m.task)["containers"][0].get("managedAgents") or [{}])[0].get("lastStatus") == "RUNNING",
                     120, "ECS Exec agent running")
        await asyncio.sleep(22)  # at least two heartbeats
        killed_at = time.time()
        # An interactive Exec session never returns without a terminal, so don't wait for
        # it: the evidence that the kill landed is the task's exit code (137 = SIGKILL).
        exec_ = subprocess.Popen(["aws", "ecs", "execute-command", "--region", REGION, "--cluster", CLUSTER,
                                  "--task", m.task, "--container", "bot", "--interactive",
                                  "--command", f"sh -c '{_KILL9}'"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await _until(lambda: _describe(m.task)["lastStatus"] == "STOPPED", 120, "killed task stopped")
        exec_.kill()
        code = _describe(m.task)["containers"][0].get("exitCode")
        if code != 137:
            raise Fail(f"exit code {code}, expected 137 (SIGKILL): the kill did not land")
        if any("ended (" in l for l in _log_lines("/meetlab-v2/staging/bot", m.session, m.started)):
            raise Fail("the bot recorded its own end: this was not a silent death")

        def failed_by_heartbeat():
            m.require_participant()
            return any("heartbeat: failed" in l for l in _log_lines("/meetlab-v2/staging/agent-runner", m.session, killed_at))
        await _until(failed_by_heartbeat, 90, "session failed by the heartbeat check")
        after = time.time() - killed_at
        if any("could not stop the bot" in l for l in _log_lines("/meetlab-v2/staging/agent-runner", m.session, killed_at)):
            raise Fail("the reconciler failed the session but could not stop its bot (a hung bot would keep running)")
        if after > 60:
            raise Fail(f"failed by heartbeat only {after:.0f}s after the kill (limit 60 s)")
        return f"exit 137, failed by heartbeat {after:.0f}s after the kill, participant still in the room"


async def scenario_stop_early():
    """Stop pressed before the bot has joined (it takes 5 s warm, up to 166 s cold).
    Removing a participant who isn't there yet does nothing, so the bot must still
    be stopped at its task, and must never turn up in the room afterwards."""
    async with Meeting(wait_for_bot=False) as m:
        _console_stop(m.room, m.bot_identity)
        stopped_at = time.time()
        while time.time() - stopped_at < 60:
            m.require_participant()
            if m.bot_present():
                raise Fail(f"the bot joined {time.time() - stopped_at:.0f}s after Stop was pressed")
            await asyncio.sleep(2)
        task = _task(m.session)
        if task and _describe(task)["lastStatus"] != "STOPPED":
            raise Fail(f"the bot's task is still {_describe(task)['lastStatus']} 60 s after Stop")
        return "Stop before join: the bot never joined, and its task is stopped"


MEDIA_BUCKET = os.getenv("MEDIA_BUCKET", "meetlab-v2-staging-media-848180123498")


async def _speak(room: rtc.Room, seconds: float):
    """The stand-in publishes a microphone track and plays a tone into it."""
    import math

    rate, per = 48000, 480  # 10 ms frames
    source = rtc.AudioSource(rate, 1)
    track = rtc.LocalAudioTrack.create_audio_track("standin-mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    for i in range(int(seconds * 100)):
        frame = rtc.AudioFrame.create(rate, 1, per)
        samples = frame.data  # already int16 samples (livekit.rtc AudioFrame)
        for j in range(per):
            samples[j] = int(8000 * math.sin(2 * math.pi * 440 * (i * per + j) / rate))
        await source.capture_frame(frame)


async def scenario_audio_recording():
    """Record pressed in the console, someone speaks, the bot is stopped: that speaker's
    audio must land in the media bucket, written by the bot task's own role."""
    s3 = boto3.client("s3", region_name=REGION)
    async with Meeting() as m:
        # Video egress is a separate scenario; its status does not decide this one (the
        # per-speaker request is recorded before egress is attempted).
        try:
            _web.open(urllib.request.Request(f"{MEET}/api/record/start?roomName={m.room}"), timeout=30)
        except urllib.error.HTTPError:
            pass
        await asyncio.sleep(12)  # one heartbeat: the bot picks up the request
        await _speak(m.human, 6)
        ecs.stop_task(cluster=CLUSTER, task=m.task, reason="acceptance: audio_recording")
        await _until(lambda: _describe(m.task)["lastStatus"] == "STOPPED", 150, "task stopped")

        def audio_in_bucket():
            listed = s3.list_objects_v2(Bucket=MEDIA_BUCKET, Prefix="recordings/").get("Contents", [])
            return [o["Key"] for o in listed if m.room in o["Key"] and "-audio-" in o["Key"]]
        await _until(audio_in_bucket, 60, "per-speaker audio in the media bucket")
        return f"{len(audio_in_bucket())} audio file(s) in s3://{MEDIA_BUCKET}/recordings/"


async def scenario_video_recording():
    """Record pressed, someone speaks, Stop recording pressed: LiveKit's composite
    egress must upload the room's mp4 to the media bucket with the write-only egress
    key (8b). Failing to start is a FAIL: every session needs its video."""
    s3 = boto3.client("s3", region_name=REGION)
    async with Meeting() as m:
        _web.open(urllib.request.Request(f"{MEET}/api/record/start?roomName={m.room}"), timeout=30)
        await _speak(m.human, 15)
        _web.open(urllib.request.Request(f"{MEET}/api/record/stop?roomName={m.room}"), timeout=30)

        def video_in_bucket():
            listed = s3.list_objects_v2(Bucket=MEDIA_BUCKET, Prefix="recordings/").get("Contents", [])
            return [o["Key"] for o in listed if m.room in o["Key"] and o["Key"].endswith("-recording.mp4")]
        await _until(video_in_bucket, 180, "the room's mp4 in the media bucket")
        return f"{video_in_bucket()[0]} in s3://{MEDIA_BUCKET}"


STT_NIM_SERVICE = "meetlab-v2-staging-stt-nim"
SPEECH = os.path.join(os.path.dirname(__file__), "fixtures", "benchmark_prompt.wav")  # tracked; conversations/ is gitignored


def _stt_nim_on():
    """Staging's NIM runs only while stt_nim_enabled (infra/v2/staging/stt_nim.tf)."""
    svc = ecs.describe_services(cluster=CLUSTER, services=[STT_NIM_SERVICE])["services"]
    return bool(svc) and svc[0]["status"] == "ACTIVE" and svc[0]["desiredCount"] > 0


def _stt_nim_ready():
    deployments = ecs.describe_services(cluster=CLUSTER, services=[STT_NIM_SERVICE])["services"][0]["deployments"]
    return [d.get("rolloutState") for d in deployments] == ["COMPLETED"]  # targets healthy behind the NLB


async def _play_wav(room: rtc.Room, path: str, seconds: float):
    """The stand-in says a recorded sentence into its microphone (16-bit mono)."""
    import wave

    with wave.open(path) as w:
        rate = w.getframerate()
        pcm = w.readframes(int(rate * seconds))
    source = rtc.AudioSource(rate, 1)
    track = rtc.LocalAudioTrack.create_audio_track("standin-voice", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    per = rate // 100  # 10 ms frames
    for i in range(0, len(pcm) // 2 - per, per):
        frame = rtc.AudioFrame(pcm[i * 2:(i + per) * 2], rate, 1, per)
        await source.capture_frame(frame)


async def scenario_transcript():
    """Someone speaks; the bot stores their turn. With the NIM on, the transcript comes
    from staging's own Parakeet NIM, not Deepgram, and the NIM returns no errors."""
    nim = _stt_nim_on()
    if nim:  # a cold NIM builds its model first (~20 min, v1 docs)
        waited = await _until(_stt_nim_ready, 2700, "the STT NIM healthy behind its load balancer")
    async with Meeting() as m:
        await asyncio.sleep(5)  # the greeting
        await _play_wav(m.human, SPEECH, 8)
        await _until(lambda: any("user utterance" in l for l in _log_lines("/meetlab-v2/staging/bot", m.session, m.started)),
                     120, "the participant's turn transcribed and stored")
        if not nim:
            return "a user turn stored (Deepgram; the NIM is off)"
        stream = f"bot/bot/{m.task.rsplit('/', 1)[-1]}"
        lines = [e["message"] for e in logs.filter_log_events(
            logGroupName="/meetlab-v2/staging/bot", logStreamNames=[stream], startTime=int(m.started * 1000))["events"]]
        # Logged for every session ("override" is only logged when it differs from the
        # stored config, and staging's stored default is already Parakeet).
        if not any("STT: model=parakeet-" in l for l in lines):
            raise Fail("the bot did not use the NIM's model")
        if any("NemotronHTTPSTTService error" in l for l in lines):
            raise Fail("the NIM returned errors")
        return f"a user turn stored, transcribed by staging's NIM (ready after {waited:.0f} s)"


def _capacity(method="GET", body=None):
    req = urllib.request.Request(f"{MEET}/api/concierge/capacity", method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(_web.open(req, timeout=30).read())


async def scenario_prewarm():
    """Prepare for study from the console: a warm bot machine comes up and stays;
    Stop preparing drops the pool back to 0 (AWS would also do it at the end time)."""
    from datetime import datetime, timezone

    _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
    until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    before = _capacity()
    if before.get("unhealthy_instances"):  # the 2026-10-02 stuck pool, made visible
        raise Fail(f"{before['unhealthy_instances']} bot machine(s) stuck unhealthy in the group: the pool cannot scale in")
    try:
        warm = _capacity("POST", {"sessions": 1, "until": until})
        if (warm["min_instances"], warm["warm_until"] is None) != (1, False):
            raise Fail(f"not prepared: {warm}")
        waited = await _until(lambda: _capacity()["ready_instances"] >= 1, 300, "a warm bot machine")
    finally:
        cold = _capacity("DELETE")  # never leave a test's pool warm
    if (cold["min_instances"], cold["warm_until"]) != (0, None):
        raise Fail(f"Stop preparing left the pool warm: {cold}")
    return f"a bot machine ready {waited:.0f} s after Prepare; Stop preparing reset the pool"


REJOIN_GRACE = 60  # BOT_REJOIN_GRACE_SECONDS default (presence.py)


async def _bot_stays(m, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if not m.bot_present():
            raise Fail(f"the bot left {time.time() - t0:.0f} s later, with a human still in the room")
        await asyncio.sleep(2)


async def scenario_two_humans():
    """Diagnosis F1: two humans in the room before the bot; one leaves, the bot stays."""
    async with Meeting(second_human=True) as m:
        await m.second.disconnect()
        await _bot_stays(m, REJOIN_GRACE + 15)
        return f"the bot stayed {REJOIN_GRACE + 15} s after the other human left"


async def scenario_refresh():
    """The only human refreshes the page (leaves, back in 10 s): the session goes on."""
    async with Meeting() as m:
        await m.human.disconnect()
        await asyncio.sleep(10)
        m.human = rtc.Room()
        await m.human.connect(os.environ["LIVEKIT_URL"], _token(m.room, "human_standin"))
        await _bot_stays(m, REJOIN_GRACE + 5)
        return "the bot was still there after the refresh, past the rejoin grace"


async def scenario_chat():
    """Diagnosis F13: a packet that isn't chat is ignored; a chat message becomes a turn."""
    async with Meeting() as m:
        await asyncio.sleep(5)  # the greeting
        await m.human.local_participant.publish_data(b"5", reliable=True, topic="lk-chat-topic")
        message = {"id": uuid.uuid4().hex, "timestamp": int(time.time() * 1000), "message": "What is two plus two?"}
        await m.human.local_participant.publish_data(json.dumps(message).encode(), reliable=True, topic="lk-chat-topic")
        await _until(lambda: any("user utterance" in l for l in _log_lines("/meetlab-v2/staging/bot", m.session, m.started)),
                     60, "the chat message stored as a user turn")
        return "a malformed packet ignored; the chat message stored as a user turn"


SCENARIOS = {"start": scenario_start, "stoptask": scenario_stoptask, "removed": scenario_removed,
             "kill9": scenario_kill9, "stop_early": scenario_stop_early,
             "audio_recording": scenario_audio_recording, "video_recording": scenario_video_recording,
             "transcript": scenario_transcript, "prewarm": scenario_prewarm,
             "two_humans": scenario_two_humans, "refresh": scenario_refresh, "chat": scenario_chat}


SCENARIO_TIMEOUT = 600
TIMEOUTS = {"transcript": 3300}  # may wait for a cold NIM first


async def main(names):
    results = []
    for name in names:
        t0 = time.time()
        try:
            # A hang (a LiveKit connect, a meet call) fails its own scenario instead of
            # freezing the run until CI's 30-minute cap (2026-10-02). Covers a cold start.
            detail, ok = await asyncio.wait_for(SCENARIOS[name](), TIMEOUTS.get(name, SCENARIO_TIMEOUT)), True
        except asyncio.TimeoutError:
            detail, ok = f"hung: no result within {TIMEOUTS.get(name, SCENARIO_TIMEOUT)} s", False
        except Fail as e:
            detail, ok = str(e), False
        except Exception as e:  # an error in the harness is a failure too, with its cause
            detail, ok = f"{type(e).__name__}: {e}", False
        results.append((name, ok, detail, time.time() - t0))
        print(f"{'PASS' if ok else 'FAIL'} {name:9} {time.time() - t0:5.0f}s  {detail}", flush=True)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Staging acceptance\n\n| Scenario | Result | Time | Detail |\n|---|---|---|---|\n")
            for name, ok, detail, secs in results:
                f.write(f"| {name} | {'PASS' if ok else '**FAIL**'} | {secs:.0f} s | {detail} |\n")
    return all(ok for _, ok, _, _ in results)


if __name__ == "__main__":
    names = sys.argv[1:] or list(SCENARIOS)
    sys.exit(0 if asyncio.run(main(names)) else 1)
