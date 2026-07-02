"""
Flutter Server Box 同步服务 — 主入口

启动方式：
    uv run uvicorn main:app --host 0.0.0.0 --port 8765

Docker 部署：
    docker compose up -d
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import settings
from database import init_db
from routers import auth_router, invite_router, sync_router, admin_router

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── 限流器 ──
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_GLOBAL])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库 + 迁移新增列"""
    logger.info("正在初始化数据库...")
    try:
        init_db()
        logger.info("数据库表已就绪")
    except Exception as e:
        logger.warning(f"数据库初始化失败（首次运行请先建库）: {e}")

    # ── 迁移：给 users 表添加 avatar_data / avatar_mime / avatar_updated_at / nickname 列 ──
    try:
        from sqlalchemy import text
        from database import engine

        with engine.connect() as conn:
            # 检查列是否已存在
            existing_cols = set()
            try:
                result = conn.execute(
                    text("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                         "WHERE TABLE_SCHEMA = :db AND TABLE_NAME = 'users'"),
                    {"db": settings.DB_NAME},
                )
                existing_cols = {row[0] for row in result.fetchall()}
            except Exception:
                pass  # INFORMATION_SCHEMA 查询可能因权限失败

            # 逐列添加
            col_defs = [
                ("avatar_data", "MEDIUMBLOB DEFAULT NULL"),
                ("avatar_mime", "VARCHAR(32) DEFAULT NULL"),
                ("avatar_updated_at", "DATETIME DEFAULT NULL"),
                ("nickname", "VARCHAR(64) DEFAULT NULL"),
                ("display_order", "INT DEFAULT NULL"),
            ]
            for col_name, col_type in col_defs:
                if col_name not in existing_cols:
                    conn.execute(text(
                        f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info(f"迁移: 新增列 {col_name}")

            # 迁移：给 invite_codes 表加 is_public 列
            try:
                result2 = conn.execute(
                    text("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                         "WHERE TABLE_SCHEMA = :db2 AND TABLE_NAME = 'invite_codes'"),
                    {"db2": settings.DB_NAME},
                )
                invite_cols = {row[0] for row in result2.fetchall()}
                if "is_public" not in invite_cols:
                    conn.execute(text(
                        "ALTER TABLE invite_codes ADD COLUMN is_public TINYINT(1) DEFAULT 0 NOT NULL"
                    ))
                    logger.info("迁移: 新增 invite_codes.is_public 列")
            except Exception as e:
                logger.warning(f"迁移 invite_codes 失败: {e}")

            conn.commit()

            # 清理：旧文件头像已丢失，清空其 avatar_url
            result = conn.execute(
                text("SELECT id, avatar_url FROM users WHERE avatar_url IS NOT NULL "
                     "AND avatar_url LIKE '/static/avatars/%' AND avatar_data IS NULL")
            )
            stale = result.fetchall()
            for row in stale:
                conn.execute(
                    text("UPDATE users SET avatar_url = NULL WHERE id = :uid"),
                    {"uid": row[0]},
                )
                logger.info(f"迁移: 清空用户 {row[0]} 的旧文件头像引用 ({row[1]})")
            conn.commit()
    except Exception as e:
        logger.warning(f"数据库迁移（追加头像列）跳过: {e}")

    # ── 默认管理员：首次启动自动创建 ──
    try:
        from models.user import User
        from services.security import hash_password
        from database import SessionLocal
        import uuid as _uuid_lib

        _db = SessionLocal()
        _existing = _db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        if not _existing:
            _admin = User(
                uuid=str(_uuid_lib.uuid4()),
                username=settings.ADMIN_USERNAME,
                email=f"{settings.ADMIN_USERNAME}@localhost",
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                email_verified=True,
                is_admin=True,
                is_active=True,
            )
            _db.add(_admin)
            _db.commit()
            logger.info(f"默认管理员已创建: {settings.ADMIN_USERNAME}")
        _db.close()
    except Exception as e:
        logger.warning(f"默认管理员创建跳过（可能已存在或数据库未就绪）: {e}")

    yield


app = FastAPI(
    title="Flutter Server Box 同步服务",
    description="跨平台 SSH 服务器数据同步后端",
    version="1.0.0",
    lifespan=lifespan,
)

# ── 全局中间件 ──
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境请限制为你的域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 注册路由 ──
app.include_router(auth_router)
app.include_router(invite_router)
app.include_router(sync_router)
app.include_router(admin_router)

# ── 静态文件（头像等） ──
import os
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ── 健康检查 ──

@app.get("/")
async def root():
    return FileResponse(os.path.join(_static_dir, "index.html"), media_type="text/html")


@app.get("/api")
async def api_root():
    return {"status": "ok", "service": "flutter-server-box-sync"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


# ── Web 个人资料页（带头像裁剪） ──

@app.get("/profile", response_class=FileResponse)
async def profile_page():
    return FileResponse(os.path.join(_static_dir, "profile.html"), media_type="text/html")


@app.get("/index", response_class=FileResponse)
async def index_page():
    return FileResponse(os.path.join(_static_dir, "index.html"), media_type="text/html")


@app.get("/admin", response_class=FileResponse)
async def admin_page():
    return FileResponse(os.path.join(_static_dir, "admin.html"), media_type="text/html")


@app.get("/invite", response_class=FileResponse)
async def invite_page():
    return FileResponse(os.path.join(_static_dir, "invite.html"), media_type="text/html")


@app.get("/doc", response_class=FileResponse)
async def doc_page():
    return FileResponse(os.path.join(_static_dir, "doc.html"), media_type="text/html")


@app.get("/public-invites", response_class=FileResponse)
async def public_invites_page():
    return FileResponse(os.path.join(_static_dir, "public-invites.html"), media_type="text/html")


@app.get("/my-invites", response_class=FileResponse)
async def user_invites_page():
    return FileResponse(os.path.join(_static_dir, "user-invites.html"), media_type="text/html")
