"""add bot_config.closing_message

The session limit has existed since f3a91c05be27, but only the browser knew
about it: the countdown ran in lib/SessionTimer.tsx and the bot talked on as if
the study had no end. A participant reaching the end of a paid session got no
signal that it was over, and no instruction about the completion code they have
to paste into the survey to be paid.

Revision ID: e7c4b9a13d02
Revises: b7d24e91c8a3
Create Date: 2026-09-08 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7c4b9a13d02'
down_revision: Union[str, None] = 'b7d24e91c8a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT = (
    "That's all the time we have for today. Thank you so much for talking with me. "
    "Please leave the room now, and copy the completion code shown on your screen "
    "into the survey."
)


def upgrade() -> None:
    op.add_column(
        'bot_config',
        sa.Column('closing_message', sa.Text(), server_default=DEFAULT, nullable=False),
    )


def downgrade() -> None:
    op.drop_column('bot_config', 'closing_message')
