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
    tmp = folder / f'{MARKER}.tmp'
    tmp.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, folder / MARKER)


def write_photo(folder: Path, name: str, data: bytes, mtime: int) -> None:
    tmp = folder / f'.partial-{name}'
    tmp.write_bytes(data)
    os.utime(tmp, (mtime, mtime))
    os.replace(tmp, folder / name)


def remove_photo(folder: Path, name: str) -> None:
    (folder / name).unlink(missing_ok=True)


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free
