"""管理后台路由 — 用户管理/审计日志"""

import logging
import datetime
import json

from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_, asc, desc
from typing import Optional

from database import get_db
from models.user import User
from models.encrypted_data import EncryptedData
from models.invite_code import InviteCode
from models.invite_usage_log import InviteUsageLog
from models.audit_log import AuditLog
from models.email_log import EmailLog
from models.password_reset import PasswordResetToken
from schemas.admin_schema import (
    AdminUserResponse, AdminUserListResponse,
    AdminLockRequest, AdminDeleteUserRequest,
    AdminTOTPRequest, AdminTOTPBindRequest, AdminUpdateOrderRequest,
    AuditLogResponse, AuditLogListResponse,
    EmailLogResponse, EmailLogListResponse,
    InviteUsageLogResponse, InviteUsageLogListResponse,
    StatsResponse,
    AdminConfigUpdateRequest, AdminConfigResponse, DEFAULT_SYSTEM_CONFIG,
)
from services.security import (
    require_admin,
    require_min_role,
    is_admin_role,
)
from models.system_config import SystemConfig
from services.totp import TOTPService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["管理后台"])

ADMIN_DEPENDS = [Depends(require_admin)]


# ── 统计 ──


@router.get("/stats", response_model=StatsResponse, dependencies=ADMIN_DEPENDS)
async def get_stats(db: Session = Depends(get_db)):
    """统计数据"""
    total_users = db.query(func.count(User.id)).scalar() or 0

    today_start = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    active_today = db.query(func.count(User.id)).filter(
        User.last_login_at >= today_start
    ).scalar() or 0

    total_data = db.query(func.count(EncryptedData.id)).scalar() or 0
    total_invites = db.query(func.count(InviteCode.id)).scalar() or 0
    used_invites = db.query(func.count(InviteCode.id)).filter(
        InviteCode.used_count > 0
    ).scalar() or 0

    return StatsResponse(
        total_users=total_users,
        active_today=active_today,
        total_data_items=total_data,
        total_invite_codes=total_invites,
        used_invite_codes=used_invites,
    )


# ── 用户管理 ──


@router.get("/users", response_model=AdminUserListResponse, dependencies=ADMIN_DEPENDS)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    # 搜索
    search: Optional[str] = Query(None, description="搜索关键词（用户名/邮箱/UUID/ID）"),
    # 排序
    sort_by: str = Query("id", description="排序字段"),
    sort_order: str = Query("asc", description="asc 或 desc"),
    # 过滤
    filter_active: Optional[str] = Query(None, description="all/active/locked"),
    filter_totp: Optional[str] = Query(None, description="all/enabled/disabled"),
    filter_admin: Optional[str] = Query(None, description="all/admin/user"),
    db: Session = Depends(get_db),
):
    """用户列表（搜索/排序/过滤）"""
    query = db.query(User)

    # ── 搜索 ──
    if search:
        search_term = f"%{search}%"
        # 尝试按 ID 精确搜索
        try:
            search_id = int(search)
            id_filter = User.id == search_id
        except ValueError:
            id_filter = False
        query = query.filter(
            or_(
                User.username.ilike(search_term),
                User.email.ilike(search_term),
                User.uuid.ilike(search_term),
                id_filter,
            )
        )

    # ── 过滤 ──
    if filter_active == "active":
        query = query.filter(User.is_active == True)
    elif filter_active == "locked":
        query = query.filter(User.is_active == False)

    if filter_totp == "enabled":
        query = query.filter(User.totp_enabled == True)
    elif filter_totp == "disabled":
        query = query.filter(User.totp_enabled == False)

    if filter_admin == "admin":
        query = query.filter(User.is_admin == True)
    elif filter_admin == "user":
        query = query.filter(User.is_admin == False)

    # ── 排序 ──
    allowed_sort_fields = {
        "id": User.id,
        "username": User.username,
        "email": User.email,
        "display_order": User.display_order,
        "created_at": User.created_at,
        "last_login_at": User.last_login_at,
        "is_active": User.is_active,
        "totp_enabled": User.totp_enabled,
    }
    sort_column = allowed_sort_fields.get(sort_by, User.id)
    order_fn = asc if sort_order == "asc" else desc

    # display_order 为空时放到最后
    if sort_by == "display_order":
        query = query.order_by(
            asc(func.coalesce(User.display_order, 999999999))
            if sort_order == "asc" else
            desc(func.coalesce(User.display_order, -1))
        )
    else:
        query = query.order_by(order_fn(sort_column), User.id)

    total = query.count()
    users = query.offset((page - 1) * page_size).limit(page_size).all()

    return AdminUserListResponse(
        users=[AdminUserResponse.model_validate(u) for u in users],
        total=total,
    )


@router.post("/user/lock", dependencies=ADMIN_DEPENDS)
async def lock_user(req: AdminLockRequest, db: Session = Depends(get_db)):
    """锁定/解锁用户"""
    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="不能锁定管理员")

    if req.lock:
        user.is_active = False
        msg = f"用户 {user.username} 已锁定"
    else:
        user.is_active = True
        user.login_attempts = 0
        user.locked_until = None
        msg = f"用户 {user.username} 已解锁"

    db.commit()
    # 审计
    _log(db, user.id, "admin_user_lock" if req.lock else "admin_user_unlock", msg, username=user.username)
    return {"message": msg}


@router.post("/user/delete", dependencies=ADMIN_DEPENDS)
async def delete_user(req: AdminDeleteUserRequest, current_user: User = Depends(require_min_role("primary_admin")), db: Session = Depends(get_db)):
    """删除用户（仅主管理员，支持邮件备份）"""
    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.is_admin or is_admin_role(user.role):
        raise HTTPException(status_code=400, detail="不能删除管理员")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")

    username = user.username
    export_data = ""

    # ── 导出数据并发送邮件 ──
    if req.export_to_email:
        from routers.auth import email_service
        records = db.query(EncryptedData).filter(
            EncryptedData.user_id == user.id,
        ).all()
        if records:
            import json
            combined = {
                "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "username": user.username,
                "app_id": user.uuid,
                "data": {
                    r.data_type: {
                        "ciphertext": r.ciphertext.decode("utf-8"),
                        "version": r.version,
                        "device_id": r.device_id,
                    }
                    for r in records
                },
            }
            export_data = json.dumps(combined, ensure_ascii=False)

        if email_service and email_service.enabled and user.email:
            await email_service.send_account_backup(
                to_email=user.email,
                uuid_key=user.uuid,
                export_data=export_data,
            )

    # ── 删除所有关联数据（保留审计日志引用） ──
    db.query(EncryptedData).filter(EncryptedData.user_id == user.id).delete()
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete()
    db.delete(user)
    db.commit()

    logger.info(f"管理员已删除用户: {username}")
    _log(db, user.id, "admin_user_delete",
         f"管理员删除了用户 {username}" +
         ("（已发送数据备份邮件）" if req.export_to_email else "（未保留数据）"),
         username=username)

    return {
        "message": f"用户 {username} 已永久删除",
        "email_sent": req.export_to_email,
    }


@router.post("/user/totp-clear", dependencies=ADMIN_DEPENDS)
async def clear_user_totp(req: AdminTOTPRequest, db: Session = Depends(get_db)):
    """清空用户 TOTP"""
    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.totp_secret = None
    user.totp_enabled = False
    db.commit()

    _log(db, user.id, "admin_totp_clear", f"管理员清除了用户 {user.username} 的 TOTP", username=user.username)
    return {"message": f"用户 {user.username} 的 TOTP 已清空"}


@router.post("/user/totp-bind-setup", dependencies=ADMIN_DEPENDS)
async def setup_user_totp(req: AdminTOTPRequest, db: Session = Depends(get_db)):
    """生成用户 TOTP 密钥和二维码（协助绑定第一步）"""
    from services.totp import TOTPService

    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    secret = TOTPService.generate_secret()
    qrcode_b64 = TOTPService.generate_qrcode_base64(
        TOTPService.get_provisioning_uri(secret, user.username, "CBox")
    )
    # 临时存储密钥
    user.totp_secret = secret
    db.commit()

    return {
        "secret": secret,
        "qrcode_b64": qrcode_b64,
        "message": "请让用户扫描二维码或输入密钥，验证后确认绑定",
    }


@router.post("/user/totp-bind-confirm", dependencies=ADMIN_DEPENDS)
async def confirm_user_totp(req: AdminTOTPBindRequest, db: Session = Depends(get_db)):
    """确认用户 TOTP 绑定"""
    from services.totp import TOTPService

    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not user.totp_secret:
        raise HTTPException(status_code=400, detail="请先生成 TOTP 密钥")

    if not TOTPService.verify_code(user.totp_secret, req.code):
        raise HTTPException(status_code=400, detail="验证码错误，请重试")

    user.totp_enabled = True
    db.commit()

    _log(db, user.id, "admin_totp_bind", f"管理员协助用户 {user.username} 绑定 TOTP", username=user.username)
    return {"message": f"用户 {user.username} 的 TOTP 已绑定成功"}


@router.put("/user/order", dependencies=ADMIN_DEPENDS)
async def update_user_order(req: AdminUpdateOrderRequest, db: Session = Depends(get_db)):
    """批量更新用户显示排序"""
    for item in req.items:
        uid = item.get("id")
        order = item.get("display_order")
        if uid is not None:
            db.query(User).filter(User.id == uid).update(
                {"display_order": order}
            )
    db.commit()
    _log(db, None, "admin_user_reorder", "管理员更新了用户排序")
    return {"message": "排序已更新"}


class AdminUpdateUserRequest(BaseModel):
    user_id: int
    email: str | None = None


@router.put("/user/profile", dependencies=ADMIN_DEPENDS)
async def admin_update_user_profile(
    req: AdminUpdateUserRequest,
    db: Session = Depends(get_db),
):
    """管理员修改用户资料（邮箱等）"""
    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.email:
        if db.query(User).filter(User.email == req.email, User.id != req.user_id).first():
            raise HTTPException(status_code=400, detail="邮箱已被使用")
        user.email = req.email
        user.email_verified = False
    db.commit()
    _log(db, req.user_id, "admin_update_profile",
         f"管理员修改了用户 {user.username} 的邮箱为 {req.email}",
         username=user.username)
    return {"message": "用户资料已更新"}


# ── 审计日志 ──


@router.get("/logs", response_model=AuditLogListResponse, dependencies=ADMIN_DEPENDS)
async def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None, description="搜索关键词"),
    action: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None, description="起始日期 ISO"),
    date_to: Optional[str] = Query(None, description="结束日期 ISO"),
    db: Session = Depends(get_db),
):
    """审计日志（搜索/过滤）"""
    query = db.query(AuditLog)

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                AuditLog.action.ilike(search_term),
                AuditLog.detail.ilike(search_term),
                AuditLog.ip_address.ilike(search_term),
                AuditLog.username.ilike(search_term),
            )
        )

    if action:
        query = query.filter(AuditLog.action == action)

    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)

    if date_from:
        try:
            dt_from = datetime.datetime.fromisoformat(date_from)
            query = query.filter(AuditLog.created_at >= dt_from)
        except ValueError:
            pass

    if date_to:
        try:
            dt_to = datetime.datetime.fromisoformat(date_to)
            query = query.filter(AuditLog.created_at <= dt_to)
        except ValueError:
            pass

    query = query.order_by(AuditLog.created_at.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()

    return AuditLogListResponse(
        logs=[AuditLogResponse(
            id=audit_log.id,
            user_id=audit_log.user_id,
            username=audit_log.username,
            action=audit_log.action,
            detail=audit_log.detail,
            ip_address=audit_log.ip_address,
            path=audit_log.path,
            success=audit_log.success,
            created_at=audit_log.created_at,
        ) for audit_log in rows],
        total=total,
    )


@router.delete("/logs/clear", dependencies=ADMIN_DEPENDS)
async def clear_all_logs(db: Session = Depends(get_db)):
    """清空所有审计日志"""
    count = db.query(AuditLog).count()
    db.query(AuditLog).delete()
    db.commit()
    _log(db, None, "admin_logs_clear", f"管理员清空了所有审计日志（共 {count} 条）")
    return {"message": f"已清空 {count} 条审计日志"}


# ════════════════════ 邮件日志 ════════════════════


@router.get("/email-logs", response_model=EmailLogListResponse, dependencies=ADMIN_DEPENDS)
async def get_email_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    search: Optional[str] = Query(None, description="搜索收件人或主题"),
    db: Session = Depends(get_db),
):
    """邮件发送日志列表（管理员）"""
    query = db.query(EmailLog)

    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(EmailLog.to_email.like(like), EmailLog.subject.like(like))
        )

    query = query.order_by(EmailLog.created_at.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()

    return EmailLogListResponse(
        logs=[EmailLogResponse(
            id=el.id,
            to_email=el.to_email,
            subject=el.subject,
            body=el.body,
            success=el.success,
            error_message=el.error_message,
            created_at=el.created_at,
        ) for el in rows],
        total=total,
    )


@router.delete("/email-logs/clear", dependencies=ADMIN_DEPENDS)
async def clear_email_logs(db: Session = Depends(get_db)):
    """清空所有邮件日志"""
    count = db.query(EmailLog).count()
    db.query(EmailLog).delete()
    db.commit()
    _log(db, None, "admin_email_logs_clear", f"管理员清空了所有邮件日志（共 {count} 条）")
    return {"message": f"已清空 {count} 条邮件日志"}


# ════════════════════ 系统配置 ════════════════════


def _get_config_value(db: Session, key: str) -> str | None:
    """从 DB 读取单条配置"""
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row else None


def _set_config_value(db: Session, key: str, value: str):
    """写入单条配置"""
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))
    db.commit()


def _load_config(db: Session) -> dict:
    """加载所有系统配置（合并默认值）"""
    config = dict(DEFAULT_SYSTEM_CONFIG)
    rows = db.query(SystemConfig).all()
    for row in rows:
        if row.key in config:
            config[row.key] = row.value
    return config


def _config_to_response(config: dict) -> AdminConfigResponse:
    """将 dict 配置转为响应模型"""
    return AdminConfigResponse(
        require_invite_for_registration=config.get("require_invite_for_registration", "true").lower() == "true",
        allow_user_create_invite=config.get("allow_user_create_invite", "false").lower() == "true",
        max_invites_per_user=int(config.get("max_invites_per_user", "5")),
    )


@router.get("/config", dependencies=ADMIN_DEPENDS)
async def get_admin_config(db: Session = Depends(get_db)):
    """获取系统配置（管理员）"""
    config = _load_config(db)
    return _config_to_response(config)


@router.put("/config", dependencies=ADMIN_DEPENDS)
async def update_admin_config(
    req: AdminConfigUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_min_role("primary_admin")),
    request: Request = None,
):
    """更新系统配置（仅主管理员）"""
    if req.require_invite_for_registration is not None:
        _set_config_value(db, "require_invite_for_registration", str(req.require_invite_for_registration).lower())
    if req.allow_user_create_invite is not None:
        _set_config_value(db, "allow_user_create_invite", str(req.allow_user_create_invite).lower())
    if req.max_invites_per_user is not None:
        _set_config_value(db, "max_invites_per_user", str(req.max_invites_per_user))

    config = _load_config(db)
    return _config_to_response(config)


# ── 邀请码使用日志 ──

@router.get("/invite-usage-logs", response_model=InviteUsageLogListResponse, dependencies=ADMIN_DEPENDS)
async def get_invite_usage_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
    search: Optional[str] = Query(None, description="搜索邀请码或用户名"),
    db: Session = Depends(get_db),
):
    """查询邀请码使用日志"""
    q = db.query(InviteUsageLog)

    if search:
        q = q.filter(
            or_(
                InviteUsageLog.invite_code.contains(search),
                InviteUsageLog.used_by_username.contains(search),
            )
        )

    total = q.count()
    logs = q.order_by(desc(InviteUsageLog.created_at)) \
            .offset((page - 1) * page_size) \
            .limit(page_size) \
            .all()

    return InviteUsageLogListResponse(
        logs=[InviteUsageLogResponse.model_validate(log) for log in logs],
        total=total,
    )


# ── 辅助 ──


def _log(db: Session, user_id: int | None, action: str, detail: str | None = None, username: str | None = None):
    """记录审计日志（username 写死到日志中，用户删除后仍可追踪）"""
    try:
        log = AuditLog(user_id=user_id, action=action, detail=detail, username=username)
        db.add(log)
        db.commit()
    except Exception as e:
        logger.warning(f"审计日志写入失败: {e}")
        db.rollback()
