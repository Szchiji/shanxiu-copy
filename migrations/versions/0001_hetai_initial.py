"""合台初始表。

Revision ID: 0001_hetai_initial
Revises:
Create Date: 2026-11-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_hetai_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hetai_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_demo_fixture", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_index("ix_hetai_users_telegram_id", "hetai_users", ["telegram_id"])
    op.create_index("ix_hetai_users_role", "hetai_users", ["role"])

    op.create_table(
        "hetai_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_demo_fixture", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("chat_id"),
    )
    op.create_index("ix_hetai_groups_chat_id", "hetai_groups", ["chat_id"])

    op.create_table(
        "hetai_group_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("hetai_groups.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("hetai_users.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("group_id", "user_id", name="uq_hetai_membership"),
    )

    op.create_table(
        "hetai_credit_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("hetai_users.id"), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "hetai_credit_ledger",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("hetai_users.id"), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_hetai_credit_idem"),
    )

    op.create_table(
        "hetai_point_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("hetai_groups.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("hetai_users.id"), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("group_id", "user_id", name="uq_hetai_point_account"),
    )

    op.create_table(
        "hetai_point_ledger",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("hetai_groups.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("hetai_users.id"), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_hetai_point_idem"),
    )

    op.create_table(
        "hetai_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("hetai_groups.id"), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("hetai_users.id"), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("review_note", sa.String(256), nullable=False, server_default=""),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("hetai_users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("is_demo_fixture", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_hetai_submissions_status", "hetai_submissions", ["status"])

    op.create_table(
        "hetai_task_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(32), nullable=False, server_default="never"),
        sa.Column("last_detail", sa.String(256), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("key"),
    )

    op.create_table(
        "hetai_processed_updates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("handled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("detail", sa.String(256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("update_id"),
    )


def downgrade() -> None:
    for table in [
        "hetai_processed_updates",
        "hetai_task_configs",
        "hetai_submissions",
        "hetai_point_ledger",
        "hetai_point_accounts",
        "hetai_credit_ledger",
        "hetai_credit_accounts",
        "hetai_group_members",
        "hetai_groups",
        "hetai_users",
    ]:
        op.drop_table(table)
