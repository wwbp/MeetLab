"""one running session per room

A repeated start for a room (a double click, a retry after meet's 10 s timeout)
created a second session and put a second bot in the meeting (diagnosis F3).
A partial unique index makes Postgres refuse a second running session per room;
/start then returns the one already running.

Rows that already break the rule are closed first, keeping the newest per room.

Revision ID: b3d9e5f1a2c8
Revises: a6c1f0e2d4b7
Create Date: 2026-10-01 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b3d9e5f1a2c8'
down_revision: Union[str, None] = 'a6c1f0e2d4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        UPDATE conversations SET status = 'ended', ended_at = now()
        WHERE status = 'running' AND id NOT IN (
            SELECT DISTINCT ON (room_name) id FROM conversations
            WHERE status = 'running' ORDER BY room_name, started_at DESC
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_conversations_one_running_per_room
        ON conversations (room_name) WHERE status = 'running'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX uq_conversations_one_running_per_room")
