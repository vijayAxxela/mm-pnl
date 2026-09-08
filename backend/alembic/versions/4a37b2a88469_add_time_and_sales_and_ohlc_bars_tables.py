"""add time_and_sales and ohlc_bars tables

Revision ID: 4a37b2a88469
Revises: b2e4f7a91c3d
Create Date: 2026-09-07 18:20:30.104579

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a37b2a88469'
down_revision: Union[str, Sequence[str], None] = 'b2e4f7a91c3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'time_and_sales',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('contract', sa.String(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('qty', sa.Float(), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('contract', 'timestamp', 'qty', 'price', name='unique_time_and_sales_row'),
    )
    op.create_index(op.f('ix_time_and_sales_contract'), 'time_and_sales', ['contract'])
    op.create_index(op.f('ix_time_and_sales_timestamp'), 'time_and_sales', ['timestamp'])

    op.create_table(
        'ohlc_bars',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('contract', sa.String(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('open', sa.Float(), nullable=True),
        sa.Column('high', sa.Float(), nullable=False),
        sa.Column('low', sa.Float(), nullable=False),
        sa.Column('close', sa.Float(), nullable=True),
        sa.Column('volume', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('contract', 'timestamp', name='unique_ohlc_bar'),
    )
    op.create_index(op.f('ix_ohlc_bars_contract'), 'ohlc_bars', ['contract'])
    op.create_index(op.f('ix_ohlc_bars_timestamp'), 'ohlc_bars', ['timestamp'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ohlc_bars_timestamp'), table_name='ohlc_bars')
    op.drop_index(op.f('ix_ohlc_bars_contract'), table_name='ohlc_bars')
    op.drop_table('ohlc_bars')

    op.drop_index(op.f('ix_time_and_sales_timestamp'), table_name='time_and_sales')
    op.drop_index(op.f('ix_time_and_sales_contract'), table_name='time_and_sales')
    op.drop_table('time_and_sales')
