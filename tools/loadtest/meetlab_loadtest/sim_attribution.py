"""Three people talk over each other: is each one's speech stored as theirs?

Diagnosis F11 (medium confidence, never reproduced): overlapping speakers merge
into one turn, or a turn lands under the wrong speaker. This run decides whether
the pipeline port (Pipecat's per-participant workers) has a failing test to aim at.

Three participants, three different sentences in three different voices, started
0.15 s apart so they overlap. Then the stored turns are read back and judged.

    SPEAKERS=abc (default) all three; SPEAKERS=a one speaker alone, the control run
    OFFSET_S=0.15 (default) seconds between one speaker's start and the next's
    GROUP_LINK=1 no fixed room: a group start link matches them (make sim-group); also
                 checks all got the same room and one session ran in it
"""
import asyncio
import os
import re
import secrets
import sys
import time
from uuid import uuid4

LINES = {  # conversation_script.py, first turn of three conversations (three voices)
    "a": "What should we prioritise for the leadership role this quarter?",
    "b": "We are seeing higher latency on the transcription service since Tuesday.",
    "c": "Can you explain what a retrieval augmented generation system does?",
}
PRESENT = 0.6  # share of a line's words a turn must contain to hold that line


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def verdict(lines: dict[str, str], stored: list[tuple[str, str]]) -> list[str]:
    """Problems with stored (speaker, text) turns against what each speaker said."""
    def held(text):
        return sorted(s for s, line in lines.items() if len(_words(text) & _words(line)) >= PRESENT * len(_words(line)))

    problems, heard = [], set()
    for speaker, text in stored:
        found = held(text)
        if len(found) > 1:
            problems.append(f"{speaker}: one turn holds " + " and ".join(s + "'s" for s in found) + " words")
        elif found and found[0] != speaker:
            problems.append(f"{speaker}: stored {found[0]}'s words")
        if speaker in found:
            heard.add(speaker)
    problems += [f"{s}: never transcribed" for s in lines if s not in heard]
    return problems


async def drive_ticket(reply, poll, ready, clock, sleep, deadline_s: float = 120, every_s: float = 1.0) -> str:
    """One ticket's way to its room, as the waiting page goes: poll, press Ready once in the
    ready check, until the view is starting with a room. Fails loud on missed/expired/done."""
    deadline = clock() + deadline_s
    pressed = False
    while True:
        view = reply["view"]
        if view["kind"] == "starting" and view.get("room"):
            return view["room"]
        if view["kind"] not in ("searching", "ready_check", "starting"):
            raise RuntimeError(f"ticket {reply['ticket']} ended {view['kind']}")
        if clock() >= deadline:
            raise TimeoutError(f"ticket {reply['ticket']} has no room after {deadline_s}s (last {view})")
        if view["kind"] == "ready_check" and not view.get("pressed") and not pressed:
            pressed = True
            reply = await ready(reply["ticket"])
            continue
        await sleep(every_s)
        reply = await poll(reply["ticket"])


async def group_room(request, n: int) -> tuple[str, str]:
    """A size-n link; n participants (distinct Prolific IDs) each take a ticket and are driven
    to a room. Returns (room, link_id). The link is stopped here if they don't share one room."""
    link = request("POST", "/links", {"name": f"sim-group-{secrets.token_hex(3)}", "pool": ["global"],
                                      "size": n, "delay_s": 0, "ready_check_s": 30})["link_id"]

    async def call(method, path, body=None):
        return await asyncio.to_thread(request, method, path, body)

    async def one():
        reply = await call("POST", "/tickets", {"link_id": link, "participant_id": secrets.token_hex(12)})
        return await drive_ticket(reply, lambda t: call("POST", f"/tickets/{t}/poll"),
                                  lambda t: call("POST", f"/tickets/{t}/ready"), time.monotonic, asyncio.sleep)
    try:
        got = await asyncio.gather(*(one() for _ in range(n)))
        if len(set(got)) != 1:
            raise RuntimeError(f"participants got different rooms: {got}")
    except BaseException:
        request("PATCH", f"/links/{link}", {"active": False})
        raise
    return got[0], link


async def main() -> int:
    from livekit import rtc
    from sqlalchemy import select

    from meetlab_loadtest import audio_scenarios as audio
    from meetlab_loadtest._sim_common import LIVEKIT_URL, request, stream_float, token
    from meetlab_core.db.engine import AsyncSessionLocal
    from meetlab_core.db.models import Conversation, Utterance
    from meetlab_loadtest.generate_conversation_audio import FIXTURES, fixture_name

    lines = {s: LINES[s] for s in os.environ.get("SPEAKERS", "abc")}
    group, link = os.environ.get("GROUP_LINK") == "1", None
    if group:
        room_name, link = await group_room(request, len(lines))
        print(f"[attribution] link={link} room={room_name} (all {len(lines)} tickets)")
    else:
        room_name = f"sim-attr-{uuid4().hex[:6]}"
        started = request("POST", "/start", {"room_name": room_name})
        session, bot = started["session_id"], started["bot_identity"]
        print(f"[attribution] room={room_name} session={session}")
        await asyncio.sleep(15)  # the bot joins and greets

    rooms, who = [], {}
    for speaker in lines:
        identity = f"sim_{speaker}_{uuid4().hex[:4]}"
        room = rtc.Room()
        await room.connect(LIVEKIT_URL, token(room_name, identity))
        rooms.append((speaker, room))
        who[identity] = speaker
    if group:
        await asyncio.sleep(15)  # every expected human is in: the bot greets

    async def say(i, speaker, room):
        signal, sr = audio.load_wav_float(str(FIXTURES / fixture_name(LINES[speaker])))
        await asyncio.sleep(float(os.environ.get("OFFSET_S", "0.15")) * i)
        await stream_float(room, signal, sr, speaker)

    await asyncio.gather(*(say(i, s, r) for i, (s, r) in enumerate(rooms)))
    await asyncio.sleep(25)  # transcription, then the bot's reply

    problems = []
    async with AsyncSessionLocal() as db:
        if group:
            sessions = (await db.execute(select(Conversation.id, Conversation.bot_identity)
                                         .where(Conversation.room_name == room_name))).all()
            print(f"[attribution] sessions in {room_name}: {len(sessions)}")
            if len(sessions) != 1:
                problems.append(f"{len(sessions)} sessions ran in the room, not 1")
            session, bot = sessions[0] if sessions else (None, None)
        rows = (await db.execute(select(Utterance.speaker_id, Utterance.text)
                                 .where(Utterance.conv_id == session, Utterance.speaker_id != bot)
                                 .order_by(Utterance.ts))).all()
    for _, room in rooms:
        await room.disconnect()
    if link:
        request("PATCH", f"/links/{link}", {"active": False})
    stored = [(who.get(sid, sid), text) for sid, text in rows]  # one person's words a row (bot.py)
    for speaker, text in stored:
        print(f"  stored  {speaker}: {text}")
    problems += verdict(lines, stored)
    print("PASS no misattribution or merged turns" if not problems else "FAIL " + "; ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
