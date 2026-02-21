"""add entities and normalisation_article_entities

Revision ID: <REPLACE_WITH_GENERATED_ID>
Revises: 91da65bf1343
Create Date: 2026-02-20
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "4f02ef243332"
down_revision: Union[str, Sequence[str], None] = "91da65bf1343"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    # ---------------------------------------------------
    # 1️⃣ data.entities
    # ---------------------------------------------------

    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="data",
    )

    op.create_index(
        "ux_data_entities_slug",
        "entities",
        ["slug"],
        unique=True,
        schema="data",
    )

    op.create_index(
        "ix_data_entities_entity_type",
        "entities",
        ["entity_type"],
        unique=False,
        schema="data",
    )

    # ---------------------------------------------------
    # 2️⃣ data.normalisation_article_entities
    # ---------------------------------------------------

    op.create_table(
        "normalisation_article_entities",
        sa.Column("normalisation_article_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "confidence_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "normalisation_article_id",
            "entity_id",
            name="pk_normalisation_article_entities",
        ),
        schema="data",
    )

    # Foreign key → normalisation_articles
    op.create_foreign_key(
        "fk_article_entities_article",
        source_table="normalisation_article_entities",
        referent_table="normalisation_articles",
        local_cols=["normalisation_article_id"],
        remote_cols=["id"],
        source_schema="data",
        referent_schema="data",
        ondelete="CASCADE",
    )

    # Foreign key → entities
    op.create_foreign_key(
        "fk_article_entities_entity",
        source_table="normalisation_article_entities",
        referent_table="entities",
        local_cols=["entity_id"],
        remote_cols=["id"],
        source_schema="data",
        referent_schema="data",
        ondelete="CASCADE",
    )

    op.create_index(
        "ix_article_entities_entity_id",
        "normalisation_article_entities",
        ["entity_id"],
        unique=False,
        schema="data",
    )


def downgrade() -> None:

    op.drop_index(
        "ix_article_entities_entity_id",
        table_name="normalisation_article_entities",
        schema="data",
    )

    op.drop_constraint(
        "fk_article_entities_entity",
        "normalisation_article_entities",
        schema="data",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_article_entities_article",
        "normalisation_article_entities",
        schema="data",
        type_="foreignkey",
    )

    op.drop_table("normalisation_article_entities", schema="data")

    op.drop_index(
        "ix_data_entities_entity_type",
        table_name="entities",
        schema="data",
    )

    op.drop_index(
        "ux_data_entities_slug",
        table_name="entities",
        schema="data",
    )

    op.drop_table("entities", schema="data")