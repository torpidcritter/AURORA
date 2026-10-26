"""Centralised configuration loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-insecure-secret-key-change-me-0123456789"  # noqa: S105 - rejected outside development


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"
    api_host: str = "0.0.0.0"  # noqa: S104 - container bind address
    api_port: int = 8100
    api_cors_origins: str = "http://localhost:3100,http://127.0.0.1:3100"
    log_level: str = "INFO"
    log_json: bool = True

    # Database
    database_url: str = "postgresql+psycopg://aurora:aurora@localhost:5432/aurora"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_s: int = 10
    db_statement_timeout_ms: int = 30_000

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    celery_task_always_eager: bool = False

    # Auth
    secret_key: SecretStr = SecretStr(_DEV_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "aurora-api"
    jwt_audience: str = "aurora"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    bcrypt_rounds: int = 12
    # Bootstrap admin, created only by the `seed` CLI, never implicitly at runtime.
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: SecretStr | None = None
    bootstrap_org_name: str = "Aurora Demo Org"

    # Rate limiting (Redis-backed so it holds across instances)
    login_rate_limit_per_minute: int = 10
    login_lockout_threshold: int = 8
    login_lockout_minutes: int = 15
    api_rate_limit_per_minute: int = 600

    # Uploads
    max_upload_bytes: int = 20 * 1024 * 1024
    max_upload_rows: int = 2_000_000

    # Storage
    storage_backend: Literal["local", "s3"] = "local"
    artifact_dir: str = "./artifacts"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "aurora"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_region: str = "us-east-1"

    # ML defaults
    model_window: int = 30
    model_horizon: int = 7
    model_d_model: int = 64
    model_heads: int = 4
    model_layers: int = 3
    model_dropout: float = 0.1
    training_max_epochs: int = 30
    training_time_limit_s: int = 1800
    mc_dropout_samples: int = 20
    default_lead_time_days: int = 7
    default_coverage_days: int = 14
    default_service_level: float = 0.95

    # Monitoring thresholds
    drift_ks_threshold: float = 0.15
    drift_mean_change_threshold: float = 0.25
    model_max_age_days: int = 30
    accuracy_wape_threshold: float = 25.0
    audit_retention_days: int = 365
    job_stale_after_minutes: int = 120

    # Integrations
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_timeout_s: float = 30.0
    anthropic_max_tokens: int = 4000
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    # Server-side refusal fallback (Claude API only; disable on Bedrock/Vertex/Foundry gateways).
    anthropic_server_fallback: bool = True
    shopify_api_key: str | None = None
    shopify_api_secret: SecretStr | None = None
    # Accepted for webhook/OAuth HMAC verification during a secret rotation; never used to sign.
    shopify_api_secret_previous: SecretStr | None = None
    shopify_api_version: str = "2025-07"
    shopify_redirect_url: str = "http://localhost:8100/shopify/callback"
    # Public HTTPS base of the API that Shopify calls, e.g. https://api.example.com. Defaults to the origin of
    # SHOPIFY_REDIRECT_URL. Shopify only accepts HTTPS webhook addresses.
    shopify_webhook_base_url: str | None = None
    shopify_scopes: str = "read_orders,read_products"
    # OAuth callback timestamps older than this are refused (the state itself expires after 10 minutes).
    shopify_oauth_max_age_s: int = 3600
    # Shopify retries a failed delivery for about 4 hours with the original trigger time, so the window must be wider
    # than that or retries after an outage are lost. Replays inside the window are stopped by the webhook-id dedupe.
    shopify_webhook_tolerance_s: int = 43200
    # Processed webhook ids are kept this long for dedupe; must stay well above shopify_webhook_tolerance_s.
    webhook_event_retention_days: int = 30
    alert_webhook_url: str | None = None
    frontend_url: str = "http://localhost:3100"
    # Fernet key(s), comma-separated, newest first, used to encrypt third-party access tokens at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_keys: SecretStr | None = None

    metrics_token: SecretStr | None = Field(default=None, description="Bearer token required by /metrics when set")

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Managed providers (Railway, Heroku) hand out postgres:// URLs.
        if v.startswith("postgres://"):
            v = "postgresql+psycopg://" + v[len("postgres://") :]
        elif v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment in ("staging", "production"):
            key = self.secret_key.get_secret_value()
            if key == _DEV_SECRET or len(key) < 32:
                raise ValueError(
                    "SECRET_KEY must be set to a random value of at least 32 characters outside development"
                )
            if "*" in self.cors_origins:
                raise ValueError("Wildcard CORS origins are not allowed outside development")
            if self.shopify_api_key and not self.token_encryption_keys:
                raise ValueError("TOKEN_ENCRYPTION_KEYS is required when the Shopify integration is enabled")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def is_production(self) -> bool:
        return self.environment in ("staging", "production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
