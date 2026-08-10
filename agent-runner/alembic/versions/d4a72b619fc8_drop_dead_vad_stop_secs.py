"""drop bot_config.vad_stop_secs — a knob that never did anything

Revision ID: d4a72b619fc8
Revises: c9f1d3e77b24
Create Date: 2026-08-10 00:00:00.000000

vad_stop_secs was a full-stack feature that changed nothing: a DB column, API
validation in PUT /config, and a slider in the console. Its only appearance in the
pipeline was inside an f-string being logged.

It was set to 0.1 on all 42 config rows during the Jul/Aug 2026 pilot by someone
trying to fix exactly the responsiveness problem it cannot influence, while the
setting that mattered (stt_endpointing_ms) sat at its default, unexposed in the
console. Dead config is worse than no config: it absorbs the effort that would
otherwise find the real control.

Removed once the console started exposing stt_endpointing_ms and
user_speech_timeout_ms, which together form the actual turn-end window. See
docs/distillation-audit.md (Iteration 1) and docs/pilot-postmortem-2026-08.md.

The downgrade restores the column and its old default so the schema round-trips,
but nothing will read it — it was never wired to anything to begin with.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4a72b619fc8'
down_revision: Union[str, None] = 'c9f1d3e77b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('bot_config', 'vad_stop_secs')


def downgrade() -> None:
    op.add_column('bot_config', sa.Column(
        'vad_stop_secs', sa.Float(), nullable=True, server_default='0.6'
    ))
