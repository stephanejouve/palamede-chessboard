"""Durable JSON writes under an exclusive lock (#34, #46).

One helper for the small JSON state files of palamede :

* :func:`locked` — exclusive ``flock`` on ``<file>.lock`` around a
  read-modify-write, so concurrent writers never drop each other's
  update ;
* :func:`write_json` — ``mkstemp`` in the same directory, ``fsync`` of
  the file, ``chmod``, ``os.replace``, then ``fsync`` of the parent
  directory so the rename itself survives a power cut (review Junior
  on #47 : the replace alone is not durable).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Exclusive ``flock`` on ``<path>.lock`` (created next to ``path``)."""
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path: Path, data: object, *, mode: int) -> None:
    """Atomically and durably replace ``path`` with ``data`` as JSON."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    _fsync_dir(path.parent)
