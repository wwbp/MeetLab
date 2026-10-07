"""add user_speech_timeout_ms to bot_config

Revision ID: c9f1d3e77b24
Revises: b8e5c7241a90
Create Date: 2026-08-10 00:00:00.000000

The turn-end window is the SUM of two values: stt_endpointing_ms (silence before
the VAD closes a speech segment) and this one (extra silence the aggregator waits
after that). It was a hardcoded constant in bot.py, which meant tuning the window
required a deploy — unworkable when the right value can only be found against real
conversations.

450 + 300 = 750ms is the calibrated default, derived by running production's own
Silero analyzer over real pilot audio and matching predicted segments against the
turns participants actually took. See tests/test_turn_calibration.py and
v1.0.0:docs/pilot-postmortem-2026-08.md (RC1).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9f1d3e77b24'
down_revision: Union[str, None] = 'b8e5c7241a90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bot_config', sa.Column(
        'user_speech_timeout_ms', sa.BigInteger(), nullable=False, server_default='300'
    ))


def downgrade() -> None:
    op.drop_column('bot_config', 'user_speech_timeout_ms')
