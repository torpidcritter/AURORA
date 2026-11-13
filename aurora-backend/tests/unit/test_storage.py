"""S3 storage creates a missing bucket on first use; local storage refuses unsafe keys."""

from __future__ import annotations

import pytest
from botocore.stub import Stubber

from app.core.config import get_settings
from app.storage import LocalStorage, S3Storage


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio.invalid:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "test-access")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("S3_BUCKET", "aurora-test")
    get_settings.cache_clear()
    try:
        yield S3Storage()
    finally:
        get_settings.cache_clear()


def test_missing_bucket_is_created_once(s3: S3Storage) -> None:
    with Stubber(s3.client) as stub:
        stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
        stub.add_response("create_bucket", {}, {"Bucket": "aurora-test"})
        stub.add_response("put_object", {}, {"Bucket": "aurora-test", "Key": "models/a.pt", "Body": b"x"})
        stub.add_response("put_object", {}, {"Bucket": "aurora-test", "Key": "models/b.pt", "Body": b"y"})
        s3.put("models/a.pt", b"x")
        s3.put("models/b.pt", b"y")  # no second head_bucket/create_bucket
        stub.assert_no_pending_responses()


def test_existing_bucket_is_used_and_other_errors_surface(s3: S3Storage) -> None:
    with Stubber(s3.client) as stub:
        stub.add_client_error("head_bucket", service_error_code="403", http_status_code=403)
        with pytest.raises(Exception, match="403"):
            s3.healthcheck()
        stub.add_response("head_bucket", {}, {"Bucket": "aurora-test"})
        assert s3.healthcheck() is True


def test_local_storage_refuses_path_escape(tmp_path) -> None:  # type: ignore[no-untyped-def]
    st = LocalStorage(str(tmp_path))
    with pytest.raises(ValueError):
        st.put("../outside", b"x")
    assert st.get(st.put("datasets/x.csv", b"a,b")) == b"a,b"
