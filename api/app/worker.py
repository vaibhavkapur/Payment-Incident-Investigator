"""Standalone recovery worker: `python -m app.worker`. Safe to run alongside the embedded one."""
from __future__ import annotations

import logging
import socket

from .services import recovery

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    recovery.run_forever(worker_id=f"worker:{socket.gethostname()}")
