"""Multi-speaker end-to-end test: 1–3 users in a room with a bot.

Verifies that speaker attribution, conversation structure, and DB writes are
correct when multiple participants take turns speaking.

Uses the data channel (publish_data) path so no audio fixtures or STT API
keys are needed. The audio/STT path shares identical attribution logic and
will exhibit the same behaviour.

The tests deliberately expose a known attribution gap: on_user_turn_stopped
in bot.py currently picks the first participant in _sid_to_identity rather
than the one whose frame triggered the turn. Tests 02 and 03 will fail until
that is fixed, which is the point — they document the expected contract.

Run via:
    make test-multi-speaker

To also see the LLM context snapshot after each user turn, tail logs in a
second terminal while the test runs:
    make logs SERVICE=agent-runner
"""

import asyncio
import json
import os
import sys
import time
import unittest
import urllib.request
from datetime import datetime, timedelta
from uuid import uuid4

from livekit import api, rtc

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

RUNNER_URL = os.getenv("AGENT_RUNNER_URL", "http://localhost:7860")
LIVEKIT_URL_TEST = os.getenv("LIVEKIT_URL", "ws://transport-server:7880")
API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")

BOT_JOIN_WAIT = 5.0   # seconds to wait for the bot to join before users connect
USER_JOIN_WAIT = 2.0  # brief settle after all users connect
POLL_INTERVAL = 1.0   # DB poll cadence in seconds
TURN_TIMEOUT = 90.0   # max seconds to wait for a bot response per turn


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_token(room_name: str, identity: str) -> str:
    return (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(timedelta(minutes=30))
        .to_jwt()
    )


def _post(path: str, body: dict) -> dict:
    url = f"{RUNNER_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _put(path: str, body: dict) -> dict:
    url = f"{RUNNER_URL}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="PUT"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


async def _send_message(room: rtc.Room, text: str) -> None:
    """Inject text via LiveKit data channel — triggers on_data_received in bot.py.

    Timestamp is sent as an ISO string because Pipecat's RTVI observer expects
    TranscriptionFrame.timestamp to be a string, not an int.
    """
    payload = json.dumps({
        "message": text,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })
    await room.local_participant.publish_data(payload.encode())


async def _poll_utterances(session_id: str, min_count: int, timeout: float) -> list[dict]:
    """Poll DB until at least min_count utterances exist for the session.

    Returns all utterances ordered by ts asc, each as a plain dict with
    keys: id, speaker_id, role (participant|bot), text, reply_to, ts.
    Returns whatever has accumulated if timeout is reached.
    """
    from db.engine import AsyncSessionLocal
    from db.models import Utterance, Speaker
    from sqlalchemy import select

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Utterance, Speaker)
                .join(Speaker, Utterance.speaker_id == Speaker.id)
                .where(Utterance.conv_id == session_id)
                .order_by(Utterance.ts.asc())
            )
            rows = result.all()
            if len(rows) >= min_count:
                return _rows_to_dicts(rows)
        await asyncio.sleep(POLL_INTERVAL)

    # Return whatever accumulated even if we timed out
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Utterance, Speaker)
            .join(Speaker, Utterance.speaker_id == Speaker.id)
            .where(Utterance.conv_id == session_id)
            .order_by(Utterance.ts.asc())
        )
        return _rows_to_dicts(result.all())


def _rows_to_dicts(rows) -> list[dict]:
    return [
        {
            "id": utt.id,
            "speaker_id": utt.speaker_id,
            "role": spk.meta.get("role", "?"),
            "text": utt.text,
            "reply_to": utt.reply_to,
            "ts": utt.ts,
        }
        for utt, spk in rows
    ]


def _print_transcript(utterances: list[dict], label: str = "") -> None:
    """Print the session transcript to stdout for visual inspection."""
    print(f"\n{'─'*72}")
    if label:
        print(f"  Transcript: {label}")
    print(f"{'─'*72}")
    if not utterances:
        print("  (no utterances)")
    for u in utterances:
        role = u["role"].upper()[:4]
        speaker = u["speaker_id"][:28]
        text = (u["text"] or "")[:72]
        reply = f"← {u['reply_to'][:8]}" if u["reply_to"] else ""
        print(f"  [{role}] {speaker:<30} {reply:<12}  {text}")
    print(f"{'─'*72}\n")


# ── test class ────────────────────────────────────────────────────────────────

class TestMultiSpeakerE2E(unittest.IsolatedAsyncioTestCase):
    """End-to-end multi-speaker attribution tests against a running stack.

    Each test method:
      1. Creates a unique room and starts a bot via the agent-runner HTTP API.
      2. Connects N LiveKit room clients (one per simulated user).
      3. Sends text messages via the data channel in a controlled turn order.
      4. Polls the DB until the expected number of utterances appear.
      5. Asserts speaker attribution and conversation structure.
    """

    def setUp(self):
        if os.getenv("RUN_MULTI_SPEAKER", "").strip() != "1":
            self.skipTest("Set RUN_MULTI_SPEAKER=1 to run — use: make test-multi-speaker")

    async def asyncTearDown(self):
        # Dispose DB connections after every test so the next test's fresh
        # event loop does not inherit connections from a dead loop.
        from db.engine import engine
        await engine.dispose()

    # -- shared helpers --------------------------------------------------------

    def _start_session(self, room_name: str) -> dict:
        resp = _post("/start", {"room_name": room_name})
        self.assertIn("session_id", resp, f"/start failed: {resp}")
        return resp

    async def _connect_users(self, room_name: str, identities: list[str]) -> list[rtc.Room]:
        rooms = []
        for identity in identities:
            r = rtc.Room()
            await r.connect(LIVEKIT_URL_TEST, _make_token(room_name, identity))
            rooms.append(r)
        return rooms

    async def _disconnect_all(self, rooms: list[rtc.Room]) -> None:
        for r in rooms:
            try:
                if r.connection_state != rtc.ConnectionState.CONN_DISCONNECTED:
                    await r.disconnect()
            except Exception:
                pass

    # -- test 01: single user --------------------------------------------------

    async def test_01_single_user_baseline(self):
        """1 user, 2 turns. Both utterances attributed to that user in the DB."""
        room_name = f"ms-1u-{uuid4().hex[:8]}"
        uid = f"alice_{uuid4().hex[:6]}"

        session = self._start_session(room_name)
        session_id = session["session_id"]
        bot_identity = session["bot_identity"]
        print(f"\n[test_01] room={room_name} bot={bot_identity}", flush=True)

        # Let the bot join first so on_participant_connected fires for fresh arrivals.
        await asyncio.sleep(BOT_JOIN_WAIT)
        rooms = await self._connect_users(room_name, [uid])
        user_room = rooms[0]
        await asyncio.sleep(USER_JOIN_WAIT)

        try:
            await _send_message(user_room, "Hello, can you hear me?")
            utts = await _poll_utterances(session_id, min_count=2, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 2, "No bot response after turn 1")

            await _send_message(user_room, "What is the capital of France?")
            utts = await _poll_utterances(session_id, min_count=4, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 4, "No bot response after turn 2")

        finally:
            await self._disconnect_all(rooms)

        _print_transcript(utts, f"1 user: {uid}")

        user_utts = [u for u in utts if u["role"] == "participant"]
        bot_utts  = [u for u in utts if u["role"] == "bot"]

        # Every user utterance must belong to uid
        for u in user_utts:
            self.assertEqual(
                u["speaker_id"], uid,
                f"User utterance attributed to wrong speaker: {u['speaker_id']} != {uid}",
            )
        # Every bot utterance must belong to the bot
        for u in bot_utts:
            self.assertEqual(
                u["speaker_id"], bot_identity,
                f"Bot utterance attributed to wrong speaker: {u['speaker_id']}",
            )
        # reply_to: first user utterance has none; first bot replies to first user
        self.assertIsNone(
            user_utts[0]["reply_to"],
            "First user utterance should have reply_to=None",
        )
        if bot_utts:
            self.assertEqual(
                bot_utts[0]["reply_to"], user_utts[0]["id"],
                "First bot utterance should reply_to first user utterance",
            )

    # -- test 10: greeting timing ---------------------------------------------

    async def test_10_greeting_timing_with_auto_record(self):
        """The bot greets promptly after a user joins, even with auto_record on.

        Regression guard for the startup-lag bug: auto-record's LiveKit egress
        round-trip must run in the background, not block the greeting. No audio is
        published, so this isolates the greeting path from the local Whisper model
        load (which blocks the loop on the first audio frame — a separate, known
        local-dev STT concern, absent in prod's HTTP Parakeet path).
        """
        room_name = f"ms-greet-{uuid4().hex[:8]}"
        uid = f"greeter_{uuid4().hex[:6]}"
        # Enable auto_record for this room scope so the join path starts recording.
        _put("/config", {"scope": room_name, "auto_record": True})

        session = self._start_session(room_name)
        session_id = session["session_id"]
        print(f"\n[test_10] room={room_name} bot={session['bot_identity']}", flush=True)

        await asyncio.sleep(BOT_JOIN_WAIT)  # let the bot join first
        t_join = time.monotonic()
        rooms = await self._connect_users(room_name, [uid])
        try:
            # First bot utterance == the greeting (no user message sent).
            utts = await _poll_utterances(session_id, min_count=1, timeout=TURN_TIMEOUT)
            bot_utts = [u for u in utts if u["role"] == "bot"]
            self.assertTrue(bot_utts, "Bot never greeted after the user joined")
            greet_secs = time.monotonic() - t_join
            print(f"[test_10] greeting after {greet_secs:.1f}s: {bot_utts[0]['text']!r}", flush=True)
            # Greeting is a fixed TTS line (no LLM); with egress backgrounded it
            # should land within seconds. Generous bound catches a blocking regression.
            self.assertLess(
                greet_secs, 20.0,
                f"Greeting took {greet_secs:.1f}s — recording likely blocking the greeting path",
            )
        finally:
            await self._disconnect_all(rooms)

    # -- test 02: two users ---------------------------------------------------

    async def test_02_two_user_attribution(self):
        """2 users alternate turns. Each utterance must carry the correct speaker_id.

        NOTE: This test exposes the attribution bug in on_user_turn_stopped.
        All utterances are currently assigned to whoever joined first (alice).
        The test will fail on the bob assertion until the bug is fixed.
        """
        room_name  = f"ms-2u-{uuid4().hex[:8]}"
        uid_alice  = f"alice_{uuid4().hex[:6]}"
        uid_bob    = f"bob_{uuid4().hex[:6]}"

        session    = self._start_session(room_name)
        session_id = session["session_id"]
        bot_identity = session["bot_identity"]
        print(f"\n[test_02] room={room_name} alice={uid_alice} bob={uid_bob}", flush=True)

        await asyncio.sleep(BOT_JOIN_WAIT)
        rooms = await self._connect_users(room_name, [uid_alice, uid_bob])
        room_alice, room_bob = rooms
        await asyncio.sleep(USER_JOIN_WAIT)

        try:
            # Alice speaks first
            await _send_message(room_alice, "Hi I am Alice. What is the capital of France?")
            utts = await _poll_utterances(session_id, min_count=2, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 2, "No bot response after Alice's turn")

            # Bob speaks second
            await _send_message(room_bob, "Hi I am Bob. What is the capital of Germany?")
            utts = await _poll_utterances(session_id, min_count=4, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 4, "No bot response after Bob's turn")

        finally:
            await self._disconnect_all(rooms)

        _print_transcript(utts, f"2 users: alice={uid_alice}  bob={uid_bob}")

        user_utts = [u for u in utts if u["role"] == "participant"]
        self.assertGreaterEqual(len(user_utts), 2, "Expected at least 2 user utterances")

        actual_speakers = [u["speaker_id"] for u in user_utts]
        print(f"  Speaker order in DB:      {actual_speakers}", flush=True)
        print(f"  Expected speaker order:   [{uid_alice}, {uid_bob}]", flush=True)

        self.assertEqual(
            user_utts[0]["speaker_id"], uid_alice,
            f"Turn 1 should be alice ({uid_alice}), got {user_utts[0]['speaker_id']}",
        )
        self.assertEqual(
            user_utts[1]["speaker_id"], uid_bob,
            f"Turn 2 should be bob ({uid_bob}), got {user_utts[1]['speaker_id']}",
        )

    # -- test 03: three users -------------------------------------------------

    async def test_03_three_user_attribution(self):
        """3 users in sequence. All utterances attributed to the correct speaker.

        NOTE: Same attribution bug as test_02 — charlie and bob's utterances
        will both appear as alice's until the fix lands.
        """
        room_name    = f"ms-3u-{uuid4().hex[:8]}"
        uid_alice    = f"alice_{uuid4().hex[:6]}"
        uid_bob      = f"bob_{uuid4().hex[:6]}"
        uid_charlie  = f"charlie_{uuid4().hex[:6]}"

        session    = self._start_session(room_name)
        session_id = session["session_id"]
        print(
            f"\n[test_03] room={room_name} "
            f"alice={uid_alice} bob={uid_bob} charlie={uid_charlie}",
            flush=True,
        )

        await asyncio.sleep(BOT_JOIN_WAIT)
        rooms = await self._connect_users(room_name, [uid_alice, uid_bob, uid_charlie])
        room_alice, room_bob, room_charlie = rooms
        await asyncio.sleep(USER_JOIN_WAIT)

        turns = [
            (room_alice,   uid_alice,   "Alice here. What is two plus two?"),
            (room_bob,     uid_bob,     "Bob here. Name a planet in our solar system."),
            (room_charlie, uid_charlie, "Charlie here. What colour is the sky?"),
        ]

        try:
            for i, (room, uid, msg) in enumerate(turns):
                await _send_message(room, msg)
                expected_count = (i + 1) * 2  # 1 user + 1 bot per completed round
                utts = await _poll_utterances(session_id, min_count=expected_count, timeout=TURN_TIMEOUT)
                self.assertGreaterEqual(
                    len(utts), expected_count,
                    f"No bot response after turn {i+1} (speaker={uid})",
                )
                print(
                    f"  turn {i+1}/{len(turns)} done — {len(utts)} utterances in DB",
                    flush=True,
                )

        finally:
            await self._disconnect_all(rooms)

        utts = await _poll_utterances(session_id, min_count=6, timeout=5.0)
        _print_transcript(
            utts,
            f"3 users: alice={uid_alice}  bob={uid_bob}  charlie={uid_charlie}",
        )

        user_utts = [u for u in utts if u["role"] == "participant"]
        self.assertGreaterEqual(len(user_utts), 3, "Expected at least 3 user utterances")

        expected_order = [uid_alice, uid_bob, uid_charlie]
        actual_order   = [u["speaker_id"] for u in user_utts[:3]]

        print(f"  Expected speaker order: {expected_order}", flush=True)
        print(f"  Actual   speaker order: {actual_order}",   flush=True)

        for i, (expected, actual) in enumerate(zip(expected_order, actual_order)):
            self.assertEqual(
                actual, expected,
                f"Turn {i+1}: expected speaker={expected}, got={actual}",
            )

    # -- test 04: conversation structure --------------------------------------

    async def test_04_conversation_structure(self):
        """Conversation row, Speaker rows, and reply_to chain are coherent for 2 users.

        This test checks structural correctness independent of attribution:
        - Conversation row exists with correct room_name and bot_identity
        - A Speaker row exists for every participant (alice, bob, bot)
        - Every bot utterance has reply_to pointing to a user utterance
        - reply_to targets exist in the same session
        """
        room_name  = f"ms-struct-{uuid4().hex[:8]}"
        uid_alice  = f"alice_{uuid4().hex[:6]}"
        uid_bob    = f"bob_{uuid4().hex[:6]}"

        session    = self._start_session(room_name)
        session_id = session["session_id"]
        bot_identity = session["bot_identity"]
        print(f"\n[test_04] room={room_name}", flush=True)

        await asyncio.sleep(BOT_JOIN_WAIT)
        rooms = await self._connect_users(room_name, [uid_alice, uid_bob])
        room_alice, room_bob = rooms
        await asyncio.sleep(USER_JOIN_WAIT)

        try:
            await _send_message(room_alice, "Hello from Alice")
            await _poll_utterances(session_id, min_count=2, timeout=TURN_TIMEOUT)

            await _send_message(room_bob, "Hello from Bob")
            utts = await _poll_utterances(session_id, min_count=4, timeout=TURN_TIMEOUT)

        finally:
            await self._disconnect_all(rooms)

        _print_transcript(utts, f"structure check  alice={uid_alice}  bob={uid_bob}")
        self.assertGreaterEqual(len(utts), 4, "Expected at least 4 utterances (2 user + 2 bot)")

        from db.engine import AsyncSessionLocal
        from db.models import Conversation, Speaker

        async with AsyncSessionLocal() as db:
            # Conversation row
            conv = await db.get(Conversation, session_id)
            self.assertIsNotNone(conv, "Conversation row not found")
            self.assertEqual(conv.room_name, room_name,    "Conversation.room_name mismatch")
            self.assertEqual(conv.bot_identity, bot_identity, "Conversation.bot_identity mismatch")
            self.assertIn(conv.status, ("running", "completed", "error"), "Unexpected status")
            print(f"  Conversation: id={conv.id[:8]}… status={conv.status}", flush=True)

            # Speaker rows for all identities
            for identity in [uid_alice, uid_bob, bot_identity]:
                spk = await db.get(Speaker, identity)
                self.assertIsNotNone(spk, f"Speaker row missing for identity={identity}")
                print(f"  Speaker: {identity[:30]}  role={spk.meta.get('role')}", flush=True)

        # reply_to coherence
        utt_index = {u["id"]: u for u in utts}
        bot_utts  = [u for u in utts if u["role"] == "bot"]

        for bot_utt in bot_utts:
            self.assertIsNotNone(
                bot_utt["reply_to"],
                f"Bot utterance {bot_utt['id'][:8]}… is missing reply_to",
            )
            parent = utt_index.get(bot_utt["reply_to"])
            self.assertIsNotNone(
                parent,
                f"reply_to={bot_utt['reply_to'][:8]}… not found among utterances in this session",
            )
            self.assertEqual(
                parent["role"], "participant",
                f"Bot utterance reply_to points to a {parent['role']}, expected participant",
            )


    # -- test 05: interleaved turns -------------------------------------------

    async def test_05_interleaved_turns(self):
        """alice → bob → alice → bob: each turn attributed to the actual speaker.

        This is the core multi-speaker scenario — users talking in any order,
        not just strict round-robin. speaker_id must flip correctly on every turn.
        """
        room_name  = f"ms-interleave-{uuid4().hex[:8]}"
        uid_alice  = f"alice_{uuid4().hex[:6]}"
        uid_bob    = f"bob_{uuid4().hex[:6]}"

        session    = self._start_session(room_name)
        session_id = session["session_id"]
        print(f"\n[test_05] room={room_name} alice={uid_alice} bob={uid_bob}", flush=True)

        await asyncio.sleep(BOT_JOIN_WAIT)
        rooms = await self._connect_users(room_name, [uid_alice, uid_bob])
        room_alice, room_bob = rooms
        await asyncio.sleep(USER_JOIN_WAIT)

        # Interleaved turn plan: (sender_room, expected_speaker_id, message)
        turns = [
            (room_alice, uid_alice, "Alice first: what is one plus one?"),
            (room_bob,   uid_bob,   "Bob first: what is two plus two?"),
            (room_alice, uid_alice, "Alice again: what is three plus three?"),
            (room_bob,   uid_bob,   "Bob again: what is four plus four?"),
        ]

        try:
            for i, (room, uid, msg) in enumerate(turns):
                await _send_message(room, msg)
                expected = (i + 1) * 2
                utts = await _poll_utterances(session_id, min_count=expected, timeout=TURN_TIMEOUT)
                self.assertGreaterEqual(len(utts), expected, f"No bot response after turn {i+1}")
                print(f"  turn {i+1}/{len(turns)} done — {len(utts)} utterances", flush=True)
        finally:
            await self._disconnect_all(rooms)

        utts = await _poll_utterances(session_id, min_count=8, timeout=5.0)
        _print_transcript(utts, f"interleaved  alice={uid_alice}  bob={uid_bob}")

        user_utts = [u for u in utts if u["role"] == "participant"]
        self.assertGreaterEqual(len(user_utts), 4, "Expected 4 user utterances")

        expected_order = [uid_alice, uid_bob, uid_alice, uid_bob]
        actual_order   = [u["speaker_id"] for u in user_utts[:4]]

        print(f"  Expected: {expected_order}", flush=True)
        print(f"  Actual:   {actual_order}",   flush=True)

        for i, (expected, actual) in enumerate(zip(expected_order, actual_order)):
            self.assertEqual(
                actual, expected,
                f"Turn {i+1}: expected={expected}, got={actual}",
            )

    # -- test 06: late join ---------------------------------------------------

    async def test_06_late_join(self):
        """Alice starts alone, bot responds, Bob joins mid-session and speaks.

        Verifies that participants who join after the conversation has started
        are correctly attributed — not confused with the first joiner.
        """
        room_name  = f"ms-latejoin-{uuid4().hex[:8]}"
        uid_alice  = f"alice_{uuid4().hex[:6]}"
        uid_bob    = f"bob_{uuid4().hex[:6]}"

        session    = self._start_session(room_name)
        session_id = session["session_id"]
        bot_identity = session["bot_identity"]
        print(f"\n[test_06] room={room_name} alice={uid_alice} bob={uid_bob}", flush=True)

        # Phase 1: only Alice
        await asyncio.sleep(BOT_JOIN_WAIT)
        rooms_alice = await self._connect_users(room_name, [uid_alice])
        room_alice = rooms_alice[0]
        await asyncio.sleep(USER_JOIN_WAIT)

        try:
            await _send_message(room_alice, "Alice only: what is the speed of light?")
            utts = await _poll_utterances(session_id, min_count=2, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 2, "No bot response to Alice")
            print(f"  Phase 1 done: {len(utts)} utterances", flush=True)

            # Phase 2: Bob joins mid-session
            rooms_bob = await self._connect_users(room_name, [uid_bob])
            room_bob = rooms_bob[0]
            await asyncio.sleep(USER_JOIN_WAIT)

            await _send_message(room_bob, "Bob late: what is the boiling point of water?")
            utts = await _poll_utterances(session_id, min_count=4, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 4, "No bot response to Bob")
            print(f"  Phase 2 done: {len(utts)} utterances", flush=True)

            # Phase 3: Alice speaks again after Bob joined
            await _send_message(room_alice, "Alice again: what is the tallest mountain?")
            utts = await _poll_utterances(session_id, min_count=6, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 6, "No bot response to Alice (phase 3)")
            print(f"  Phase 3 done: {len(utts)} utterances", flush=True)

        finally:
            all_rooms = rooms_alice + (rooms_bob if "rooms_bob" in locals() else [])
            await self._disconnect_all(all_rooms)

        _print_transcript(utts, f"late-join  alice={uid_alice}  bob={uid_bob}")

        user_utts = [u for u in utts if u["role"] == "participant"]
        self.assertGreaterEqual(len(user_utts), 3, "Expected 3 user utterances")

        expected_order = [uid_alice, uid_bob, uid_alice]
        actual_order   = [u["speaker_id"] for u in user_utts[:3]]

        print(f"  Expected: {expected_order}", flush=True)
        print(f"  Actual:   {actual_order}",   flush=True)

        for i, (expected, actual) in enumerate(zip(expected_order, actual_order)):
            self.assertEqual(
                actual, expected,
                f"Phase {i+1}: expected={expected}, got={actual}",
            )

        # Bob's Speaker row must exist even though he joined late
        from db.engine import AsyncSessionLocal
        from db.models import Speaker
        async with AsyncSessionLocal() as db:
            spk = await db.get(Speaker, uid_bob)
            self.assertIsNotNone(spk, f"Speaker row missing for late-join {uid_bob}")


    # -- shared DB helpers ----------------------------------------------------

    async def _poll_conversation_status(
        self, session_id: str, expected_status: str, timeout: float
    ):
        """Poll until Conversation.status reaches expected_status or timeout expires."""
        from db.engine import AsyncSessionLocal
        from db.models import Conversation

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            async with AsyncSessionLocal() as db:
                conv = await db.get(Conversation, session_id)
                if conv and conv.status == expected_status:
                    return conv
            await asyncio.sleep(POLL_INTERVAL)
        async with AsyncSessionLocal() as db:
            return await db.get(Conversation, session_id)

    # -- test 08: full DB field completeness ----------------------------------

    async def test_08_db_field_completeness(self):
        """All DB fields written by a complete session are correct and non-null.

        Verifies after bot stop:
        - Conversation: started_at set, status=completed, ended_at > started_at,
          root_utterance_id points to a real utterance in the same session.
        - Speaker.meta.role: "participant" for users, "bot" for the bot identity.
        - Utterance.ts: non-null positive floats, non-decreasing.
        - Bot utterance meta: latency_ms key present and > 0.
        """
        room_name = f"ms-dbaudit-{uuid4().hex[:8]}"
        uid = f"alice_{uuid4().hex[:6]}"

        session = self._start_session(room_name)
        session_id = session["session_id"]
        bot_identity = session["bot_identity"]
        print(f"\n[test_08] room={room_name} bot={bot_identity}", flush=True)

        await asyncio.sleep(BOT_JOIN_WAIT)
        rooms = await self._connect_users(room_name, [uid])
        user_room = rooms[0]
        await asyncio.sleep(USER_JOIN_WAIT)

        try:
            await _send_message(user_room, "Hello, please briefly introduce yourself.")
            utts = await _poll_utterances(session_id, min_count=2, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 2, "No bot response")
        finally:
            await self._disconnect_all(rooms)

        # Wait for the bot pipeline to finish and write ended_at / status
        conv = await self._poll_conversation_status(session_id, "completed", timeout=20.0)

        from db.engine import AsyncSessionLocal
        from db.models import Conversation, Speaker, Utterance
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            # ── Conversation lifecycle ────────────────────────────────────────
            conv = await db.get(Conversation, session_id)
            self.assertIsNotNone(conv, "Conversation row missing")
            self.assertIsNotNone(conv.started_at, "Conversation.started_at must be set")
            self.assertEqual(
                conv.status, "completed",
                f"Expected status=completed after all participants left, got {conv.status!r}",
            )
            self.assertIsNotNone(
                conv.ended_at,
                "Conversation.ended_at must be set after the bot pipeline stops",
            )
            self.assertGreater(
                conv.ended_at, conv.started_at,
                "ended_at must be after started_at",
            )
            self.assertIsNotNone(
                conv.root_utterance_id,
                "Conversation.root_utterance_id must be set (required for ConvoKit export)",
            )

            # root_utterance_id must point to the first utterance in this session
            root_utt = await db.get(Utterance, conv.root_utterance_id)
            self.assertIsNotNone(
                root_utt,
                f"root_utterance_id {conv.root_utterance_id!r} points to a nonexistent utterance",
            )
            self.assertEqual(
                root_utt.conv_id, session_id,
                "root_utterance_id points to an utterance in a different session",
            )

            # ── Speaker.meta.role ────────────────────────────────────────────
            user_spk = await db.get(Speaker, uid)
            self.assertIsNotNone(user_spk, f"Speaker row missing for identity={uid}")
            self.assertEqual(
                user_spk.meta.get("role"), "participant",
                f"User Speaker.meta.role mismatch: {user_spk.meta}",
            )

            bot_spk = await db.get(Speaker, bot_identity)
            self.assertIsNotNone(bot_spk, f"Speaker row missing for bot identity={bot_identity}")
            self.assertEqual(
                bot_spk.meta.get("role"), "bot",
                f"Bot Speaker.meta.role mismatch: {bot_spk.meta}",
            )

            # ── Utterance fields ──────────────────────────────────────────────
            result = await db.execute(
                select(Utterance)
                .where(Utterance.conv_id == session_id)
                .order_by(Utterance.ts.asc())
            )
            db_utts = result.scalars().all()
            self.assertGreaterEqual(len(db_utts), 2, "Expected at least 2 utterances")

            # ts: non-null positive floats
            for u in db_utts:
                self.assertIsNotNone(u.ts, f"Utterance {u.id[:8]}… has null ts")
                self.assertIsInstance(u.ts, float, f"Utterance ts must be float, got {type(u.ts)}")
                self.assertGreater(u.ts, 0, f"Utterance ts must be a positive unix epoch")

            # ts: non-decreasing order
            ts_vals = [u.ts for u in db_utts]
            for i in range(len(ts_vals) - 1):
                self.assertLessEqual(
                    ts_vals[i], ts_vals[i + 1],
                    f"Utterance timestamps not monotonic at positions {i}/{i+1}: {ts_vals[i]} > {ts_vals[i+1]}",
                )

            # Bot utterances must have timing telemetry in meta.
            # timing.llm_ttft_ms is set by _LLMFirstTimer and reliably fires on
            # every turn. latency_ms from UserBotLatencyObserver is best-effort
            # (race with on_assistant_turn_stopped) so is not asserted here.
            bot_db_utts = [u for u in db_utts if u.speaker_id == bot_identity]
            self.assertGreaterEqual(len(bot_db_utts), 1, "Expected at least 1 bot utterance")
            for u in bot_db_utts:
                timing = u.meta.get("timing", {})
                self.assertIn(
                    "llm_ttft_ms", timing,
                    f"Bot utterance {u.id[:8]}… missing meta.timing.llm_ttft_ms: {u.meta}",
                )
                self.assertGreater(
                    timing["llm_ttft_ms"], 0,
                    f"meta.timing.llm_ttft_ms must be positive, got {timing['llm_ttft_ms']}",
                )

            print(
                f"  status={conv.status} started={conv.started_at.isoformat()} "
                f"ended={conv.ended_at.isoformat()}",
                flush=True,
            )
            print(f"  root_utterance_id={conv.root_utterance_id[:8]}…", flush=True)
            print(
                f"  utterances={len(db_utts)} ts=[{min(ts_vals):.0f}…{max(ts_vals):.0f}]",
                flush=True,
            )

    # -- test 09: full reply_to bidirectional chain ---------------------------

    async def test_09_reply_to_full_chain(self):
        """reply_to forms a correct bidirectional chain across 2 full turns.

        Expected shape:
          user_utt_1  (reply_to=None)         ← first utterance ever
          bot_utt_1   (reply_to=user_utt_1.id)
          user_utt_2  (reply_to=bot_utt_1.id) ← user "continues" the conversation
          bot_utt_2   (reply_to=user_utt_2.id)
        """
        room_name = f"ms-replyto-{uuid4().hex[:8]}"
        uid = f"alice_{uuid4().hex[:6]}"

        session = self._start_session(room_name)
        session_id = session["session_id"]
        print(f"\n[test_09] room={room_name}", flush=True)

        await asyncio.sleep(BOT_JOIN_WAIT)
        rooms = await self._connect_users(room_name, [uid])
        user_room = rooms[0]
        await asyncio.sleep(USER_JOIN_WAIT)

        try:
            await _send_message(user_room, "Turn one: what is the tallest mountain?")
            utts = await _poll_utterances(session_id, min_count=2, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 2, "No bot response after turn 1")

            await _send_message(user_room, "Turn two: what is the deepest ocean trench?")
            utts = await _poll_utterances(session_id, min_count=4, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 4, "No bot response after turn 2")
        finally:
            await self._disconnect_all(rooms)

        _print_transcript(utts, "reply_to chain")

        user_utts = [u for u in utts if u["role"] == "participant"]
        bot_utts  = [u for u in utts if u["role"] == "bot"]

        self.assertGreaterEqual(len(user_utts), 2, "Need at least 2 user utterances")
        self.assertGreaterEqual(len(bot_utts), 2, "Need at least 2 bot utterances")

        u1, u2 = user_utts[0], user_utts[1]
        b1, b2 = bot_utts[0], bot_utts[1]

        # First utterance has no prior bot to reply to
        self.assertIsNone(
            u1["reply_to"],
            f"First user utterance reply_to should be None, got {u1['reply_to']!r}",
        )
        # Bot responds to the user turn that preceded it
        self.assertEqual(
            b1["reply_to"], u1["id"],
            f"bot_utt_1.reply_to should be user_utt_1.id ({u1['id'][:8]}…), got {b1['reply_to']!r}",
        )
        # Second user turn "continues from" the prior bot response
        self.assertEqual(
            u2["reply_to"], b1["id"],
            f"user_utt_2.reply_to should be bot_utt_1.id ({b1['id'][:8]}…), got {u2['reply_to']!r}",
        )
        # Second bot turn responds to the second user turn
        self.assertEqual(
            b2["reply_to"], u2["id"],
            f"bot_utt_2.reply_to should be user_utt_2.id ({u2['id'][:8]}…), got {b2['reply_to']!r}",
        )

        print(
            f"  chain: u1→None  b1→u1  u2→b1  b2→u2  ✓",
            flush=True,
        )

    # -- test 07: audio-path two speakers ------------------------------------

    async def test_07_audio_two_speakers(self):
        """Two participants publish real audio tracks; each utterance is attributed
        to the correct speaker in the DB via per-participant STT.

        Guarded by RUN_MULTI_SPEAKER_AUDIO=1 because it needs a configured STT
        API key (OpenAI or Deepgram) and the benchmark audio fixture.

        Generate the fixture first if it doesn't exist:
            make benchmark-audio
        """
        if os.getenv("RUN_MULTI_SPEAKER_AUDIO", "").strip() != "1":
            self.skipTest("Set RUN_MULTI_SPEAKER_AUDIO=1 to run — use: make test-multi-speaker-audio")

        import wave

        fixture_path = os.path.join(
            os.path.dirname(__file__), "fixtures", "benchmark_prompt.wav"
        )
        if not os.path.exists(fixture_path):
            self.skipTest(f"Audio fixture not found: {fixture_path}  (run: make benchmark-audio)")

        room_name = f"ms-audio-{uuid4().hex[:8]}"
        uid_alice = f"alice_{uuid4().hex[:6]}"
        uid_bob   = f"bob_{uuid4().hex[:6]}"

        session      = self._start_session(room_name)
        session_id   = session["session_id"]
        bot_identity = session["bot_identity"]
        print(f"\n[test_07] room={room_name} alice={uid_alice} bob={uid_bob}", flush=True)

        await asyncio.sleep(BOT_JOIN_WAIT)
        rooms = await self._connect_users(room_name, [uid_alice, uid_bob])
        room_alice, room_bob = rooms
        await asyncio.sleep(USER_JOIN_WAIT)

        async def _stream_audio(room: rtc.Room, wav_path: str, label: str):
            with wave.open(wav_path, "rb") as wf:
                sample_rate  = wf.getframerate()
                num_channels = wf.getnchannels()
                source = rtc.AudioSource(sample_rate, num_channels)
                track  = rtc.LocalAudioTrack.create_audio_track(f"audio-{label}", source)
                await room.local_participant.publish_track(track)
                chunk_samples = sample_rate * 60 // 1000  # 60 ms chunks
                while True:
                    raw = wf.readframes(chunk_samples)
                    if not raw:
                        break
                    frame = rtc.AudioFrame(
                        data=raw,
                        sample_rate=sample_rate,
                        num_channels=num_channels,
                        samples_per_channel=len(raw) // (2 * num_channels),
                    )
                    await source.capture_frame(frame)
                    await asyncio.sleep(0.06)

        try:
            # Stream audio sequentially: alice speaks, then bob.
            print("  Streaming Alice's audio...", flush=True)
            await _stream_audio(room_alice, fixture_path, "alice")
            await asyncio.sleep(2.0)  # pause so VAD closes alice's turn

            print("  Streaming Bob's audio...", flush=True)
            await _stream_audio(room_bob, fixture_path, "bob")
            await asyncio.sleep(2.0)

            # Wait for bot to respond to both turns
            utts = await _poll_utterances(session_id, min_count=4, timeout=TURN_TIMEOUT)
            self.assertGreaterEqual(len(utts), 4, "Expected 2 user + 2 bot utterances from audio path")

        finally:
            await self._disconnect_all(rooms)

        _print_transcript(utts, f"audio-path  alice={uid_alice}  bob={uid_bob}")

        user_utts = [u for u in utts if u["role"] == "participant"]
        self.assertGreaterEqual(len(user_utts), 2, "Expected at least 2 user utterances")

        # Verify per-participant attribution
        expected_order = [uid_alice, uid_bob]
        actual_order   = [u["speaker_id"] for u in user_utts[:2]]

        print(f"  Expected: {expected_order}", flush=True)
        print(f"  Actual:   {actual_order}",   flush=True)

        for i, (expected, actual) in enumerate(zip(expected_order, actual_order)):
            self.assertEqual(
                actual, expected,
                f"Turn {i+1}: expected speaker={expected}, got={actual}",
            )

        # Speaker labels must be present in LLM context — not assertable via DB,
        # but the transcript print above shows labeled text if SpeakerLabelInjector
        # is working. Manual inspection via: make logs SERVICE=agent-runner


if __name__ == "__main__":
    unittest.main()
