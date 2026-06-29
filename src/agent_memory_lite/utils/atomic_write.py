"""Atomic text-file write: write a temp file in the same dir, then os.replace.

``os.replace`` is atomic on POSIX and Windows, so a concurrent reader sees either
the OLD or the NEW complete file -- never a torn / truncated / zero-byte one. Used
by the workspace registry so a reader (e.g. the read guard's registry_load_error
check) cannot observe a half-written registry as 'corrupt' and turn a transient
write into a spurious workspace_unavailable refusal.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path

# Windows raises PermissionError from os.replace when the destination is momentarily
# open (e.g. a concurrent reader's read_text). Retry briefly so a transient lock
# never surfaces as a write failure. POSIX never hits this (replace-over-open is fine).
_REPLACE_RETRIES = 20
_REPLACE_BACKOFF_S = 0.005


def atomic_write_text(path: Path, data: str, *, encoding: str = "utf-8") -> None:
    """Write ``data`` to ``path`` atomically (temp file in the same dir + replace).

    The temp file is created in ``path``'s parent so the final ``os.replace`` stays
    on one volume (a cross-volume replace is not atomic and fails on Windows). The
    replace is retried on a transient Windows ``PermissionError``. On any other error
    the temp file is cleaned up and the original error re-raised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(data)
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == _REPLACE_RETRIES - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_S)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_text_retrying(path: Path, *, encoding: str = "utf-8") -> str:
    """``Path.read_text`` with a brief retry on a transient ``OSError`` -- a Windows
    file lock while a concurrent ``atomic_write_text`` is mid-replace. Without this a
    transient lock would surface as a 'corrupt' read. A genuine, persistent error
    (or a real ``json``-level corruption seen by the caller) still propagates after
    the retries are exhausted.
    """
    for attempt in range(_REPLACE_RETRIES):
        try:
            return path.read_text(encoding=encoding)
        except OSError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_S)
    raise AssertionError("unreachable")  # pragma: no cover
