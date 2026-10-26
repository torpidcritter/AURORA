"""Settings: spec variable names, precedence and refusals."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for k in ("DATABASE_URL", "ARTIFACT_DIR", "BOOTSTRAP_ADMIN_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_spec_db_variables_build_an_encoded_url(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, DB_HOST="postgres", DB_USER="aurora", DB_PASSWORD="p@ss/w:rd", DB_NAME="aurora")
    assert s.database_url == "postgresql+psycopg://aurora:p%40ss%2Fw%3Ard@postgres:5432/aurora"


def test_database_url_wins_over_spec_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, DATABASE_URL="postgres://x:y@h/d", DB_HOST="ignored")
    assert s.database_url == "postgresql+psycopg://x:y@h/d"


def test_admin_password_and_artifact_dir_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, ADMIN_PASSWORD="Adm1n-pass-xyz", ARTIFACT_ARTIFACTS_DIR="/data/a")
    assert s.bootstrap_admin_password is not None
    assert s.bootstrap_admin_password.get_secret_value() == "Adm1n-pass-xyz"
    assert s.artifact_dir == "/data/a"
    s = _settings(monkeypatch, ADMIN_PASSWORD="a", BOOTSTRAP_ADMIN_PASSWORD="b", ARTIFACT_DIR="/x")
    assert s.bootstrap_admin_password.get_secret_value() == "b"  # type: ignore[union-attr]
    assert s.artifact_dir == "/x"


@pytest.mark.parametrize(
    ("var", "value", "message"),
    [
        ("AUTH_ENABLED", "false", "always requires authentication"),
        ("DB_ENABLED", "false", "requires PostgreSQL"),
        ("DB_TYPE", "sqlite", "must be postgresql"),
    ],
)
def test_unsupported_modes_are_refused(monkeypatch: pytest.MonkeyPatch, var: str, value: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(monkeypatch, **{var: value})


def test_empty_variables_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Compose passes unset optional settings through as empty strings.
    s = _settings(monkeypatch, ANTHROPIC_API_KEY="", TRAINING_MAX_EPOCHS="", STORAGE_BACKEND="")
    assert s.anthropic_api_key is None
    assert s.training_max_epochs == 30
    assert s.storage_backend == "local"
