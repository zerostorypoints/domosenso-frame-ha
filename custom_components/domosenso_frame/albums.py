"""Album sync config entry: registry, timer, transport, services and repair issues."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from datetime import timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import aiohttp
import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .album_layout import sanitize_name
from .album_share import ShareClient, api_base, parse_share_url
from .album_sync import AlbumRecord, SyncOptions, remove_album, sync_album

_LOGGER = logging.getLogger(__name__)
DOMAIN = 'domosenso_frame'
DATA = 'domosenso_frame_albums'
SIGNAL = 'domosenso_frame_albums_updated'
STORE_KEY = 'domosenso_frame.albums'
PLATFORMS = ['sensor']
SERVICES = ('album_add', 'album_remove', 'album_sync')
DEFAULT_PHOTO_DIR = '/config/www/ramka'
DEFAULTS = {'interval_min': 60, 'max_edge': 2560, 'quality': 85, 'free_floor': 1_000_000_000, 'host_map': []}
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)
UNLOAD_WAIT_S = 30
FLOW_ERRORS = {'auth_failed': 'wrong_password', 'unreachable': 'unreachable'}


class AlbumError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_host_map(text: str) -> list[dict[str, str]]:
    """Lines `from -> to`, each side an origin only; raises ValueError otherwise."""
    entries = []
    for line in (text or '').splitlines():
        if not line.strip():
            continue
        left, arrow, right = line.partition('->')
        if not arrow:
            raise ValueError(line)
        entries.append({'from': _origin(left), 'to': _origin(right)})
    return entries


def format_host_map(entries: list[dict[str, str]]) -> str:
    return '\n'.join(f"{e['from']} -> {e['to']}" for e in entries)


def _origin(text: str) -> str:
    text = text.strip()
    parts = urlsplit(text)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.path or '?' in text
            or '#' in text or '@' in parts.netloc or parts.netloc.endswith(':')):
        raise ValueError(text)
    parts.port  # raises ValueError for a bad port
    return f'{parts.scheme}://{parts.netloc.lower()}'


def _clean_path(path: str | None) -> str | None:
    return (path or '').strip().strip('/') or None


def path_collides(rec: dict) -> bool:
    """The resolved path is the wanted one with a `-<n>` suffix (another folder held the name).

    Wanted is the override, else the album name under the same parent: initial_path keeps the
    YYYY/MM parent and a remote rename keeps the current parent, so only the last part differs.
    """
    path, name = rec.get('path'), rec.get('name')
    if not path:
        return False
    wanted = rec.get('path_override') or (name and str(PurePosixPath(path).with_name(sanitize_name(name))))
    return bool(wanted) and re.fullmatch(re.escape(wanted) + r'-\d+', path) is not None


class AlbumsManager:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.photo_dir = Path(entry.data['photo_dir'])
        opts = {**DEFAULTS, **entry.options}
        self.options = SyncOptions(max_edge=int(opts['max_edge']), quality=int(opts['quality']),
                                   free_floor=int(opts['free_floor']))
        self.interval = int(opts['interval_min'])
        self.host_map = list(opts['host_map'])
        self.store = Store(hass, 1, STORE_KEY)
        self.albums: dict[str, AlbumRecord] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        # Own session without a cookie jar: the client sends sharing_sid itself, and a shared jar
        # would mix sessions of albums on the same host.
        self.session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
        self.closing = False
        self._unsub = None
        self._task: asyncio.Task | None = None

    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        self.albums = {rec['id']: rec for rec in data.get('albums', [])}
        for rec in self.albums.values():
            self._issues(rec)

    def start(self) -> None:
        self._unsub = async_track_time_interval(self.hass, self._tick, timedelta(minutes=self.interval),
                                                name='domosenso_frame albums', cancel_on_shutdown=True)

    async def async_close(self) -> None:
        """Stop the timer and wait (bounded) for running syncs; files are written atomically."""
        self.closing = True
        if self._unsub:
            self._unsub()
        try:
            async with asyncio.timeout(UNLOAD_WAIT_S):
                while (self._task and not self._task.done()) or any(lk.locked() for lk in self.locks.values()):
                    await asyncio.sleep(0.5)
        except TimeoutError:
            _LOGGER.warning('Album sync still running after %s s; cancelling it', UNLOAD_WAIT_S)
            if self._task:
                self._task.cancel()

    def public(self, rec: AlbumRecord) -> dict:
        lock = self.locks.get(rec['id'])
        return {'id': rec['id'], 'name': rec.get('name'), 'path': rec.get('path'), 'photos': rec.get('photos', 0),
                'last_sync': rec.get('last_sync'),
                'status': 'syncing' if lock and lock.locked() else rec.get('status'),
                'error': rec.get('error'), 'mode': rec.get('mode'), 'heic': rec.get('heic'),
                'skipped_count': len(rec.get('skipped') or [])}

    def public_list(self) -> list[dict]:
        return [self.public(rec) for rec in self.albums.values()]

    async def add(self, url: str, password: str | None = None, path: str | None = None) -> AlbumRecord:
        """Create, save and sync once; the record is kept whatever the first sync returns."""
        url = str(url).strip()
        try:
            link = parse_share_url(url)
        except ValueError as err:
            raise AlbumError('invalid_link') from err
        if any(_same_link(rec['url'], link) for rec in self.albums.values()):
            raise AlbumError('already_added')
        rid = secrets.token_hex(8)
        self.albums[rid] = AlbumRecord(
            id=rid, url=url, password=password or None, path_override=_clean_path(path), path=None,
            remote_album_id=None, name=None, version=None, last_sync=None, last_full_list=None,
            status='syncing', error=None, mode=None, heic=None, photos=0, skipped=[])
        await self._save()
        return await self._sync_one(rid) or self.albums[rid]

    async def remove(self, album_id: str) -> AlbumRecord | None:
        """None when removed; the record shown as syncing when its sync is running."""
        if album_id not in self.albums:
            raise AlbumError('unknown_album')
        lock = self._lock(album_id)
        if lock.locked():
            return {**self.albums[album_id], 'status': 'syncing'}
        async with lock:
            rec = self.albums[album_id]
            await remove_album(rec, self.photo_dir, self._run_blocking)
            del self.albums[album_id]
            for key in ('awaiting_confirmation', 'path_collision'):
                ir.async_delete_issue(self.hass, DOMAIN, f'album_{album_id}_{key}')
            await self._save()
        self.locks.pop(album_id, None)
        return None

    async def sync(self, album_id: str | None = None, confirm_deletions: bool = False) -> list[AlbumRecord]:
        if album_id is not None and album_id not in self.albums:
            raise AlbumError('unknown_album')
        ids = [album_id] if album_id is not None else list(self.albums)
        results = [await self._sync_one(rid, confirm_deletions) for rid in ids]
        return [rec for rec in results if rec is not None]

    def _lock(self, album_id: str) -> asyncio.Lock:
        return self.locks.setdefault(album_id, asyncio.Lock())

    def _run_blocking(self, fn):
        return self.hass.async_add_executor_job(fn)

    async def _transport(self, method, url, params=None, data=None, headers=None):
        try:
            async with self.session.request(method, url, params=params, data=data, headers=headers,
                                            timeout=REQUEST_TIMEOUT) as resp:
                return resp.status, resp.headers.get('Content-Type', ''), await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise OSError(type(err).__name__) from err  # the client maps OSError to Unreachable

    async def _sync_one(self, album_id: str, confirm: bool = False) -> AlbumRecord | None:
        lock = self._lock(album_id)
        if lock.locked():
            rec = self.albums.get(album_id)
            return {**rec, 'status': 'syncing'} if rec else None
        async with lock:
            rec = self.albums.get(album_id)
            if rec is None:
                return None
            async_dispatcher_send(self.hass, SIGNAL)
            link = parse_share_url(rec['url'])
            client = ShareClient(self._transport, api_base(link, self.host_map), link.passphrase, rec['password'])
            try:
                result = await sync_album(rec, client, self.photo_dir, self.options, dt_util.get_default_time_zone(),
                                          int(time.time()), self._run_blocking, confirm)
                rec = result.record
            except Exception as err:  # noqa: BLE001 - one album must not stop the others
                _LOGGER.error('Album %s sync failed unexpectedly: %s', album_id, type(err).__name__)
                rec = {**rec, 'status': 'error', 'error': 'unexpected'}
            self.albums[album_id] = rec
            self._issues(rec)
            await self._save()
            return rec

    @callback
    def _tick(self, _now) -> None:
        if not self.closing and (self._task is None or self._task.done()):
            self._task = self.entry.async_create_background_task(self.hass, self._sync_all(),
                                                                 'domosenso_frame album sync')

    async def _sync_all(self) -> None:
        for rid in list(self.albums):
            if self.closing:
                return
            lock = self.locks.get(rid)
            if lock is None or not lock.locked():
                await self._sync_one(rid)

    async def _save(self) -> None:
        await self.store.async_save({'albums': list(self.albums.values())})
        async_dispatcher_send(self.hass, SIGNAL)

    def _issues(self, rec: AlbumRecord) -> None:
        placeholders = {'name': rec.get('name') or rec['id'], 'path': rec.get('path') or ''}
        for key, active in (('awaiting_confirmation', rec.get('status') == 'awaiting_confirmation'),
                            ('path_collision', path_collides(rec))):
            issue_id = f"album_{rec['id']}_{key}"
            if active:
                ir.async_create_issue(self.hass, DOMAIN, issue_id, is_fixable=False,
                                      severity=ir.IssueSeverity.WARNING, translation_key=f'album_{key}',
                                      translation_placeholders=placeholders)
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)


def _same_link(url: str, link) -> bool:
    try:
        return parse_share_url(url) == link
    except ValueError:
        return False


ADD_SCHEMA = vol.Schema({vol.Required('url'): cv.string, vol.Optional('password'): cv.string,
                         vol.Optional('path'): cv.string})
REMOVE_SCHEMA = vol.Schema({vol.Required('album_id'): cv.string})
SYNC_SCHEMA = vol.Schema({vol.Optional('album_id'): cv.string,
                          vol.Optional('confirm_deletions', default=False): cv.boolean})


def _invalid(err: AlbumError) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=err.code)


def _register_services(hass) -> None:
    async def album_add(call):
        manager = hass.data[DATA]
        try:
            rec = await manager.add(call.data['url'], call.data.get('password'), call.data.get('path'))
        except AlbumError as err:
            raise _invalid(err) from err
        return {'album': manager.public(rec)}

    async def album_remove(call):
        manager = hass.data[DATA]
        try:
            busy = await manager.remove(call.data['album_id'])
        except AlbumError as err:
            raise _invalid(err) from err
        return {'removed': busy is None}

    async def album_sync(call):
        manager = hass.data[DATA]
        try:
            records = await manager.sync(call.data.get('album_id'), call.data['confirm_deletions'])
        except AlbumError as err:
            raise _invalid(err) from err
        return {'albums': [manager.public(rec) for rec in records]}

    for name, handler, schema in (('album_add', album_add, ADD_SCHEMA), ('album_remove', album_remove, REMOVE_SCHEMA),
                                  ('album_sync', album_sync, SYNC_SCHEMA)):
        hass.services.async_register(DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.OPTIONAL)


async def _reload(hass, entry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass, entry) -> bool:
    manager = AlbumsManager(hass, entry)
    await manager.async_load()
    hass.data[DATA] = manager
    _register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    manager.start()
    return True


async def async_unload_entry(hass, entry) -> bool:
    if manager := hass.data.get(DATA):
        await manager.async_close()
    for name in SERVICES:
        hass.services.async_remove(DOMAIN, name)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.pop(DATA, None)
    return unloaded


async def async_remove_entry(hass, entry) -> None:
    """The registry holds passwords and passphrases; it goes with the entry. Photo folders stay."""
    await Store(hass, 1, STORE_KEY).async_remove()
