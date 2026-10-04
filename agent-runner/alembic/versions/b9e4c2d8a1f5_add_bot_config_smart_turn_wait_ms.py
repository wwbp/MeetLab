"""add bot_config.smart_turn_wait_ms

How long smart turn keeps an unfinished speaker's turn open before replying anyway. On the
July pilot's real speech, 41% of real turn ends were judged unfinished and waited for this
backstop (Pipecat's default, 3 s): the dial between cutting people off and keeping them waiting.

Revision ID: b9e4c2d8a1f5
Revises: a8d3f1c2b7e4
Create Date: 2026-10-04 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b9e4c2d8a1f5'
down_revision: Union[str, None] = 'a8d3f1c2b7e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bot_config', sa.Column('smart_turn_wait_ms', sa.Integer(), server_default='3000', nullable=False))


def downgrade() -> None:
    op.drop_column('bot_config', 'smart_turn_wait_ms')
