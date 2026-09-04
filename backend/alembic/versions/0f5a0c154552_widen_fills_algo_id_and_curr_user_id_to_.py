"""widen fills algo_id and curr_user_id to string

Revision ID: 0f5a0c154552
Revises: 1f26bc5fcfdf
Create Date: 2026-09-04 14:13:00.992229

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f5a0c154552'
down_revision: Union[str, Sequence[str], None] = '1f26bc5fcfdf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("fills", "algo_id", type_=sa.String(), postgresql_using="algo_id::text")
    op.alter_column("fills", "curr_user_id", type_=sa.String(), postgresql_using="curr_user_id::text")


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("fills", "algo_id", type_=sa.BigInteger(), postgresql_using="algo_id::bigint")
    op.alter_column("fills", "curr_user_id", type_=sa.BigInteger(), postgresql_using="curr_user_id::bigint")
