"""GET /conversations/{id}/utterances: a conversation's turns as data, in order, with exact
times and who spoke (bot or person). The load test scores what the bot heard and said
from it (quality.py); a researcher can read it too. Against the running local runner, like
test_recordings_transcript.py; the conversation is seeded with its own short-lived engine."""
import asyncio
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.environ.get("AGENT_RUNNER_URL", "http://localhost:7860")
AUTH = {"Authorization": f"Bearer {os.environ.get('BOT_RUNNER_SECRET', 'changeme')}"}


async def _seed(conv_id: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.dialects.postgresql import insert

    from db.models import Conversation, Speaker, Utterance
    from db.url import database_url

    engine = create_async_engine(database_url(os.environ))
    person, bot = f"load_000_{conv_id[:6]}", f"bot_{conv_id[:6]}"
    async with engine.begin() as db:
        await db.execute(insert(Conversation).values(id=conv_id, room_name=f"utt-{conv_id[:6]}",
                                                     started_at=datetime.now(timezone.utc), status="completed"))
        await db.execute(insert(Speaker).values([{"id": person, "meta": {}}, {"id": bot, "meta": {"role": "bot"}}]))
        await db.execute(insert(Utterance).values([
            {"id": uuid.uuid4().hex, "speaker_id": bot, "conv_id": conv_id, "ts": 1000.0, "text": "Hello there."},
            {"id": uuid.uuid4().hex, "speaker_id": person, "conv_id": conv_id, "ts": 1003.5, "text": "What is new?"},
            {"id": uuid.uuid4().hex, "speaker_id": bot, "conv_id": conv_id, "ts": 1005.25, "text": "Not much."},
        ]))
    await engine.dispose()


class TestConversationUtterances(unittest.TestCase):
    def test_turns_in_order_with_times_and_who_spoke(self):
        conv_id = str(uuid.uuid4())
        asyncio.run(_seed(conv_id))
        r = requests.get(f"{BASE}/conversations/{conv_id}/utterances", headers=AUTH, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([(u["bot"], u["ts"], u["text"]) for u in r.json()["utterances"]],
                         [(True, 1000.0, "Hello there."), (False, 1003.5, "What is new?"), (True, 1005.25, "Not much.")])

    def test_an_unknown_conversation_is_404(self):
        r = requests.get(f"{BASE}/conversations/{uuid.uuid4()}/utterances", headers=AUTH, timeout=10)
        self.assertEqual(r.status_code, 404)

    @unittest.skipUnless(os.environ.get("BOT_RUNNER_SECRET"), "the runner is open when no key is set (local default)")
    def test_it_needs_the_runner_key(self):
        r = requests.get(f"{BASE}/conversations/{uuid.uuid4()}/utterances", timeout=10)
        self.assertIn(r.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
