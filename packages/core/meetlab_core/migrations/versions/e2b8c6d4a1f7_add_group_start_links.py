"""add start_links, matches, tickets (group start links)

Stored start links and their matchmaking queue: a ticket per participant, a match per
formed group (one room). Additive; nothing reads them until the runner's /links and
/tickets endpoints do.

Revision ID: e2b8c6d4a1f7
Revises: d7e2a4c9f1b6
Create Date: 2026-11-02 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = 'e2b8c6d4a1f7'
down_revision: Union[str, None] = 'd7e2a4c9f1b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_tz = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'start_links',
        sa.Column('link_id', sa.String(64), primary_key=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('pool', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('size', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('min_size', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('delay_s', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_wait_s', sa.Integer(), nullable=False, server_default='300'),
        sa.Column('password_hash', sa.Text()),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('legacy', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', _tz, nullable=False, server_default=sa.func.now()),
        sa.Column('stopped_at', _tz),
    )
    op.create_table(
        'matches',
        sa.Column('room_name', sa.String(256), primary_key=True),
        sa.Column('link_id', sa.String(64), sa.ForeignKey('start_links.link_id'), nullable=False),
        sa.Column('bot_config_scope', sa.String(256), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('created_at', _tz, nullable=False, server_default=sa.func.now()),
        sa.Column('ready_deadline', _tz),
        sa.Column('starts_at', _tz),
        sa.Column('closed_at', _tz),
        sa.Column('session_id', sa.String(64)),
        sa.Column('setup_claimed_at', _tz),
    )
    op.create_index('ix_matches_link_id', 'matches', ['link_id'])
    op.create_table(
        'tickets',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('link_id', sa.String(64), sa.ForeignKey('start_links.link_id'), nullable=False),
        sa.Column('participant_id', sa.String(256)),
        sa.Column('state', sa.String(16), nullable=False, server_default='searching'),
        sa.Column('queued_at', _tz, nullable=False, server_default=sa.func.now()),
        sa.Column('last_seen', _tz, nullable=False, server_default=sa.func.now()),
        sa.Column('ready_at', _tz),
        sa.Column('placed_at', _tz),
        sa.Column('requeued_at', _tz),
        sa.Column('room_name', sa.String(256), sa.ForeignKey('matches.room_name')),
        sa.CheckConstraint(
            "state IN ('searching', 'found', 'ready', 'placed', 'done', 'expired', 'removed')",
            name='ck_tickets_state'),
    )
    op.create_index('uq_tickets_one_live_per_participant', 'tickets', ['link_id', 'participant_id'],
                    unique=True, postgresql_where=sa.text("state IN ('searching', 'found', 'ready', 'placed')"))
    op.create_index('ix_tickets_link_state', 'tickets', ['link_id', 'state'])


def downgrade() -> None:
    op.drop_table('tickets')
    op.drop_table('matches')
    op.drop_table('start_links')
