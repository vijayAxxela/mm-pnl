"""add alert_settings and alert_emails tables

Revision ID: 3dc6ebf3f2ee
Revises: f65d8207f6c0
Create Date: 2026-09-03 07:50:01.912224

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3dc6ebf3f2ee'
down_revision: Union[str, Sequence[str], None] = 'f65d8207f6c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "alert_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sound_alert_step", sa.Float(), nullable=False, server_default="500.0"),
        sa.Column("email_alert_step", sa.Float(), nullable=False, server_default="1000.0"),
        sa.Column("trading_day", sa.String(), nullable=True),
        sa.Column("last_sound_threshold", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("last_email_threshold", sa.Float(), nullable=False, server_default="0.0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "alert_emails",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_alert_emails_id"), "alert_emails", ["id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_alert_emails_id"), table_name="alert_emails")
    op.drop_table("alert_emails")
    op.drop_table("alert_settings")
