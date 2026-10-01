"""add conversations.heartbeat_at

A bot that dies hard (OOM, lost instance) runs no code on the way out, and the
room-gone reconciler cannot see it while people are still in the room: the row
stayed 'running' and the meeting silently had no bot. The running bot now writes
heartbeat_at every 10 s; heartbeat.py fails sessions that go silent.

Revision ID: a6c1f0e2d4b7
Revises: e7c4b9a13d02
Create Date: 2026-10-01 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a6c1f0e2d4b7'
down_revision: Union[str, None] = 'e7c4b9a13d02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('conversations', 'heartbeat_at')
