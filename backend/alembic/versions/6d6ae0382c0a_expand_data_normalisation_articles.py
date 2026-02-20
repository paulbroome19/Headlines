"""expand data.normalisation_articles

Revision ID: 6d6ae0382c0a
Revises: 492f1fd8f571
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6d6ae0382c0a"
down_revision: Union[str, Sequence[str], None] = "492f1fd8f571"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns
    op.add_column(
        "normalisation_articles",
        sa.Column("ingested_article_id", sa.Integer(), nullable=True),
        schema="data",
    )

    op.add_column(
        "normalisation_articles",
        sa.Column("provider", sa.String(length=32), nullable=True),
        schema="data",
    )

    op.add_column(
        "normalisation_articles",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        schema="data",
    )

    op.add_column(
        "normalisation_articles",
        sa.Column("content_snippet", sa.Text(), nullable=True),
        schema="data",
    )

    # Foreign key to ingested_articles
    op.create_foreign_key(
        "fk_normalisation_ingested_article",
        source_table="normalisation_articles",
        referent_table="ingested_articles",
        local_cols=["ingested_article_id"],
        remote_cols=["id"],
        source_schema="data",
        referent_schema="data",
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_normalisation_ingested_article",
        "normalisation_articles",
        schema="data",
        type_="foreignkey",
    )

    op.drop_column("normalisation_articles", "content_snippet", schema="data")
    op.drop_column("normalisation_articles", "published_at", schema="data")
    op.drop_column("normalisation_articles", "provider", schema="data")
    op.drop_column("normalisation_articles", "ingested_article_id", schema="data")