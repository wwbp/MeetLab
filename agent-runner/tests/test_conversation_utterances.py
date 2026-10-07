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

from tests import _http

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
            {"id": uuid.uuid4().hex, "speaker_id": person, "conv_id": conv_id, "ts": 1003.5, "text": "What is new?",
             "meta": {"source": "chat"}},
            {"id": uuid.uuid4().hex, "speaker_id": bot, "conv_id": conv_id, "ts": 1005.25, "text": "Not much."},
        ]))
    await engine.dispose()


async def _seed_people(conv_id: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.dialects.postgresql import insert

    from db.models import Conversation, Speaker, Utterance
    from db.url import database_url

    engine = create_async_engine(database_url(os.environ))
    ana, ben, bot = f"Ana__{conv_id[:6]}", f"Ben__{conv_id[:6]}", f"bot_{conv_id[:6]}"
    async with engine.begin() as db:
        await db.execute(insert(Conversation).values(id=conv_id, room_name=f"ppl-{conv_id[:6]}",
                                                     started_at=datetime.now(timezone.utc), status="completed"))
        await db.execute(insert(Speaker).values([
            {"id": ana, "meta": {"role": "participant", "display_name": "Ana", "prolific_id": "5f2a91b3c4d5e6f708192a3b"}},
            {"id": ben, "meta": {"role": "participant", "display_name": "Ben", "prolific_id_invalid": "oops"}},
            {"id": bot, "meta": {"role": "bot"}}]))
        await db.execute(insert(Utterance).values([
            {"id": uuid.uuid4().hex, "speaker_id": ana, "conv_id": conv_id, "ts": 1.0, "text": "Hi."},
            {"id": uuid.uuid4().hex, "speaker_id": ana, "conv_id": conv_id, "ts": 3.0, "text": "Again."},
            {"id": uuid.uuid4().hex, "speaker_id": ben, "conv_id": conv_id, "ts": 2.0, "text": "Hello."},
            {"id": uuid.uuid4().hex, "speaker_id": bot, "conv_id": conv_id, "ts": 2.5, "text": "Welcome."}]))
    await engine.dispose()
    return ana, ben


class TestConversationSpeakers(unittest.TestCase):
    """GET /conversations/{id}/speakers: who took part, with the Prolific ID a paid study is
    matched on (the payment export researchers wrote SQL for). People only, not the bot."""

    def test_each_person_once_with_their_prolific_id(self):
        conv_id = str(uuid.uuid4())
        ana, ben = asyncio.run(_seed_people(conv_id))
        r = _http.get(f"{BASE}/conversations/{conv_id}/speakers", headers=AUTH, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["speakers"], [
            {"speaker": ana, "display_name": "Ana", "prolific_id": "5f2a91b3c4d5e6f708192a3b", "prolific_id_invalid": None},
            {"speaker": ben, "display_name": "Ben", "prolific_id": None, "prolific_id_invalid": "oops"},
        ])

    def test_an_unknown_conversation_is_404(self):
        self.assertEqual(_http.get(f"{BASE}/conversations/{uuid.uuid4()}/speakers", headers=AUTH, timeout=10).status_code, 404)


class TestConversationUtterances(unittest.TestCase):
    def test_turns_in_order_with_times_and_who_spoke(self):
        conv_id = str(uuid.uuid4())
        asyncio.run(_seed(conv_id))
        r = _http.get(f"{BASE}/conversations/{conv_id}/utterances", headers=AUTH, timeout=10)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([(u["bot"], u["ts"], u["text"]) for u in r.json()["utterances"]],
                         [(True, 1000.0, "Hello there."), (False, 1003.5, "What is new?"), (True, 1005.25, "Not much.")])

    def test_a_typed_turn_says_so(self):
        # Chat becomes a turn like speech; researchers need to tell them apart (user, 2026-10-05).
        conv_id = str(uuid.uuid4())
        asyncio.run(_seed(conv_id))
        r = _http.get(f"{BASE}/conversations/{conv_id}/utterances", headers=AUTH, timeout=10)
        self.assertEqual([u["source"] for u in r.json()["utterances"]], [None, "chat", None])

    def test_an_unknown_conversation_is_404(self):
        r = _http.get(f"{BASE}/conversations/{uuid.uuid4()}/utterances", headers=AUTH, timeout=10)
        self.assertEqual(r.status_code, 404)

    @unittest.skipUnless(os.environ.get("BOT_RUNNER_SECRET"), "the runner is open when no key is set (local default)")
    def test_it_needs_the_runner_key(self):
        r = _http.get(f"{BASE}/conversations/{uuid.uuid4()}/utterances", timeout=10)
        self.assertIn(r.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
