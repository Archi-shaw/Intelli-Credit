"""Private file storage. Files live outside any web-served directory and are only
streamed back through authenticated, organisation-scoped API endpoints."""
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


def save_bytes(subdir: str, data: bytes, extension: str) -> str:
    """Persist bytes under a random name; returns the path relative to the storage root."""
    ext = extension.lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{1,8}", ext):
        ext = "bin"
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
    return resolve(rel_path).read_bytes()


def delete(rel_path: str | None) -> None:
    if not rel_path:
        return
    try:
        resolve(rel_path).unlink(missing_ok=True)
    except (ValueError, OSError):
        pass
