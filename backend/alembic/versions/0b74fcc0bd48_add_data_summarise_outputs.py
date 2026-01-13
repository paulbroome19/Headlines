"""add data summarise outputs

Revision ID: 0b74fcc0bd48
Revises: d81441a036fd
Create Date: 2026-01-11 18:39:06.433904

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0b74fcc0bd48'
down_revision: Union[str, Sequence[str], None] = 'd81441a036fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    op.create_table(
        "summarise_outputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("normalisation_article_id", sa.Integer(), nullable=False),
        sa.Column("variant", sa.String(), nullable=False, server_default="short"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="data",
    )

    op.create_index(
        "ix_summarise_outputs_normalisation_article_id",
        "summarise_outputs",
        ["normalisation_article_id"],
        schema="data",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_summarise_outputs_normalisation_article_id",
        table_name="summarise_outputs",
        schema="data",
    )
    op.drop_table("summarise_outputs", schema="data")