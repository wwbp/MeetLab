"""Three people talk over each other: is each one's speech stored as theirs?

Diagnosis F11 (medium confidence, never reproduced): overlapping speakers merge
into one turn, or a turn lands under the wrong speaker. This run decides whether
the pipeline port (Pipecat's per-participant workers) has a failing test to aim at.

Three participants, three different sentences in three different voices, started
0.15 s apart so they overlap. Then the stored turns are read back and judged.

    SPEAKERS=abc (default) all three; SPEAKERS=a one speaker alone, the control run
"""
import asyncio
import os
import re
import sys
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

LINES = {  # conversation_script.py, first turn of three conversations (three voices)
    "a": "What should we prioritise for the leadership role this quarter?",
    "b": "We are seeing higher latency on the transcription service since Tuesday.",
    "c": "Can you explain what a retrieval augmented generation system does?",
}
PRESENT = 0.6  # share of a line's words a turn must contain to hold that line


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def heard_as(speaker: str, text: str, who: dict[str, str]) -> list[tuple[str, str]]:
    """A stored turn as the LLM reads it: each "identity: words" piece, by its label."""
    parts = re.split(r"(?:^|\s)(" + "|".join(map(re.escape, who)) + r"): ", text) if who else [text]
    if len(parts) == 1:
        return [(speaker, text)]
    return [(who[label], words.strip()) for label, words in zip(parts[1::2], parts[2::2])]


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


async def main() -> int:
    from livekit import rtc
    from sqlalchemy import select

    import audio_scenarios as audio
    from _sim_common import LIVEKIT_URL, request, stream_float, token
    from db.engine import AsyncSessionLocal
    from db.models import Utterance
    from generate_conversation_audio import FIXTURES, fixture_name

    room_name = f"sim-attr-{uuid4().hex[:6]}"
    started = request("POST", "/start", {"room_name": room_name})
    session, bot = started["session_id"], started["bot_identity"]
    print(f"[attribution] room={room_name} session={session}")
    await asyncio.sleep(15)  # the bot joins and greets

    lines = {s: LINES[s] for s in os.environ.get("SPEAKERS", "abc")}
    rooms, who = [], {}
    for speaker in lines:
        identity = f"sim_{speaker}_{uuid4().hex[:4]}"
        room = rtc.Room()
        await room.connect(LIVEKIT_URL, token(room_name, identity))
        rooms.append((speaker, room))
        who[identity] = speaker

    async def say(i, speaker, room):
        signal, sr = audio.load_wav_float(str(FIXTURES / fixture_name(LINES[speaker])))
        await asyncio.sleep(0.15 * i)
        await stream_float(room, signal, sr, speaker)

    await asyncio.gather(*(say(i, s, r) for i, (s, r) in enumerate(rooms)))
    await asyncio.sleep(25)  # transcription, then the bot's reply

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Utterance.speaker_id, Utterance.text)
                                 .where(Utterance.conv_id == session, Utterance.speaker_id != bot)
                                 .order_by(Utterance.ts))).all()
    for _, room in rooms:
        await room.disconnect()
    stored = [piece for sid, text in rows for piece in heard_as(who.get(sid, sid), text, who)]
    for speaker, text in stored:
        print(f"  stored  {speaker}: {text}")
    problems = verdict(lines, stored)
    print("PASS no misattribution or merged turns" if not problems else "FAIL " + "; ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
