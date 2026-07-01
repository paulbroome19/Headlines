"""merge pool + depth migration heads

#66 (pool stamp columns, b8e4d2f6a1c3) and #67 (story-summary depth_tier,
c9a1e7b3d5f2) both branch off a7f3c1d9e2b4. Part 4 (Top-Stories-by-region)
depends on both, so this no-op merge unifies the two heads into one lineage.

Revision ID: d1f5a9c2e7b4
Revises: b8e4d2f6a1c3, c9a1e7b3d5f2
Create Date: 2026-07-01 17:30:00.000000
"""

revision = 'd1f5a9c2e7b4'
down_revision = ('b8e4d2f6a1c3', 'c9a1e7b3d5f2')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
