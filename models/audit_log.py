"""审计日志模型

记录所有敏感操作，用于安全审计和问题追踪。
"""

import datetime
from sqlalchemy import String, Boolean, DateTime, Integer, Text, func, BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 操作用户（未登录则为 null）
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 操作用户名（写死到日志中，用户删除后仍可追踪）
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 操作类型
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # 操作详情
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 来源 IP
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # 请求路径
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 是否成功
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action}>"
