"""S2a Lists write-side (типизированные списки + поэлементная видимость)

Заменяет транзитные list_categories/list_items (старая форма) на target-схему Lists:
lists (Scoped: owner/family/scope), list_items (self-ref дерево, status, specific_attrs JSON),
list_items_hidden_from (H3-a), list_item_history (action-лог). Данные одноразовые (req §10).

Revision ID: b1f2a3c4d5e6
Revises: 237ca7b0e901
Create Date: 2026-06-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1f2a3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "237ca7b0e901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("(CURRENT_TIMESTAMP)")


def upgrade() -> None:
    # ── Снос транзитных таблиц Lists (старая форма) ───────────────────────────
    op.drop_table("list_items")        # FK → list_categories: дропаем раньше родителя
    op.drop_table("list_categories")

    # ── Target-схема Lists ────────────────────────────────────────────────────
    op.create_table(
        "lists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "task", "shopping", "idea", "movie", "gift", "link",
                name="listtype", native_enum=False, length=20,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("emoji", sa.String(length=8), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("family_id", sa.Integer(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("personal", "family", name="scope", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("lists", schema=None) as b:
        b.create_index(b.f("ix_lists_family_id"), ["family_id"], unique=False)
        b.create_index(b.f("ix_lists_owner_id"), ["owner_id"], unique=False)

    op.create_table(
        "list_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("list_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("added_by", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "done", name="itemstatus", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("specific_attrs", sa.JSON(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["list_id"], ["lists.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["list_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("list_items", schema=None) as b:
        b.create_index(b.f("ix_list_items_list_id"), ["list_id"], unique=False)
        b.create_index(b.f("ix_list_items_parent_id"), ["parent_id"], unique=False)

    op.create_table(
        "list_items_hidden_from",
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["list_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("entity_id", "user_id"),
    )

    op.create_table(
        "list_item_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "created", "edited", "status_changed", "moved",
                name="historyaction", native_enum=False, length=20,
            ),
            nullable=False,
        ),
        sa.Column("detail", sa.String(length=200), nullable=True),
        sa.Column("at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["list_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("list_item_history", schema=None) as b:
        b.create_index(b.f("ix_list_item_history_at"), ["at"], unique=False)
        b.create_index(b.f("ix_list_item_history_item_id"), ["item_id"], unique=False)


def downgrade() -> None:
    # ── Снос target-схемы Lists ───────────────────────────────────────────────
    op.drop_table("list_item_history")
    op.drop_table("list_items_hidden_from")
    op.drop_table("list_items")
    op.drop_table("lists")

    # ── Восстановление транзитных таблиц (форма baseline 237ca7b0e901) ─────────
    op.create_table(
        "list_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("family_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("emoji", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["list_categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("list_categories", schema=None) as b:
        b.create_index(b.f("ix_list_categories_family_id"), ["family_id"], unique=False)
        b.create_index(b.f("ix_list_categories_parent_id"), ["parent_id"], unique=False)

    op.create_table(
        "list_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("added_by", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_checked", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["list_categories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("list_items", schema=None) as b:
        b.create_index(b.f("ix_list_items_category_id"), ["category_id"], unique=False)
