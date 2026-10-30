"""Object storage seam. Only a local-disk backend is implemented; add an S3 backend by
implementing the same four methods (put/path/delete/exists)."""
import hashlib
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from ..config import get_settings


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("bad storage key")
        return p

    def put(self, key: str, fileobj: BinaryIO, max_bytes: int) -> tuple[int, str]:
        p = self._safe(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        h, size = hashlib.sha256(), 0
        try:
            with open(p, "wb") as out:
                while chunk := fileobj.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("file too large")
                    h.update(chunk)
                    out.write(chunk)
        except Exception:
            p.unlink(missing_ok=True)
            raise
        return size, h.hexdigest()

    def path(self, key: str) -> Path:
        return self._safe(key)

    def exists(self, key: str) -> bool:
        return self._safe(key).exists()

    def delete(self, key: str):
        self._safe(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str):
        shutil.rmtree(self._safe(prefix), ignore_errors=True)


def get_storage() -> LocalStorage:
    return LocalStorage(get_settings().storage_dir)


def new_key(workspace_id, session_id, ext: str) -> str:
    return f"{workspace_id}/{session_id}/{uuid.uuid4().hex}{ext}"
