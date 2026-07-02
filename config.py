"""同步服务全局配置"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ---------- 数据库 ----------
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "sync_user"
    DB_PASSWORD: str = "changeme"
    DB_NAME: str = "sync_server"

    @property
    def DATABASE_URL(self) -> str:
        return f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"

    # ---------- JWT ----------
    JWT_SECRET_KEY: str = "change-this-to-a-random-secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 小时

    # ---------- 加密 ----------
    # AES-GCM salt（存在服务端，但密钥从用户密码派生，服务端依然零信任）
    ENC_SALT: str = "sync-server-salt-change-me"

    # ---------- TOTP ----------
    TOTP_ISSUER: str = "FlutterServerBox"

    # ---------- SMTP ----------
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: Optional[int] = None
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: Optional[str] = None
    SMTP_STARTTLS: bool = True     # STARTTLS (port 587)
    SMTP_USE_TLS: bool = False     # 隐式 SSL/TLS (port 465)，与 STARTTLS 二选一

    # ---------- 安全限制 ----------
    LOGIN_MAX_ATTEMPTS: int = 5          # 锁定前允许失败次数
    LOGIN_LOCK_MINUTES: int = 15         # 锁定时间
    IP_BAN_THRESHOLD: int = 10           # 每小时失败次数 → IP 封禁
    IP_BAN_HOURS: int = 1
    RATE_LIMIT_GLOBAL: str = "100/minute"
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_SYNC: str = "30/minute"

    # ---------- 管理员 ----------
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123456789"

    # ---------- 服务端口 ----------
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8765

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
