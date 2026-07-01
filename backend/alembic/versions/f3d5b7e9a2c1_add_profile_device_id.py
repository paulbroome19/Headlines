"""add device_id to profiles — device-keyed identity

One account per DEVICE, keyed by a stable client-supplied device id (an iOS
Keychain UUID that survives reinstall). Profiles are scoped to it so a reinstalled
device recovers ITS OWN profile and no device can list another's. Nullable +
partial-unique so legacy profiles (no device id) are untouched and one profile
exists per device.

Revision ID: f3d5b7e9a2c1
Revises: e2c4a6f8d1b9
Create Date: 2026-07-01 22:40:00.000000
"""
from alembic import op

revision = 'f3d5b7e9a2c1'
down_revision = 'e2c4a6f8d1b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE data.profiles ADD COLUMN IF NOT EXISTS device_id TEXT")
    # One profile per device (only where a device id is set — legacy rows are NULL).
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_profiles_device_id "
        "ON data.profiles (device_id) WHERE device_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS data.uq_profiles_device_id")
    op.execute("ALTER TABLE data.profiles DROP COLUMN IF EXISTS device_id")
