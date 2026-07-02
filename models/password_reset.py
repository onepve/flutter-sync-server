"""密码重置令牌模型"""

import datetime
from sqlalchemy import String, Boolean, DateTime, Integer, Text, func, ForeignKey, BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 关联用户
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # 重置令牌（安全随机串）
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)

    # 过期时间
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)

    # 是否已使用
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    @property
    def is_expired(self) -> bool:
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=datetime.timezone.utc)
        return datetime.datetime.now(datetime.timezone.utc) > expires

    def __repr__(self) -> str:
        return f"<PasswordResetToken id={self.id} user={self.user_id}>"
