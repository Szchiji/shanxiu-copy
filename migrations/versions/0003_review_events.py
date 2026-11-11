"""投稿审核事件表，用于可审计的通过/驳回与更正。

Revision ID: 0003_review_events
Revises: 0002_processed_update_status
Create Date: 2026-11-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_review_events"
down_revision: Union[str, None] = "0002_processed_update_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hetai_review_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("hetai_submissions.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("hetai_users.id"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("previous_status", sa.String(16), nullable=False, server_default=""),
        sa.Column("new_status", sa.String(16), nullable=False, server_default=""),
        sa.Column("note", sa.String(256), nullable=False, server_default=""),
        sa.Column("reward_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_demo_fixture", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_hetai_review_events_submission_id", "hetai_review_events", ["submission_id"])
    op.create_index("ix_hetai_review_events_actor_id", "hetai_review_events", ["actor_id"])


def downgrade() -> None:
    op.drop_index("ix_hetai_review_events_actor_id", table_name="hetai_review_events")
    op.drop_index("ix_hetai_review_events_submission_id", table_name="hetai_review_events")
    op.drop_table("hetai_review_events")
