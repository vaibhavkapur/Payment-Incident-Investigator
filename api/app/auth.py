"""Operator identity and organization membership.

AUTH_MODE=supabase verifies Supabase access tokens (HS256 with the project's JWT secret) and
uses the `sub` claim as the user id. AUTH_MODE=dev trusts an X-User-Id header for the seeded
users so the system can run without a Supabase project. Both modes then require an explicit
membership row for the requested organization; the org never comes from the token alone.
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, Path

from .config import settings
from .db import fetch_one, tx


@dataclass(frozen=True)
class Principal:
    user_id: str
    org_id: str
    role: str
    display_name: str

    def can_approve(self) -> bool:
        return self.role in ("approver", "admin")


def _user_id(authorization: str | None, x_user_id: str | None) -> str:
    mode = settings().AUTH_MODE
    if mode == "supabase":
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        token = authorization.split(" ", 1)[1]
        try:
            claims = jwt.decode(token, settings().SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
        except jwt.PyJWTError as exc:
            raise HTTPException(401, f"invalid token: {exc}") from exc
        return str(claims["sub"])
    if not x_user_id:
        raise HTTPException(401, "X-User-Id header required in dev auth mode")
    return x_user_id


def current_principal(
    org_id: str = Path(...),
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> Principal:
    user_id = _user_id(authorization, x_user_id)
    with tx() as conn:
        row = fetch_one(conn, "select role, display_name from memberships where org_id = :org and user_id = :user", org=org_id, user=user_id)
    if not row:
        raise HTTPException(403, "not a member of this organization")
    return Principal(user_id=user_id, org_id=org_id, role=row["role"], display_name=row["display_name"])


def approver(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.can_approve():
        raise HTTPException(403, "approver or admin role required")
    return principal


def list_user_memberships(user_id: str) -> list[dict]:
    with tx() as conn:
        from .db import fetch_all

        return fetch_all(
            conn,
            "select m.org_id, o.name as org_name, o.slug, m.role, m.display_name from memberships m join organizations o on o.id = m.org_id where m.user_id = :user order by o.name",
            user=user_id,
        )
