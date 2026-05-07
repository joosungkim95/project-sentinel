"""add real_money_total column to portfolio_snapshots

Distinguishes real-capital adapters (Coinbase, Kalshi, future Alpaca live)
from paper accounts (current Alpaca paper). The Discord alert and
dashboard read total_value but it's dominated by ~$100k of paper capital,
masking the ~$28 of real money exposure.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-07 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'portfolio_snapshots',
        sa.Column(
            'real_money_total',
            sa.Float(),
            nullable=False,
            server_default='0.0',
        ),
    )


def downgrade() -> None:
    op.drop_column('portfolio_snapshots', 'real_money_total')
