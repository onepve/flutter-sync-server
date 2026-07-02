"""加密数据存储模型

服务端零信任 — 只存密文，不存解密密钥。
密钥从用户密码通过 Argon2id 派生，服务端无法解密。
"""

import datetime
from sqlalchemy import String, Boolean, DateTime, Integer, LargeBinary, Text, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class EncryptedData(Base):
    __tablename__ = "encrypted_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 关联用户
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # 数据类型标识（例如 "servers", "keys", "settings"）
    data_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # 设备标识（从哪个设备上传的）
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # 密文（AES-256-GCM 加密后的二进制）
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    # 数据版本号（单调递增，用于冲突检测）
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # 数据大小（明文，供前端展示）
    plaintext_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 时间
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<EncryptedData id={id} user={self.user_id} type={self.data_type}>"
