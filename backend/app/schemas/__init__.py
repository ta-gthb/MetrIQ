"""Pydantic request/response schemas.

Modules are imported explicitly by consumers (``from app.schemas.cases import
CaseCreateRequest``) so this package stays a light namespace and cannot create
import cycles with the router layer.
"""

from app.schemas.common import ErrorOut, MessageOut, PageMeta, Paginated

__all__ = ["ErrorOut", "MessageOut", "PageMeta", "Paginated"]
