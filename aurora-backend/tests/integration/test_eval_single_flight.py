"""Concurrent cache misses for the same series compute the baseline evaluation once (load-test finding)."""

from __future__ import annotations

import json
import threading
import time
import uuid

import numpy as np
import pandas as pd

from app.core.redis_client import get_redis
from app.db.session import get_sessionmaker
from app.services import forecast_service


def test_concurrent_misses_compute_once(monkeypatch, org):
    y = pd.Series(np.arange(120, dtype=float), index=pd.date_range("2025-01-01", periods=120, freq="D"))
    pid = uuid.uuid4()
    calls = []

    def slow_compute(db, org_id, product_id, series, horizon, fast, fp):
        calls.append(1)
        time.sleep(0.4)
        payload = {"fingerprint": fp, "computed": True}
        get_redis().set(f"aurora:eval:{org_id}:{product_id}:{horizon}:{fp}", json.dumps(payload))
        return payload

    monkeypatch.setattr(forecast_service, "_compute_evaluation", slow_compute)
    results = []

    def worker():
        with get_sessionmaker()() as db:
            results.append(forecast_service.baseline_evaluation(db, org.org_id, pid, y, 7))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 5 and all(r["computed"] for r in results)
    assert len(calls) == 1
