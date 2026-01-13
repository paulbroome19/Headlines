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
    # 1) rename schema bulletin -> feeds
    op.execute("ALTER SCHEMA bulletins RENAME TO feeds")

    # 2) create scripts schema
    op.execute("CREATE SCHEMA IF NOT EXISTS scripts")


def downgrade() -> None:
    # reverse order
    op.execute("DROP SCHEMA IF EXISTS scripts CASCADE")
    op.execute("ALTER SCHEMA feeds RENAME TO bulletins")