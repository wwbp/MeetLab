"""drop bot_config.stt_vad_mode, a dead knob

It was always "local" (server VAD was removed long ago) and changed nothing: the bot
turned it into turn_detection=False for every value. Dropped 2026-10-14; the downgrade
restores it as "local", what every row held.

Revision ID: d7e2a4c9f1b6
Revises: c4d2e7f1a9b3
Create Date: 2026-10-14 05:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd7e2a4c9f1b6'
down_revision: Union[str, None] = 'c4d2e7f1a9b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('bot_config', 'stt_vad_mode')


def downgrade() -> None:
    op.add_column('bot_config', sa.Column('stt_vad_mode', sa.String(length=32), nullable=False, server_default='local'))
