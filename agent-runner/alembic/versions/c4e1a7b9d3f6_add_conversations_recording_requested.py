"""add conversations.recording_requested

Per-speaker capture was switched on through an in-process registry: the runner's
/recordings/start looked up the room's audio sink in its own memory. Since 4c the
bot runs in its own task, so a recording started from the console never reached
it. The runner now sets this flag; the bot reads it back on each heartbeat.

Revision ID: c4e1a7b9d3f6
Revises: b3d9e5f1a2c8
Create Date: 2026-10-01 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e1a7b9d3f6'
down_revision: Union[str, None] = 'b3d9e5f1a2c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('recording_requested', sa.Boolean(),
                                             server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    op.drop_column('conversations', 'recording_requested')
