"""add_stt_endpointing_ms_to_bot_config

Revision ID: a1c3f9d22e74
Revises: 0d82f8e4f833
Create Date: 2026-06-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c3f9d22e74'
down_revision: Union[str, None] = '0d82f8e4f833'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bot_config', sa.Column(
        'stt_endpointing_ms', sa.BigInteger(), nullable=False, server_default='200'
    ))


def downgrade() -> None:
    op.drop_column('bot_config', 'stt_endpointing_ms')
