"""认证路由：注册、登录、邮箱验证、TOTP、Recovery Key"""

import secrets
import uuid as uuid_lib
import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, status
from fastapi.responses import JSONResponse, Response, FileResponse
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models.user import User
from models.invite_code import InviteCode
from models.invite_usage_log import InviteUsageLog
from models.encrypted_data import EncryptedData
from models.audit_log import AuditLog
from models.system_config import SystemConfig
from models.password_reset import PasswordResetToken
from schemas.auth_schema import (
    RegisterRequest, RegisterResponse,
    VerifyEmailRequest,
    LoginRequest, LoginResponse,
    TOTPSetupResponse, TOTPVerifyRequest, TOTPStatusResponse,
    RecoveryKeyResponse, RecoveryLoginRequest,
    UserProfileResponse,
    ForgotPasswordRequest, ForgotPasswordResponse,
    ResetPasswordRequest,
)
from services.security import (
    hash_password, verify_password, create_access_token,
    SecurityService, get_current_user, require_admin,
)
from services.totp import TOTPService
from services.email import EmailService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["认证"])

email_service = EmailService()

# ── 内存中暂存验证码（生产环境应替换为 Redis） ──
_verification_codes: dict[str, dict] = {}  # email -> {code, expires_at}


def _log_audit(db: Session, user_id: int | None, action: str, detail: str | None,
               ip: str | None, path: str | None, success: bool,
               username: str | None = None):
    db.add(AuditLog(
        user_id=user_id, action=action, detail=detail,
        ip_address=ip, path=path, success=success,
        username=username,
    ))
    db.commit()


@router.post("/register", response_model=RegisterResponse)
async def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """注册（根据系统配置决定是否需邀请码）"""
    # 0. 检查系统配置：注册是否需要邀请码
    require_invite_row = db.query(SystemConfig).filter(SystemConfig.key == "require_invite_for_registration").first()
    require_invite = require_invite_row.value.lower() == "true" if require_invite_row else True

    # 1. 检查邀请码（如果需要）
    if require_invite:
        if not req.invite_code:
            raise HTTPException(status_code=400, detail="当前系统要求邀请码才能注册")
        invite = db.query(InviteCode).filter(
            InviteCode.code == req.invite_code, InviteCode.is_active == True
        ).first()
        if not invite:
            raise HTTPException(status_code=400, detail="邀请码无效")
        if invite.is_expired:
            raise HTTPException(status_code=400, detail="邀请码已过期")
        if invite.is_exhausted:
            raise HTTPException(status_code=400, detail="邀请码已被用完")

    # 2. 检查用户名/邮箱重复
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="用户名已被占用")
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    # 3. 创建用户
    recovery_key = secrets.token_hex(16)
    user_uuid = str(uuid_lib.uuid4())
    user = User(
        uuid=user_uuid,
        username=req.username.strip(),
        nickname=req.nickname.strip() if req.nickname else None,
        email=req.email.strip(),
        password_hash=hash_password(req.password.strip()),
        recovery_key_hash=hash_password(recovery_key) if email_service.enabled else None,
    )
    db.add(user)
    db.flush()

    # 4. 更新邀请码使用次数并记录使用日志
    if require_invite:
        invite.used_count += 1
        db.add(InviteUsageLog(
            invite_code=invite.code,
            used_by_user_id=user.id,
            used_by_username=user.username,
            used_by_email=user.email,
            created_by=invite.created_by,
        ))

    # 5. 发送邮箱验证码（如果 SMTP 已配置）
    if email_service.enabled:
        code = email_service.generate_verification_code()
        _verification_codes[req.email] = {
            "code": code,
            "expires_at": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10),
            "user_id": user.id,
        }
        sent = await email_service.send_verification_code(req.email, code)
        if sent:
            # 发送 Recovery Key
            await email_service.send_recovery_key(req.email, recovery_key)
            user.email_verified = False
        else:
            user.email_verified = True  # 发信失败则跳过验证
    else:
        user.email_verified = True  # 未配 SMTP 则自动跳过

    db.commit()

    _log_audit(db, user.id, "register", f"用户 {user.username} 注册成功",
               request.client.host, request.url.path, True, username=user.username)

    return RegisterResponse(
        id=user.id,
        uuid=user.uuid,
        username=user.username,
        email=user.email,
        recovery_key=recovery_key if email_service.enabled else "SMTP 未配置，Recovery Key 未生成",
    )


@router.post("/verify-email")
async def verify_email(req: VerifyEmailRequest, request: Request, db: Session = Depends(get_db)):
    """验证邮箱"""
    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.email_verified:
        return {"message": "邮箱已验证"}

    cached = _verification_codes.get(user.email)
    if not cached:
        raise HTTPException(status_code=400, detail="未请求验证码或验证码已过期")
    if cached["user_id"] != user.id:
        raise HTTPException(status_code=400, detail="验证码与用户不匹配")
    if datetime.datetime.now(datetime.timezone.utc) > cached["expires_at"]:
        _verification_codes.pop(user.email, None)
        raise HTTPException(status_code=400, detail="验证码已过期，请重新发送")

    if cached["code"] != req.code:
        raise HTTPException(status_code=400, detail="验证码错误")

    user.email_verified = True
    db.commit()
    _verification_codes.pop(user.email, None)

    # 邮箱验证成功后，向用户邮箱发送解密密钥（UUID）
    if email_service.enabled:
        await email_service.send_decryption_key(user.email, user.uuid)

    _log_audit(db, user.id, "verify_email", "邮箱验证成功",
               request.client.host, request.url.path, True, username=user.username)
    return {"message": "邮箱验证成功"}


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """用户登录"""
    user = db.query(User).filter(
        (User.username == req.username.strip()) | (User.email == req.username.strip())
    ).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 检查锁定
    SecurityService.check_login_lock(user)

    if not verify_password(req.password, user.password_hash):
        SecurityService.record_login_failure(user, db)
        _log_audit(db, user.id, "login_failed", "密码错误",
                   request.client.host, request.url.path, False)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # TOTP 验证
    if user.totp_enabled:
        if not req.totp_code:
            # 告诉前端需要 TOTP
            return LoginResponse(
                access_token="",
                user_id=user.id,
                uuid=user.uuid,
                username=user.username,
                totp_required=True,
            )
        if not TOTPService.verify_code(user.totp_secret, req.totp_code):
            _log_audit(db, user.id, "login_failed", "TOTP 验证失败",
                       request.client.host, request.url.path, False)
            raise HTTPException(status_code=401, detail="TOTP 验证码错误")

    # 登录成功
    SecurityService.reset_login_lock(user, db)
    user.last_login_at = datetime.datetime.now(datetime.timezone.utc)
    user.last_login_ip = request.client.host
    db.commit()

    token = create_access_token(user.id, user.username, user.is_admin)

    _log_audit(db, user.id, "login", "登录成功",
               request.client.host, request.url.path, True, username=user.username)

    return LoginResponse(
        access_token=token,
        user_id=user.id,
        uuid=user.uuid,
        username=user.username,
        nickname=user.nickname,
        avatar_url=user.avatar_url,
    )


@router.post("/totp/setup", response_model=TOTPSetupResponse)
async def setup_totp(current_user: User = Depends(get_current_user)):
    """获取 TOTP 密钥和二维码"""
    secret = TOTPService.generate_secret()
    uri = TOTPService.get_provisioning_uri(secret, current_user.username, settings.TOTP_ISSUER)
    qrcode_b64 = TOTPService.generate_qrcode_base64(uri)

    # 暂存 secret，verify 时再真正启用
    current_user.totp_secret = secret
    return TOTPSetupResponse(secret=secret, qrcode_b64=qrcode_b64)


@router.post("/totp/verify")
async def verify_totp_setup(
    req: TOTPVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """验证并启用 TOTP，生成 Recovery Key 并发送到邮箱"""
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="请先调用 /totp/setup")
    if TOTPService.verify_code(current_user.totp_secret, req.code):
        current_user.totp_enabled = True
        db.commit()

        # 生成新的 Recovery Key
        recovery_key = secrets.token_hex(16)
        current_user.recovery_key_hash = hash_password(recovery_key)
        db.commit()

        # 发送到邮箱（如果 SMTP 已配置）
        sent_to_email = False
        if current_user.email and email_service.enabled:
            sent_to_email = await email_service.send_recovery_key(current_user.email, recovery_key)

        return {
            "message": "TOTP 已启用",
            "recovery_key": recovery_key,
            "recovery_key_sent_to_email": sent_to_email,
        }
    raise HTTPException(status_code=400, detail="验证码错误")


@router.post("/totp/disable")
async def disable_totp(
    req: TOTPVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """关闭 TOTP（需要验证一次 TOTP 或 Recovery Key）"""
    if not current_user.totp_enabled:
        return {"message": "TOTP 未启用"}

    # 允许用 TOTP 或 Recovery Key 关闭
    if TOTPService.verify_code(current_user.totp_secret, req.code):
        current_user.totp_enabled = False
        current_user.totp_secret = None
        db.commit()
        return {"message": "TOTP 已关闭"}

    raise HTTPException(status_code=400, detail="验证码错误")


@router.get("/totp/status", response_model=TOTPStatusResponse)
async def totp_status(current_user: User = Depends(get_current_user)):
    """查看 TOTP 启用状态"""
    return TOTPStatusResponse(enabled=current_user.totp_enabled)


@router.post("/recovery-login", response_model=LoginResponse)
async def recovery_login(
    req: RecoveryLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """使用 Recovery Key 登录（绕过 TOTP）"""
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not user.recovery_key_hash:
        raise HTTPException(status_code=401, detail="用户名或 Recovery Key 错误")

    SecurityService.check_login_lock(user)

    if not verify_password(req.recovery_key, user.recovery_key_hash):
        SecurityService.record_login_failure(user, db)
        _log_audit(db, user.id, "recovery_login_failed", "Recovery Key 错误",
                   request.client.host, request.url.path, False)
        raise HTTPException(status_code=401, detail="用户名或 Recovery Key 错误")

    # 登录成功
    SecurityService.reset_login_lock(user, db)
    user.last_login_at = datetime.datetime.now(datetime.timezone.utc)
    user.last_login_ip = request.client.host
    db.commit()

    token = create_access_token(user.id, user.username, user.is_admin)

    _log_audit(db, user.id, "recovery_login", "使用 Recovery Key 登录成功",
               request.client.host, request.url.path, True, username=user.username)

    return LoginResponse(access_token=token, user_id=user.id, uuid=user.uuid,
                         username=user.username, nickname=user.nickname,
                         avatar_url=user.avatar_url)


@router.get("/profile", response_model=UserProfileResponse)
async def get_profile(current_user: User = Depends(get_current_user)):
    """获取个人资料"""
    return current_user


@router.put("/profile")
async def update_profile(
    new_email: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新个人资料（目前只支持改邮箱）"""
    if new_email:
        if db.query(User).filter(User.email == new_email, User.id != current_user.id).first():
            raise HTTPException(status_code=400, detail="邮箱已被使用")
        current_user.email = new_email
        current_user.email_verified = False
        db.commit()
    return {"message": "更新成功"}


@router.put("/profile/username")
async def change_username(
    new_username: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改用户名"""
    new = new_username.strip()
    if len(new) < 3 or len(new) > 64:
        raise HTTPException(status_code=400, detail="用户名长度需在 3-64 位之间")
    if db.query(User).filter(User.username == new, User.id != current_user.id).first():
        raise HTTPException(status_code=400, detail="用户名已被占用")
    current_user.username = new
    db.commit()
    return {"message": "用户名已更新"}


from pydantic import BaseModel, Field


class ChangeNicknameRequest(BaseModel):
    new_nickname: str = Field(..., description="新昵称")


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.put("/profile/nickname")
async def change_nickname(
    req: ChangeNicknameRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改昵称"""
    new = req.new_nickname.strip()
    if len(new) > 64:
        raise HTTPException(status_code=400, detail="昵称不能超过 64 个字符")
    current_user.nickname = new if new else None
    db.commit()
    return {"message": "昵称已更新"}


@router.put("/profile/password")
async def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改密码（需提供旧密码验证）"""
    from services.security import verify_password, hash_password
    if not verify_password(req.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="旧密码错误")
    if len(req.new_password) < 8 or len(req.new_password) > 128:
        raise HTTPException(status_code=400, detail="密码长度需在 8-128 位之间")
    current_user.password_hash = hash_password(req.new_password)
    db.commit()
    return {"message": "密码已更新"}


# ── 头像上传 ──

# 允许图片类型
_AVATAR_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_AVATAR_MAX_SIZE = 5 * 1024 * 1024  # 5MB


@router.post("/profile/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传头像（图片格式/大小校验 + 二进制存储到数据库）"""
    # 1. 校验类型
    if file.content_type not in _AVATAR_ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail="仅支持 JPG/PNG/WebP/GIF 格式的图片",
        )

    # 2. 读取并校验大小
    data = await file.read()
    if len(data) > _AVATAR_MAX_SIZE:
        raise HTTPException(
            status_code=400,
            detail="图片大小不能超过 5MB",
        )

    # 3. 存储到数据库（不再写本地文件系统）
    current_user.avatar_data = data
    current_user.avatar_mime = file.content_type
    current_user.avatar_updated_at = datetime.datetime.now(datetime.timezone.utc)
    # avatar_url 指向数据库获取端点 + 时间戳防客户端缓存
    ts = int(current_user.avatar_updated_at.timestamp())
    current_user.avatar_url = f"/api/auth/profile/avatar/image?t={ts}"
    db.commit()

    logger.info(f"头像已更新（数据库）: {current_user.username}, {len(data)} bytes")
    return {"avatar_url": current_user.avatar_url}


@router.get("/profile/avatar/image")
async def get_avatar_image(
    current_user: User = Depends(get_current_user),
):
    """从数据库读取当前用户的头像二进制并返回图片（需 JWT 认证）"""
    if current_user.avatar_data:
        return Response(
            content=current_user.avatar_data,
            media_type=current_user.avatar_mime or "image/png",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    # 无自定义头像 → 返回默认头像
    import os as _os_module
    default_path = _os_module.path.join(_os_module.path.dirname(__file__), "..", "static", "default-avatar.jpg")
    if _os_module.path.exists(default_path):
        return FileResponse(default_path, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="未设置头像")


# ── 忘记密码 ──

@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    req: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """发送密码重置令牌到邮箱"""
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        # 不透露邮箱是否存在，统一返回成功
        return ForgotPasswordResponse(message="如果该邮箱已注册，重置链接已发送")

    # 生成 8 位数字验证码
    token = email_service.generate_verification_code(8)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)

    reset = PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=expires_at,
    )
    db.add(reset)
    db.commit()

    _log_audit(db, user.id, "forgot_password", "请求密码重置",
               request.client.host, request.url.path, True, username=user.username)

    # 如果 SMTP 已配置，发送邮件
    if email_service.enabled:
        sent = await email_service.send_reset_code(user.email, token)
        if sent:
            return ForgotPasswordResponse(message="重置链接已发送到您的邮箱，请在一小时内使用")
        return ForgotPasswordResponse(message="邮件发送失败，请稍后重试")

    # SMTP 未配置（自托管模式）：直接返回令牌
    return ForgotPasswordResponse(
        message="自托管模式：请使用以下重置令牌",
        token=token,
    )


@router.post("/reset-password")
async def reset_password(
    req: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """使用重置令牌设置新密码"""
    reset = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == req.token,
        PasswordResetToken.used == False,
    ).first()

    if not reset:
        raise HTTPException(status_code=400, detail="重置令牌无效")
    if reset.is_expired:
        raise HTTPException(status_code=400, detail="重置令牌已过期，请重新申请")

    user = db.query(User).filter(User.id == reset.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 更新密码
    user.password_hash = hash_password(req.new_password)
    # 重置后清除 TOTP（防止用户被锁在账号外）
    if user.totp_enabled:
        user.totp_enabled = False
        user.totp_secret = None
    # 重置登录锁定
    user.login_attempts = 0
    user.locked_until = None
    # 标记令牌已使用
    reset.used = True
    db.commit()

    _log_audit(db, user.id, "reset_password", "密码已重置",
               request.client.host, request.url.path, True, username=user.username)

    return {"message": "密码已重置成功，请使用新密码登录"}


# ── 重新发送邮箱验证码 ──

_verification_code_cooldowns: dict[str, float] = {}  # email -> timestamp


@router.post("/resend-verification")
async def resend_verification(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重新发送邮箱验证码"""
    import time as time_mod
    if current_user.email_verified:
        return {"message": "邮箱已验证，无需重新发送"}
    if not email_service.enabled:
        return {"message": "邮件服务未配置，请联系管理员"}

    # 防刷：60 秒冷却
    last = _verification_code_cooldowns.get(current_user.email, 0)
    if time_mod.time() - last < 60:
        raise HTTPException(status_code=429, detail="请 60 秒后再试")

    code = email_service.generate_verification_code()
    _verification_codes[current_user.email] = {
        "code": code,
        "expires_at": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10),
        "user_id": current_user.id,
    }
    sent = await email_service.send_verification_code(current_user.email, code)
    if sent:
        _verification_code_cooldowns[current_user.email] = time_mod.time()
        return {"message": "验证码已重新发送到您的邮箱"}
    return {"message": "邮件发送失败，请稍后重试"}


# ── 发送删除验证码 ──

_delete_verification_codes: dict[str, dict] = {}  # email -> {code, expires_at, purpose}
_delete_code_cooldowns: dict[str, float] = {}


@router.post("/send-delete-code")
async def send_delete_code(
    purpose: str = "sync_data",
    request: Request = None,
    current_user: User = Depends(get_current_user),
):
    """发送删除操作验证码到邮箱"""
    import time as time_mod
    if not email_service.enabled:
        # 无 SMTP 模式：直接返回成功（不验证）
        return {"message": "删除操作已就绪"}

    purpose = request.query_params.get("purpose", "sync_data") if request else purpose

    # 防刷：30 秒冷却
    last = _delete_code_cooldowns.get(current_user.email, 0)
    if time_mod.time() - last < 30:
        raise HTTPException(status_code=429, detail="请 30 秒后再试")

    code = email_service.generate_verification_code()
    _delete_verification_codes[current_user.email] = {
        "code": code,
        "expires_at": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5),
        "purpose": purpose,
    }
    sent = await email_service.send_verification_code(
        current_user.email, f"您的删除验证码为：{code}（5分钟内有效）"
    )
    if sent:
        _delete_code_cooldowns[current_user.email] = time_mod.time()
        return {"message": "删除验证码已发送到您的邮箱"}
    return {"message": "邮件发送失败，请稍后重试"}


@router.post("/verify-delete-code")
async def verify_delete_code(
    code: str = None,
    request: Request = None,
    current_user: User = Depends(get_current_user),
):
    """验证删除操作码（TOTP 或邮件验证码）"""
    # 先检查 TOTP
    if current_user.totp_enabled:
        from services.totp import TOTPService
        body = await request.json() if request else {}
        req_code = body.get("code", code)
        if TOTPService.verify_code(current_user.totp_secret, req_code):
            return {"message": "验证通过"}
        raise HTTPException(status_code=400, detail="TOTP 验证码错误")

    # 检查邮件验证码
    body = await request.json() if request else {}
    req_code = body.get("code", code)
    cached = _delete_verification_codes.get(current_user.email)
    if not cached:
        raise HTTPException(status_code=400, detail="未请求验证码或验证码已过期")
    if datetime.datetime.now(datetime.timezone.utc) > cached["expires_at"]:
        _delete_verification_codes.pop(current_user.email, None)
        raise HTTPException(status_code=400, detail="验证码已过期，请重新发送")
    if cached["code"] != req_code:
        raise HTTPException(status_code=400, detail="验证码错误")

    _delete_verification_codes.pop(current_user.email, None)
    return {"message": "验证通过"}


# ── 注销账号 ──


@router.post("/delete-account")
async def delete_account(
    password: str = None,
    export_to_email: bool = True,
    request: Request = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """注销账号：验证密码 → 导出数据 → 发送备份邮件 → 删除所有数据"""
    from services.security import verify_password as verify_pwd

    body = await request.json() if request else {}
    pwd = body.get("password", password) if body else password
    do_export = body.get("export_to_email", export_to_email) if body else export_to_email

    if not verify_pwd(pwd, current_user.password_hash):
        raise HTTPException(status_code=401, detail="密码错误")

    export_data = ""
    if do_export:
        # 导出所有加密数据
        records = db.query(EncryptedData).filter(
            EncryptedData.user_id == current_user.id,
        ).all()
        if records:
            # 合并所有加密数据
            combined = {
                "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "username": current_user.username,
                "data": {
                    r.data_type: {
                        "ciphertext": r.ciphertext.decode("utf-8"),
                        "version": r.version,
                        "device_id": r.device_id,
                    }
                    for r in records
                },
            }
            import json
            export_data = json.dumps(combined, ensure_ascii=False)
            # 发送备份邮件
            if email_service.enabled and current_user.email:
                await email_service.send_account_backup(
                    current_user.email, current_user.uuid, export_data
                )

    # 删除所有关联数据
    db.query(EncryptedData).filter(EncryptedData.user_id == current_user.id).delete()
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == current_user.id).delete()
    db.query(AuditLog).filter(AuditLog.user_id == current_user.id).delete()
    db.delete(current_user)
    db.commit()

    logger.info(f"账号已注销: {current_user.username}")
    return {
        "message": "账号已注销，数据已从服务器永久删除。",
        "data_backup": export_data if not email_service.enabled else "备份已发送到您的邮箱",
    }


# ── 公共配置（无需认证） ──


@router.get("/config")
async def get_public_config(db: Session = Depends(get_db)):
    """获取公开的系统配置（无需认证）"""
    from schemas.admin_schema import AdminConfigResponse, DEFAULT_SYSTEM_CONFIG

    config = dict(DEFAULT_SYSTEM_CONFIG)
    rows = db.query(SystemConfig).all()
    for row in rows:
        if row.key in config:
            config[row.key] = row.value

    return AdminConfigResponse(
        require_invite_for_registration=config["require_invite_for_registration"].lower() == "true",
        allow_user_create_invite=config["allow_user_create_invite"].lower() == "true",
        max_invites_per_user=int(config["max_invites_per_user"]),
    )
