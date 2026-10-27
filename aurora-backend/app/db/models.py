"""SQLAlchemy 2.x ORM models. PostgreSQL is the authoritative store.

Every tenant-owned table carries ``org_id`` and every query in the service layer filters on it,
which is how object-level authorization across organisations is enforced.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("viewer", "operator", "analyst", "admin")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="ck_users_role"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped on password change / deactivation / role change so existing access tokens stop working.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Two-factor login (ADR-040). Secrets are encrypted with TOKEN_ENCRYPTION_KEYS; recovery codes are stored hashed.
    totp_secret: Mapped[str | None] = mapped_column(Text)
    totp_pending: Mapped[str | None] = mapped_column(Text)  # set up but not yet confirmed with a code
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)  # last accepted step: each code works once
    recovery_codes: Mapped[list | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def two_factor_enabled(self) -> bool:
        return self.totp_enabled_at is not None


class RefreshToken(TimestampMixin, Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(50))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))


class PasswordResetToken(TimestampMixin, Base):
    """Admin-issued one-time reset link. Only the SHA-256 of the token is stored; the link itself is shown once."""

    __tablename__ = "password_reset_tokens"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Dataset(TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("org_id", "sha256", name="uq_datasets_org_sha"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # imported | rejected
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    missing_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="zero")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_report: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    column_mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("org_id", "sku", name="uq_products_org_sku"),
        CheckConstraint("on_hand >= 0", name="ck_products_on_hand_nonneg"),
        CheckConstraint("on_order >= 0", name="ck_products_on_order_nonneg"),
        CheckConstraint(
            "lead_time_days IS NULL OR (lead_time_days >= 1 AND lead_time_days <= 365)", name="ck_products_lead"
        ),
        CheckConstraint("service_level IS NULL OR (service_level > 0.5 AND service_level < 1)", name="ck_products_sl"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    category: Mapped[str | None] = mapped_column(String(120))
    unit_cost: Mapped[float | None] = mapped_column(Float)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    service_level: Mapped[float | None] = mapped_column(Float)
    on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inventory_known: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pack_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    min_order_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"), index=True)
    # Quantity price breaks from the supplier: [{"min_qty": int, "unit_cost": float}], cheapest last (ADR-038).
    price_breaks: Mapped[list | None] = mapped_column(JSONB)
    # How dates absent from uploads are treated when building the daily series:
    # "zero" = absence means no sales (typical POS exports); "interpolate" = absence means unknown.
    missing_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="zero")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SalesRecord(Base):
    __tablename__ = "sales_records"
    __table_args__ = (
        UniqueConstraint("product_id", "sale_date", "source", name="uq_sales_product_date_source"),
        CheckConstraint("quantity >= 0", name="ck_sales_qty_nonneg"),
        Index("ix_sales_org_date", "org_id", "sale_date"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    sale_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="upload")
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"))


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("org_id", "idempotency_key", name="uq_jobs_org_idem"),
        CheckConstraint(f"status IN {JOB_STATUSES}", name="ck_jobs_status"),
        Index("ix_jobs_org_created", "org_id", "created_at"),
        # at most one queued/running training job per organisation, enforced by the database
        Index(
            "uq_jobs_one_active_training",
            "org_id",
            unique=True,
            postgresql_where=text("kind = 'train' AND status IN ('queued', 'running')"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    celery_task_id: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelVersion(TimestampMixin, Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("org_id", "version", name="uq_model_versions_org_version"),
        Index("uq_model_versions_one_active", "org_id", unique=True, postgresql_where=text("status = 'active'")),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="transformer")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")
    artifact_key: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    training_meta: Mapped[dict] = mapped_column(JSONB, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gate_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ForecastRun(TimestampMixin, Base):
    __tablename__ = "forecast_runs"
    __table_args__ = (Index("ix_forecast_runs_product_created", "product_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id", ondelete="SET NULL"))
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))


class BacktestResult(TimestampMixin, Base):
    __tablename__ = "backtest_results"
    __table_args__ = (Index("ix_backtests_product_created", "product_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False)
    folds: Mapped[int] = mapped_column(Integer, nullable=False)
    best_method: Mapped[str | None] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "uq_alerts_open_dedup", "org_id", "dedup_key", unique=True, postgresql_where=text("status <> 'resolved'")
        ),
        Index("ix_alerts_org_status_created", "org_id", "status", "created_at"),
        CheckConstraint("severity IN ('low','medium','high','critical')", name="ck_alerts_severity"),
        CheckConstraint("status IN ('open','acknowledged','resolved')", name="ck_alerts_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AlertDelivery(TimestampMixin, Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (UniqueConstraint("alert_id", "channel", name="uq_alert_delivery_channel"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_org_created", "org_id", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ExternalSignal(TimestampMixin, Base):
    __tablename__ = "external_signals"
    __table_args__ = (
        CheckConstraint("magnitude >= -1 AND magnitude <= 1", name="ck_signal_magnitude"),
        CheckConstraint("end_date >= start_date", name="ck_signal_dates"),
        Index("ix_signals_org_dates", "org_id", "start_date", "end_date"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(30), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    magnitude: Mapped[float] = mapped_column(Float, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ShopifyShop(TimestampMixin, Base):
    __tablename__ = "shopify_shops"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    shop_domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    uninstalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Outcome of the last webhook registration per topic, e.g. {"orders/create": "registered"}.
    webhooks: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    webhooks_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShopifyOrder(TimestampMixin, Base):
    """Ledger of what each Shopify order added to daily sales, so cancellations and refunds can be reversed exactly
    once and never remove more than the order added."""

    __tablename__ = "shopify_orders"
    __table_args__ = (UniqueConstraint("shop_domain", "order_id", name="uq_shopify_orders_shop_order"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    order_id: Mapped[str] = mapped_column(String(40), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    lines: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {sku: quantity added}
    removed: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    refund_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthState(TimestampMixin, Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_webhook_provider_external"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    shop_domain: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


PO_STATUSES = ("draft", "approved", "sent", "partially_received", "received", "cancelled")
OVERRIDE_KINDS = ("set", "scale")


class Supplier(TimestampMixin, Base):
    """Who a product is bought from. Its lead time applies to products that have none of their own."""

    __tablename__ = "suppliers"
    __table_args__ = (
        Index("uq_suppliers_org_name", "org_id", func.lower(text("name")), unique=True),
        CheckConstraint(
            "lead_time_days IS NULL OR (lead_time_days >= 1 AND lead_time_days <= 365)", name="ck_suppliers_lead"
        ),
        CheckConstraint("min_order_value IS NULL OR min_order_value >= 0", name="ck_suppliers_min_value"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    min_order_value: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PurchaseOrder(TimestampMixin, Base):
    """A purchase order. Lines change only in draft; stock moves when it is sent (on order) and received (on hand)."""

    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("org_id", "number", name="uq_purchase_orders_org_number"),
        CheckConstraint(f"status IN {PO_STATUSES}", name="ck_purchase_orders_status"),
        Index("ix_purchase_orders_org_status", "org_id", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    supplier_name: Mapped[str | None] = mapped_column(String(200))  # kept as it was when the order was sent
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    expected_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")  # manual | suggested
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "product_id", name="uq_po_lines_order_product"),
        CheckConstraint("quantity > 0", name="ck_po_lines_qty_pos"),
        CheckConstraint("received_qty >= 0 AND received_qty <= quantity", name="ck_po_lines_received"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="ck_po_lines_cost"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[float | None] = mapped_column(Float)
    received_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # What the reorder suggestion said when the line was drafted from it, for later review.
    suggested_qty: Mapped[int | None] = mapped_column(Integer)
    suggestion: Mapped[dict | None] = mapped_column(JSONB)


class ForecastOverride(TimestampMixin, Base):
    """A planner's adjustment to the statistical forecast for a date range: set units/day, or scale it.

    Stored forecasts stay as the model made them; overrides are applied when forecasts are read for decisions, so the
    model's and the planner's accuracy can both be scored against actual sales afterwards.
    """

    __tablename__ = "forecast_overrides"
    __table_args__ = (
        CheckConstraint(f"kind IN {OVERRIDE_KINDS}", name="ck_overrides_kind"),
        CheckConstraint("end_date >= start_date", name="ck_overrides_dates"),
        CheckConstraint("value >= 0", name="ck_overrides_value"),
        Index("ix_overrides_org_product", "org_id", "product_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Location(TimestampMixin, Base):
    """A place stock is kept (warehouse, store). Each organisation has exactly one default location."""

    __tablename__ = "locations"
    __table_args__ = (
        Index("uq_locations_org_code", "org_id", func.lower(text("code")), unique=True),
        Index("uq_locations_org_default", "org_id", unique=True, postgresql_where=text("is_default")),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class StockLevel(Base):
    """Stock of one product at one location. ``Product.on_hand``/``on_order`` are kept equal to the sums."""

    __tablename__ = "stock_levels"
    __table_args__ = (
        UniqueConstraint("product_id", "location_id", name="uq_stock_levels_product_location"),
        CheckConstraint("on_hand >= 0 AND on_order >= 0", name="ck_stock_levels_nonneg"),
        Index("ix_stock_levels_org_location", "org_id", "location_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False)
    on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class StockTransfer(TimestampMixin, Base):
    """A completed move of stock between two locations (a record; totals do not change)."""

    __tablename__ = "stock_transfers"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_stock_transfers_qty"),
        CheckConstraint("from_location_id <> to_location_id", name="ck_stock_transfers_distinct"),
        Index("ix_stock_transfers_org_created", "org_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    from_location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False)
    to_location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


DIGEST_SECTIONS = ("stockouts", "overstock", "accuracy", "alerts", "purchasing")
DIGEST_CHANNELS = ("in_app", "webhook", "slack", "email")


class DigestSchedule(TimestampMixin, Base):
    """A recurring summary sent to a channel (ADR-036). Targets (URLs, addresses) are encrypted at rest."""

    __tablename__ = "digest_schedules"
    __table_args__ = (
        CheckConstraint("frequency IN ('daily','weekly')", name="ck_digest_schedules_frequency"),
        CheckConstraint(f"channel IN {DIGEST_CHANNELS}", name="ck_digest_schedules_channel"),
        CheckConstraint("hour_utc BETWEEN 0 AND 23", name="ck_digest_schedules_hour"),
        CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="ck_digest_schedules_weekday"),
        Index("ix_digest_schedules_due", "next_run_at", postgresql_where=text("is_active")),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    weekday: Mapped[int | None] = mapped_column(Integer)  # 0 = Monday, weekly only
    hour_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    sections: Mapped[list] = mapped_column(JSONB, nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    target_encrypted: Mapped[str | None] = mapped_column(Text)
    target_hint: Mapped[str | None] = mapped_column(String(200))  # masked, safe to show
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DigestRun(TimestampMixin, Base):
    """One built digest: its content (also the in-app copy) and how delivery went."""

    __tablename__ = "digest_runs"
    __table_args__ = (
        CheckConstraint("status IN ('delivered','failed','in_app')", name="ck_digest_runs_status"),
        Index("ix_digest_runs_org_created", "org_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("digest_schedules.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trigger: Mapped[str] = mapped_column(String(10), nullable=False, default="schedule")  # schedule | manual


__all__ = [
    "Base",
    "DigestSchedule",
    "DigestRun",
    "Organization",
    "User",
    "RefreshToken",
    "PasswordResetToken",
    "Dataset",
    "Product",
    "SalesRecord",
    "Job",
    "ModelVersion",
    "ForecastRun",
    "BacktestResult",
    "Alert",
    "AlertDelivery",
    "AuditEvent",
    "ExternalSignal",
    "ShopifyOrder",
    "ShopifyShop",
    "OAuthState",
    "WebhookEvent",
    "Supplier",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "ForecastOverride",
    "Location",
    "StockLevel",
    "StockTransfer",
]
