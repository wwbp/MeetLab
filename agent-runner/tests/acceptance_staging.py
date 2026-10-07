"""Live acceptance tests for v2 staging: what a bot session must do, end to end.

Runs from a laptop and from CI (the `acceptance` job in .github/workflows/infra-v2.yml)
the same way. Every scenario keeps a stand-in participant in the room, so the
room-gone cleanup can never be what closes a session and hide a failure.

  start      a bot started through meet's console joins the room
  stoptask   ECS StopTask (SIGTERM): exit 0, the bot leaves, it records 'completed'   (4c PR 4)
  removed    the bot removed from the room: its task stops by itself, exit 0          (4c PR 4)
  kill9      kill -9 inside the task (ECS Exec): exit 137, and the heartbeat check
             fails the session within 60 s while the participant is still there, and
             is able to stop its task (a hung bot must not keep running)              (4c PR 5);
             then a new bot rejoins with the conversation so far                      (rejoin)
  stop_early       Stop before the bot joins: it never joins, and its task stops
  audio_recording  per-speaker audio reaches the media bucket through the bot's role  (8a)
  video_recording  LiveKit egress uploads the room's mp4 with the egress key          (8b)
  transcript       a participant's speech becomes a stored turn; with staging's NIM on,
                   transcribed by the NIM (waits for a cold NIM first)
  prewarm          Prepare for study (console) brings up a warm bot machine; Stop
                   preparing resets the pool; no bot machine is stuck unhealthy
  two_humans       two humans in before the bot, one leaves: the bot stays     (F1)
  refresh          the only human refreshes: the bot is still there after the grace
  chat             a non-RTVI packet is ignored; RTVI chat becomes the sender's turn, once, marked chat (F13)
  bot_ready        RTVI: client-ready gets bot-ready; the room hears no tokens, transcripts or metrics
  auto_record      with no Record press: the room's video, the bot's own audio and its greeting line
  record_auth      an outsider cannot start or stop a room's recording (F10): 401
  study_prolific   a participant joins as a study sends them (Prolific ID, before the bot): the completion
                   code is HMAC(room:ID) and their speaker record keeps the ID          (D2)
  session_limit    a room's time limit: the bot speaks its closing message when time is up   (D2)
  our_models       a room configured for our LLM (vLLM) and voice (Kokoro): the bot hears,
                   answers and speaks on them (waits for cold models first)          (L3)
  turn_relay       a participant's browser (Chromium) allowed no direct path (relay-only) joins
                   through TURN over TLS on 443 and receives the bot's greeting      (D1)

A scenario that cannot reach its situation (e.g. the participant drops before the
kill) is a FAIL, not a skip: an acceptance test that didn't test anything passed nothing.

    CONSOLE_PASSWORD=... LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \\
    uv run --no-project --with livekit --with livekit-api --with boto3 --with playwright \\
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
import urllib.parse
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


def _post(path, body, method="POST"):
    req = urllib.request.Request(MEET + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"}, method=method)
    return json.loads(_web.open(req, timeout=30).read())


def completion_code(room: str, participant_id: str, secret: str) -> str:
    """meet/lib/completion-code.ts in Python: the code a participant pastes into the survey,
    HMAC-SHA256(secret, "<room>:<id>"), 8 characters with no 0/O or 1/I."""
    import hashlib
    import hmac
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    digest = hmac.new(secret.encode(), f"{room}:{participant_id}".encode(), hashlib.sha256).digest()
    return "".join(alphabet[b % len(alphabet)] for b in digest[:8])


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

    def __init__(self, wait_for_bot=True, second_human=False, room=None):
        self.wait_for_bot, self.second_human = wait_for_bot, second_human
        self.room = room or f"accept-{uuid.uuid4().hex[:6]}"

    async def __aenter__(self):
        # The real order: the console creates the room, people join, then the bot starts.
        # (Joining creates a LiveKit room, and the console refuses to create an existing
        # one; LiveKit Cloud's room list lagged long enough to hide that, ours doesn't.)
        _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
        _post("/api/concierge/rooms", {"name": self.room})
        self.human = rtc.Room()
        await self.human.connect(os.environ["LIVEKIT_URL"], _token(self.room, "human_standin"))
        self.second = rtc.Room()
        if self.second_human:  # in the room before the bot, as in diagnosis F1
            await self.second.connect(os.environ["LIVEKIT_URL"], _token(self.room, "human_second"))
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


# No single quotes: the whole thing runs inside sh -c '...'. Skips PID 1 (the init; Linux
# ignores in-namespace signals to PID 1) and this shell ($$: its own command line matches the
# pattern, and /proc lists 67 before 7), and kills the bot's python process.
_KILL9 = ('for p in /proc/[0-9]*; do n=${p#/proc/}; case $n in 1|$$) continue;; esac; '
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

        # Rejoin with context (the user's decision, 2026-10-05): a new bot takes the room and
        # continues the conversation (here: the greeting the dead bot stored).
        def new_bot():
            m.require_participant()
            return any(p.identity.startswith("bot_") and p.identity != m.bot_identity
                       for p in m.human.remote_participants.values())
        await _until(new_bot, 150, "a new bot in the room after the death")
        rejoined = time.time() - killed_at
        # CloudWatch delivers a log line seconds after it's written (2026-10-06: the rejoin was
        # logged, but read the moment the new bot appeared): wait for it, as below.
        rejoin_line = lambda: next((l for l in _log_lines("/meetlab-v2/staging/agent-runner", m.session, killed_at)  # noqa: E731
                                    if "resumes it" in l), None)
        try:
            await _until(rejoin_line, 60, "the runner's log of the rejoin")
        except Fail:
            raise Fail("a bot joined, but the runner logged no rejoin of this session")
        line = rejoin_line()
        resumed = line.split("session ")[-1].split()[0]
        # CloudWatch delivers a log line seconds after it's written: wait for it (2026-10-05:
        # the line was there, "with 2 turns", but read too early).
        load_line = lambda: next((l for l in _log_lines("/meetlab-v2/staging/bot", resumed, killed_at) if "turns (" in l), "")  # noqa: E731
        await _until(load_line, 60, "the new bot's log of the conversation it loaded")
        loaded = load_line()
        turns = int(loaded.split(" with ")[1].split()[0]) if " with " in loaded else 0
        if turns < 1:
            raise Fail(f"the new bot ({resumed}) did not load the conversation so far: {loaded[:160]!r}")
        return (f"exit 137, failed by heartbeat {after:.0f}s after the kill; a new bot resumed with "
                f"{turns} turn(s) {rejoined:.0f}s after the kill, participant still in the room")


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


SPEECH = os.path.join(os.path.dirname(__file__), "fixtures", "benchmark_prompt.wav")  # tracked; conversations/ is gitignored


def _service_on(name):
    """A GPU service runs only while switched on (infra/v2/stack: stt_nim.tf, models.tf)."""
    svc = ecs.describe_services(cluster=CLUSTER, services=[f"meetlab-v2-staging-{name}"])["services"]
    return bool(svc) and svc[0]["status"] == "ACTIVE" and svc[0]["desiredCount"] > 0


def deploy_state(service: dict) -> tuple[str, str]:
    """ready, waiting, or failed (with ECS's reason) for a service's current deploy.
    A task ECS cannot place is failed at once: it would only retry every 30 min for ever
    (the FP8 deploy, 2026-10-03), and the wait for a model is 45 min."""
    primary = next(d for d in service["deployments"] if d["status"] == "PRIMARY")
    if primary["rolloutState"] == "COMPLETED" and len(service["deployments"]) == 1:
        return "ready", ""  # targets healthy behind the NLB
    if primary["rolloutState"] == "FAILED":
        return "failed", primary.get("rolloutStateReason", "")
    stuck = [e["message"] for e in service["events"]
             if e["createdAt"] >= primary["createdAt"] and "unable to place a task" in e["message"]]
    return ("failed", stuck[0]) if stuck else ("waiting", "")


APP_SERVICES = ["livekit", "meet", "agent-runner"]  # the GPU models have their own, longer waits


def still_deploying(services: list[dict]) -> list[str]:
    """The switched-on services whose deploy has not finished. A LiveKit machine swap
    (stop-first, minutes down) once made every scenario fail with a 500 (2026-10-04)."""
    waiting = []
    for svc in services:
        if svc["status"] != "ACTIVE" or svc["desiredCount"] == 0:
            continue
        name = svc["serviceName"].removeprefix("meetlab-v2-staging-")
        state, why = deploy_state(svc)
        if state == "failed":
            raise Fail(f"{name}'s deploy will not finish: {why}")
        if state == "waiting":
            waiting.append(name)
    return waiting


def _service_ready(name):
    state, why = deploy_state(ecs.describe_services(cluster=CLUSTER, services=[f"meetlab-v2-staging-{name}"])["services"][0])
    if state == "failed":
        raise Fail(f"{name}'s deploy will not finish: {why}")
    return state == "ready"


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
    """Someone speaks; the bot stores their turn, transcribed by whichever speech server is on
    (infra/v2/stack/runner.tf): the GPU NIM if on, else Parakeet on CPU (stt_cpu.tf), else
    Deepgram. Ours must be the one used, with no errors."""
    server = "stt-nim" if _service_on("stt-nim") else "stt-cpu" if _service_on("stt-cpu") else None
    if server:  # a cold NIM builds its model first (~20 min); the CPU server is up in ~1 min
        waited = await _until(lambda: _service_ready(server), 2700 if server == "stt-nim" else 600,
                              f"{server} healthy behind its load balancer")
    async with Meeting() as m:
        await asyncio.sleep(5)  # the greeting
        await _play_wav(m.human, SPEECH, 8)
        await _until(lambda: any("user utterance" in l for l in _log_lines("/meetlab-v2/staging/bot", m.session, m.started)),
                     120, "the participant's turn transcribed and stored")
        if not server:
            return "a user turn stored (Deepgram; no speech server of ours is on)"
        stream = f"bot/bot/{m.task.rsplit('/', 1)[-1]}"
        lines = [e["message"] for e in logs.filter_log_events(
            logGroupName="/meetlab-v2/staging/bot", logStreamNames=[stream], startTime=int(m.started * 1000))["events"]]
        # Logged for every session ("override" is only logged when it differs from the
        # stored config, and staging's stored default is already Parakeet).
        if not any("STT: model=parakeet-" in l for l in lines):
            raise Fail(f"the bot did not use Parakeet ({server})")
        if any("NemotronHTTPSTTService error" in l for l in lines):
            raise Fail(f"{server} returned errors")
        return f"a user turn stored, transcribed by {server} (ready after {waited:.0f} s)"


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
    """Diagnosis F13, now RTVI (rtvi.py): a packet that isn't RTVI is ignored; chat sent as meet
    sends it (RTVI send-text) becomes the sender's turn, once."""
    async with Meeting() as m:
        await asyncio.sleep(5)  # the greeting
        await m.human.local_participant.publish_data(b"5", reliable=True, topic="lk-chat-topic")
        message = {"id": uuid.uuid4().hex, "timestamp": int(time.time() * 1000), "message": "What is two plus two?",
                   "label": "rtvi-ai", "type": "send-text", "data": {"content": "What is two plus two?"}}
        await m.human.local_participant.publish_data(json.dumps(message).encode(), reliable=True, topic="lk-chat-topic")
        utterances = lambda: [l for l in _log_lines("/meetlab-v2/staging/bot", m.session, m.started)  # noqa: E731
                              if "user utterance" in l]
        await _until(utterances, 60, "the chat message stored as a user turn")
        await asyncio.sleep(5)  # time for a duplicate to land, if there were one
        # Counted in the database, the record of truth: the log is only the signal to look
        # (a CloudWatch read once miscounted, 2026-10-06).
        typed = [u for u in _meeting_get(m.session, "utterances") if not u["bot"]]
        if [u.get("source") for u in typed] != ["chat"]:
            raise Fail(f"the chat message should be one user turn marked chat, got: {[u.get('source') for u in typed]}")
        return "a non-RTVI packet ignored; RTVI chat stored as the sender's turn, once, marked as chat"


async def scenario_bot_ready():
    """RTVI (rtvi.py): a page that says client-ready hears bot-ready; the room hears nothing
    private (no LLM tokens, transcripts or metrics: the user's choice A, 2026-10-05)."""
    async with Meeting() as m:
        heard = []
        m.human.on("data_received", lambda p: heard.append(p.data))
        ready = {"label": "rtvi-ai", "type": "client-ready", "id": uuid.uuid4().hex,
                 "data": {"version": "2.1.0", "about": {"library": "acceptance"}}}
        await m.human.local_participant.publish_data(json.dumps(ready).encode(), reliable=True)
        types = lambda: {json.loads(d).get("type") for d in heard if d[:1] == b"{"}  # noqa: E731
        await _until(lambda: "bot-ready" in types(), 20, "bot-ready after client-ready")
        await asyncio.sleep(15)  # the greeting plays meanwhile: speaking events, and nothing else
        private = types() & {"bot-llm-text", "bot-tts-text", "user-transcription", "user-llm-text", "metrics", "bot-output"}
        if private:
            raise Fail(f"the room heard {sorted(private)}")
        return f"bot-ready after client-ready; the room heard only {sorted(types())}"


async def scenario_auto_record():
    """Auto-record: with nobody pressing Record, the room's video, the bot's own audio and
    its greeting line are all recorded (the greeting plays 1 s after someone joins)."""
    s3 = boto3.client("s3", region_name=REGION)
    room = f"accept-{uuid.uuid4().hex[:6]}"
    _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
    _post("/api/console/config", {"scope": room, "auto_record": True}, method="PUT")
    async with Meeting(room=room) as m:
        await _until(lambda: any("bot line" in l for l in _log_lines("/meetlab-v2/staging/bot", m.session, m.started)),
                     60, "the greeting stored as the bot's line")
        await asyncio.sleep(10)
        _web.open(urllib.request.Request(f"{MEET}/api/record/stop?roomName={room}"), timeout=30)
        ecs.stop_task(cluster=CLUSTER, task=m.task, reason="acceptance: auto_record")  # flushes the audio
        await _until(lambda: _describe(m.task)["lastStatus"] == "STOPPED", 150, "task stopped")

        def recorded():
            keys = [o["Key"] for o in s3.list_objects_v2(Bucket=MEDIA_BUCKET, Prefix="recordings/").get("Contents", [])
                    if room in o["Key"]]
            return any(k.endswith("-recording.mp4") for k in keys) and any(f"-audio-{m.bot_identity}" in k for k in keys)
        await _until(recorded, 180, "the room's video and the bot's own audio in the media bucket")
        return "video, the bot's own audio and its greeting line, with no Record press"


async def scenario_record_auth():
    """F10: the recording endpoints act only for the console or someone admitted to the room.
    An outsider (no console cookie, no room token) who knows a room's name gets 401."""
    outsider = urllib.request.build_opener()  # no cookies
    for action in ("start", "stop"):
        try:
            outsider.open(urllib.request.Request(f"{MEET}/api/record/{action}?roomName=accept-anything"), timeout=30)
            raise Fail(f"an outsider could {action} a recording")
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise Fail(f"{action}: expected 401, got {e.code}")
    return "start and stop refuse an outsider (401)"


def _join_as_participant(room: str, name: str, prolific: str = ""):
    """What a participant's browser gets from the pre-join screen: /api/connection-details."""
    q = urllib.parse.urlencode({"roomName": room, "participantName": name, "metadata": prolific})
    return json.loads(_web.open(f"{MEET}/api/connection-details?{q}", timeout=30).read())


def _meeting_get(session: str, what: str) -> list:
    return json.loads(_web.open(f"{MEET}/api/meetings/{session}/{what}", timeout=30).read())[what]


async def scenario_study_prolific():
    """D2: a participant joins with their Prolific ID before the bot, as studies run. Their
    completion code is HMAC(room:ID) (docs/study-support.md) and their speaker record keeps the
    ID a paid study is matched on (lost for early joiners until #163)."""
    room, pid = f"accept-{uuid.uuid4().hex[:6]}", uuid.uuid4().hex[:24]
    _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
    _post("/api/concierge/rooms", {"name": room})
    details = _join_as_participant(room, "Ana", pid)
    expected = completion_code(room, pid, os.environ["LIVEKIT_API_SECRET"])
    if details["completionCode"] != expected:
        raise Fail(f"completion code {details['completionCode']} is not HMAC(room:ID) {expected}")
    person = rtc.Room()
    await person.connect(os.environ["LIVEKIT_URL"], details["participantToken"])
    try:
        started = _post(f"/api/concierge/rooms/{room}/bots", {})["request"]
        session = started["runnerSessionId"]
        await _until(lambda: any(p.identity.startswith("bot_") for p in person.remote_participants.values()), 420, "bot in the room")
        await asyncio.sleep(5)  # the greeting
        await _play_wav(person, SPEECH, 8)
        await _until(lambda: any(s.get("display_name") == "Ana" for s in _meeting_get(session, "speakers")), 120, "Ana's speaker record")
        ana = next(s for s in _meeting_get(session, "speakers") if s["display_name"] == "Ana")
        if ana["prolific_id"] != pid:
            raise Fail(f"stored Prolific ID {ana['prolific_id']!r}, expected {pid}")
        return f"completion code {expected} = HMAC(room:ID); Prolific ID stored for a participant who joined before the bot"
    finally:
        await person.disconnect()


async def scenario_session_limit():
    """D2: a room limited to 1 minute: the bot speaks the room's closing message when time is up."""
    room, closing = f"accept-{uuid.uuid4().hex[:6]}", f"Time is up, thank you. Code check {uuid.uuid4().hex[:4]}."
    _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
    _post("/api/console/config", {"scope": room, "session_limit_minutes": 1, "closing_message": closing}, method="PUT")
    _post("/api/concierge/rooms", {"name": room})
    details = _join_as_participant(room, "Ana", uuid.uuid4().hex[:24])
    if details.get("sessionLimitSeconds") != 60:
        raise Fail(f"the browser was told {details.get('sessionLimitSeconds')} s, expected 60")
    person = rtc.Room()
    await person.connect(os.environ["LIVEKIT_URL"], details["participantToken"])
    try:
        session = _post(f"/api/concierge/rooms/{room}/bots", {})["request"]["runnerSessionId"]
        await _until(lambda: any(p.identity.startswith("bot_") for p in person.remote_participants.values()), 420, "bot in the room")
        joined = time.time()
        await _until(lambda: any(u["bot"] and u["text"] == closing for u in _meeting_get(session, "utterances")),
                              180, "the bot's closing message")
        return f"closing message spoken {time.time() - joined:.0f} s after the bot joined (limit 60 s; browser told 60 s)"
    finally:
        await person.disconnect()


OUR_LLM = "Qwen/Qwen2.5-7B-Instruct"  # infra/v2/stack/models.tf


async def scenario_our_models():
    """L3: a room configured for our LLM and our voice. Someone speaks; the bot greets,
    hears, answers and speaks the answer on our models (the reply's latency is measured
    to its first audio, so a stored reply with a latency means Kokoro spoke it)."""
    t0 = time.time()
    for name in ("llm", "tts") + (("stt-nim",) if _service_on("stt-nim") else ()):
        await _until(lambda: _service_ready(name), 2700, f"our {name} healthy behind its load balancer")
    room = f"accept-{uuid.uuid4().hex[:6]}"
    _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
    _post("/api/console/config", {"scope": room, "llm_model": OUR_LLM, "tts_provider": "kokoro", "tts_voice": "alloy"},
          method="PUT")
    async with Meeting(room=room) as m:
        bot_log = lambda: _log_lines("/meetlab-v2/staging/bot", m.session, m.started)
        await _until(lambda: any("bot line" in l for l in bot_log()), 60, "the greeting spoken and stored")
        await _play_wav(m.human, SPEECH, 8)
        await _until(lambda: any("bot reply" in l and "latency_ms=None" not in l for l in bot_log()),
                     120, "the bot's spoken answer")
        stream = f"bot/bot/{m.task.rsplit('/', 1)[-1]}"
        lines = [e["message"] for e in logs.filter_log_events(
            logGroupName="/meetlab-v2/staging/bot", logStreamNames=[stream], startTime=int(m.started * 1000))["events"]]
        errors = [l for l in lines if ("OpenAILLMService" in l or "OpenAITTSService" in l) and "error" in l.lower()]
        if errors:
            raise Fail(f"our models returned errors: {errors[0][:200]}")
        reply = next(l for l in bot_log() if "bot reply" in l and "latency_ms=None" not in l).split("latency_ms=")[1].split()[0]
        stt = "the NIM" if any("STT: model=parakeet-" in l for l in lines) else "Deepgram"
        return f"heard ({stt}), answered by {OUR_LLM}, spoken by Kokoro; latency_ms={reply} (models ready after {m.started - t0:.0f} s)"


# Every peer connection the page opens, so the check can read Chromium's own stats.
_RECORD_PCS = """(() => { const PC = window.RTCPeerConnection; window.__pcs = [];
  window.RTCPeerConnection = function (...a) { const pc = new PC(...a); window.__pcs.push(pc); return pc; };
  window.RTCPeerConnection.prototype = PC.prototype; })()"""

# What arrived and over which path: audio bytes in, and the selected pair's local candidate type.
_RELAY_STATS = """async () => { let bytes = 0, relay = false;
  for (const pc of window.__pcs) { const st = await pc.getStats(); const by = new Map(); st.forEach(r => by.set(r.id, r));
    st.forEach(r => {
      if (r.type === "inbound-rtp" && r.kind === "audio") bytes += r.bytesReceived || 0;
      if (r.type === "candidate-pair" && r.state === "succeeded" && r.nominated)
        relay = relay || by.get(r.localCandidateId)?.candidateType === "relay";
    }); }
  return {bytes, relay}; }"""


async def scenario_turn_relay():
    """D1: a participant's browser (Chromium, meet's LiveKit client) allowed only relayed paths
    (iceTransportPolicy "relay") joins, and the bot's greeting arrives over a relay: our TURN
    over TLS on 443, the only one LiveKit offers. A real browser, because LiveKit's Python SDK
    on Linux can't relay over TLS at all (2026-10-05), while browsers are what participants use."""
    from playwright.async_api import async_playwright
    room = f"accept-{uuid.uuid4().hex[:6]}"
    _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
    _post("/api/concierge/rooms", {"name": room})
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        try:
            page = await browser.new_page()
            await page.add_script_tag(url="https://cdn.jsdelivr.net/npm/livekit-client@2.17.1/dist/livekit-client.umd.js")
            await page.evaluate(_RECORD_PCS)
            await page.evaluate("""async ([url, token]) => { window.__room = new LivekitClient.Room();
                await window.__room.connect(url, token, {rtcConfig: {iceTransportPolicy: "relay"}}); }""",
                                [os.environ["LIVEKIT_URL"], _token(room, "human_standin")])
            _post(f"/api/concierge/rooms/{room}/bots", {})

            async def bot_in():
                return await page.evaluate(
                    "() => [...window.__room.remoteParticipants.values()].some(p => p.identity.startsWith('bot_'))")
            joined = await _until(bot_in, 420, "bot in the room")
            async def heard():
                st = await page.evaluate(_RELAY_STATS)
                return st["relay"] and st["bytes"] > 5000
            await _until(heard, 90, "the bot's audio over a relayed path")
            return f"the bot's greeting reached a relay-only Chromium over TURN/TLS 443 (bot joined after {joined:.0f}s)"
        finally:
            await browser.close()


SCENARIOS = {"start": scenario_start, "stoptask": scenario_stoptask, "removed": scenario_removed,
             "kill9": scenario_kill9, "stop_early": scenario_stop_early,
             "audio_recording": scenario_audio_recording, "video_recording": scenario_video_recording,
             "transcript": scenario_transcript, "two_humans": scenario_two_humans, "refresh": scenario_refresh,
             "chat": scenario_chat, "auto_record": scenario_auto_record, "our_models": scenario_our_models, "record_auth": scenario_record_auth,
             "study_prolific": scenario_study_prolific, "session_limit": scenario_session_limit,
             "turn_relay": scenario_turn_relay, "bot_ready": scenario_bot_ready,
             "prewarm": scenario_prewarm}  # last: its Stop preparing cools the pool the run prepared


SCENARIO_TIMEOUT = 600
TIMEOUTS = {"transcript": 3300, "our_models": 3300}  # may wait for cold GPU services first


async def _prepared():
    """Prepare for study, as a researcher would, so scenarios don't wait 2-3 min for a
    machine (no bot machine is kept warm on staging: LEDGER)."""
    from datetime import datetime, timezone

    _post("/api/console/login", {"password": os.environ["CONSOLE_PASSWORD"]})
    until = (datetime.now(timezone.utc) + timedelta(minutes=90)).isoformat()
    _capacity("POST", {"sessions": 1, "until": until})
    waited = await _until(lambda: _capacity()["ready_instances"] >= 1, 420, "a prepared bot machine")
    print(f"prepared: a bot machine ready after {waited:.0f} s", flush=True)


async def main(names):
    def app_ready():
        services = ecs.describe_services(cluster=CLUSTER, services=[f"meetlab-v2-staging-{n}" for n in APP_SERVICES])["services"]
        return not still_deploying(services)
    waited = await _until(app_ready, 900, "LiveKit, meet and the runner done deploying")
    if waited > 5:
        print(f"waited {waited:.0f} s for the app's services to finish deploying", flush=True)
    await _prepared()
    try:
        return await _run(names)
    finally:
        _capacity("DELETE")  # the pool returns to its baseline even if a scenario hangs


async def _run(names):
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
    if os.getenv("NO_VIDEO"):  # our own LiveKit with egress switched off (egress_count = 0, egress_server.tf)
        skipped = [n for n in names if n in ("video_recording", "auto_record")]
        if skipped:
            print(f"not run (no video recording on this LiveKit): {', '.join(skipped)}", flush=True)
        names = [n for n in names if n not in skipped]
    if "our_models" in names and not (_service_on("llm") and _service_on("tts")):  # model_services (models.tf)
        print("not run (our models are off): our_models", flush=True)
        names.remove("our_models")
    sys.exit(0 if asyncio.run(main(names)) else 1)
