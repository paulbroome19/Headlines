"""rename bulletin schema to feeds and add scripts schema

Revision ID: 1ec2a96a3abd
Revises: a53b893ef645
Create Date: 2026-01-11 17:13:56.329183

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ec2a96a3abd'
down_revision: Union[str, Sequence[str], None] = 'a53b893ef645'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            -- If bulletins exists, rename to feeds
            IF EXISTS (
                SELECT 1
                FROM information_schema.schemata
                WHERE schema_name = 'bulletins'
            ) THEN
                EXECUTE 'ALTER SCHEMA bulletins RENAME TO feeds';
            END IF;

            -- If feeds doesn't exist yet, create it
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.schemata
                WHERE schema_name = 'feeds'
            ) THEN
                EXECUTE 'CREATE SCHEMA feeds';
            END IF;

            -- Ensure scripts schema exists
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.schemata
                WHERE schema_name = 'scripts'
            ) THEN
                EXECUTE 'CREATE SCHEMA scripts';
            END IF;
        END $$;
        """)


def downgrade() -> None:
    pass