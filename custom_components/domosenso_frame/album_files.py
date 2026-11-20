"""Atomic file writes for album sync (no Home Assistant import)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

MARKER = '.sync.json'


def read_marker(folder: Path) -> dict | None:
    try:
        value = json.loads((folder / MARKER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_marker(folder: Path, marker: dict) -> None:
    _replace_via_temp(folder / f'{MARKER}.tmp', folder / MARKER,
                      json.dumps(marker, ensure_ascii=False, indent=2).encode('utf-8'), None)


def write_photo(folder: Path, name: str, data: bytes, mtime: int) -> None:
    _replace_via_temp(folder / f'.partial-{name}', folder / name, data, mtime)


def _replace_via_temp(tmp: Path, target: Path, data: bytes, mtime: int | None) -> None:
    try:
        tmp.write_bytes(data)
        if mtime is not None:
            os.utime(tmp, (mtime, mtime))
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def remove_photo(folder: Path, name: str) -> None:
    (folder / name).unlink(missing_ok=True)


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free
