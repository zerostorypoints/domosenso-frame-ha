"""Folder layout and file names for album sync."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, tzinfo
from pathlib import Path

from .album_files import read_marker
from .album_share import RemoteItem

_BAD = re.compile(r'[/\\\x00-\x1f\x7f]')
MAX_NAME = 80
MAX_BYTES = 200


def sanitize_name(name: str) -> str:
    text = _BAD.sub('_', unicodedata.normalize('NFC', name)).lstrip(' .')[:MAX_NAME]
    text = text.encode('utf-8')[:MAX_BYTES].decode('utf-8', 'ignore')
    return text.rstrip(' ') or 'album'


def initial_path(name: str, start_time: int, tz: tzinfo) -> str:
    return f'{datetime.fromtimestamp(start_time, tz):%Y/%m}/{sanitize_name(name)}'


def _owner(folder: Path) -> str | None:
    value = (read_marker(folder) or {}).get('album_id')
    return value if isinstance(value, str) else None


def resolve_path(photo_dir: Path, wanted: str, own_album_id: str) -> str:
    candidate, n = wanted, 1
    while (photo_dir / candidate).exists() and _owner(photo_dir / candidate) != own_album_id:
        n += 1
        candidate = f'{wanted}-{n}'
    return candidate


def local_filename(item: RemoteItem) -> str:
    return f'{sanitize_name(item.filename.rsplit(".", 1)[0])}_{item.id}.jpg'
