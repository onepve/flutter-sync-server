"""认证相关请求/响应模型"""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


# ── 注册 ──

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    invite_code: Optional[str] = Field(None, min_length=8, description="邀请码（系统要求时必填）")
    nickname: Optional[str] = Field(None, max_length=64, description="可选昵称，可为空")


class RegisterResponse(BaseModel):
    id: int
    uuid: str
    username: str
    email: str
    message: str = "注册成功，请验证邮箱"
    recovery_key: str = "请妥善保管，此密钥仅显示一次"


# ── 邮箱验证 ──

class VerifyEmailRequest(BaseModel):
    user_id: int
    code: str = Field(..., min_length=6, max_length=6)


# ── 登录 ──

class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: Optional[str] = None  # 开启了 TOTP 时必填


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    uuid: str
    username: str
    nickname: Optional[str] = None
    avatar_url: Optional[str] = None
    totp_required: bool = False


# ── TOTP ──

class TOTPSetupResponse(BaseModel):
    secret: str
    qrcode_b64: str  # base64 编码的 QR 码图片


class TOTPVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class TOTPStatusResponse(BaseModel):
    enabled: bool


# ── Recovery Key ──

class RecoveryKeyResponse(BaseModel):
    recovery_key: str


class RecoveryLoginRequest(BaseModel):
    username: str
    recovery_key: str


# ── Profile ──

class UserProfileResponse(BaseModel):
    id: int
    uuid: str
    username: str
    nickname: str | None = None
    email: str
    avatar_url: str | None = None
    email_verified: bool
    totp_enabled: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── 密码重置 ──

class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., description="注册邮箱")


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=8, max_length=8, description="8 位数字验证码")
    new_password: str = Field(..., min_length=8, max_length=128, description="新密码")


class ForgotPasswordResponse(BaseModel):
    message: str
    token: str | None = None  # SMTP 未配置时直接返回令牌（自托管模式）
