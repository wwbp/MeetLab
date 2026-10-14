"""The migrated database's schema, read back from Postgres (the stack's, after `alembic upgrade
head`). stt_vad_mode was a dead knob: always "local", read by nothing since step 1 (2026-10-14)
removed it from the code; step 2 drops the column.
    docker compose -f .devcontainer/docker-compose.yml exec -T -w /app/packages/core agent-runner \\
        python -m unittest tests.test_schema -v
"""
import asyncio
import unittest

from sqlalchemy import text


def _columns(table: str) -> set[str]:
    async def read():
        from meetlab_core.db.engine import engine
        await engine.dispose(close=False)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                    {"t": table})
                return {r[0] for r in rows}
        finally:
            await engine.dispose()
    return asyncio.run(read())


class SchemaTest(unittest.TestCase):
    def test_the_dead_stt_vad_mode_column_is_dropped(self):
        columns = _columns("bot_config")
        self.assertIn("stt_model", columns, "read the right table")
        self.assertNotIn("stt_vad_mode", columns)


if __name__ == "__main__":
    unittest.main()
