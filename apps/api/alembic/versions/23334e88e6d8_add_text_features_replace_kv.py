"""add_text_features_replace_kv

Revision ID: 23334e88e6d8
Revises: a3766803a62d
Create Date: 2026-06-18 08:41:26.314677

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '23334e88e6d8'
down_revision: Union[str, None] = 'a3766803a62d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.drop_column('pdf_extracts', 'kv_pairs')
    op.add_column('pdf_extracts',
        sa.Column('text_features_json', postgresql.JSONB, nullable=True)
    )

def downgrade():
    op.drop_column('pdf_extracts', 'text_features_json')
    op.add_column('pdf_extracts',
        sa.Column('kv_pairs', sa.JSON, nullable=True)
    )
