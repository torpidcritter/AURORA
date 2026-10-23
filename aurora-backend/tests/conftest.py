"""Shared fixtures. Tests run against a dedicated PostgreSQL database (never production) and fakeredis."""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://aurora:aurora@localhost:5432/aurora_test")
os.environ["ENVIRONMENT"] = "test"
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ["ARTIFACT_DIR"] = tempfile.mkdtemp(prefix="aurora-test-artifacts-")
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["LOG_JSON"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ.pop("ANTHROPIC_API_KEY", None)

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.redis_client import set_redis_override  # noqa: E402
from app.db.models import Base, Organization  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.services import auth_service  # noqa: E402

assert "aurora_test" in get_settings().database_url, "refusing to run tests against a non-test database"

PASSWORD = "Sup3r-Secret-pw!"


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> Iterator[None]:
    engine = get_engine()
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean(_migrate: None) -> Iterator[None]:
    fake = fakeredis.FakeRedis()
    set_redis_override(fake)
    yield
    set_redis_override(None)
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with get_engine().begin() as c:
        c.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db() -> Iterator[Session]:
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def app():  # type: ignore[no-untyped-def]
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(app)


class OrgFixture:
    def __init__(self, client: TestClient, db: Session, name: str = "Acme"):
        self.client = client
        org = Organization(name=name)
        db.add(org)
        db.flush()
        self.org_id = org.id
        self.users: dict[str, uuid.UUID] = {}
        self.emails: dict[str, str] = {}
        for role in ("viewer", "operator", "analyst", "admin"):
            email = f"{role}-{uuid.uuid4().hex[:6]}@example.com"
            u = auth_service.create_user(db, org_id=org.id, email=email, password=PASSWORD, role=role)
            self.users[role] = u.id
            self.emails[role] = email
        db.commit()
        self._tokens: dict[str, str] = {}

    def token(self, role: str) -> str:
        if role not in self._tokens:
            r = self.client.post("/auth/login", json={"email": self.emails[role], "password": PASSWORD})
            assert r.status_code == 200, r.text
            self._tokens[role] = r.json()["access_token"]
        return self._tokens[role]

    def h(self, role: str = "admin") -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(role)}"}


@pytest.fixture
def org(client: TestClient, db: Session) -> OrgFixture:
    return OrgFixture(client, db)


@pytest.fixture
def other_org(client: TestClient, db: Session) -> OrgFixture:
    return OrgFixture(client, db, name="Globex")
