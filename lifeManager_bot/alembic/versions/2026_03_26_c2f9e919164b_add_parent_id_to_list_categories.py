"""add_parent_id_to_list_categories

Revision ID: c2f9e919164b
Revises: 
Create Date: 2026-03-26

Initial migration — creates all tables from scratch.
If tables already exist, upgrade() skips creation gracefully.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c2f9e919164b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    # --- families ---
    if 'families' not in existing:
        op.create_table(
            'families',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(100), nullable=False),
            sa.Column('invite_code', sa.String(20), nullable=False, unique=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )

    # --- users ---
    if 'users' not in existing:
        op.create_table(
            'users',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('telegram_id', sa.BigInteger(), nullable=False),
            sa.Column('username', sa.String(100), nullable=True),
            sa.Column('full_name', sa.String(200), nullable=False),
            sa.Column('family_id', sa.Integer(), sa.ForeignKey('families.id'), nullable=True),
            sa.Column('timezone', sa.String(50), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_users_telegram_id', 'users', ['telegram_id'], unique=True)

    # --- list_categories (with parent_id from the start) ---
    if 'list_categories' not in existing:
        op.create_table(
            'list_categories',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('family_id', sa.Integer(), sa.ForeignKey('families.id'), nullable=False),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('name', sa.String(100), nullable=False),
            sa.Column('emoji', sa.String(10), nullable=True),
            sa.Column('parent_id', sa.Integer(),
                      sa.ForeignKey('list_categories.id', ondelete='CASCADE'),
                      nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_list_categories_family_id', 'list_categories', ['family_id'])
        op.create_index('ix_list_categories_parent_id', 'list_categories', ['parent_id'])
    else:
        # Таблица уже есть (старая версия без parent_id) — добавляем колонку
        cols = [c['name'] for c in inspector.get_columns('list_categories')]
        if 'parent_id' not in cols:
            with op.batch_alter_table('list_categories') as batch_op:
                batch_op.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
                batch_op.create_index('ix_list_categories_parent_id', ['parent_id'])
                batch_op.create_foreign_key(
                    'fk_list_categories_parent_id',
                    'list_categories', ['parent_id'], ['id'],
                    ondelete='CASCADE',
                )

    # --- list_items ---
    if 'list_items' not in existing:
        op.create_table(
            'list_items',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('category_id', sa.Integer(),
                      sa.ForeignKey('list_categories.id'), nullable=False),
            sa.Column('added_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('is_checked', sa.Boolean(), nullable=False, default=False),
            sa.Column('position', sa.Integer(), nullable=False, default=0),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index('ix_list_items_category_id', 'list_items', ['category_id'])


def downgrade() -> None:
    op.drop_table('list_items')
    op.drop_table('list_categories')
    op.drop_table('users')
    op.drop_table('families')