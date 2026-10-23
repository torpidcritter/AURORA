"""Deterministic synthetic sales generator. DEMO/TEST DATA ONLY: never used by production code paths."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def generate_sales(
    n_products: int = 5,
    days: int = 180,
    end: date = date(2025, 6, 30),
    seed: int = 7,
    intermittent: bool = True,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = end - timedelta(days=days - 1)
    dates = pd.date_range(start, end, freq="D")
    rows = []
    categories = ["Beverages", "Snacks", "Household", "Personal Care"]
    for i in range(n_products):
        sku = f"SKU-{i + 1:03d}"
        base = rng.uniform(8, 60)
        trend = rng.uniform(-0.02, 0.05)
        weekly = rng.uniform(0.1, 0.4)
        t = np.arange(days)
        dow = dates.dayofweek.values
        season = 1 + weekly * np.where(dow >= 5, 1.0, -0.4)
        mean = np.maximum(base * season + trend * t, 0.5)
        qty = rng.poisson(mean).astype(float)
        if intermittent and i == n_products - 1:
            # last product: intermittent, low-volume demand
            qty = rng.poisson(0.6, days).astype(float) * rng.integers(0, 2, days)
        for d, q in zip(dates, qty, strict=True):
            rows.append(
                {
                    "date": d.date().isoformat(),
                    "sku": sku,
                    "units_sold": int(q),
                    "product_name": f"Demo product {i + 1}",
                    "category": categories[i % len(categories)],
                }
            )
    return pd.DataFrame(rows)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()
