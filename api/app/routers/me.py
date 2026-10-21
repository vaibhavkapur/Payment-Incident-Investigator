from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException

from ..auth import _user_id, list_user_memberships
from ..config import settings
from ..db import fetch_all, tx

router = APIRouter(tags=["me"])


@router.get("/me")
def me(authorization: str | None = Header(default=None), x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user_id = _user_id(authorization, x_user_id)
    return {"user_id": user_id, "memberships": list_user_memberships(user_id), "auth_mode": settings().AUTH_MODE, "agent_mode": settings().effective_agent_mode}


@router.get("/dev/users")
def dev_users() -> list[dict[str, Any]]:
    """Seeded operators for the dev auth mode. Disabled in supabase mode."""
    if settings().AUTH_MODE != "dev":
        raise HTTPException(404, "not available")
    with tx() as conn:
        return fetch_all(conn, "select m.user_id, m.display_name, m.role, o.name as org_name, o.id as org_id from memberships m join organizations o on o.id = m.org_id order by o.name, m.role")
