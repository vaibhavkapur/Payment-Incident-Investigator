"""Resolves an operator-supplied identifier (order id, provider payment reference or a
free-text complaint) to an order inside the caller's organization."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import Connection

from ..db import fetch_one

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
PI_RE = re.compile(r"\bpi_[A-Za-z0-9_]+\b")
ORD_RE = re.compile(r"\bORD-[A-Z0-9]{4,}\b")


def resolve(conn: Connection, org_id: str, source_type: str, source_value: str, complaint_text: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    """Returns (order, candidates_tried). Only rows in org_id are ever considered."""
    tried: list[str] = []
    if source_type == "order_id":
        tried.append(f"order_id={source_value}")
        return _order_by_id_or_ref(conn, org_id, source_value), tried
    if source_type == "payment_ref":
        tried.append(f"payment_ref={source_value}")
        return _order_by_payment_ref(conn, org_id, source_value), tried
    text = f"{source_value}\n{complaint_text or ''}"
    for ref in PI_RE.findall(text):
        tried.append(f"payment_ref={ref}")
        order = _order_by_payment_ref(conn, org_id, ref)
        if order:
            return order, tried
    for ident in UUID_RE.findall(text) + ORD_RE.findall(text):
        tried.append(f"order={ident}")
        order = _order_by_id_or_ref(conn, org_id, ident)
        if order:
            return order, tried
    return None, tried


def _order_by_id_or_ref(conn: Connection, org_id: str, ident: str) -> dict[str, Any] | None:
    if UUID_RE.fullmatch(ident):
        row = fetch_one(conn, "select * from orders where org_id = :org and id = :id", org=org_id, id=ident)
        if row:
            return row
    return fetch_one(conn, "select * from orders where org_id = :org and external_ref = :ref", org=org_id, ref=ident)


def _order_by_payment_ref(conn: Connection, org_id: str, ref: str) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        "select o.* from orders o join payment_attempts pa on pa.order_id = o.id where pa.org_id = :org and pa.provider_payment_ref = :ref",
        org=org_id, ref=ref,
    )
