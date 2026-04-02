"""add platform column to trades table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('trades', sa.Column('platform', sa.String(30), nullable=True))
    op.create_index(op.f('ix_trades_platform'), 'trades', ['platform'])


def downgrade() -> None:
    op.drop_index(op.f('ix_trades_platform'), table_name='trades')
    op.drop_column('trades', 'platform')
