"""邮件发送日志模型

记录每封邮件的发送记录，用于管理员追踪邮件送达情况。
"""

import datetime
from sqlalchemy import String, Boolean, DateTime, Integer, Text, func, BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class EmailLog(Base):
    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 收件人
    to_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # 邮件主题
    subject: Mapped[str] = mapped_column(String(255), nullable=False)

    # 邮件 HTML 正文
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # 是否发送成功
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 失败原因
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 触发此邮件的用户 ID（可为空，如忘记密码是未登录触发的）
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<EmailLog id={self.id} to={self.to_email} success={self.success}>"
