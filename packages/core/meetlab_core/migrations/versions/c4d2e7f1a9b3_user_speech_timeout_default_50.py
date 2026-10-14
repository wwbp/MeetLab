"""bot_config.user_speech_timeout_ms default 300 -> 50

B4 (2026-10-05): the wait comes after the transcript, so it only delays the reply; 300 -> 50 ms
was 0.24 s faster with turn splitting unchanged. Rows still on the old default move with it; a
row set to anything else was chosen and keeps its value.

Revision ID: c4d2e7f1a9b3
Revises: b9e4c2d8a1f5
Create Date: 2026-10-05 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c4d2e7f1a9b3'
down_revision: Union[str, None] = 'b9e4c2d8a1f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('bot_config', 'user_speech_timeout_ms', server_default='50')
    op.execute("UPDATE bot_config SET user_speech_timeout_ms = 50 WHERE user_speech_timeout_ms = 300")


def downgrade() -> None:
    op.alter_column('bot_config', 'user_speech_timeout_ms', server_default='300')
    op.execute("UPDATE bot_config SET user_speech_timeout_ms = 300 WHERE user_speech_timeout_ms = 50")
