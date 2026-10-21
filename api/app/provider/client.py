"""Provider lookups. The model never sees credentials: the sandbox client reads the key
from the server environment named by the connection's secret_ref. Results are labeled
with their environment so sandbox or simulated evidence is never presented as settlement."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import Connection

from ..db import fetch_all, fetch_one


class ProviderUnavailable(Exception):
    pass


@dataclass
class ProviderPayment:
    provider_payment_ref: str
    status: str
    amount_minor: int
    amount_received_minor: int
    currency: str
    created_at: datetime | None
    environment: str
    metadata: dict[str, Any]

    def as_fields(self) -> dict[str, Any]:
        return {
            "provider_payment_ref": self.provider_payment_ref,
            "status": self.status,
            "amount_minor": self.amount_minor,
            "amount_received_minor": self.amount_received_minor,
            "currency": self.currency,
            "provider_created_at": self.created_at.isoformat() if self.created_at else None,
            "environment": self.environment,
            "order_id_metadata": self.metadata.get("order_id"),
            "settlement": "not established by this record",
        }


class ProviderClient(Protocol):
    environment: str

    def get_payment(self, conn: Connection, org_id: str, ref: str) -> ProviderPayment: ...
    def count_payments_for_order(self, conn: Connection, org_id: str, order_id: str) -> int | None: ...


class SimulatedProviderClient:
    environment = "simulated"

    def get_payment(self, conn: Connection, org_id: str, ref: str) -> ProviderPayment:
        row = fetch_one(conn, "select * from simulated_provider_payments where org_id = :org and provider_payment_ref = :ref", org=org_id, ref=ref)
        if not row:
            raise ProviderUnavailable(f"provider returned 404 for {ref}")
        if row["lookup_unavailable"]:
            raise ProviderUnavailable("provider API unavailable (503) for this lookup")
        return ProviderPayment(
            provider_payment_ref=row["provider_payment_ref"], status=row["status"], amount_minor=int(row["amount_minor"]),
            amount_received_minor=int(row["amount_minor"]) if row["status"] == "succeeded" else 0, currency=row["currency"],
            created_at=row["created_at"], environment=self.environment, metadata=row["metadata"] or {},
        )

    def count_payments_for_order(self, conn: Connection, org_id: str, order_id: str) -> int | None:
        rows = fetch_all(conn, "select 1 from simulated_provider_payments where org_id = :org and metadata->>'order_id' = :oid", org=org_id, oid=order_id)
        return len(rows)


class StripeSandboxClient:
    environment = "sandbox"

    def __init__(self, secret_ref: str):
        key = os.environ.get(secret_ref, "")
        if not key.startswith("sk_test_"):
            raise ProviderUnavailable(f"sandbox credential {secret_ref} is not configured (test-mode keys only)")
        import stripe

        self._client = stripe.StripeClient(key)

    def get_payment(self, conn: Connection, org_id: str, ref: str) -> ProviderPayment:
        import stripe

        try:
            pi = self._client.payment_intents.retrieve(ref)
        except stripe.StripeError as exc:
            raise ProviderUnavailable(f"provider lookup failed: {exc.user_message or exc}") from exc
        if pi.livemode:
            raise ProviderUnavailable("refusing to use a live-mode provider record")
        metadata = dict(pi.metadata or {})
        if metadata.get("org_id") not in (None, org_id):
            raise ProviderUnavailable("provider record is bound to a different organization")
        return ProviderPayment(
            provider_payment_ref=pi.id, status=pi.status, amount_minor=pi.amount, amount_received_minor=pi.amount_received,
            currency=pi.currency, created_at=datetime.fromtimestamp(pi.created, tz=timezone.utc), environment=self.environment, metadata=metadata,
        )

    def count_payments_for_order(self, conn: Connection, org_id: str, order_id: str) -> int | None:
        try:
            result = self._client.payment_intents.search({"query": f"metadata['order_id']:'{order_id}'"})
            return len(result.data)
        except Exception:
            return None


def client_for(conn: Connection, org_id: str, provider_connection_id: str | None = None) -> tuple[ProviderClient, dict[str, Any]]:
    if provider_connection_id:
        connection = fetch_one(conn, "select * from provider_connections where org_id = :org and id = :id", org=org_id, id=provider_connection_id)
    else:
        connection = fetch_one(conn, "select * from provider_connections where org_id = :org and provider = 'stripe' order by created_at limit 1", org=org_id)
    if not connection:
        raise ProviderUnavailable("organization has no provider connection")
    if connection["environment"] == "sandbox":
        return StripeSandboxClient(connection["secret_ref"]), connection
    return SimulatedProviderClient(), connection
