import asyncio
import importlib
import io
import json
import os
import sys
import types
from datetime import timezone
from pathlib import Path

import pillow_heif
from PIL import Image

pkg = types.ModuleType('album_sync_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg
album_sync = importlib.import_module('album_sync_pkg.album_sync')
album_share = importlib.import_module('album_sync_pkg.album_share')
album_plan = importlib.import_module('album_sync_pkg.album_plan')
album_image = importlib.import_module('album_sync_pkg.album_image')

URL = 'https://photos.example.org/mo/sharing/Zq7Lm3Xp9'
PASSPHRASE = 'Zq7Lm3Xp9'
PASSWORD = 'pw-Velvet-Otter-42'
START = 1_197_000_000  # 2007-12-07 UTC
MARKER_KEYS = {'album_id', 'remote_album_id', 'source_host', 'version', 'items', 'last_sync'}


def _image(fmt):
    buf = io.BytesIO()
    img = Image.new('RGB', (64, 48), (20, 120, 200))
    if fmt == 'HEIF':
        pillow_heif.from_pillow(img).save(buf, quality=60)
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


JPEG, HEIC = _image('JPEG'), _image('HEIF')


def item(n, kind='photo'):
    return album_share.RemoteItem(n, f'IMG_{n:04}.JPG', 1000, START + n, kind, f'{n}_ck')


def album(name='trip', count=2, version=1):
    return album_share.RemoteAlbum(77, name, count, START, version)


class FakeClient:
    def __init__(self, items, name='trip', version=1, originals=None, thumbs=None, error=None):
        self.remote, self.list = album(name, len(items), version), list(items)
        self.originals, self.thumbs = originals or {}, thumbs or {}
        self.error, self.calls = error, []

    async def login(self):
        self.calls.append('login')
        if self.error:
            raise self.error

    async def album(self):
        self.calls.append('album')
        return self.remote

    async def items(self):
        self.calls.append('items')
        return list(self.list)

    async def _get(self, kind, table, it):
        self.calls.append((kind, it.id))
        value = table.get(it.id, JPEG)
        if isinstance(value, Exception):
            raise value
        return value

    async def original(self, it):
        return await self._get('original', self.originals, it)

    async def thumbnail(self, it):
        return await self._get('thumbnail', self.thumbs, it)


def record(**kw):
    base = dict(id='a1', url=URL, password=PASSWORD, path_override=None, path=None, remote_album_id=None,
                name=None, version=None, last_sync=None, last_full_list=None, status=None, error=None,
                mode=None, heic=None, photos=0, skipped=[])
    return base | kw


async def _direct(fn):
    return fn()


def sync(rec, client, photo_dir, now=10_000, confirm=False, **options):
    return asyncio.run(album_sync.sync_album(rec, client, photo_dir, album_sync.SyncOptions(**options),
                                             timezone.utc, now, _direct, confirm=confirm))


def marker(folder):
    return json.loads((folder / '.sync.json').read_text())


def photos(folder):
    return sorted(p.name for p in folder.iterdir() if p.name != '.sync.json')


def test_first_sync_creates_folder_marker_files(tmp_path):
    client = FakeClient([item(1), item(2)])
    res = sync(record(), client, tmp_path)
    rec, folder = res.record, tmp_path / '2007/12/trip'
    assert rec['path'] == '2007/12/trip'
    assert (rec['status'], rec['error'], rec['mode'], rec['heic']) == ('ok', None, 'originals', None)
    assert photos(folder) == ['IMG_0001_1.jpg', 'IMG_0002_2.jpg']
    m = marker(folder)
    assert set(m) == MARKER_KEYS
    assert m['items'] == {'1': 'IMG_0001_1.jpg', '2': 'IMG_0002_2.jpg'}
    assert (m['album_id'], m['remote_album_id'], m['source_host'], m['version'], m['last_sync']) == (
        'a1', 77, 'photos.example.org', 1, 10_000)
    assert (rec['remote_album_id'], rec['name'], rec['version'], rec['photos']) == (77, 'trip', 1, 2)
    assert (rec['last_sync'], rec['last_full_list']) == (10_000, 10_000)
    assert (res.added, res.removed, res.changed) == (2, 0, True)
    assert client.calls[:3] == ['login', 'album', 'items'] and client.calls.count('login') == 1


def test_path_override_wins(tmp_path):
    res = sync(record(path_override='family/best'), FakeClient([item(1)]), tmp_path)
    assert res.record['path'] == 'family/best'
    assert photos(tmp_path / 'family/best') == ['IMG_0001_1.jpg']


def test_path_collision_with_foreign_folder_gets_suffix(tmp_path):
    (tmp_path / '2007/12/trip').mkdir(parents=True)
    (tmp_path / '2007/12/trip/mine.jpg').write_bytes(b'x')
    res = sync(record(), FakeClient([item(1)]), tmp_path)
    assert res.record['path'] == '2007/12/trip-2'
    assert os.listdir(tmp_path / '2007/12/trip') == ['mine.jpg']


def test_path_outside_photo_dir_is_refused(tmp_path):
    photo_dir = tmp_path / 'ramka'
    photo_dir.mkdir()
    res = sync(record(path_override='../escape'), FakeClient([item(1)]), photo_dir)
    assert (res.record['status'], res.record['error']) == ('error', 'invalid_path')
    assert not (tmp_path / 'escape').exists()


def test_unchanged_version_skips_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(album_plan, 'VERSION_TRACKS_CONTENT', True)
    rec = sync(record(), FakeClient([item(1)]), tmp_path, now=1000).record
    client = FakeClient([item(1)])
    res = sync(rec, client, tmp_path, now=2000)
    assert client.calls == ['login', 'album']
    assert (res.record['status'], res.record['last_sync'], res.record['last_full_list']) == ('ok', 2000, 1000)
    assert res.changed is False


def test_lists_every_time_while_version_does_not_track_content(tmp_path, monkeypatch):
    monkeypatch.setattr(album_plan, 'VERSION_TRACKS_CONTENT', False)
    rec = sync(record(), FakeClient([item(1)]), tmp_path, now=1000).record
    client = FakeClient([item(1), item(2)])
    res = sync(rec, client, tmp_path, now=2000)
    assert 'items' in client.calls and res.added == 1


def test_daily_full_list_even_if_version_same(tmp_path, monkeypatch):
    monkeypatch.setattr(album_plan, 'VERSION_TRACKS_CONTENT', True)
    rec = sync(record(), FakeClient([item(1)]), tmp_path, now=1000).record
    client = FakeClient([item(1), item(2)])
    res = sync(rec, client, tmp_path, now=1000 + 86400)
    assert 'items' in client.calls and res.added == 1
    assert res.record['last_full_list'] == 1000 + 86400


def test_remote_removal_deletes_file(tmp_path):
    rec = sync(record(), FakeClient([item(1), item(2), item(3)]), tmp_path).record
    res = sync(rec, FakeClient([item(1), item(2)]), tmp_path)
    folder = tmp_path / rec['path']
    assert photos(folder) == ['IMG_0001_1.jpg', 'IMG_0002_2.jpg']
    assert set(marker(folder)['items']) == {'1', '2'}
    assert (res.added, res.removed, res.changed, res.record['photos']) == (0, 1, True, 2)


def test_videos_are_not_downloaded(tmp_path):
    client = FakeClient([item(1), item(2, 'video')])
    res = sync(record(), client, tmp_path)
    assert photos(tmp_path / res.record['path']) == ['IMG_0001_1.jpg']
    assert ('original', 2) not in client.calls


def test_hold_sets_awaiting_confirmation_and_keeps_files(tmp_path):
    rec = sync(record(), FakeClient([item(1), item(2), item(3)]), tmp_path).record
    client = FakeClient([item(1), item(4)])
    res = sync(rec, client, tmp_path)
    assert res.record['status'] == 'awaiting_confirmation'
    assert photos(tmp_path / rec['path']) == ['IMG_0001_1.jpg', 'IMG_0002_2.jpg', 'IMG_0003_3.jpg']
    assert not [c for c in client.calls if isinstance(c, tuple)]
    assert (res.added, res.removed, res.changed) == (0, 0, False)


def test_confirm_deletes_held_files(tmp_path):
    rec = sync(record(), FakeClient([item(1), item(2), item(3)]), tmp_path).record
    rec = sync(rec, FakeClient([item(1)]), tmp_path).record
    res = sync(rec, FakeClient([item(1)]), tmp_path, confirm=True)
    assert photos(tmp_path / rec['path']) == ['IMG_0001_1.jpg']
    assert (res.record['status'], res.removed) == ('ok', 2)


def _failing_client(error):
    return FakeClient([item(1), item(2)], error=error)


def test_auth_failed_keeps_files(tmp_path):
    rec = sync(record(), FakeClient([item(1), item(2)]), tmp_path).record
    res = sync(rec, _failing_client(album_share.AuthFailed(1001)), tmp_path, now=20_000)
    assert (res.record['status'], res.record['error']) == ('auth_failed', 'wrong_password')
    assert photos(tmp_path / rec['path']) == ['IMG_0001_1.jpg', 'IMG_0002_2.jpg']
    assert res.record['last_sync'] == 10_000 and res.changed is False


def test_unreachable_keeps_files(tmp_path):
    rec = sync(record(), FakeClient([item(1), item(2)]), tmp_path).record
    res = sync(rec, _failing_client(album_share.Unreachable('HTTP 502')), tmp_path)
    assert res.record['status'] == 'unreachable'
    assert photos(tmp_path / rec['path']) == ['IMG_0001_1.jpg', 'IMG_0002_2.jpg']


def test_incomplete_listing_creates_nothing(tmp_path):
    client = FakeClient([item(1), item(2)])
    client.remote = album(count=3)
    res = sync(record(), client, tmp_path)
    assert (res.record['status'], res.record['error']) == ('error', 'incomplete_listing')
    assert list(tmp_path.iterdir()) == []


def test_unexpected_response_is_an_error_not_a_crash(tmp_path):
    for err in (KeyError('list'), IndexError(0), album_share.ShareError(117)):
        res = sync(record(), _failing_client(err), tmp_path)
        assert (res.record['status'], res.record['error']) == ('error', 'unexpected_response')


def test_no_download_falls_back_to_thumbnails(tmp_path):
    client = FakeClient([item(1), item(2)], originals={1: album_share.NoDownload(804)})
    res = sync(record(), client, tmp_path)
    folder = tmp_path / res.record['path']
    assert res.record['mode'] == 'thumbnails'
    assert ('original', 2) not in client.calls and ('thumbnail', 2) in client.calls
    assert int(os.stat(folder / 'IMG_0002_2.jpg').st_mtime) == START + 2


def test_heic_is_converted(tmp_path):
    res = sync(record(), FakeClient([item(1), item(2)], originals={1: HEIC}), tmp_path)
    assert (res.record['heic'], res.record['mode'], res.added) == ('converted', 'originals', 2)


def test_heif_unavailable_uses_thumbnail_for_that_item(tmp_path, monkeypatch):
    def unavailable():
        raise album_image.HeifUnavailable('no libheif')
    monkeypatch.setattr(album_image, '_register_heif', unavailable)
    client = FakeClient([item(1), item(2)], originals={1: HEIC})
    res = sync(record(), client, tmp_path)
    assert (res.record['heic'], res.record['mode']) == ('thumbnails', 'originals')
    assert ('thumbnail', 1) in client.calls and ('thumbnail', 2) not in client.calls
    assert photos(tmp_path / res.record['path']) == ['IMG_0001_1.jpg', 'IMG_0002_2.jpg']


def test_unreadable_image_is_skipped_and_retried(tmp_path):
    client = FakeClient([item(1), item(2)], originals={1: b'not an image'})
    res = sync(record(), client, tmp_path)
    assert (res.record['status'], res.record['skipped'], res.added) == ('ok', [1], 1)
    assert set(marker(tmp_path / res.record['path'])['items']) == {'2'}
    res = sync(res.record, FakeClient([item(1), item(2)]), tmp_path)
    assert (res.record['skipped'], res.added) == ([], 1)


def test_disk_floor_stops_before_download(tmp_path, monkeypatch):
    free = iter([5_000_000_000, 10])
    monkeypatch.setattr(album_sync, 'free_bytes', lambda path: next(free))
    res = sync(record(), FakeClient([item(1), item(2)]), tmp_path)
    folder = tmp_path / res.record['path']
    assert res.record['status'] == 'disk_full'
    assert photos(folder) == ['IMG_0001_1.jpg']
    assert marker(folder)['items'] == {'1': 'IMG_0001_1.jpg'}
    assert (res.added, res.record['photos']) == (1, 1)


def test_network_drop_mid_album_resumes(tmp_path):
    client = FakeClient([item(1), item(2), item(3)], originals={2: album_share.Unreachable('reset')})
    res = sync(record(), client, tmp_path)
    folder = tmp_path / res.record['path']
    assert res.record['status'] == 'unreachable'
    assert photos(folder) == ['IMG_0001_1.jpg']
    assert marker(folder)['items'] == {'1': 'IMG_0001_1.jpg'}
    res = sync(res.record, FakeClient([item(1), item(2), item(3)]), tmp_path)
    assert res.record['status'] == 'ok' and res.added == 2
    assert photos(folder) == ['IMG_0001_1.jpg', 'IMG_0002_2.jpg', 'IMG_0003_3.jpg']


def test_remote_rename_moves_folder(tmp_path):
    rec = sync(record(), FakeClient([item(1)]), tmp_path).record
    res = sync(rec, FakeClient([item(1)], name='lake'), tmp_path)
    assert res.record['path'] == '2007/12/lake' and res.record['name'] == 'lake'
    assert not (tmp_path / '2007/12/trip').exists()
    assert photos(tmp_path / '2007/12/lake') == ['IMG_0001_1.jpg']
    assert marker(tmp_path / '2007/12/lake')['album_id'] == 'a1'
    assert (res.added, res.changed) == (0, True)


def test_remote_rename_into_foreign_folder_gets_suffix(tmp_path):
    rec = sync(record(), FakeClient([item(1)]), tmp_path).record
    (tmp_path / '2007/12/lake').mkdir()
    res = sync(rec, FakeClient([item(1)], name='lake'), tmp_path)
    assert res.record['path'] == '2007/12/lake-2'
    assert list((tmp_path / '2007/12/lake').iterdir()) == []


def test_remove_album_deletes_own_folder(tmp_path):
    rec = sync(record(), FakeClient([item(1)]), tmp_path).record
    asyncio.run(album_sync.remove_album(rec, tmp_path, _direct))
    assert not (tmp_path / rec['path']).exists()


def test_remove_album_spares_foreign_folder(tmp_path):
    rec = sync(record(), FakeClient([item(1)]), tmp_path).record
    folder = tmp_path / rec['path']
    (folder / '.sync.json').write_text(json.dumps({'album_id': 'other'}))
    asyncio.run(album_sync.remove_album(rec, tmp_path, _direct))
    assert photos(folder) == ['IMG_0001_1.jpg']
    (folder / '.sync.json').unlink()
    asyncio.run(album_sync.remove_album(rec, tmp_path, _direct))
    assert photos(folder) == ['IMG_0001_1.jpg']


def test_no_secret_under_photo_dir(tmp_path):
    rec = sync(record(), FakeClient([item(1), item(2)], originals={1: HEIC}), tmp_path).record
    sync(rec, FakeClient([item(1)], name='lake'), tmp_path, confirm=True)
    files = [p for p in tmp_path.rglob('*')]
    assert files
    for path in files:
        assert PASSWORD not in str(path) and PASSPHRASE not in str(path)
        if path.is_file():
            data = path.read_bytes()
            assert PASSWORD.encode() not in data and PASSPHRASE.encode() not in data


def test_skipped_item_forces_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(album_plan, 'VERSION_TRACKS_CONTENT', True)
    rec = sync(record(), FakeClient([item(1)], originals={1: b'junk'}), tmp_path, now=1000).record
    client = FakeClient([item(1)])
    res = sync(rec, client, tmp_path, now=2000)
    assert 'items' in client.calls and (res.added, res.record['skipped']) == (1, [])


def test_write_error_is_reported_and_marker_kept(tmp_path, monkeypatch):
    def broken(folder, name, data, mtime):
        raise PermissionError(13, 'read-only')
    rec = sync(record(), FakeClient([item(1)]), tmp_path).record
    monkeypatch.setattr(album_sync, 'write_photo', broken)
    res = sync(rec, FakeClient([item(1), item(2)]), tmp_path)
    assert (res.record['status'], res.record['error'], res.record['photos']) == ('error', 'write_failed', 1)
    assert marker(tmp_path / rec['path'])['items'] == {'1': 'IMG_0001_1.jpg'}
