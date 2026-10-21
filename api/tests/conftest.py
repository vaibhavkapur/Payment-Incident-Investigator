"""Integration tests. They need Postgres (docker compose up -d, migrations applied) and the
merchant app running on MERCHANT_URL in simulated provider mode. The API is exercised
in-process with the embedded worker disabled so tests control recovery execution."""
from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable

import httpx
import pytest

os.environ.setdefault("WORKER_EMBEDDED", "false")
os.environ.setdefault("AGENT_MODE", "deterministic")
os.environ.setdefault("AUTH_MODE", "dev")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import fetch_one, tx  # noqa: E402
from app.main import app  # noqa: E402

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
ANALYST_A = "aaaaaaaa-0000-0000-0000-000000000001"
APPROVER_A = "aaaaaaaa-0000-0000-0000-000000000002"
ADMIN_B = "bbbbbbbb-0000-0000-0000-000000000001"


@pytest.fixture(scope="session")
def merchant() -> httpx.Client:
    client = httpx.Client(base_url=settings().MERCHANT_URL, timeout=20)
    try:
        health = client.get("/health").json()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"merchant app is not running at {settings().MERCHANT_URL}: {exc}")
    if health.get("provider_mode") != "simulated":
        pytest.fail("tests require the merchant in PROVIDER_MODE=simulated")
    return client


@pytest.fixture(scope="session")
def api() -> TestClient:
    with TestClient(app) as client:
        yield client


def headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0, interval: float = 0.2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met in time")


class Scenario:
    """Builds merchant state through the public checkout and labeled fault injection."""

    def __init__(self, merchant: httpx.Client, org_id: str = ORG_A):
        self.m = merchant
        self.org = org_id

    def checkout(self, amount: int = 4999, currency: str = "usd") -> str:
        r = self.m.post("/checkout", json={"org_id": self.org, "amount_minor": amount, "currency": currency, "external_ref": f"T-{uuid.uuid4().hex[:8].upper()}"})
        r.raise_for_status()
        return r.json()["order_id"]

    def fault(self, order_id: str, kind: str, params: dict | None = None, remaining: int = 1) -> None:
        self.m.post("/faults", json={"org_id": self.org, "order_id": order_id, "kind": kind, "params": params or {}, "remaining": remaining}).raise_for_status()

    def confirm(self, order_id: str, outcome: str = "succeeded") -> httpx.Response:
        return self.m.post(f"/checkout/{order_id}/confirm", json={"org_id": self.org, "outcome": outcome})

    def retry(self, order_id: str) -> None:
        self.m.post(f"/checkout/{order_id}/retry", json={"org_id": self.org}).raise_for_status()

    def order(self, order_id: str) -> dict:
        with tx() as conn:
            return fetch_one(conn, "select * from orders where id = :id", id=order_id)

    def wait_order_status(self, order_id: str, status: str, timeout: float = 10.0) -> None:
        wait_for(lambda: self.order(order_id)["status"] == status, timeout)

    def wait_processing(self, order_id: str, outcome: str, timeout: float = 10.0) -> None:
        def done() -> bool:
            with tx() as conn:
                return fetch_one(conn, "select 1 from processing_attempts where order_id = :o and outcome = :s", o=order_id, s=outcome) is not None
        wait_for(done, timeout)

    def payment_ref(self, order_id: str) -> str:
        with tx() as conn:
            return fetch_one(conn, "select provider_payment_ref from payment_attempts where order_id = :o order by created_at desc limit 1", o=order_id)["provider_payment_ref"]


@pytest.fixture
def scenario(merchant: httpx.Client) -> Scenario:
    return Scenario(merchant)


def create_incident(api: TestClient, org: str, user: str, **body) -> dict:
    body.setdefault("source_type", "order_id")
    r = api.post(f"/orgs/{org}/incidents", json=body, headers=headers(user))
    assert r.status_code == 201, r.text
    return api.get(f"/orgs/{org}/incidents/{r.json()['id']}", headers=headers(user)).json()
