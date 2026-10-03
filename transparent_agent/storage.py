"""Private local files and errors that must never be treated as observer failures."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class PersistenceError(RuntimeError):
    """A required trace or session write failed."""


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def atomic_json(path: Path, value: dict) -> None:
    temporary = None
    try:
        private_directory(path.parent)
        fd, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError) as exc:
        raise PersistenceError(f"cannot save session {path}: {exc}") from exc
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
