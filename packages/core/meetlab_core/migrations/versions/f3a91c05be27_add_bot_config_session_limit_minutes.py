"""add bot_config.session_limit_minutes

Revision ID: f3a91c05be27
Revises: 26c3c9e3dec1
Create Date: 2026-07-29 20:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a91c05be27'
down_revision: Union[str, None] = '26c3c9e3dec1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'bot_config',
        sa.Column('session_limit_minutes', sa.BigInteger(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('bot_config', 'session_limit_minutes')
