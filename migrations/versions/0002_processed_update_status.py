"""Webhook 处理状态与结果，支持失败重试。

Revision ID: 0002_processed_update_status
Revises: 0001_hetai_initial
Create Date: 2026-11-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_processed_update_status"
down_revision: Union[str, None] = "0001_hetai_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("hetai_processed_updates") as batch:
        batch.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="processed")
        )
        batch.add_column(sa.Column("outcome", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()))
        batch.create_index("ix_hetai_processed_updates_status", ["status"])


def downgrade() -> None:
    with op.batch_alter_table("hetai_processed_updates") as batch:
        batch.drop_index("ix_hetai_processed_updates_status")
        batch.drop_column("updated_at")
        batch.drop_column("outcome")
        batch.drop_column("status")
