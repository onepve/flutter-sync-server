"""数据同步路由（上传/下载/差异对比）"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.encrypted_data import EncryptedData
from models.audit_log import AuditLog
from schemas.sync_schema import (
    SyncUploadRequest, SyncUploadResponse,
    SyncDownloadResponse,
    SyncDiffRequest, SyncDiffResponse, SyncDiffItem,
    SyncStatusResponse, SyncStatusItem,
)
from services.security import get_current_user
from services.crypto import CryptoService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sync", tags=["数据同步"])


def _log_audit(db: Session, user_id: int | None, action: str, detail: str | None,
               ip: str | None, path: str | None, success: bool,
               username: str | None = None):
    db.add(AuditLog(
        user_id=user_id, action=action, detail=detail,
        ip_address=ip, path=path, success=success,
        username=username,
    ))
    db.commit()


@router.post("/upload", response_model=SyncUploadResponse)
async def upload_data(
    req: SyncUploadRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传加密数据"""
    # 查找该用户+类型的现有记录
    existing = db.query(EncryptedData).filter(
        EncryptedData.user_id == current_user.id,
        EncryptedData.data_type == req.data_type,
    ).first()

    if existing:
        # 冲突检测：如果服务端版本更高，拒绝覆盖
        if existing.version > req.client_version:
            raise HTTPException(
                status_code=409,
                detail=f"服务端版本({existing.version})高于客户端({req.client_version})，请先下载",
            )
        # 更新
        existing.ciphertext = req.ciphertext.encode("utf-8")
        existing.device_id = req.device_id
        existing.plaintext_size = req.plaintext_size
        existing.version += 1
        db.commit()
        db.refresh(existing)

        _log_audit(db, current_user.id, "sync_upload",
                   f"更新 {req.data_type} v{existing.version}",
                   request.client.host, request.url.path, True, username=current_user.username)

        return SyncUploadResponse(version=existing.version)
    else:
        # 新增
        record = EncryptedData(
            user_id=current_user.id,
            data_type=req.data_type,
            device_id=req.device_id,
            ciphertext=req.ciphertext.encode("utf-8"),
            version=1,
            plaintext_size=req.plaintext_size,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        _log_audit(db, current_user.id, "sync_upload",
                   f"新建 {req.data_type} v1",
                   request.client.host, request.url.path, True, username=current_user.username)

        return SyncUploadResponse(version=1)


@router.get("/download/{data_type}", response_model=SyncDownloadResponse)
async def download_data(
    data_type: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载指定类型的加密数据"""
    record = db.query(EncryptedData).filter(
        EncryptedData.user_id == current_user.id,
        EncryptedData.data_type == data_type,
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="未找到数据")

    _log_audit(db, current_user.id, "sync_download",
               f"下载 {data_type} v{record.version}",
               request.client.host, request.url.path, True, username=current_user.username)

    return SyncDownloadResponse(
        data_type=record.data_type,
        ciphertext=record.ciphertext.decode("utf-8"),
        version=record.version,
        plaintext_size=record.plaintext_size,
        updated_at=record.updated_at,
    )


@router.post("/diff", response_model=SyncDiffResponse)
async def check_diff(
    req: SyncDiffRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对比本地版本和服务端版本，返回需要下载的类型列表"""
    items = []
    for data_type, client_version in req.local_versions.items():
        record = db.query(EncryptedData).filter(
            EncryptedData.user_id == current_user.id,
            EncryptedData.data_type == data_type,
        ).first()

        server_version = record.version if record else 0
        needs_download = server_version > client_version

        items.append(SyncDiffItem(
            data_type=data_type,
            server_version=server_version,
            client_version=client_version,
            needs_download=needs_download,
        ))

    return SyncDiffResponse(items=items)


@router.get("/status", response_model=SyncStatusResponse)
async def sync_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查看所有设备的同步状态"""
    records = db.query(EncryptedData).filter(
        EncryptedData.user_id == current_user.id,
    ).all()

    devices = [
        SyncStatusItem(
            device_id=r.device_id,
            data_type=r.data_type,
            version=r.version,
            updated_at=r.updated_at,
        )
        for r in records
    ]

    return SyncStatusResponse(devices=devices)


@router.delete("/{data_type}")
async def delete_data(
    data_type: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除指定类型的同步数据"""
    record = db.query(EncryptedData).filter(
        EncryptedData.user_id == current_user.id,
        EncryptedData.data_type == data_type,
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="未找到数据")

    db.delete(record)
    db.commit()

    _log_audit(db, current_user.id, "sync_delete",
               f"删除 {data_type}",
               request.client.host, request.url.path, True, username=current_user.username)

    return {"message": "数据已删除"}


@router.post("/export-to-email")
async def export_to_email(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """导出加密同步数据到邮箱"""
    records = db.query(EncryptedData).filter(
        EncryptedData.user_id == current_user.id,
    ).all()

    if not records:
        raise HTTPException(status_code=404, detail="没有可导出的数据")

    combined = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
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

    from services.email import EmailService
    email_service_sync = EmailService()
    if email_service_sync.enabled and current_user.email:
        sent = await email_service_sync.send_account_backup(
            current_user.email, current_user.uuid, export_data
        )
        if sent:
            return {"message": "导出数据已发送到您的邮箱"}
        return {"message": "邮件发送失败，请稍后重试"}

    # 无 SMTP 模式：直接返回数据
    return {"message": "导出数据如下", "data": export_data}
