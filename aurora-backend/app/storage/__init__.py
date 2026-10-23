"""Object storage abstraction: local filesystem for development, S3-compatible (MinIO/AWS) for deployments."""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_\-.]{0,400}$")


def _check_key(key: str) -> str:
    if not _KEY_RE.match(key) or ".." in key:
        raise ValueError(f"invalid storage key: {key!r}")
    return key


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def healthcheck(self) -> bool: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / _check_key(key)).resolve()
        if self.root not in p.parents:
            raise ValueError("path escapes storage root")
        return p

    def put(self, key: str, data: bytes) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + f".tmp{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, p)  # atomic on POSIX
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def healthcheck(self) -> bool:
        probe = self.root / ".healthcheck"
        probe.write_bytes(b"ok")
        return probe.read_bytes() == b"ok"


class S3Storage:
    def __init__(self) -> None:
        import boto3

        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key.get_secret_value() if s.s3_access_key else None,
            aws_secret_access_key=s.s3_secret_key.get_secret_value() if s.s3_secret_key else None,
            region_name=s.s3_region,
        )

    def put(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=_check_key(key), Body=data)
        return key

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=_check_key(key))
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))

    def healthcheck(self) -> bool:
        self.client.head_bucket(Bucket=self.bucket)
        return True


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(s.artifact_dir)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
