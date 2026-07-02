"""管理员相关请求/响应模型"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ── 邀请码 ──

class InviteCreateRequest(BaseModel):
    """创建邀请码"""
    max_uses: int = Field(default=1, ge=-1, description="-1=无限, >=1=限制次数")
    expires_in_days: Optional[int] = Field(default=30, ge=1, description="有效期天数，null=永久")
    expires_at: Optional[str] = Field(default=None, description="自定义过期时间 ISO 格式（如 2026-12-31T23:59:59）")
    is_public: bool = Field(default=False, description="是否公开")


class InviteBatchRequest(BaseModel):
    """批量生成邀请码"""
    count: int = Field(default=5, ge=1, le=500)
    max_uses: int = Field(default=1, ge=-1)
    expires_in_days: Optional[int] = Field(default=30, ge=1)
    expires_at: Optional[str] = None
    is_public: bool = False


class InviteCodeResponse(BaseModel):
    id: int
    code: str
    max_uses: int
    used_count: int
    is_active: bool
    is_public: bool
    is_expired: bool
    expires_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class InviteListResponse(BaseModel):
    codes: list[InviteCodeResponse]
    total: int


class InviteDisableRequest(BaseModel):
    code: str
    lock: bool = True


class InviteDeleteRequest(BaseModel):
    code: str


class InviteResetRequest(BaseModel):
    code: str


# ── 用户管理 ──

class AdminUserResponse(BaseModel):
    id: int
    uuid: str
    username: str
    email: str
    nickname: Optional[str] = None
    email_verified: bool
    totp_enabled: bool
    is_active: bool
    is_admin: bool
    display_order: Optional[int] = None
    created_at: datetime
    last_login_at: Optional[datetime]
    login_attempts: int
    avatar_url: Optional[str] = None

    model_config = {"from_attributes": True}


class AdminUserListResponse(BaseModel):
    users: list[AdminUserResponse]
    total: int


class AdminLockRequest(BaseModel):
    user_id: int
    lock: bool = True


class AdminDeleteUserRequest(BaseModel):
    """管理员删除用户"""
    user_id: int
    export_to_email: bool = Field(default=False, description="是否发送数据备份邮件到用户邮箱")


class AdminTOTPRequest(BaseModel):
    user_id: int


class AdminTOTPBindRequest(BaseModel):
    """管理员协助用户绑定 TOTP"""
    user_id: int
    secret: str = Field(..., description="TOTP 密钥")
    code: str = Field(..., min_length=6, max_length=6, description="验证码确认")


class AdminUpdateOrderRequest(BaseModel):
    """更新用户显示排序"""
    items: list[dict] = Field(..., description="[{id: int, display_order: int}]")


# ── 审计日志 ──

class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int]
    username: Optional[str] = None
    action: str
    detail: Optional[str]
    ip_address: Optional[str]
    path: Optional[str]
    success: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    logs: list[AuditLogResponse]
    total: int


# ── 邮件日志 ──

class EmailLogResponse(BaseModel):
    id: int
    to_email: str
    subject: str
    body: str
    success: bool
    error_message: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EmailLogListResponse(BaseModel):
    logs: list[EmailLogResponse]
    total: int


# ── 统计数据 ──

class StatsResponse(BaseModel):
    total_users: int
    active_today: int
    total_data_items: int
    total_invite_codes: int
    used_invite_codes: int


# ── 系统配置 ──

DEFAULT_SYSTEM_CONFIG = {
    "require_invite_for_registration": "true",
    "allow_user_create_invite": "false",
    "max_invites_per_user": "5",
}


class AdminConfigUpdateRequest(BaseModel):
    """更新系统配置"""
    require_invite_for_registration: Optional[bool] = None
    allow_user_create_invite: Optional[bool] = None
    max_invites_per_user: Optional[int] = None


class AdminConfigResponse(BaseModel):
    require_invite_for_registration: bool = True
    allow_user_create_invite: bool = False
    max_invites_per_user: int = 5
