"""default_stt_to_parakeet_ep100

Promote parakeet-tdt-0.6b-v2 + endpointing 100ms to the bot defaults
(Experiment 6). Idempotently retargets the existing 'global' row only if it
still holds the old nova-3 default, and lowers the column server_default so
fresh rows inherit ep=100. nova-3 stays a valid per-room override.

Revision ID: c7f1a2b3d4e5
Revises: a1c3f9d22e74
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7f1a2b3d4e5'
down_revision: Union[str, None] = 'a1c3f9d22e74'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New rows inherit ep=100 at the DB level.
    op.alter_column('bot_config', 'stt_endpointing_ms', server_default='100')
    # Retarget the global fallback only if it was never customised away from the
    # old default — never clobber an operator's explicit choice.
    op.execute(
        "UPDATE bot_config "
        "SET stt_model = 'parakeet-tdt-0.6b-v2', stt_endpointing_ms = 100 "
        "WHERE scope = 'global' AND stt_model = 'nova-3-general'"
    )


def downgrade() -> None:
    op.alter_column('bot_config', 'stt_endpointing_ms', server_default='200')
    op.execute(
        "UPDATE bot_config "
        "SET stt_model = 'nova-3-general', stt_endpointing_ms = 200 "
        "WHERE scope = 'global' AND stt_model = 'parakeet-tdt-0.6b-v2'"
    )
