"""Private file storage. Files live outside any web-served directory and are only
streamed back through authenticated, organisation-scoped API endpoints.

``STORAGE_BACKEND=local`` writes files under ``STORAGE_DIR``; ``database`` keeps the bytes in the ``stored_files``
table (serverless hosts such as Vercel have no persistent disk). Both use the same random relative paths, so
callers never know which backend is active. Writes are committed immediately on their own connection, like a file
write, and callers delete them again if their own transaction fails.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

from app.core.config import settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def storage_root() -> Path:
    root = Path(settings.STORAGE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_filename(name: str) -> str:
    base = os.path.basename(name or "").strip().replace("\x00", "")
    base = _SAFE_NAME.sub("_", base)
    base = base.lstrip(".") or "upload"
    return base[:200]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _db() -> bool:
    return settings.STORAGE_BACKEND == "database"


def _engine():
    from app.core.database import engine

    return engine


def save_bytes(subdir: str, data: bytes, extension: str) -> str:
    """Persist bytes under a random name; returns the path relative to the storage root."""
    ext = extension.lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{1,8}", ext):
        ext = "bin"
    if _db():
        from sqlalchemy import insert

        from app.core.database import utcnow
        from app.models.platform import StoredFile

        rel = f"{Path(subdir).as_posix().strip('/')}/{uuid.uuid4().hex}.{ext}"
        if ".." in Path(rel).parts:
            raise ValueError("Invalid storage path")
        with _engine().begin() as conn:
            conn.execute(insert(StoredFile).values(path=rel, data=data, size=len(data), created_at=utcnow()))
        return rel
    rel_dir = Path(subdir)
    target_dir = storage_root() / rel_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    rel_path = rel_dir / f"{uuid.uuid4().hex}.{ext}"
    full = storage_root() / rel_path
    with open(full, "wb") as fh:
        fh.write(data)
    try:
        os.chmod(full, 0o600)
    except OSError:  # object-storage FUSE mounts (e.g. Cloud Storage) have no POSIX modes; access is IAM-controlled
        pass
    return str(rel_path)


def resolve(rel_path: str) -> Path:
    """Resolve a stored relative path, refusing anything that escapes the storage root."""
    root = storage_root()
    full = (root / rel_path).resolve()
    if root not in full.parents and full != root:
        raise ValueError("Invalid storage path")
    return full


def read_bytes(rel_path: str) -> bytes:
    if _db():
        from sqlalchemy import select

        from app.models.platform import StoredFile

        with _engine().connect() as conn:
            data = conn.execute(select(StoredFile.data).where(StoredFile.path == rel_path)).scalar()
        if data is None:
            raise FileNotFoundError(rel_path)
        return bytes(data)
    return resolve(rel_path).read_bytes()


def check() -> None:
    """Readiness probe: raises if the configured backend cannot be used."""
    if _db():
        from sqlalchemy import text

        with _engine().connect() as conn:
            conn.execute(text("SELECT 1 FROM stored_files LIMIT 1"))
        return
    root = storage_root()
    probe = root / ".ready-probe"
    probe.write_bytes(b"ok")
    probe.unlink(missing_ok=True)


def delete(rel_path: str | None) -> None:
    if not rel_path:
        return
    if _db():
        from sqlalchemy import delete as sql_delete

        from app.models.platform import StoredFile

        try:
            with _engine().begin() as conn:
                conn.execute(sql_delete(StoredFile).where(StoredFile.path == rel_path))
        except Exception:  # noqa: BLE001 - best effort, mirrors unlink(missing_ok=True)
            pass
        return
    try:
        resolve(rel_path).unlink(missing_ok=True)
    except (ValueError, OSError):
        pass
