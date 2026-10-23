"""Model registry: immutable versions, a single active pointer per organisation, gated promotion, rollback."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import ModelVersion
from app.ml import training
from app.ml.transformer_model import TimeSeriesTransformer
from app.storage import get_storage

GATE_TOLERANCE = 0.05  # transformer may be at most 5% worse (relative WAPE) than the best baseline overall

_cache: OrderedDict[str, tuple[dict, TimeSeriesTransformer]] = OrderedDict()
_lock = threading.Lock()


def next_version(db: Session, org_id: uuid.UUID) -> str:
    versions = db.scalars(select(ModelVersion.version).where(ModelVersion.org_id == org_id)).all()
    best = (1, -1, 0)
    for v in versions:
        try:
            major, minor, patch = (int(x) for x in v.lstrip("v").split("."))
            best = max(best, (major, minor, patch))
        except ValueError:
            continue
    return f"v{best[0]}.{best[1] + 1}.0"


def evaluate_gate(metrics: dict) -> tuple[bool, str]:
    t = metrics.get("transformer_wape")
    b = metrics.get("best_baseline_wape")
    naive = (metrics.get("overall", {}).get("seasonal_naive") or {}).get("wape")
    if t is None:
        return False, "Transformer WAPE undefined on the evaluation segment"
    if naive is not None and t >= naive:
        return False, f"Transformer WAPE {t:.2f} does not beat seasonal naive ({naive:.2f})"
    if b is not None and t > b * (1 + GATE_TOLERANCE):
        return (
            False,
            f"Transformer WAPE {t:.2f} is more than {GATE_TOLERANCE:.0%} worse than "
            f"{metrics.get('best_baseline')} ({b:.2f})",
        )
    return True, f"Transformer WAPE {t:.2f} vs best baseline {metrics.get('best_baseline')} {b}"


def register(
    db: Session, *, org_id: uuid.UUID, bundle: dict, job_id: uuid.UUID | None, created_by: uuid.UUID | None
) -> ModelVersion:
    data, digest = training.serialize(bundle)
    version = next_version(db, org_id)
    key = f"models/{org_id}/{version}-{digest[:12]}.pt"
    get_storage().put(key, data)
    passed, reason = evaluate_gate(bundle["metrics"])
    mv = ModelVersion(
        org_id=org_id,
        version=version,
        kind="transformer",
        status="candidate",
        artifact_key=key,
        checksum=digest,
        config=bundle["config"],
        metrics={**bundle["metrics"], "gate_reason": reason, "importance": bundle["importance"]},
        training_meta={**bundle["training"], "products": sorted(bundle["products"])},
        job_id=job_id,
        created_by=created_by,
        gate_passed=passed,
    )
    db.add(mv)
    db.flush()
    return mv


def get_version(db: Session, org_id: uuid.UUID, version_id: uuid.UUID) -> ModelVersion:
    mv = db.get(ModelVersion, version_id)
    if mv is None or mv.org_id != org_id:
        raise NotFound("Model version not found")
    return mv


def get_active(db: Session, org_id: uuid.UUID) -> ModelVersion | None:
    return db.scalar(select(ModelVersion).where(ModelVersion.org_id == org_id, ModelVersion.status == "active"))


def promote(db: Session, org_id: uuid.UUID, version_id: uuid.UUID, *, force: bool = False) -> ModelVersion:
    # Lock all of the org's versions so two concurrent promotions serialise.
    rows = db.scalars(select(ModelVersion).where(ModelVersion.org_id == org_id).with_for_update()).all()
    mv = next((r for r in rows if r.id == version_id), None)
    if mv is None:
        raise NotFound("Model version not found")
    if mv.status == "active":
        return mv
    if mv.status == "rejected" and not force:
        raise Conflict("This version was rejected; use force to promote it anyway")
    if not mv.gate_passed and not force:
        raise ValidationFailed(
            "Version did not pass the validation gate", details={"reason": mv.metrics.get("gate_reason")}
        )
    if not get_storage().exists(mv.artifact_key):
        raise Conflict("Model artifact is missing from storage")
    db.execute(
        update(ModelVersion)
        .where(ModelVersion.org_id == org_id, ModelVersion.status == "active")
        .values(status="archived")
    )
    db.flush()
    mv.status = "active"
    mv.promoted_at = datetime.now(UTC)
    db.flush()
    return mv


def rollback(db: Session, org_id: uuid.UUID) -> ModelVersion:
    """Re-activate the most recently promoted version other than the current active one."""
    current = get_active(db, org_id)
    q = select(ModelVersion).where(
        ModelVersion.org_id == org_id, ModelVersion.status == "archived", ModelVersion.promoted_at.isnot(None)
    )
    if current is not None:
        q = q.where(ModelVersion.id != current.id)
    prev = db.scalar(q.order_by(ModelVersion.promoted_at.desc()).limit(1))
    if prev is None:
        raise Conflict("No previously active version to roll back to")
    return promote(db, org_id, prev.id, force=True)


def load(mv: ModelVersion) -> tuple[dict, TimeSeriesTransformer]:
    """Load (and cache by checksum) a model artifact. Artifacts are immutable, so the cache is safe."""
    with _lock:
        hit = _cache.get(mv.checksum)
        if hit is not None:
            _cache.move_to_end(mv.checksum)
            return hit
    bundle, model = training.deserialize(get_storage().get(mv.artifact_key), mv.checksum)
    with _lock:
        _cache[mv.checksum] = (bundle, model)
        while len(_cache) > 4:
            _cache.popitem(last=False)
    return bundle, model
