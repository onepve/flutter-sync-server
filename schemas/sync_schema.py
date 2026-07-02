"""数据同步相关请求/响应模型"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class SyncUploadRequest(BaseModel):
    """上传加密数据"""
    data_type: str = Field(..., description="数据类型: servers|keys|settings")
    device_id: str = Field(..., max_length=128)
    ciphertext: str = Field(..., description="AES-256-GCM 加密后的 base64 字符串")
    plaintext_size: int = Field(default=0, ge=0)
    client_version: int = Field(default=0, ge=0, description="客户端当前版本，用于冲突检测")


class SyncUploadResponse(BaseModel):
    version: int
    message: str = "同步成功"


class SyncDownloadResponse(BaseModel):
    data_type: str
    ciphertext: str  # base64
    version: int
    plaintext_size: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class SyncDiffRequest(BaseModel):
    """客户端上报各类型的本地版本，服务端返回有更新的类型列表"""
    local_versions: dict[str, int]  # {"servers": 3, "keys": 1, "settings": 2}


class SyncDiffItem(BaseModel):
    data_type: str
    server_version: int
    client_version: int
    needs_download: bool


class SyncDiffResponse(BaseModel):
    items: list[SyncDiffItem]


class SyncStatusItem(BaseModel):
    device_id: str
    data_type: str
    version: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class SyncStatusResponse(BaseModel):
    devices: list[SyncStatusItem]
