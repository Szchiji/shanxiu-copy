"""收藏等补充表。"""
from __future__ import annotations

from typing import Optional

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bot.models import Base


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "lamp_id", name="uq_fav_user_lamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    lamp_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class LampView(Base):
    """浏览次数：每条资料每个北京日一行，每次打开详情 +1（老师看自己的不算）。Additive table, no migration."""
    __tablename__ = "lamp_views"
    __table_args__ = (UniqueConstraint("lamp_id", "day", name="uq_lamp_views_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lamp_id: Mapped[str] = mapped_column(String(36), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)  # Asia/Shanghai calendar day
    views: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ReviewNotice(Base):
    """Bot moderation message sent to an admin for a pending review (also the "already notified" mark).
    Kept so 通过/拒绝 from the bot or the admin backend can update every admin's copy. Additive table."""
    __tablename__ = "review_notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(String(36), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(8), default="text")  # text | caption | none (nobody reachable)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChannelDirectory(Base):
    """按区目录: one pinned-style message per city channel, edited in place on every refresh."""

    __tablename__ = "channel_directories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    chat: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Coupon(Base):
    """老师自己发的限时优惠的一项（如「100P 限2张」）。一次发布 = 一批（batch_id 相同，共用有效期 / 状态，最多 5 项）；
    每位老师同一时间最多 1 批有效。频道帖子记在这批的第一项（id 最小）上。旧的单张优惠 = 只有一项的一批。"""

    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coupon_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)  # NULL (旧数据) = coupon_id
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    lamp_id: Mapped[str] = mapped_column(String(36), index=True)
    city: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(String(64))
    ends_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC
    quota: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 0 = 不限
    claimed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active / ended / stopped
    stopped_by: Mapped[int] = mapped_column(BigInteger, nullable=True)
    # channel post (优惠推送): edited to 「已结束」 once the coupon ends / expires / is stopped / sold out
    post_chat: Mapped[str] = mapped_column(String(64), nullable=True)
    post_mid: Mapped[int] = mapped_column(Integer, nullable=True)
    post_closed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CouponClaim(Base):
    """客人领取：每一批优惠每人只能领 1 张（选其中一项），领到一个 6 位码，到店出示给老师。
    (batch_id, user_id) 唯一 → 并发也领不到第二张。"""

    __tablename__ = "coupon_claims"
    __table_args__ = (UniqueConstraint("coupon_id", "user_id", name="uq_coupon_claim_user"),
                      UniqueConstraint("batch_id", "user_id", name="uq_coupon_claim_batch_user"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coupon_id: Mapped[str] = mapped_column(String(36), index=True)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)  # = 所属优惠的 batch_id
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserPlace(Base):
    """常用位置: one rounded point per user (客人看附近 / 老师资料的大致位置). Never sent to other users."""

    __tablename__ = "user_places"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class LabelSyncState(Base):
    """Per-city progress of in-place channel link-label sync."""

    __tablename__ = "label_sync_state"

    city: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="idle", server_default="idle")
    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retry: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    missing: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    gone: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed: Mapped[list] = mapped_column(JSONB, default=list)
    reviews_stored: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reviews_legacy: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    note: Mapped[str] = mapped_column(String(256), default="", server_default="")
