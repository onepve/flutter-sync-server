"""邀请码使用日志模型

记录每次邀请码被使用时的详细信息。
"""
import datetime
from sqlalchemy import String, DateTime, Integer, func, BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class InviteUsageLog(Base):
    __tablename__ = "invite_usage_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 使用的邀请码
    invite_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 使用者信息（写死，用户删除后仍可追踪）
    used_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_by_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    used_by_email: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 邀请码创建者 ID
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<InviteUsageLog id={self.id} code={self.invite_code} user={self.used_by_username}>"
