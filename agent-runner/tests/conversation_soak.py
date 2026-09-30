"""Load test that can hear the bot.

Replaces soak_meeting.py, which could not. That harness published one five-word
question on a loop from a microphone that never stopped, never subscribed to the
bot's audio track, and scored a run on whether the HTTP call starting the bot
returned 200. On 2026-08-20 it reported "40/40 bots started, 0 errored rooms" for
a run in which the bot said nothing but its opening greeting. A person joined the
room, heard silence, and knew more in thirty seconds than the harness knew in six
minutes.

What is different here:

  * **It subscribes to the bot's audio.** A reply is bytes arriving on the bot's
    track. Not a log line, not a database row — the thing a participant would
    actually hear.
  * **Turn-based speech.** Publish a turn, stop, wait. The microphone toggles the
    way a person's does, so voice-activity detection and turn-taking see a
    realistic signal rather than a continuous stream.
  * **Real conversations.** Five multi-turn scripts with follow-ups that only make
    sense given the previous answer, so context has to survive to answer them.
  * **A verdict.** Reply rate first, then latency. A run the bot slept through
    fails, however much audio was pushed at it.

Usage:
    ROOMS=1 DURATION_MIN=3 uv run python tests/conversation_soak.py

Requires fixtures: uv run python tests/generate_conversation_audio.py
"""
import asyncio
import json
import os
import statistics as st
import sys
import time
import wave
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import multiprocessing as mp

import numpy as np
from livekit import rtc

from conversation_script import (  # noqa: E402
    conversation_for,
    reply_verdict,
    summarise_replies,
)
from _sim_common import MEET_URL, RUNNER_URL, env, start_bot, token  # noqa: E402
from harness_sharding import combine_shards, shard_rooms, shard_worker_count  # noqa: E402
from generate_conversation_audio import FIXTURES, fixture_name  # noqa: E402

ROOMS = int(env("ROOMS", "1"))
ROOM_NAME = env("ROOM_NAME", "")
DURATION_MIN = float(env("DURATION_MIN", "3"))
SETTLE_SECS = float(env("SETTLE_SECS", "3"))
STAGGER = float(env("STAGGER", "2"))
SAMPLE_RATE = 24000
FRAME_MS = 20

# Audio quieter than this is silence, not speech. The bot's track carries frames
# continuously once published, so "did audio arrive" is not the question —
# "did audio with energy in it arrive" is.
SPEECH_RMS = 300.0

# How long the bot's track must stay quiet before the simulated participant
# treats the answer as finished. Must clear the inter-sentence synthesis gap
# (~350-600ms) with margin; a person also leaves a beat before replying.
QUIET_GAP_S = float(env("QUIET_GAP_S", "1.8"))

# Live LiveKit handles, held so nothing is garbage collected mid-run. Kept here
# rather than on the per-room result because results cross a process boundary:
# an rtc.AudioSource cannot be pickled, so a result carrying one silently fails
# to send and the whole shard is reported lost.
_KEEPALIVE: list = []


def load_turn_audio(text: str) -> np.ndarray:
    path = FIXTURES / fixture_name(text)
    if not path.exists():
        raise SystemExit(
            f"Missing fixture for {text[:40]!r}.\n"
            f"Run: uv run python tests/generate_conversation_audio.py"
        )
    with wave.open(str(path)) as wf:
        # OpenAI streams its WAV output, so the header carries a placeholder
        # frame count (2147483647) rather than the real one. Read until EOF
        # instead of believing it.
        chunks = []
        while chunk := wf.readframes(4096):
            chunks.append(chunk)
        data = np.frombuffer(b"".join(chunks), dtype=np.int16)
        if wf.getnchannels() == 2:
            data = data[::2]
        return data


class BotListener:
    """Watches the bot's audio track: when it speaks, and whether it is quiet.

    This is the point of the rewrite — a reply is audible bytes, so that is what
    gets measured, rather than a log line or a database row.

    Two signals, and both matter. `first_audio_since_arm` answers "did the bot
    respond to *this* turn". `last_audio_at` answers "has the bot finished
    talking", which is what a person waits for before speaking again. Measuring
    without the second one produces negative latencies, because the bot is still
    finishing its previous answer while the next turn is being spoken.
    """

    def __init__(self):
        self.first_audio_since_arm: float | None = None
        self.last_audio_at: float = 0.0
        self._task: asyncio.Task | None = None

    def watch(self, track: rtc.Track) -> None:
        stream = rtc.AudioStream(track)

        async def _pump():
            async for event in stream:
                samples = np.frombuffer(event.frame.data, dtype=np.int16)
                if not samples.size:
                    continue
                rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
                if rms > SPEECH_RMS:
                    now = time.time()
                    self.last_audio_at = now
                    if self.first_audio_since_arm is None:
                        self.first_audio_since_arm = now

        self._task = asyncio.create_task(_pump())

    def arm(self) -> None:
        """Start listening for a reply to the turn that just finished."""
        self.first_audio_since_arm = None

    async def wait_until_quiet(self, *, quiet_for: float = QUIET_GAP_S, timeout: float = 25.0) -> None:
        """Wait for the bot to stop talking, as a person would before speaking.

        `quiet_for` has to exceed the bot's *inter-sentence* gap, not just any
        gap. TTS is aggregated per sentence, so while the next sentence is being
        synthesised the track goes quiet for roughly 350-600ms (see
        interruption.py, which documents the same threshold problem for
        BOT_VAD_STOP_SECS). At 600ms this listener decided the bot had finished
        mid-reply and spoke over it — which barged the bot into silence, lost the
        reply, and credited the tail of the previous answer to the next turn as a
        179ms "response". Every reply-rate figure from the 2026-08-20 ramp is
        wrong for that reason.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if time.time() - self.last_audio_at > quiet_for:
                return
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()


async def run_room(idx: int) -> dict:
    room_name = (
        ROOM_NAME if ROOM_NAME and ROOMS == 1
        else f"convo-{uuid4().hex[:6]}-r{idx:02d}"
    )
    script = conversation_for(idx)
    # expected counts turns actually attempted, since the script cycles.
    result = {"room": room_name, "idx": idx, "expected": 0,
              "pairs": [], "error": None, "disconnected": False}

    try:
        resp = start_bot(room_name)
        result["session_id"] = resp.get("session_id")
    except Exception as e:
        result["error"] = f"/start failed: {e!r}"
        return result

    identity = f"speaker_{idx}_{uuid4().hex[:4]}"
    room = rtc.Room()
    listener = BotListener()

    @room.on("track_subscribed")
    def _on_track(track, publication, participant):
        # Never let a handler raise: the SDK reports it as "error running user
        # callback" and the room can end up in a state it cannot resume from.
        try:
            if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity.startswith("bot"):
                listener.watch(track)
        except Exception as e:
            print(f"  [{room_name}] track handler error: {e!r}")

    closing = {"intentional": False}

    @room.on("disconnected")
    def _on_disconnected(*a):
        if closing["intentional"]:
            return  # the run finished and we asked to leave
        # Silent disconnects were previously indistinguishable from the bot
        # simply not replying, which is how a harness fault looks like a system
        # fault.
        result["disconnected"] = True
        print(f"  [{room_name}] DISCONNECTED mid-run — results for this room are suspect")

    try:
        await room.connect(env("LIVEKIT_URL", ""), token(room_name, identity))
        # Small queue so capture_frame paces close to real time. With the
        # default 1000ms buffer the first second of a clip is accepted
        # instantly, so the publish loop finishes about a second before the bot
        # has actually heard the utterance — and every latency measured from
        # that moment is inflated by the difference.
        source = rtc.AudioSource(SAMPLE_RATE, 1, queue_size_ms=120)
        track = rtc.LocalAudioTrack.create_audio_track("mic", source)
        # Held on the result so neither is garbage collected mid-run; a collected
        # source unpublishes the track and the room tears itself down.
        _KEEPALIVE.append((source, track, room))
        await room.local_participant.publish_track(track)
        # NOT muting between turns, despite it being the more realistic
        # behaviour. In livekit-rtc as vendored here, LocalAudioTrack.mute()
        # makes the SDK unpublish and republish the track: every toggle produces
        # a new track SID, the client's own publication map goes out of sync
        # (a storm of KeyError inside _on_room_event), and the room eventually
        # drops. That churn — not the network — is what cut reply rates to 24%
        # in the 2026-08-20 verification run.
        #
        # An unmuted participant sending silence between turns is also a fair
        # model of a real meeting, where most people stay unmuted. The bot sees
        # silence rather than absence, which its VAD already handles.
        print(f"  [{room_name}] joined as {identity} — listening for the bot")

        # Let the bot join, greet, and finish greeting before the first turn —
        # otherwise turn one is measured against the tail of the greeting.
        await asyncio.sleep(SETTLE_SECS + 4.0)

        deadline = time.time() + DURATION_MIN * 60
        # Cycle the script rather than running it once. A real meeting keeps
        # going, and a room that ends after five turns is too short for a person
        # to join and listen to.
        turns = (t for _ in iter(int, 1) for t in script)
        for turn in turns:
            if time.time() > deadline:
                break
            result["expected"] += 1

            # Wait for the bot to finish its previous answer, as a person would.
            # Speaking over it would credit that answer to this turn and produce
            # a negative latency.
            await listener.wait_until_quiet()

            audio = load_turn_audio(turn.text)

            # Unmute, speak, mute again — the microphone genuinely toggles, so
            # the room shows what a real participant taking a turn looks like and
            # the bot sees an absence of audio between turns rather than silence.
            n = int(SAMPLE_RATE * FRAME_MS / 1000)
            for i in range(0, len(audio) - n, n):
                chunk = audio[i : i + n]
                # No manual sleep. capture_frame paces itself against the
                # source's queue — adding a sleep on top publishes at roughly
                # half real-time, which stretches the speech, leaves gaps the
                # server reads as pauses, and stops the participant ever
                # registering as an active speaker.
                await source.capture_frame(
                    rtc.AudioFrame(chunk.tobytes(), SAMPLE_RATE, 1, len(chunk))
                )
            turn_end = time.time()

            # Only now does bot audio count as a reply to this turn.
            listener.arm()
            waited = 0.0
            while listener.first_audio_since_arm is None and waited < turn.expect_reply_within_s:
                await asyncio.sleep(0.05)
                waited += 0.05
            result["pairs"].append((turn_end, listener.first_audio_since_arm))

            await asyncio.sleep(turn.pause_after_s)

    except Exception as e:
        result["error"] = repr(e)
    finally:
        await listener.stop()
        closing["intentional"] = True
        try:
            await room.disconnect()
        except Exception:
            pass
    return result


async def _run_shard(indices: list[int]) -> list[dict]:
    async def staggered(i, slot):
        await asyncio.sleep(slot * STAGGER)
        return await run_room(i)

    return list(await asyncio.gather(
        *(staggered(i, slot) for slot, i in enumerate(indices))
    ))


def _shard_entry(indices: list[int], out) -> None:
    """Worker-process entry point. Own interpreter, own event loop."""
    try:
        results = asyncio.run(_run_shard(indices))
    except Exception as e:  # a dead worker must still report its share
        results = [{"room": f"shard-{indices[0] if indices else '?'}", "idx": i,
                    "expected": 0, "pairs": [], "error": repr(e),
                    "disconnected": False} for i in indices]
    out.put(results)


def main() -> int:
    workers = shard_worker_count(rooms=ROOMS, cpus=int(env("HARNESS_WORKERS", "0")) or (os.cpu_count() or 2))
    shards = shard_rooms(ROOMS, workers)
    print(f"\nconversation soak — {ROOMS} room(s) across {len(shards)} process(es)")
    print(f"target: {MEET_URL or RUNNER_URL}\n")

    if len(shards) <= 1:
        results = asyncio.run(_run_shard(shards[0] if shards else []))
    else:
        # Separate processes, not tasks. One interpreter cannot pace twenty
        # real-time audio publishers and twenty stream consumers, which is why
        # the 2026-08-20 ramp measured the harness rather than the server.
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        procs = [ctx.Process(target=_shard_entry, args=(sh, q), daemon=True)
                 for sh in shards]
        for pr in procs:
            pr.start()

        # Bounded wait. A child that dies before reporting would otherwise hang
        # the parent forever on q.get(), which is a worse failure than a missing
        # shard: the run never ends and nothing says why.
        budget = DURATION_MIN * 60 + 180
        deadline = time.time() + budget
        collected = []
        for _ in procs:
            remaining = max(1.0, deadline - time.time())
            try:
                collected.append(q.get(timeout=remaining))
            except Exception:
                print("  a shard failed to report within the budget")
                break
        for pr in procs:
            pr.join(timeout=15)
            if pr.is_alive():
                pr.terminate()

        results = [r for batch in collected for r in batch]
        reported = {r["idx"] for r in results}
        # Rooms whose worker never reported still count against the total, so a
        # lost shard cannot quietly shrink the denominator.
        for sh in shards:
            for i in sh:
                if i not in reported:
                    results.append({"room": f"room-{i}", "idx": i, "expected": 0,
                                    "pairs": [], "error": "shard lost",
                                    "disconnected": False})

    all_pairs, expected = [], 0
    print(f"{'room':<22} {'turns':>6} {'replied':>8} {'mean ms':>9}  status")
    print("-" * 62)
    for r in sorted(results, key=lambda x: x["idx"]):
        s = summarise_replies(r["pairs"], expected=r["expected"])
        status = r["error"] or ("DISCONNECTED" if r.get("disconnected") else f"{s.reply_rate:.0%}")
        print(
            f"{r['room']:<22} {len(r['pairs']):>6} {s.replied:>8} "
            f"{s.mean_latency_ms:>8.0f}  {status}"
        )
    merged = combine_shards([{"expected": r["expected"], "pairs": r["pairs"]} for r in results])
    all_pairs, expected = merged["pairs"], merged["expected"]

    overall = summarise_replies(all_pairs, expected=expected)
    lat = sorted(overall.latencies_ms)
    ok, why = reply_verdict(
        reply_rate=overall.reply_rate, mean_latency_ms=overall.mean_latency_ms
    )

    def pct(q):
        return lat[min(len(lat) - 1, int(len(lat) * q))] if lat else 0.0

    print("-" * 62)
    print(f"  turns expected   {expected}")
    print(f"  replied          {overall.replied}  ({overall.reply_rate:.0%})")
    if lat:
        print(f"  mean             {overall.mean_latency_ms:.0f} ms")
        print(f"  p10 / p50 / p90  {pct(0.10):.0f} / {st.median(lat):.0f} / {pct(0.90):.0f} ms")
        print(f"  max              {max(lat):.0f} ms")
    print(f"\n  VERDICT: {'PASS' if ok else 'FAIL'} — {why}\n")

    if path := env("SOAK_RESULTS_PATH", ""):
        Path(path).write_text(json.dumps({
            "rooms": ROOMS, "expected": expected, "replied": overall.replied,
            "reply_rate": overall.reply_rate, "mean_ms": overall.mean_latency_ms,
            "latencies_ms": lat, "pass": ok,
        }, indent=2))

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
