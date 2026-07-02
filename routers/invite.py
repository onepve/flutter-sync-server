"""邀请码管理路由（管理员专用）"""

import secrets
import datetime
import io
import csv
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional

from database import get_db
from models.user import User
from models.invite_code import InviteCode
from models.audit_log import AuditLog
from models.system_config import SystemConfig
from schemas.admin_schema import (
    InviteCreateRequest, InviteBatchRequest,
    InviteCodeResponse, InviteListResponse,
    InviteDisableRequest, InviteDeleteRequest, InviteResetRequest,
)
from services.security import require_admin, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/invite", tags=["邀请码"])

ADMIN_DEPENDS = [Depends(require_admin)]


def _calc_expires(days: int | None, custom_at: str | None = None) -> datetime.datetime | None:
    """计算过期时间"""
    if custom_at:
        try:
            return datetime.datetime.fromisoformat(custom_at)
        except ValueError:
            pass
    if days is None:
        return None
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)


@router.post("/create", response_model=InviteCodeResponse, dependencies=ADMIN_DEPENDS)
async def create_invite(req: InviteCreateRequest, db: Session = Depends(get_db)):
    """创建单个邀请码"""
    code = secrets.token_hex(16)
    invite = InviteCode(
        code=code,
        max_uses=req.max_uses,
        expires_at=_calc_expires(req.expires_in_days, req.expires_at),
        is_public=req.is_public,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


@router.post("/batch", response_model=InviteListResponse, dependencies=ADMIN_DEPENDS)
async def batch_create_invites(req: InviteBatchRequest, db: Session = Depends(get_db)):
    """批量生成邀请码"""
    codes = []
    expires_at = _calc_expires(req.expires_in_days, req.expires_at)
    for _ in range(req.count):
        code = secrets.token_hex(16)
        invite = InviteCode(
            code=code,
            max_uses=req.max_uses,
            expires_at=expires_at,
            is_public=req.is_public,
        )
        db.add(invite)
        codes.append(invite)
    db.commit()
    for c in codes:
        db.refresh(c)
    return InviteListResponse(
        codes=[InviteCodeResponse.model_validate(c) for c in codes],
        total=len(codes),
    )


@router.get("/list", response_model=InviteListResponse, dependencies=ADMIN_DEPENDS)
async def list_invites(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: str | None = Query(None),
    filter_status: str | None = Query(None, description="all/active/expired/disabled/exhausted"),
    db: Session = Depends(get_db),
):
    """邀请码列表（支持搜索和过滤）"""
    query = db.query(InviteCode)

    # 搜索（按邀请码本身）
    if search:
        query = query.filter(InviteCode.code.ilike(f"%{search}%"))

    # 注：is_expired/is_exhausted 是属性，不能直接在 sqlalchemy 查询中用
    # 我们过滤过期：expires_at < now
    now = datetime.datetime.now(datetime.timezone.utc)
    if filter_status == "active":
        query = query.filter(
            InviteCode.is_active == True,
            (InviteCode.expires_at.is_(None)) | (InviteCode.expires_at >= now),
            InviteCode.used_count < InviteCode.max_uses,
        )
    elif filter_status == "expired":
        query = query.filter(
            InviteCode.is_active == True,
            InviteCode.expires_at.isnot(None),
            InviteCode.expires_at < now,
        )
    elif filter_status == "disabled":
        query = query.filter(InviteCode.is_active == False)
    elif filter_status == "exhausted":
        query = query.filter(
            InviteCode.is_active == True,
            InviteCode.max_uses > 0,
            InviteCode.used_count >= InviteCode.max_uses,
        )

    query = query.order_by(InviteCode.created_at.desc())
    total = query.count()
    codes = query.offset((page - 1) * page_size).limit(page_size).all()

    return InviteListResponse(
        codes=[InviteCodeResponse.model_validate(c) for c in codes],
        total=total,
    )


@router.patch("/lock", dependencies=ADMIN_DEPENDS)
async def lock_invite(req: InviteDisableRequest, db: Session = Depends(get_db)):
    """锁定/解锁邀请码"""
    invite = db.query(InviteCode).filter(InviteCode.code == req.code).first()
    if not invite:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    invite.is_active = not req.lock
    db.commit()
    action = "已锁定" if req.lock else "已解锁"
    return {"message": f"邀请码 {invite.code} {action}"}


@router.post("/reset", dependencies=ADMIN_DEPENDS)
async def reset_invite(req: InviteResetRequest, db: Session = Depends(get_db)):
    """复用：重置邀请码使用次数归零"""
    invite = db.query(InviteCode).filter(InviteCode.code == req.code).first()
    if not invite:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    invite.used_count = 0
    invite.is_active = True
    db.commit()
    return {"message": f"邀请码 {invite.code} 已复用（使用次数归零）"}


@router.delete("/delete", dependencies=ADMIN_DEPENDS)
async def delete_invite(req: InviteDeleteRequest, db: Session = Depends(get_db)):
    """删除邀请码"""
    invite = db.query(InviteCode).filter(InviteCode.code == req.code).first()
    if not invite:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    code_str = invite.code
    db.delete(invite)
    db.commit()
    return {"message": f"邀请码 {code_str} 已删除"}


# ── 用户自创邀请码 ──


class UserCreateInviteRequest(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=100)
    expires_in_days: Optional[int] = Field(default=30, ge=1, le=365)


@router.post("/user-create")
async def user_create_invite(
    req: UserCreateInviteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """普通用户创建自己的邀请码（受系统配置限制）"""
    # 检查系统配置
    allow_row = db.query(SystemConfig).filter(SystemConfig.key == "allow_user_create_invite").first()
    allow = allow_row.value.lower() == "true" if allow_row else False
    if not allow:
        raise HTTPException(status_code=403, detail="当前系统未开放普通用户创建邀请码")

    max_per_user_row = db.query(SystemConfig).filter(SystemConfig.key == "max_invites_per_user").first()
    max_invites = int(max_per_user_row.value) if max_per_user_row else 5

    # 检查用户已创建的邀请码数量
    existing_count = db.query(InviteCode).filter(
        InviteCode.created_by == current_user.id,
        InviteCode.is_active == True,
    ).count()
    if existing_count >= max_invites:
        raise HTTPException(status_code=400, detail=f"已达到最大创建数量（{max_invites} 个），请先删除旧的邀请码")

    code = secrets.token_hex(16)
    invite = InviteCode(
        code=code,
        max_uses=req.max_uses,
        expires_at=_calc_expires(req.expires_in_days),
        created_by=current_user.id,
        is_public=False,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return InviteCodeResponse.model_validate(invite)


@router.get("/user-list")
async def user_list_invites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查看自己创建的邀请码"""
    codes = db.query(InviteCode).filter(
        InviteCode.created_by == current_user.id,
    ).order_by(InviteCode.created_at.desc()).all()
    return InviteListResponse(
        codes=[InviteCodeResponse.model_validate(c) for c in codes],
        total=len(codes),
    )


@router.delete("/user-delete")
async def user_delete_invite(
    invite_id: int = Query(..., description="邀请码 ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除自己创建的邀请码"""
    invite = db.query(InviteCode).filter(
        InviteCode.id == invite_id,
        InviteCode.created_by == current_user.id,
    ).first()
    if not invite:
        raise HTTPException(status_code=404, detail="邀请码不存在或不属于当前用户")
    db.delete(invite)
    db.commit()
    return {"message": "邀请码已删除"}


@router.get("/public")
async def list_public_invites(db: Session = Depends(get_db)):
    """公开邀请码列表（无需认证，用于注册页面展示）"""
    now = datetime.datetime.now(datetime.timezone.utc)
    codes = db.query(InviteCode).filter(
        InviteCode.is_public == True,
        InviteCode.is_active == True,
        (InviteCode.expires_at.is_(None)) | (InviteCode.expires_at >= now),
        (InviteCode.max_uses == -1) | (InviteCode.used_count < InviteCode.max_uses),
    ).order_by(InviteCode.created_at.desc()).limit(50).all()

    return {
        "total": len(codes),
        "codes": [{
            "id": c.id,
            "code": c.code,
            "max_uses": c.max_uses,
            "used_count": c.used_count,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        } for c in codes],
    }


@router.get("/export", dependencies=ADMIN_DEPENDS)
async def export_invites_csv(db: Session = Depends(get_db)):
    """导出邀请码 CSV"""
    codes = db.query(InviteCode).order_by(InviteCode.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["邀请码", "最大使用次数", "已使用", "状态", "公开", "过期时间", "创建时间"])
    for c in codes:
        if c.is_expired:
            status = "已过期"
        elif not c.is_active:
            status = "已禁用"
        elif c.is_exhausted:
            status = "已用完"
        else:
            status = "有效"
        writer.writerow([
            c.code, c.max_uses, c.used_count,
            status,
            "是" if c.is_public else "否",
            c.expires_at.strftime("%Y-%m-%d %H:%M") if c.expires_at else "永久",
            c.created_at.strftime("%Y-%m-%d %H:%M"),
        ])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invite_codes.csv"},
    )
