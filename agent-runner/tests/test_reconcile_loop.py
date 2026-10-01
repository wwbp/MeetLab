"""Every runner process reconciles; none waits to be elected (design iteration 6).

The loop used to start only in the process that won a Postgres advisory lock, once,
at startup. In a rolling deploy the OLD task still holds that lock while the new
one starts: every new process lost, never retried, and after the old task stopped
nothing reconciled at all. Found on staging 2026-10-01: a bot killed with kill -9
was never failed, because no process was running the heartbeat check. Every write
the loop makes is conditional on status='running', so running it everywhere is safe.

Against the container's Postgres. Run with:
    docker compose -f .devcontainer/docker-compose.yml exec -T agent-runner \
        uv run python -m unittest tests.test_reconcile_loop -v
"""
import asyncio
import os
import unittest
from unittest import mock

from sqlalchemy import text

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")

import runner  # noqa: E402
from db.engine import engine  # noqa: E402

OLD_ELECTION_LOCK = 8_142_026  # the key the removed election used


class ReconcileLoopTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await engine.dispose()

    async def asyncTearDown(self):
        await engine.dispose()

    async def test_a_process_reconciles_even_while_an_old_task_holds_the_lock(self):
        async with engine.connect() as old_task:
            # Non-blocking: if another process (the local dev runner) already holds it,
            # that is exactly the situation under test.
            held_here = (await old_task.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": OLD_ELECTION_LOCK})).scalar()
            env = {"CONVERSATION_RECONCILE_INTERVAL_SECONDS": "0", "DISABLE_CONVERSATION_RECONCILE": ""}
            with mock.patch.dict(os.environ, env), \
                 mock.patch.object(runner, "reconcile_stale_conversations", new=mock.AsyncMock()), \
                 mock.patch.object(runner, "fail_silent_sessions", new=mock.AsyncMock()) as fail_silent:
                loop = await runner._start_conversation_reconcile_loop()
                await asyncio.sleep(0.05)
                if loop:
                    loop.cancel()
            if held_here:
                await old_task.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": OLD_ELECTION_LOCK})
        self.assertGreaterEqual(fail_silent.await_count, 1, "this process never ran the heartbeat check")


if __name__ == "__main__":
    unittest.main()
