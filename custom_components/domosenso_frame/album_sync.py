"""Sync one shared album into photo_dir (no Home Assistant import)."""
from __future__ import annotations

import errno
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import tzinfo
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from . import album_image, album_plan
from .album_files import MARKER, free_bytes, read_marker, remove_photo, write_marker, write_photo
from .album_layout import initial_path, local_filename, resolve_path, sanitize_name
from .album_share import (AuthFailed, NoDownload, RemoteAlbum, RemoteItem, ShareClient, ShareError,
                          Unreachable, parse_share_url)

RunBlocking = Callable[[Callable[[], Any]], Awaitable[Any]]
_BAD_IMAGE = (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError)
_BAD_RESPONSE = (ShareError, KeyError, IndexError, AttributeError, TypeError, ValueError)


@dataclass(frozen=True)
class SyncOptions:
    max_edge: int = 2560
    quality: int = 85
    free_floor: int = 1_000_000_000
    full_list_every: int = 86400


class AlbumRecord(TypedDict):
    id: str
    url: str
    password: str | None
    path_override: str | None
    path: str | None
    remote_album_id: int | None
    name: str | None
    version: int | None
    last_sync: int | None
    last_full_list: int | None
    status: str | None
    error: str | None
    mode: str | None
    heic: str | None
    photos: int
    skipped: list[int]


@dataclass(frozen=True)
class SyncResult:
    record: AlbumRecord
    added: int
    removed: int
    changed: bool


class _DiskFull(Exception):
    pass


class _InvalidPath(Exception):
    pass


@dataclass
class _Run:
    rec: dict
    photo_dir: Path
    options: SyncOptions
    run_blocking: RunBlocking
    now: int
    folder: Path | None = None
    marker: dict | None = None
    added: int = 0
    removed: int = 0
    renamed: bool = False
    created: bool = False
    mode: str | None = None
    heic_converted: bool = False
    heic_thumbs: bool = False
    skipped: list[int] = field(default_factory=list)

    async def io(self, fn, *args):
        return await self.run_blocking(lambda: fn(*args))


async def sync_album(record: AlbumRecord, client: ShareClient, photo_dir: Path, options: SyncOptions,
                     tz: tzinfo, now: int, run_blocking: RunBlocking, confirm: bool = False) -> SyncResult:
    """Never raises for share, network, listing, image or disk problems; files on disk are kept."""
    run = _Run(dict(record), photo_dir, options, run_blocking, now)
    try:
        await _sync(run, client, tz, confirm)
    except AuthFailed:
        _fail(run, 'auth_failed', 'wrong_password')
    except Unreachable:
        _fail(run, 'unreachable')
    except album_plan.IncompleteListing:
        _fail(run, 'error', 'incomplete_listing')
    except _InvalidPath:
        _fail(run, 'error', 'invalid_path')
    except _DiskFull:
        _fail(run, 'disk_full')
    except OSError as err:
        if err.errno == errno.ENOSPC:
            _fail(run, 'disk_full')
        else:
            _fail(run, 'error', 'write_failed')
    except _BAD_RESPONSE:
        _fail(run, 'error', 'unexpected_response')
    changed = bool(run.added or run.removed or run.renamed or run.created)
    return SyncResult(run.rec, run.added, run.removed, changed)


def _fail(run: _Run, status: str, error: str | None = None) -> None:
    run.rec.update(status=status, error=error)
    if run.marker is not None:
        run.rec['photos'] = len(run.marker['items'])


async def _sync(run: _Run, client: ShareClient, tz: tzinfo, confirm: bool) -> None:
    rec, now = run.rec, run.now
    await client.login()
    remote = await client.album()
    if (album_plan.VERSION_TRACKS_CONTENT and not confirm and rec.get('status') == 'ok' and rec.get('path')
            and remote.id == rec.get('remote_album_id')
            and remote.version == rec.get('version') and not rec.get('skipped')
            and now - (rec.get('last_full_list') or 0) < run.options.full_list_every):
        rec.update(status='ok', error=None, last_sync=now)
        return
    listing = await client.items()
    local = await run.io(_local_items, run.photo_dir, rec)
    plan = album_plan.plan_sync(local, listing, remote.item_count, confirm)
    rec['last_full_list'] = now
    if plan.hold:
        rec.update(status='awaiting_confirmation', error=None)
        return
    run.marker = {'album_id': rec['id'], 'remote_album_id': remote.id,
                  'source_host': urlsplit(parse_share_url(rec['url']).origin).netloc,
                  'version': remote.version, 'items': {str(k): v for k, v in local.items()},
                  'last_sync': now}
    run.folder = await run.io(_place_folder, run, remote, tz)
    await _download(run, client, plan.download)
    await _delete(run, plan.delete)
    await run.io(write_marker, run.folder, run.marker)
    heic = 'thumbnails' if run.heic_thumbs else 'converted' if run.heic_converted else rec.get('heic')
    rec.update(status='ok', error=None, remote_album_id=remote.id, name=remote.name, version=remote.version,
               last_sync=now, mode=run.mode or rec.get('mode') or 'originals', heic=heic,
               photos=len(run.marker['items']), skipped=run.skipped)


def _owned(folder: Path, own: str) -> bool:
    return (read_marker(folder) or {}).get('album_id') == own


def _inside(photo_dir: Path, path: str) -> bool:
    root = photo_dir.resolve()
    target = (photo_dir / path).resolve()
    return target != root and target.is_relative_to(root)


def _local_items(photo_dir: Path, rec: dict) -> dict[int, str]:
    """Marker entries whose file is still there; a missing file is fetched again."""
    path = rec.get('path')
    marker = read_marker(photo_dir / path) if path else None
    if not marker or marker.get('album_id') != rec['id'] or not isinstance(marker.get('items'), dict):
        return {}
    folder = photo_dir / path
    return {int(k): v for k, v in marker['items'].items()
            if isinstance(v, str) and v and PurePosixPath(v).name == v and (folder / v).is_file()}


def _rename_target(photo_dir: Path, path: str, wanted: str) -> str:
    """First of wanted, wanted-2, ... that is free; the current path if it comes first."""
    candidate, n = wanted, 1
    while candidate != path and (photo_dir / candidate).exists():
        n += 1
        candidate = f'{wanted}-{n}'
    return candidate


def _place_folder(run: _Run, remote: RemoteAlbum, tz: tzinfo) -> Path:
    """Pick (first sync), follow a remote rename, then create the album folder and its marker."""
    rec, photo_dir, own = run.rec, run.photo_dir, run.rec['id']
    path = rec.get('path')
    if not path:
        path = rec.get('path_override') or initial_path(remote.name, remote.start_time, tz)
    elif not rec.get('path_override') and rec.get('name') not in (None, remote.name):
        new = _rename_target(photo_dir, path, str(PurePosixPath(path).with_name(sanitize_name(remote.name))))
        old = photo_dir / path
        if new != path and (not old.exists() or _owned(old, own)):
            if old.exists():
                (photo_dir / new).parent.mkdir(parents=True, exist_ok=True)
                old.rename(photo_dir / new)
                run.renamed = True
            path = new
    if not _inside(photo_dir, path):
        raise _InvalidPath(path)
    path = resolve_path(photo_dir, path, own)
    rec['path'] = path
    folder = photo_dir / path
    run.created = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    write_marker(folder, run.marker)
    return folder


async def _download(run: _Run, client: ShareClient, items: list[RemoteItem]) -> None:
    thumbs = False
    for item in items:
        if await run.io(free_bytes, run.photo_dir) < run.options.free_floor:
            raise _DiskFull
        if not thumbs:
            try:
                raw = await client.original(item)
            except NoDownload:
                thumbs = True
        if thumbs:
            raw = await client.thumbnail(item)
        data = await _convert(run, client, item, raw)
        if data is None:
            run.skipped.append(item.id)
            continue
        name = local_filename(item)
        await run.io(write_photo, run.folder, name, data, item.time)
        run.marker['items'][str(item.id)] = name
        run.added += 1
        await run.io(write_marker, run.folder, run.marker)
    if items:
        run.mode = 'thumbnails' if thumbs else 'originals'


async def _convert(run: _Run, client: ShareClient, item: RemoteItem, raw: bytes) -> bytes | None:
    """Return screen-ready JPEG, or None when the image cannot be decoded."""
    try:
        data = await _normalize(run, raw)
    except album_image.HeifUnavailable:
        run.heic_thumbs = True
        thumb = await client.thumbnail(item)  # client errors take the album-level path
        try:
            return await _normalize(run, thumb)
        except (album_image.HeifUnavailable, *_BAD_IMAGE):
            return None
    except _BAD_IMAGE:
        return None
    run.heic_converted |= album_image.is_heif(raw)
    return data


async def _normalize(run: _Run, raw: bytes) -> bytes:
    return await run.io(album_image.normalize, raw, run.options.max_edge, run.options.quality)


async def _delete(run: _Run, names: list[str]) -> None:
    for name in names:
        await run.io(remove_photo, run.folder, name)
        run.marker['items'] = {k: v for k, v in run.marker['items'].items() if v != name}
        run.removed += 1
        await run.io(write_marker, run.folder, run.marker)


async def remove_album(record: AlbumRecord, photo_dir: Path, run_blocking: RunBlocking) -> None:
    """Delete what the sync owns in the album folder, and the folder only if nothing else is left."""
    await run_blocking(lambda: _remove(record, photo_dir))


def _remove(record: AlbumRecord, photo_dir: Path) -> None:
    path = record.get('path')
    if not (path and _inside(photo_dir, path)):
        return
    folder = photo_dir / path
    marker = read_marker(folder)
    if not marker or marker.get('album_id') != record['id']:
        return
    items = marker.get('items')
    names = {v for v in items.values() if isinstance(v, str) and PurePosixPath(v).name == v} \
        if isinstance(items, dict) else set()
    for name in names:
        remove_photo(folder, name)
    for leftover in (*folder.glob('.partial-*'), folder / f'{MARKER}.tmp', folder / MARKER):
        leftover.unlink(missing_ok=True)
    try:
        folder.rmdir()  # fails (and keeps the folder) while the user's own files are in it
    except OSError:
        pass
