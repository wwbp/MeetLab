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
import threading
import time
import unittest
from unittest import mock

import asyncpg
from fastapi.testclient import TestClient

os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "secret")
os.environ.setdefault("LIVEKIT_URL", "ws://transport-server:7880")

import runner  # noqa: E402
from db.url import database_url  # noqa: E402

OLD_ELECTION_LOCK = 8_142_026  # the key the removed election used


class ReconcileLoopTest(unittest.TestCase):
    # Through the app's real startup, as production runs it. The first version of this
    # test called the loop function directly, and passed while the function had lost
    # its @app.on_event("startup") registration (staging, 2026-10-01): nothing
    # reconciled at all.
    def test_the_app_starts_reconciling_even_while_another_process_holds_the_old_lock(self):
        locked, release = threading.Event(), threading.Event()

        async def old_task_holds_the_lock():  # its own session and event loop
            conn = await asyncpg.connect(database_url(os.environ).replace("+asyncpg", ""))
            await conn.fetchval("SELECT pg_try_advisory_lock($1)", OLD_ELECTION_LOCK)
            locked.set()
            while not release.is_set():
                await asyncio.sleep(0.05)
            await conn.close()  # session ends, lock released

        holder = threading.Thread(target=lambda: asyncio.run(old_task_holds_the_lock()))
        holder.start()
        locked.wait(10)
        env = {"CONVERSATION_RECONCILE_INTERVAL_SECONDS": "0", "DISABLE_CONVERSATION_RECONCILE": ""}
        try:
            with mock.patch.dict(os.environ, env), \
                 mock.patch.object(runner, "reconcile_stale_conversations", new=mock.AsyncMock()), \
                 mock.patch.object(runner, "fail_silent_sessions", new=mock.AsyncMock()) as fail_silent, \
                 TestClient(runner.app):
                time.sleep(0.3)
                ran = fail_silent.await_count
        finally:
            release.set()
            holder.join(10)
        self.assertGreaterEqual(ran, 1, "the app started without running the heartbeat check")


if __name__ == "__main__":
    unittest.main()
