"""Scheduled monitoring: stockout risk, demand drift, model staleness and accuracy -> alerts.

Retraining policy: monitors *recommend* retraining by raising an alert. They never auto-promote a model;
promotion always goes through the validation gate in model_registry.

Thresholds are the organisation's effective settings (settings_service): its override where set, else the global
default. The validation WAPE threshold and the live-accuracy window stay global.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Product
from app.ml import drift
from app.services import accuracy_service, alert_service, inventory_service, model_registry, series, settings_service


def run_for_org(db: Session, org_id: uuid.UUID) -> dict:
    s = get_settings()
    eff = settings_service.for_org(db, org_id)
    out = {"stockout_alerts": 0, "drift_alerts": 0, "model_alerts": 0, "resolved": 0}
    skus = dict(db.execute(select(Product.id, Product.sku).where(Product.org_id == org_id)).tuples().all())

    active_stockout: set[str] = set()
    for r in inventory_service.risk_matrix(db, org_id, org_settings=eff):
        if r.get("risk_level") in ("critical", "high"):
            key = f"stockout:{r['product_id']}"
            active_stockout.add(key)
            dts = r.get("days_until_stockout")
            alert_service.upsert(
                db,
                org_id=org_id,
                kind="stockout_risk",
                severity=r["risk_level"],
                dedup_key=key,
                product_id=uuid.UUID(r["product_id"]),
                title=f"{r['sku']}: {r['risk_level']} stockout risk"
                + (f" (~{dts:.1f} days of stock)" if dts is not None else ""),
                message=r.get("narrative", ""),
                details={
                    k: r.get(k)
                    for k in (
                        "urgency",
                        "days_until_stockout",
                        "stockout_probability_lead_time",
                        "recommended_order_qty",
                        "reorder_point",
                        "on_hand",
                    )
                },
            )
            out["stockout_alerts"] += 1
    out["resolved"] += alert_service.resolve_where_cleared(db, org_id, "stockout:", active_stockout)

    active_drift: set[str] = set()
    for pid, y in series.load_many(db, org_id).items():
        d = drift.detect(y.values, ks_threshold=eff.drift_ks_threshold, mean_threshold=eff.drift_mean_change_threshold)
        if d["drift"]:
            key = f"drift:{pid}"
            active_drift.add(key)
            alert_service.upsert(
                db,
                org_id=org_id,
                kind="demand_drift",
                severity="medium",
                dedup_key=key,
                product_id=pid,
                title=f"{skus.get(pid, pid)}: demand distribution shifted",
                message="; ".join(d["reasons"]) + ". Consider retraining and reviewing inventory parameters.",
                details=d,
            )
            out["drift_alerts"] += 1
    out["resolved"] += alert_service.resolve_where_cleared(db, org_id, "drift:", active_drift)

    active = model_registry.get_active(db, org_id)
    model_keys: set[str] = set()
    if active is not None:
        # Age of the checkpoint (when it was trained and registered), not of the promotion: rolling back to an old
        # version must not reset its age.
        age = (datetime.now(UTC) - active.created_at).days
        if age > eff.model_max_age_days:
            model_keys.add("model:stale")
            alert_service.upsert(
                db,
                org_id=org_id,
                kind="model_stale",
                severity="medium",
                dedup_key="model:stale",
                title=f"Active model {active.version} is {age} days old",
                message=f"Retraining is recommended after {eff.model_max_age_days} days.",
                details={"age_days": age, "version": active.version, "max_age_days": eff.model_max_age_days},
            )
            out["model_alerts"] += 1
        wape = active.metrics.get("transformer_wape")
        if wape is not None and wape > s.accuracy_wape_threshold:
            model_keys.add("model:accuracy")
            alert_service.upsert(
                db,
                org_id=org_id,
                kind="model_accuracy",
                severity="medium",
                dedup_key="model:accuracy",
                title=f"Model {active.version} WAPE {wape:.1f}% exceeds {s.accuracy_wape_threshold}%",
                message="Accuracy on held-out data is below target; review data quality and retrain.",
                details={"wape": wape},
            )
            out["model_alerts"] += 1
    live = accuracy_service.live_accuracy(db, org_id, s.accuracy_live_window_days)
    out["live_accuracy"] = {k: live[k] for k in ("points", "wape", "mape", "sufficient")}
    value = live.get(eff.accuracy_live_metric)
    if live["sufficient"] and value is not None and value > eff.accuracy_live_threshold:
        model_keys.add("model:live_accuracy")
        name = eff.accuracy_live_metric.upper()
        worst = [r["sku"] for r in live["by_product"][:3] if r.get("sku")]
        alert_service.upsert(
            db,
            org_id=org_id,
            kind="model_accuracy",
            severity="medium",
            dedup_key="model:live_accuracy",
            title=f"Live forecast {name} {value:.1f}% exceeds {eff.accuracy_live_threshold:g}%",
            message=f"Over the last {live['window_days']} days ({live['points']} product-days), served forecasts "
            f"missed actual demand by more than the target. Least accurate: {', '.join(worst) or 'n/a'}. Review recent "
            "data and consider retraining; a new model is promoted only if it passes the validation gate.",
            details={k: live[k] for k in ("window_start", "window_end", "points", "wape", "mape", "by_method")}
            | {"metric": eff.accuracy_live_metric, "threshold": eff.accuracy_live_threshold, "worst_products": worst},
        )
        out["model_alerts"] += 1
    out["resolved"] += alert_service.resolve_where_cleared(db, org_id, "model:", model_keys)
    db.commit()
    return out
