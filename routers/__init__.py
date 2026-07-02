from .auth import router as auth_router
from .invite import router as invite_router
from .sync import router as sync_router
from .admin import router as admin_router

__all__ = ["auth_router", "invite_router", "sync_router", "admin_router"]
