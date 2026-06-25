"""reminders v0.4: max_occurrences/until/fired_count/delivery_target (S1/S2)

MVP-фидбэк партия №1 (spec-reminders v0.4): условие остановки повторений (S1) +
адресат доставки (S2). SQLite → batch_alter_table.

Revision ID: b7c9d1e2f3a4
Revises: f4c2a9d7b1e3
Create Date: 2026-06-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c9d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "f4c2a9d7b1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reminders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("max_occurrences", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("until", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("fired_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "delivery_target",
                sa.Enum(
                    "owner", "family", "partner",
                    name="deliverytarget", native_enum=False, length=20,
                ),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("reminders", schema=None) as batch_op:
        batch_op.drop_column("delivery_target")
        batch_op.drop_column("fired_count")
        batch_op.drop_column("until")
        batch_op.drop_column("max_occurrences")
