"""v1 → v2 without loss: a database at v1's last migration, filled with v1-shaped rows, upgraded
to v2's head keeps every v1 row and every v1 column value, except the changes we chose. And the
check that proves it (migration_check.py) catches a missing or altered row or file.
Against the container's Postgres (each test makes and drops its own database):
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \\
        uv run python -m unittest tests.test_migration_v1_to_v2 -v
"""
import asyncio
import os
import subprocess
import unittest
import uuid

import asyncpg

from db.url import database_url
from migration_check import V1_TO_V2 as ALLOWED, compare, compare_files, fingerprint, pilot_rows, prune_to_pilot, restrict

V1_HEAD = "e7c4b9a13d02"  # v1's (main's) last migration; v2 adds six on top
AGENT_RUNNER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

V1_ROWS = """
INSERT INTO speakers (id, meta) VALUES
  ('p1', '{"prolific_id": "5f0c0ffee0000000000000a1", "name": "Ada"}'), ('bot_x', '{}');
INSERT INTO conversations (id, room_name, bot_identity, started_at, ended_at, status, meta) VALUES
  ('c-ended', 'study-a', 'bot_x', '2026-09-01 10:00+00', '2026-09-01 10:20+00', 'ended', '{"agent_name": "a"}'),
  ('c-old',   'study-b', 'bot_x', '2026-09-02 10:00+00', NULL, 'running', '{}'),
  ('c-new',   'study-b', 'bot_x', '2026-09-02 11:00+00', NULL, 'running', '{}');
INSERT INTO utterances (id, speaker_id, conv_id, ts, text, meta) VALUES
  ('u1', 'p1', 'c-ended', 1756720800.5, 'Hello there, I have a question.', '{"timing": {"stt_ms": 312}}'),
  ('u2', 'bot_x', 'c-ended', 1756720802.1, 'Of course, ask away.', '{"reply_to_ms": 1450}');
INSERT INTO media_files (id, conv_id, type, status, path, created_at, meta) VALUES
  ('m-s3', 'c-ended', 'recording', 'available', 'recordings/study-a.mp4', '2026-09-01 10:21+00', '{"egress_id": "EG_1"}'),
  ('m-local', 'c-ended', 'transcript', 'available', '/app/media/study-a.txt', '2026-09-01 10:21+00', '{}');
INSERT INTO events (conv_id, type, severity, room_name, payload, created_at) VALUES
  ('c-ended', 'room_finished', 'info', 'study-a', '{"by": "webhook"}', '2026-09-01 10:20+00');
INSERT INTO bot_config (scope, system_prompt, greeting, llm_model, tts_voice, stt_model, stt_vad_mode,
                        tts_provider, tts_aggregation_mode, user_speech_timeout_ms, updated_at) VALUES
  ('global', 'You are helpful.', 'Hi!', 'gpt-5.4-nano', 'v', 'parakeet-tdt-0.6b-v2', 'local', 'elevenlabs', 'sentence', 300, '2026-09-15 09:00+00'),
  ('female_warm', 'Be warm.', 'Hello!', 'gpt-5.4-nano', 'v', 'parakeet-tdt-0.6b-v2', 'local', 'elevenlabs', 'sentence', 300, '2026-09-15 09:00+00'),
  ('tuned', 'Be brief.', 'Hey.', 'gpt-5.4-nano', 'v', 'parakeet-tdt-0.6b-v2', 'local', 'elevenlabs', 'sentence', 450, '2026-09-15 09:00+00');
"""


class Scratch:
    """A throwaway database on the container's Postgres server."""

    def __init__(self):
        self.base = database_url(os.environ).replace("postgresql+asyncpg", "postgresql")
        self.name = f"migtest_{uuid.uuid4().hex[:8]}"
        self.url = self.base.rsplit("/", 1)[0] + "/" + self.name

    async def admin(self, sql):
        c = await asyncpg.connect(self.base)
        try:
            await c.execute(sql)
        finally:
            await c.close()

    def alembic(self, revision):
        env = {**os.environ, "DATABASE_URL": self.url.replace("postgresql://", "postgresql+asyncpg://")}
        subprocess.run(["uv", "run", "--no-sync", "alembic", "upgrade", revision], cwd=AGENT_RUNNER,
                       env=env, check=True, capture_output=True)


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = Scratch()
        asyncio.run(self.db.admin(f"CREATE DATABASE {self.db.name}"))
        self.addCleanup(lambda: asyncio.run(self.db.admin(f"DROP DATABASE {self.db.name} WITH (FORCE)")))
        self.db.alembic(V1_HEAD)
        asyncio.run(self._sql(V1_ROWS))

    async def _sql(self, sql):
        c = await asyncpg.connect(self.db.url)
        try:
            await c.execute(sql)
        finally:
            await c.close()

    async def _fingerprint(self, like=None):
        c = await asyncpg.connect(self.db.url)
        try:
            return await fingerprint(c, like)
        finally:
            await c.close()

    def test_upgrade_to_v2_keeps_every_v1_row_and_value(self):
        before = asyncio.run(self._fingerprint())
        self.db.alembic("head")
        after = asyncio.run(self._fingerprint(like=before))

        report = compare(before, after, allowed=ALLOWED)
        self.assertEqual(report.problems, [], "v1 data lost or changed by v2's upgrades")
        # The allowed changes, on exactly the rows they should touch: the older running session,
        # and the two Bot Config rows at 300 ms (the one at 450 keeps its value).
        self.assertEqual(report.changed, {"conversations": ["c-old"], "bot_config": ["1", "2"]})

    def test_the_check_catches_a_lost_row_and_a_changed_value(self):
        before = asyncio.run(self._fingerprint())
        asyncio.run(self._sql("DELETE FROM utterances WHERE id = 'u2';"
                              "UPDATE speakers SET meta = '{}' WHERE id = 'p1';"))
        after = asyncio.run(self._fingerprint(like=before))

        problems = compare(before, after, allowed=ALLOWED).problems
        self.assertIn("utterances: 1 row(s) missing: u2", problems)
        self.assertIn("speakers: 1 row(s) changed: p1", problems)

    def test_the_check_catches_a_row_that_should_not_be_there(self):
        before = asyncio.run(self._fingerprint())
        asyncio.run(self._sql("INSERT INTO speakers (id, meta) VALUES ('stray', '{}');"))
        after = asyncio.run(self._fingerprint(like=before))
        self.assertIn("speakers: 1 row(s) that should not be there: stray", compare(before, after, allowed=ALLOWED).problems)


class PilotOnlyTest(MigrationTest):
    """v2 production keeps only the pilot (user, 2026-10-06): sessions from start links
    (`link-*` rooms) and what belongs to them; Bot Config (the study conditions) stays.
    Load tests, benches and developer tests stay in v1 and its snapshot only."""

    PILOT = """
    INSERT INTO speakers (id, meta) VALUES ('p-pilot', '{"prolific_id": "5f0c0ffee0000000000000b2"}');
    INSERT INTO conversations (id, room_name, bot_identity, started_at, ended_at, status, meta) VALUES
      ('c-pilot', 'link-mubrfzcb-b79ceb1d', 'bot_x', '2026-09-21 14:00+00', '2026-09-21 14:06+00', 'ended', '{}');
    INSERT INTO utterances (id, speaker_id, conv_id, ts, text, meta) VALUES
      ('u-pilot', 'p-pilot', 'c-pilot', 1790000000.0, 'I think the second option.', '{}'),
      ('u-pilot-bot', 'bot_x', 'c-pilot', 1790000001.5, 'Why that one?', '{}');
    INSERT INTO media_files (id, conv_id, type, status, path, created_at, meta) VALUES
      ('m-pilot', 'c-pilot', 'audio_track', 'available', 'recordings/pilot.wav', '2026-09-21 14:06+00', '{}');
    INSERT INTO events (conv_id, type, severity, room_name, payload, created_at) VALUES
      ('c-pilot', 'room_finished', 'info', 'link-mubrfzcb-b79ceb1d', '{}', '2026-09-21 14:06+00'),
      (NULL, 'admin_action', 'info', NULL, '{}', '2026-09-21 13:00+00');
    """

    def test_only_the_pilot_and_bot_config_arrive_and_every_pilot_row_is_whole(self):
        asyncio.run(self._sql(self.PILOT))
        before = asyncio.run(self._fingerprint())

        async def keep():
            c = await asyncpg.connect(self.db.url)
            try:
                return await pilot_rows(c)
            finally:
                await c.close()
        kept = asyncio.run(keep())
        self.assertEqual(kept["conversations"], {"c-pilot"})
        self.assertEqual(kept["utterances"], {"u-pilot", "u-pilot-bot"})
        self.assertEqual(kept["speakers"], {"p-pilot", "bot_x"}, "the speakers of kept turns, the bot included")

        self.db.alembic("head")

        async def prune():
            c = await asyncpg.connect(self.db.url)
            try:
                await prune_to_pilot(c)
            finally:
                await c.close()
        asyncio.run(prune())
        after = asyncio.run(self._fingerprint(like=before))

        report = compare(restrict(before, kept), after, allowed=ALLOWED)
        self.assertEqual(report.problems, [], "a pilot row lost or changed")
        self.assertEqual({t: set(v["rows"]) for t, v in after.items() if t != "bot_config"},
                         {t: kept[t] for t in kept if t != "bot_config"}, "nothing but the pilot remains")
        self.assertEqual(len(after["bot_config"]["rows"]), 3, "every Bot Config row stays (study conditions)")


class FileCheckTest(unittest.TestCase):
    def test_a_missing_or_different_file_is_caught(self):
        v1 = {"recordings/a.mp4": (10, "aa"), "recordings/b.wav": (20, "bb"), "recordings/c.wav": (5, "cc")}
        v2 = {"recordings/a.mp4": (10, "aa"), "recordings/b.wav": (20, "XX"), "extra.txt": (1, "zz")}
        self.assertEqual(compare_files(v1, v2), [
            "files: 1 missing: recordings/c.wav",
            "files: 1 different: recordings/b.wav",
        ])
        self.assertEqual(compare_files(v1, dict(v1)), [])


if __name__ == "__main__":
    unittest.main()
