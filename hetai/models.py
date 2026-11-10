"""合台领域模型：全局用户、群租户、分账本。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "hetai_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="member", index=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo_fixture: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    credit_account: Mapped[Optional["CreditAccount"]] = relationship(back_populates="user")
    memberships: Mapped[list["GroupMember"]] = relationship(back_populates="user")


class Group(Base):
    __tablename__ = "hetai_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo_fixture: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    members: Mapped[list["GroupMember"]] = relationship(back_populates="group")
    submissions: Mapped[list["Submission"]] = relationship(back_populates="group")


class GroupMember(Base):
    __tablename__ = "hetai_group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_hetai_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("hetai_groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("hetai_users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    group: Mapped[Group] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class CreditAccount(Base):
    """全局信用（声誉），不可按群消费。"""

    __tablename__ = "hetai_credit_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("hetai_users.id"), unique=True, index=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="credit_account")


class CreditLedger(Base):
    __tablename__ = "hetai_credit_ledger"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_hetai_credit_idem"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("hetai_users.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(256), default="")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PointAccount(Base):
    """群内可消费积分。"""

    __tablename__ = "hetai_point_accounts"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_hetai_point_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("hetai_groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("hetai_users.id"), index=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PointLedger(Base):
    __tablename__ = "hetai_point_ledger"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_hetai_point_idem"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("hetai_groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("hetai_users.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(256), default="")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Submission(Base):
    __tablename__ = "hetai_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("hetai_groups.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("hetai_users.id"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    review_note: Mapped[str] = mapped_column(String(256), default="")
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("hetai_users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_demo_fixture: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    group: Mapped[Group] = relationship(back_populates="submissions")
    author: Mapped[User] = relationship(foreign_keys=[author_id])
    reviewer: Mapped[Optional[User]] = relationship(foreign_keys=[reviewed_by])


class TaskConfig(Base):
    __tablename__ = "hetai_task_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="never")
    last_detail: Mapped[str] = mapped_column(String(256), default="尚未运行；本版本不发送通知")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ProcessedUpdate(Base):
    """Webhook 去重，保证同一 update 只处理一次。"""

    __tablename__ = "hetai_processed_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    update_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    handled: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
