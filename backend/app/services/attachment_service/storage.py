"""Storage backends behind a single interface (PRD 19.3, 20)."""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from app.config import settings

_SAFE_KEY = re.compile(r"^[A-Za-z0-9._/-]+$")


class StorageError(RuntimeError):
    pass


def sanitise_filename(filename: str | None) -> str:
    """Strip paths and unsafe characters; never trust a user-supplied name."""
    base = os.path.basename((filename or "upload").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._") or "upload"
    return base[:180]


def validate_key(key: str) -> str:
    key = key.replace("\\", "/").lstrip("/")
    if not _SAFE_KEY.match(key) or ".." in key:
        raise StorageError(f"unsafe storage key: {key!r}")
    return key


class StorageBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    def save(self, key: str, data: bytes, content_type: str | None = None) -> None: ...

    @abstractmethod
    def read(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def local_path(self, key: str) -> Path | None: ...


class LocalStorageBackend(StorageBackend):
    """Filesystem backend used for local development, tests and the SIH demo.

    Files live outside the web root; access is only ever granted through a
    signed, time-limited URL minted by the API (PRD 19.1).
    """

    name = "local"

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.STORAGE_LOCAL_PATH).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        safe = validate_key(key)
        path = (self.root / safe).resolve()
        if not str(path).startswith(str(self.root)):
            raise StorageError("resolved storage path escapes the storage root")
        return path

    def save(self, key: str, data: bytes, content_type: str | None = None) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise StorageError(f"object not found: {key}")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def local_path(self, key: str) -> Path:
        return self._resolve(key)


class SupabaseStorageBackend(StorageBackend):
    """Supabase Storage backend used in deployment (PRD 20)."""

    name = "supabase"

    def __init__(self, url: str | None = None, service_key: str | None = None, bucket: str | None = None):
        self.url = (url or settings.SUPABASE_URL or "").rstrip("/")
        self.service_key = service_key or settings.SUPABASE_SERVICE_ROLE_KEY or ""
        self.bucket = bucket or settings.STORAGE_BUCKET
        if not self.url or not self.service_key:
            raise StorageError(
                "Supabase storage requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.service_key}",
            "apikey": self.service_key,
        }

    def _object_url(self, key: str) -> str:
        return f"{self.url}/storage/v1/object/{self.bucket}/{validate_key(key)}"

    def save(self, key: str, data: bytes, content_type: str | None = None) -> None:
        headers = {**self._headers, "Content-Type": content_type or "application/octet-stream",
                   "x-upsert": "false"}
        response = httpx.post(self._object_url(key), content=data, headers=headers, timeout=60)
        if response.status_code >= 400:
            raise StorageError(f"supabase upload failed ({response.status_code}): {response.text[:200]}")

    def read(self, key: str) -> bytes:
        response = httpx.get(self._object_url(key), headers=self._headers, timeout=60)
        if response.status_code >= 400:
            raise StorageError(f"supabase download failed ({response.status_code})")
        return response.content

    def delete(self, key: str) -> None:
        response = httpx.delete(self._object_url(key), headers=self._headers, timeout=60)
        if response.status_code >= 400 and response.status_code != 404:
            raise StorageError(f"supabase delete failed ({response.status_code})")

    def exists(self, key: str) -> bool:
        response = httpx.head(self._object_url(key), headers=self._headers, timeout=30)
        return response.status_code < 400

    def local_path(self, key: str) -> None:
        return None


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        if settings.STORAGE_BACKEND == "supabase":
            _storage = SupabaseStorageBackend()
        else:
            _storage = LocalStorageBackend()
    return _storage


def reset_storage() -> None:
    """Test hook: force the backend to be re-created from current settings."""
    global _storage
    _storage = None
