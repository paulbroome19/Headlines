"""add categories to normalisation_articles

Revision ID: 91da65bf1343
Revises: 6d6ae0382c0a
Create Date: 2026-02-20 20:39:52.959252
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "91da65bf1343"
down_revision: Union[str, Sequence[str], None] = "6d6ae0382c0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add categorisation fields to data.normalisation_articles.

    Fields added:
    - category_slugs (TEXT[])        → multiple category tags
    - category_primary (TEXT)        → main category for sorting/default UI
    - category_method (VARCHAR(32))  → keywords / llm / hybrid
    - category_version (INT)         → taxonomy versioning
    """

    # Multi-category support
    op.add_column(
        "normalisation_articles",
        sa.Column(
            "category_slugs",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        schema="data",
    )

    # Primary category
    op.add_column(
        "normalisation_articles",
        sa.Column(
            "category_primary",
            sa.Text(),
            nullable=True,
        ),
        schema="data",
    )

    # Classification method
    op.add_column(
        "normalisation_articles",
        sa.Column(
            "category_method",
            sa.String(length=32),
            nullable=False,
            server_default="keywords",
        ),
        schema="data",
    )

    # Taxonomy version
    op.add_column(
        "normalisation_articles",
        sa.Column(
            "category_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        schema="data",
    )

    # GIN index for fast category filtering
    op.create_index(
        "ix_data_normalisation_articles_category_slugs_gin",
        "normalisation_articles",
        ["category_slugs"],
        unique=False,
        schema="data",
        postgresql_using="gin",
    )

    # Index for primary category sorting
    op.create_index(
        "ix_data_normalisation_articles_category_primary",
        "normalisation_articles",
        ["category_primary"],
        unique=False,
        schema="data",
    )

    # Remove server defaults (so they aren't permanently baked in)
    op.alter_column(
        "normalisation_articles",
        "category_slugs",
        schema="data",
        server_default=None,
    )

    op.alter_column(
        "normalisation_articles",
        "category_method",
        schema="data",
        server_default=None,
    )

    op.alter_column(
        "normalisation_articles",
        "category_version",
        schema="data",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_data_normalisation_articles_category_primary",
        table_name="normalisation_articles",
        schema="data",
    )

    op.drop_index(
        "ix_data_normalisation_articles_category_slugs_gin",
        table_name="normalisation_articles",
        schema="data",
    )

    op.drop_column("normalisation_articles", "category_version", schema="data")
    op.drop_column("normalisation_articles", "category_method", schema="data")
    op.drop_column("normalisation_articles", "category_primary", schema="data")
    op.drop_column("normalisation_articles", "category_slugs", schema="data")