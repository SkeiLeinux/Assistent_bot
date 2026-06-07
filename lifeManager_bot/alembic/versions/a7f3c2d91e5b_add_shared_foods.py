"""add shared_foods table and shared_food_id to food_entries

Revision ID: a7f3c2d91e5b
Revises: 51b617b93453
Create Date: 2026-05-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7f3c2d91e5b'
down_revision: Union[str, Sequence[str], None] = '51b617b93453'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'shared_foods',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('weight_g', sa.Float(), nullable=False),
        sa.Column('kcal_per_100g', sa.Float(), nullable=False),
        sa.Column('protein_per_100g', sa.Float(), nullable=False),
        sa.Column('fat_per_100g', sa.Float(), nullable=False),
        sa.Column('carbs_per_100g', sa.Float(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('use_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('shared_foods', schema=None) as batch_op:
        batch_op.create_index('ix_shared_foods_name', ['name'], unique=False)
        batch_op.create_index('ix_shared_foods_created_by', ['created_by'], unique=False)

    with op.batch_alter_table('food_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('shared_food_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_food_entries_shared_food_id', ['shared_food_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('food_entries', schema=None) as batch_op:
        batch_op.drop_index('ix_food_entries_shared_food_id')
        batch_op.drop_column('shared_food_id')

    with op.batch_alter_table('shared_foods', schema=None) as batch_op:
        batch_op.drop_index('ix_shared_foods_name')
        batch_op.drop_index('ix_shared_foods_created_by')

    op.drop_table('shared_foods')
