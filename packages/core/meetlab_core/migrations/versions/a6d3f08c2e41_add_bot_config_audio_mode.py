"""add bot_config.audio_mode: existing rooms stay normal

Revision ID: a6d3f08c2e41
Revises: e2b8c6d4a1f7
Create Date: 2026-11-05 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a6d3f08c2e41'
down_revision: Union[str, None] = 'e2b8c6d4a1f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'bot_config',
        sa.Column('audio_mode', sa.String(), server_default='normal', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('bot_config', 'audio_mode')
