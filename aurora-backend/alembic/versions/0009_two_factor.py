"""two-factor login (TOTP) on users

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27 09:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('totp_secret', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('totp_pending', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('totp_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('totp_last_step', sa.BigInteger(), nullable=True))
    op.add_column('users', sa.Column('recovery_codes', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    for col in ('recovery_codes', 'totp_last_step', 'totp_enabled_at', 'totp_pending', 'totp_secret'):
        op.drop_column('users', col)
