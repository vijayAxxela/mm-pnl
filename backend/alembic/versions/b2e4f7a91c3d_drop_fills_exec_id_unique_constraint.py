"""drop fills exec_id unique constraint

Revision ID: b2e4f7a91c3d
Revises: 3d264945d705
Create Date: 2026-09-04 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2e4f7a91c3d'
down_revision: Union[str, Sequence[str], None] = '3d264945d705'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # exec_id is no longer unique — TT can reuse an exec_id across genuinely
    # different fills. row_hash (every column matching) is the real
    # duplicate check, still enforced unique below.
    op.drop_index(op.f('ix_fills_exec_id'), table_name='fills')
    op.create_index(op.f('ix_fills_exec_id'), 'fills', ['exec_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_fills_exec_id'), table_name='fills')
    op.create_index(op.f('ix_fills_exec_id'), 'fills', ['exec_id'], unique=True)
