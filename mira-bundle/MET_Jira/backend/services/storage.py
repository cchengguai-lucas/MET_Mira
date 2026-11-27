"""Small synchronous disk primitives intended to run through ``anyio.to_thread``."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import BinaryIO


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_replace(source: Path, target: Path) -> None:
    os.replace(source, target)
    _fsync_directory(target.parent)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with tmp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_copy_limited(source: BinaryIO, path: Path, limit: int) -> int:
    """Copy a file object to a unique temporary name, fsync, then atomically publish it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    size = 0
    try:
        with tmp.open("xb") as handle:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ValueError("file_too_large")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(tmp, path)
        return size
    finally:
        tmp.unlink(missing_ok=True)
