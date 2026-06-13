"""R1 nutrition retrieval: provenance on food_entries (ADR-0013)

Добавляет провенанс КБЖУ на запись дневника (spec-nutrition-retrieval §10):
  - food_entries.source_ref — ссылка/код источника (URL OFF, цитата web), nullable;
  - food_entries.source — расширен 10→20 символов (семантика сменилась с input-mode
    на ProvenanceSource; длиннейшее значение enum «nutritionix» = 11).

SQLite не умеет ALTER COLUMN/ADD без пересоздания таблицы — используем batch-mode.

Revision ID: e7a1c0ffee01
Revises: 933845dd7c61
Create Date: 2026-06-13 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a1c0ffee01'
down_revision: Union[str, Sequence[str], None] = '933845dd7c61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('food_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_ref', sa.String(length=500), nullable=True))
        batch_op.alter_column(
            'source',
            existing_type=sa.String(length=10),
            type_=sa.String(length=20),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('food_entries', schema=None) as batch_op:
        batch_op.alter_column(
            'source',
            existing_type=sa.String(length=20),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
        batch_op.drop_column('source_ref')
