"""add events.severity (+ indexes for console filtering)

Revision ID: b7d24e91c8a3
Revises: d4a72b619fc8

Re-parented when this branch was merged forward. It originally pointed at
f3a91c05be27, which main has since given three more children (endpointing default,
user_speech_timeout_ms, dropping vad_stop_secs). Leaving it would have created a
second alembic head, which is what stops the runner booting with
"Can't locate revision identified by ...".
Create Date: 2026-07-29 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d24e91c8a3'
down_revision: Union[str, None] = 'd4a72b619fc8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'events',
        sa.Column('severity', sa.String(length=16), server_default='info', nullable=False),
    )
    op.create_index('ix_events_severity', 'events', ['severity'])
    op.create_index('ix_events_room_name', 'events', ['room_name'])
    # The console reads newest-first, usually filtered — make that cheap.
    op.create_index('ix_events_created_at', 'events', [sa.text('created_at DESC')])


def downgrade() -> None:
    op.drop_index('ix_events_created_at', table_name='events')
    op.drop_index('ix_events_room_name', table_name='events')
    op.drop_index('ix_events_severity', table_name='events')
    op.drop_column('events', 'severity')
