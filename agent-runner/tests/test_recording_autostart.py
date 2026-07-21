"""In-process tests for start_recording_for_room and its WAV-sink coupling.

Run in the container so the DB and transport-server are reachable:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_recording_autostart -v

These import runner in-process (not over HTTP) so they can observe the
module-level AudioTrackSink registry, which lives in the same process as bot().
The test rooms don't exist in LiveKit, so composite egress raises TwirpError —
exactly the path that proves per-speaker WAV capture is enabled independently
of whether the composite recording succeeds.
"""

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-key")
os.environ.setdefault("DEEPGRAM_API_KEY", "test-deepgram-key")

import io
import zipfile

import audio_tracks
import bot
import runner
from sqlalchemy import select

from db.engine import AsyncSessionLocal, engine
from db.models import Conversation, MediaFile


class _NullFlush:
    async def __call__(self, sid, wav_bytes, meta):
        pass


async def _make_running_conversation(room_name: str) -> str:
    conv_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        async with db.begin():
            db.add(Conversation(id=conv_id, room_name=room_name, status="running"))
    return conv_id


class StartRecordingForRoomTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # The shared asyncpg pool binds to the loop of first use; IsolatedAsyncioTestCase
        # gives each test a fresh loop, so reset the pool here. close=False abandons the
        # old-loop connections instead of closing them (SQLAlchemy's cross-loop idiom) —
        # closing them would raise "attached to a different loop" and, worse, leave the
        # shared engine poisoned for sibling test modules under `unittest discover`.
        await engine.dispose(close=False)
        self.addAsyncCleanup(engine.dispose, close=False)

    async def test_enables_wav_sink_even_when_composite_egress_fails(self):
        room = f"autostart-wav-{uuid.uuid4().hex[:8]}"
        await _make_running_conversation(room)
        sink = audio_tracks.AudioTrackSink(_NullFlush())
        audio_tracks.register_sink(room, sink)
        self.addCleanup(audio_tracks.unregister_sink, room)

        status, payload = await runner.start_recording_for_room(room)

        # Room absent in LiveKit → composite egress fails (404/502)…
        self.assertIn(status, (404, 409, 502), payload)
        # …but per-speaker WAV capture is turned on regardless.
        self.assertTrue(sink.enabled)

    async def test_no_running_session_returns_404_and_no_sink(self):
        room = f"autostart-ghost-{uuid.uuid4().hex[:8]}"
        status, payload = await runner.start_recording_for_room(room)
        self.assertEqual(status, 404, payload)
        self.assertIn("no active session", payload["error"])


class BuildAudioTrackSinkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await engine.dispose(close=False)
        self.addAsyncCleanup(engine.dispose, close=False)

    async def test_flush_writes_wav_and_records_audio_track_media_file(self):
        room = f"tracksink-{uuid.uuid4().hex[:8]}"
        conv_id = await _make_running_conversation(room)

        sink = bot.build_audio_track_sink(
            room_name=room,
            session_id=conv_id,
            sid_to_identity={"PA_x": "alice__abc123"},
        )
        sink.enable()
        sink.offer("PA_x", b"\x01\x02" * 400, 16000, 1)
        await sink.flush_all()

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(MediaFile).where(
                    MediaFile.conv_id == conv_id, MediaFile.type == "audio_track"
                )
            )).scalars().all()

        self.assertEqual(len(rows), 1)
        mf = rows[0]
        self.assertEqual(mf.status, "available")
        self.assertTrue(mf.path)
        self.assertEqual(mf.meta["speaker_id"], "alice__abc123")
        self.assertEqual(mf.meta["part"], 0)

    async def test_three_users_and_bot_produce_four_tracks(self):
        # 3 human participants + the bot's own TTS = 4 source-separated WAVs.
        room = f"tracksink-4way-{uuid.uuid4().hex[:8]}"
        conv_id = await _make_running_conversation(room)
        bot_identity = "bot_4way_abc"
        sid_map = {
            "PA_alice": "alice__a1",
            "PA_bob": "bob__b2",
            "PA_carol": "carol__c3",
        }
        sink = bot.build_audio_track_sink(room, conv_id, sid_map, bot_identity=bot_identity)
        sink.enable()
        # Each human's inbound audio (PerSpeakerAudioRecorder) ...
        for sid in sid_map:
            sink.offer(sid, b"\x01\x02" * 200, 16000, 1)
        # ... and the bot's own TTS output (BotAudioRecorder), keyed by bot identity.
        sink.offer(bot_identity, b"\x05\x06" * 200, 24000, 1)
        await sink.flush_all()

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(MediaFile).where(
                    MediaFile.conv_id == conv_id, MediaFile.type == "audio_track"
                )
            )).scalars().all()

        self.assertEqual(len(rows), 4)
        speakers = {r.meta["speaker_id"] for r in rows}
        self.assertEqual(speakers, {"alice__a1", "bob__b2", "carol__c3", bot_identity})
        self.assertTrue(all(r.status == "available" and r.path for r in rows))

    async def test_speaker_resolved_from_live_map_at_flush_time(self):
        # The sink resolves speaker_id at flush time against the live identity map
        # (by reference), so audio offered before a participant is mapped still
        # tags correctly once the map is populated.
        room = f"tracksink-live-{uuid.uuid4().hex[:8]}"
        conv_id = await _make_running_conversation(room)
        sid_map: dict = {}  # empty when the sink is built
        sink = bot.build_audio_track_sink(room, conv_id, sid_map)
        sink.enable()
        sink.offer("PA_y", b"\x01\x02" * 400, 16000, 1)
        sid_map["PA_y"] = "carol__def456"  # mapped later (on_participant_connected)
        await sink.flush("PA_y")

        async with AsyncSessionLocal() as db:
            mf = (await db.execute(
                select(MediaFile).where(
                    MediaFile.conv_id == conv_id, MediaFile.type == "audio_track"
                )
            )).scalars().one()
        self.assertEqual(mf.meta["speaker_id"], "carol__def456")

    async def test_flush_after_identity_dropped_loses_speaker(self):
        # Regression guard for the on_participant_disconnected ordering bug: if the
        # identity is removed from the map BEFORE the flush, speaker_id resolves to
        # null. bot.on_participant_disconnected must therefore flush BEFORE popping
        # _sid_to_identity — this test documents why that order is load-bearing.
        room = f"tracksink-drop-{uuid.uuid4().hex[:8]}"
        conv_id = await _make_running_conversation(room)
        sid_map = {"PA_z": "dave__ghi789"}
        sink = bot.build_audio_track_sink(room, conv_id, sid_map)
        sink.enable()
        sink.offer("PA_z", b"\x01\x02" * 400, 16000, 1)
        del sid_map["PA_z"]  # WRONG order: identity gone before flush
        await sink.flush("PA_z")

        async with AsyncSessionLocal() as db:
            mf = (await db.execute(
                select(MediaFile).where(
                    MediaFile.conv_id == conv_id, MediaFile.type == "audio_track"
                )
            )).scalars().one()
        self.assertIsNone(mf.meta["speaker_id"])


class DownloadAudioTracksZipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await engine.dispose(close=False)
        self.addAsyncCleanup(engine.dispose, close=False)

    async def test_bundles_all_speaker_tracks_into_one_zip(self):
        room = f"zip-{uuid.uuid4().hex[:8]}"
        conv_id = await _make_running_conversation(room)
        sink = bot.build_audio_track_sink(
            room, conv_id, {"PA_a": "alice__a1", "PA_b": "bob__b2"}, bot_identity="bot_z"
        )
        sink.enable()
        sink.offer("PA_a", b"\x01\x02" * 200, 16000, 1)
        sink.offer("PA_b", b"\x03\x04" * 200, 16000, 1)
        sink.offer("bot_z", b"\x05\x06" * 200, 24000, 1)
        await sink.flush_all()

        resp = await runner.download_audio_tracks(conv_id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.media_type, "application/zip")
        with zipfile.ZipFile(io.BytesIO(resp.body)) as zf:
            names = zf.namelist()
        self.assertEqual(len(names), 3)  # alice, bob, bot — one download
        self.assertTrue(all(n.endswith(".wav") for n in names))

    async def test_no_tracks_returns_404(self):
        room = f"zip-empty-{uuid.uuid4().hex[:8]}"
        conv_id = await _make_running_conversation(room)
        resp = await runner.download_audio_tracks(conv_id)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
