"""
安全服务

包含 JWT 签发/验证、密码哈希、登录锁定、IP 封禁、限流等。
"""

import datetime
import logging
from typing import Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models.user import User

logger = logging.getLogger(__name__)

# ── 密码哈希 ──
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── JWT Bearer 认证 ──
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── JWT ──

def create_access_token(user_id: int, username: str, is_admin: bool = False) -> str:
    """签发 JWT Token"""
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=settings.JWT_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "username": username,
        "admin": is_admin,
        "exp": expire,
        "iat": datetime.datetime.now(datetime.timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """解析 JWT Token，返回 payload 或 None"""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        return None


# ── FastAPI 依赖：获取当前用户 ──

class SecurityService:
    """登录锁定 + IP 封禁逻辑"""

    @staticmethod
    def check_login_lock(user: User) -> None:
        """检查用户是否被锁定"""
        if not user.is_active:
            raise HTTPException(status_code=403, detail="账户已被禁用")

        if user.locked_until:
            locked_until = user.locked_until if user.locked_until.tzinfo is not None else user.locked_until.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            if locked_until > now:
                remaining = (locked_until - now).seconds // 60
                raise HTTPException(
                    status_code=429,
                    detail=f"账户已被锁定，请在 {remaining} 分钟后重试"
                )

    @staticmethod
    def record_login_failure(user: User, db: Session) -> None:
        """记录登录失败并锁定"""
        user.login_attempts += 1
        if user.login_attempts >= settings.LOGIN_MAX_ATTEMPTS:
            user.locked_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                minutes=settings.LOGIN_LOCK_MINUTES
            )
            logger.warning(f"用户 {user.username} 已被锁定至 {user.locked_until}")
        db.commit()

    @staticmethod
    def reset_login_lock(user: User, db: Session) -> None:
        """登录成功后重置锁定计数"""
        user.login_attempts = 0
        user.locked_until = None
        db.commit()


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """从 JWT Token 中解析当前用户"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证 Token",
        )

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
        )

    user_id = int(payload.get("sub", 0))
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )

    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求当前用户是管理员"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user
