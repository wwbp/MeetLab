"""raise stt_endpointing_ms default to 450ms and migrate pre-pilot rows

Revision ID: b8e5c7241a90
Revises: f3a91c05be27
Create Date: 2026-08-06 00:00:00.000000

The Jul/Aug 2026 pilot ran with stt_endpointing_ms=100, meaning the bot treated
100ms of silence as "you have finished speaking". A thinking pause is several
times that, so single sentences were split into as many as 31 fragments and
participants were cut off mid-thought. See docs/pilot-postmortem-2026-08.md (RC2).

450ms survives an ordinary pause while staying responsive.

Existing rows are migrated too, not just the default. Every value in the table
predates this change and none of them were chosen deliberately — the field is not
even exposed in the console, which is precisely how it went unnoticed. Rows at 300
or above are left alone so a future deliberate choice is never overwritten.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e5c7241a90'
down_revision: Union[str, None] = 'f3a91c05be27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'bot_config',
        'stt_endpointing_ms',
        existing_type=sa.BigInteger(),
        existing_nullable=False,
        server_default='450',
    )
    # Bring pre-pilot rows (100ms per-link, 200ms global) onto the new default.
    op.execute(
        "UPDATE bot_config SET stt_endpointing_ms = 450 WHERE stt_endpointing_ms < 300"
    )


def downgrade() -> None:
    op.alter_column(
        'bot_config',
        'stt_endpointing_ms',
        existing_type=sa.BigInteger(),
        existing_nullable=False,
        server_default='100',
    )
    op.execute(
        "UPDATE bot_config SET stt_endpointing_ms = 100 WHERE stt_endpointing_ms = 450"
    )
