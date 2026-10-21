from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import fetch_one, tx
from .routers import incidents, me, proposals
from .services import recovery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    with tx() as conn:
        fetch_one(conn, "select 1 as ok")
    stop = threading.Event()
    thread = None
    if settings().WORKER_EMBEDDED:
        thread = threading.Thread(target=recovery.run_forever, args=(stop, "api-embedded"), daemon=True, name="recovery-worker")
        thread.start()
    yield
    stop.set()
    if thread:
        thread.join(timeout=5)


app = FastAPI(title="Payment Incident Investigator API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings().CORS_ORIGINS.split(",") if o.strip()], allow_methods=["*"], allow_headers=["*"])
app.include_router(me.router)
app.include_router(incidents.router)
app.include_router(proposals.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "agent_mode": settings().effective_agent_mode, "auth_mode": settings().AUTH_MODE, "worker_embedded": settings().WORKER_EMBEDDED}
