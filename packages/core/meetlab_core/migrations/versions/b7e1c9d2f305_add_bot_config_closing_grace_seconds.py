"""add bot_config.closing_grace_seconds: after the closing line, how long before the room closes

Revision ID: b7e1c9d2f305
Revises: a6d3f08c2e41
Create Date: 2026-11-18 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e1c9d2f305'
down_revision: Union[str, None] = 'a6d3f08c2e41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'bot_config',
        sa.Column('closing_grace_seconds', sa.BigInteger(), server_default='30', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('bot_config', 'closing_grace_seconds')
