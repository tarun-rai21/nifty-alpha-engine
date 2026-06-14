"""add_y_labels_to_features

Revision ID: ab92e57402e3
Revises: 0ae3d8f99a21
Create Date: 2026-06-14 14:39:53.435509

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab92e57402e3'
down_revision: Union[str, None] = '0ae3d8f99a21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("features", sa.Column("y_1d",  sa.Float(), nullable=True))
    op.add_column("features", sa.Column("y_3d",  sa.Float(), nullable=True))
    op.add_column("features", sa.Column("y_5d",  sa.Float(), nullable=True))
    op.add_column("features", sa.Column("y_10d", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("features", "y_10d")
    op.drop_column("features", "y_5d")
    op.drop_column("features", "y_3d")
    op.drop_column("features", "y_1d")
