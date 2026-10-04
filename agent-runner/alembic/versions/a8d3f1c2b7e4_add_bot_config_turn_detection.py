"""add bot_config.turn_detection

How a person's turn is judged over: "silence" (after a fixed pause, the behaviour so far)
or "smart_turn" (each person's own Smart Turn v3, smart_turn.py). The silence rule split
~19% of sentences at a mid-sentence pause in the 2026-10-04 load tests.

Revision ID: a8d3f1c2b7e4
Revises: c4e1a7b9d3f6
Create Date: 2026-10-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8d3f1c2b7e4'
down_revision: Union[str, None] = 'c4e1a7b9d3f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bot_config', sa.Column('turn_detection', sa.String(16), server_default='silence', nullable=False))


def downgrade() -> None:
    op.drop_column('bot_config', 'turn_detection')
