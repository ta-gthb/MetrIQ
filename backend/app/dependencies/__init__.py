"""FastAPI dependencies: authentication, authorization and scope enforcement."""

from app.dependencies.auth import (
    get_client_ip,
    get_current_active_user,
    get_current_user,
    get_optional_user,
)
from app.dependencies.permissions import require_any_permission, require_permission

__all__ = [
    "get_client_ip",
    "get_current_active_user",
    "get_current_user",
    "get_optional_user",
    "require_any_permission",
    "require_permission",
]
