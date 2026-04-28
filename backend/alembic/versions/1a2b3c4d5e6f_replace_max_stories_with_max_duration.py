"""replace max_stories with max_duration_minutes on profiles

Revision ID: 1a2b3c4d5e6f
Revises: 0f1c2e3a4b5d
Create Date: 2026-04-27
"""
from alembic import op
import sqlalchemy as sa

revision = '1a2b3c4d5e6f'
down_revision = '0f1c2e3a4b5d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'profiles',
        sa.Column('max_duration_minutes', sa.Integer(), nullable=False, server_default='5'),
        schema='data',
    )
    op.drop_column('profiles', 'max_stories', schema='data')


def downgrade() -> None:
    op.add_column(
        'profiles',
        sa.Column('max_stories', sa.Integer(), nullable=False, server_default='8'),
        schema='data',
    )
    op.drop_column('profiles', 'max_duration_minutes', schema='data')
