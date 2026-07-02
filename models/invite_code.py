"""邀请码模型"""

import datetime
from sqlalchemy import String, Boolean, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # 有效期（null = 永久）
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    # 使用次数（-1 = 无限，>=1 = 限制次数）
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 谁创建的（管理员 ID）
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 公开/不公开（公开邀请码可在注册页面看到）
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<InviteCode {self.code}>"

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        # MySQL DateTime 不带时区，补上 UTC 再比较
        if self.expires_at.tzinfo is None:
            expires = self.expires_at.replace(tzinfo=datetime.timezone.utc)
        else:
            expires = self.expires_at
        return now > expires

    @property
    def is_exhausted(self) -> bool:
        if self.max_uses == -1:
            return False
        return self.used_count >= self.max_uses
