"""add pnl and drawdown columns to portfolio_snapshots

Revision ID: a1b2c3d4e5f6
Revises: 55b2358c0f4d
Create Date: 2026-04-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '55b2358c0f4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add daily_pnl, weekly_pnl, total_pnl, drawdown_from_peak columns."""
    op.add_column('portfolio_snapshots',
                  sa.Column('daily_pnl', sa.Float(), nullable=False,
                            server_default='0'))
    op.add_column('portfolio_snapshots',
                  sa.Column('weekly_pnl', sa.Float(), nullable=False,
                            server_default='0'))
    op.add_column('portfolio_snapshots',
                  sa.Column('total_pnl', sa.Float(), nullable=False,
                            server_default='0'))
    op.add_column('portfolio_snapshots',
                  sa.Column('drawdown_from_peak', sa.Float(), nullable=False,
                            server_default='0'))


def downgrade() -> None:
    """Remove pnl/drawdown columns."""
    op.drop_column('portfolio_snapshots', 'drawdown_from_peak')
    op.drop_column('portfolio_snapshots', 'total_pnl')
    op.drop_column('portfolio_snapshots', 'weekly_pnl')
    op.drop_column('portfolio_snapshots', 'daily_pnl')
