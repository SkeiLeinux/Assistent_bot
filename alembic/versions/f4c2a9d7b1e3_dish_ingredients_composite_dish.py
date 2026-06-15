"""dish_ingredients: состав составного блюда (фича «одно блюдо из ингредиентов»)

Новая таблица состава-снимка (app/contexts/nutrition/models/dish_ingredient.py):
строка на каждый ингредиент, родитель — либо запись дневника (food_entry_id), либо
блюдо каталога (shared_food_id); CHECK — ровно один родитель. ON DELETE CASCADE на
оба FK (SQLite форсит PRAGMA foreign_keys=ON). Провенанс на ингредиент (source/source_ref).

Revision ID: f4c2a9d7b1e3
Revises: e7a1c0ffee01
Create Date: 2026-06-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4c2a9d7b1e3'
down_revision: Union[str, Sequence[str], None] = 'e7a1c0ffee01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'dish_ingredients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('food_entry_id', sa.Integer(), nullable=True),
        sa.Column('shared_food_id', sa.Integer(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('weight_g', sa.Float(), nullable=True),
        sa.Column('kcal', sa.Float(), nullable=False),
        sa.Column('protein', sa.Float(), nullable=False),
        sa.Column('fat', sa.Float(), nullable=False),
        sa.Column('carbs', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.Column('source_ref', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ['food_entry_id'], ['food_entries.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['shared_food_id'], ['shared_foods.id'], ondelete='CASCADE'
        ),
        sa.CheckConstraint(
            '(food_entry_id IS NULL) <> (shared_food_id IS NULL)',
            name='ck_dish_ingredient_one_parent',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_dish_ingredients_food_entry_id'),
        'dish_ingredients', ['food_entry_id'], unique=False,
    )
    op.create_index(
        op.f('ix_dish_ingredients_shared_food_id'),
        'dish_ingredients', ['shared_food_id'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_dish_ingredients_shared_food_id'), table_name='dish_ingredients')
    op.drop_index(op.f('ix_dish_ingredients_food_entry_id'), table_name='dish_ingredients')
    op.drop_table('dish_ingredients')
