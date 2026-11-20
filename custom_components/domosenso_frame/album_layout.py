"""Folder layout and file names for album sync."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, tzinfo
from pathlib import Path

from .album_share import RemoteItem

_BAD = re.compile(r'[/\\\x00-\x1f\x7f]')
MAX_NAME = 80


def sanitize_name(name: str) -> str:
    text = _BAD.sub('_', unicodedata.normalize('NFC', name)).strip(' ').lstrip('.').strip(' ')
    return text[:MAX_NAME].rstrip(' ') or 'album'


def initial_path(name: str, start_time: int, tz: tzinfo) -> str:
    return f'{datetime.fromtimestamp(start_time, tz):%Y/%m}/{sanitize_name(name)}'


def _marker_album_id(folder: Path) -> str | None:
    try:
        value = json.loads((folder / '.sync.json').read_text(encoding='utf-8')).get('album_id')
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def resolve_path(photo_dir: Path, wanted: str, own_album_id: str) -> str:
    candidate, n = wanted, 1
    while (photo_dir / candidate).exists() and _marker_album_id(photo_dir / candidate) != own_album_id:
        n += 1
        candidate = f'{wanted}-{n}'
    return candidate


def local_filename(item: RemoteItem) -> str:
    return f'{sanitize_name(item.filename.rsplit(".", 1)[0])}_{item.id}.jpg'
