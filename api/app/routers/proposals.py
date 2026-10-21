from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import Principal, current_principal
from ..db import tx
from ..services import approvals

router = APIRouter(prefix="/orgs/{org_id}/proposals", tags=["proposals"])


class Decision(BaseModel):
    reason: str = Field(default="", max_length=1000)


@router.post("/{proposal_id}/approve")
def approve(proposal_id: str, body: Decision, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    with tx() as conn:
        return approvals.approve(conn, p, proposal_id)


@router.post("/{proposal_id}/reject")
def reject(proposal_id: str, body: Decision, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    with tx() as conn:
        return approvals.reject(conn, p, proposal_id, body.reason)
