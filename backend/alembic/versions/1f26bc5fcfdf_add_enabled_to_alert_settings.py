"""add enabled to alert_settings

Revision ID: 1f26bc5fcfdf
Revises: 3dc6ebf3f2ee
Create Date: 2026-09-03 08:30:46.442402

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f26bc5fcfdf'
down_revision: Union[str, Sequence[str], None] = '3dc6ebf3f2ee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "alert_settings",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("alert_settings", "enabled")
