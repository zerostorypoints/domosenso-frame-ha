"""Isolated HA smoke for album sync against a fake Synology Photos share server.

Runs inside the official HA image (see docs/albums.md). All values are fictional.
With ALBUMS_SMOKE_OFFLINE=1 (and `docker run --network none`) it checks instead that the bridge and
the albums entry load when pillow-heif cannot be installed, and that HEIC then uses thumbnails.
"""
import asyncio, importlib, importlib.util, io, json, logging, os, secrets, sys, traceback
from pathlib import Path

from aiohttp import web
from PIL import Image

from homeassistant import bootstrap, loader
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
import voluptuous as vol  # after homeassistant: HA maps voluptuous to probatio, whose errors services raise

if not os.environ.get('ALBUMS_SMOKE_DEBUG'):
    logging.disable(logging.CRITICAL)

DOMAIN = 'domosenso_frame'
PASSPHRASE, PASSWORD = 'TestShare1', 'pw'
PASSPHRASE2 = 'TestShare2'
OFFLINE = os.environ.get('ALBUMS_SMOKE_OFFLINE') == '1'
FAKE_HEIC = b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic' + b'\x00' * 64  # HEIF header only
SHARE_URL = f'http://photos.invalid/photo/mo/sharing/{PASSPHRASE}'
SHARE_URL2 = f'http://photos.invalid/photo/mo/sharing/{PASSPHRASE2}'
JUNE = 1781524800  # 2026-06-15 12:00 UTC
JANUARY = 1768046400  # 2026-01-10 12:00 UTC
EXIF_IFD, GPS_IFD, DATETIME_ORIGINAL = 0x8769, 0x8825, 0x9003
HEIC_TAKEN = '2026:06:15 14:00:00'


def check(cond, message):
    if not cond:
        raise AssertionError(message)


def jpeg(color, size=(64, 48)):
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, format='JPEG', quality=90)
    return out.getvalue()


def heic(color):
    """HEIC with DateTimeOriginal and GPS; needs pillow_heif, which the albums entry has HA install."""
    import pillow_heif
    pillow_heif.register_heif_opener()
    exif = Image.Exif()
    exif.get_ifd(EXIF_IFD)[DATETIME_ORIGINAL] = HEIC_TAKEN
    gps = exif.get_ifd(GPS_IFD)
    gps[1], gps[2], gps[3], gps[4] = 'N', (47.0, 22.0, 0.0), 'E', (8.0, 32.0, 0.0)
    out = io.BytesIO()
    Image.new('RGB', (64, 48), color).save(out, format='HEIF', exif=exif.tobytes())
    data = out.getvalue()
    with Image.open(io.BytesIO(data)) as back:
        check(back.getexif().get_ifd(GPS_IFD), 'generated HEIC has no GPS to strip')
    return data


class Share:
    def __init__(self, passphrase, password, name, start_time):
        self.passphrase, self.password, self.name, self.start_time = passphrase, password, name, start_time
        self.version, self.no_download = 1, False
        self.items = {}  # id -> dict(filename, type, time, data)

    def add(self, item_id, filename, data, kind='photo'):
        self.items[item_id] = {'filename': filename, 'type': kind, 'time': self.start_time + item_id, 'data': data}
        self.version += 1

    def remove(self, *ids):
        for item_id in ids:
            del self.items[item_id]
        self.version += 1


class FakeSynology:
    """The five share calls of DSM's /mo/sharing/webapi/entry.cgi, with the observed error shapes."""

    def __init__(self):
        self.shares, self.sids, self.agents, self.delay, self.logins = {}, {}, set(), 0.0, 0
        self.in_flight, self.max_in_flight = 0, 0
        self.thumb = jpeg((10, 200, 10), (32, 24))

    @staticmethod
    def error(code):
        return web.json_response({'error': {'code': code}, 'success': False})

    async def handle(self, request):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return await self.answer(request)
        finally:
            self.in_flight -= 1

    async def answer(self, request):
        self.agents.add(request.headers.get('User-Agent'))
        args = dict(await request.post()) if request.method == 'POST' else dict(request.query)
        api = args.get('api')
        if api == 'SYNO.Core.Sharing.Login':
            if 'X-SYNO-SHARING' in request.headers:
                return self.error(101)
            share = self.shares.get(json.loads(args.get('sharing_id', '""')))
            if share is None:
                return self.error(101)
            if share.password is not None and json.loads(args.get('password', '""')) != share.password:
                return self.error(1001)
            self.logins += 1
            sid = secrets.token_urlsafe(16)
            self.sids[sid] = share.passphrase
            resp = web.json_response({'data': {'sharing_sid': sid}, 'success': True})
            resp.set_cookie('sharing_sid', sid)
            return resp
        header = request.headers.get('X-SYNO-SHARING')
        share = self.shares.get(header)
        if share is None or self.sids.get(request.cookies.get('sharing_sid')) != header:
            return self.error(101)
        if api == 'SYNO.Foto.Browse.Album' and args.get('method') == 'get':
            return web.json_response({'data': {'list': [{
                'id': 7, 'name': share.name, 'item_count': len(share.items), 'start_time': share.start_time,
                'version': share.version}]}, 'success': True})
        if api == 'SYNO.Foto.Browse.Item' and args.get('method') == 'list':
            offset, limit = int(args['offset']), int(args['limit'])
            rows = [{'id': i, 'filename': it['filename'], 'filesize': len(it['data']), 'time': it['time'],
                     'type': it['type'], 'additional': {'thumbnail': {'cache_key': f'{i}_{share.version}'}}}
                    for i, it in sorted(share.items.items())]
            return web.json_response({'data': {'list': rows[offset:offset + limit]}, 'success': True})
        if api == 'SYNO.Foto.Download' and request.method == 'GET' and args.get('force_download') == 'true':
            if share.no_download:
                return self.error(804)
            item = share.items.get(json.loads(args['unit_id'])[0])
            if item is None:
                return self.error(120)
            return web.Response(body=item['data'], content_type='application/force-download')
        if api == 'SYNO.Foto.Thumbnail' and request.method == 'GET' and args.get('size') == 'xl':
            if int(args['id']) not in share.items:
                return self.error(120)
            return web.Response(body=self.thumb, content_type='image/jpeg')
        return self.error(103)


def tree(root):
    """Relative path -> (size, mtime, bytes) of every file under root."""
    root = Path(root)
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime, p.read_bytes())
            for p in sorted(root.rglob('*')) if p.is_file()}


def photos(folder):
    return sorted(p.name for p in Path(folder).iterdir() if p.name != '.sync.json')


async def wait_for(predicate, message, timeout=20):
    for _ in range(int(timeout / 0.1)):
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(message)


async def main():
    config_dir = os.environ.get('HA_CONFIG_DIR', '/config')
    check(importlib.util.find_spec('pillow_heif') is None, 'pillow_heif present before HA setup; cannot show HA installs it')
    fake = FakeSynology()
    app = web.Application()
    app.router.add_route('*', '/photo/mo/sharing/webapi/entry.cgi', fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = runner.addresses[0][1]

    hass = HomeAssistant(config_dir)
    loader.async_setup(hass)
    try:
        config = {'http': {'server_host': '127.0.0.1', 'server_port': 8123},
                  DOMAIN: {'endpoints': [{'id': 'frame', 'name': 'Demo frame', 'dashboard': '/dashboard-ramka/ramka',
                                          'display': {}, 'temperature': [{'entity': 'sensor.demo_temperature', 'label': 'Office'}]}]}}
        check(await bootstrap.async_from_config_dict(config, hass), 'HA bootstrap failed')
        check(hass.data.get(DOMAIN, {}).get('source') == 'yaml', 'YAML bridge not loaded')
        importlib.invalidate_caches()
        check(importlib.util.find_spec('pillow_heif') is None, 'the bridge alone installed pillow_heif')
        albums = sys.modules['custom_components.domosenso_frame.albums']
        config_flow = sys.modules.get('custom_components.domosenso_frame.config_flow') or importlib.import_module('custom_components.domosenso_frame.config_flow')
        albums.STARTUP_DELAY_S = 3600  # the startup sync is exercised on its own below
        issues = ir.async_get(hass)
        store_path = Path(hass.config.path('.storage', albums.STORE_KEY))

        share = Share(PASSPHRASE, PASSWORD, 'Summer Trip', JUNE)
        for n, color in ((101, (200, 0, 0)), (102, (0, 0, 200)), (103, (200, 200, 0)), (104, (0, 200, 200))):
            share.add(n, f'IMG_{n:04d}.JPG', jpeg(color))
        share.add(106, 'MOV_0106.MOV', b'not a photo', kind='video')
        share2 = Share(PASSPHRASE2, None, 'Winter Walk', JANUARY)
        share2.add(201, 'IMG_0201.JPG', jpeg((90, 90, 90)))
        share2.add(202, 'IMG_0202.JPG', jpeg((30, 140, 70)))
        fake.shares = {PASSPHRASE: share, PASSPHRASE2: share2}

        photo_dir = Path(config_dir, 'www', 'ramka')
        hand = photo_dir / '2026' / '06' / 'Summer Trip'
        hand.mkdir(parents=True)
        (hand / 'hand.jpg').write_bytes(jpeg((1, 2, 3)))
        hand_before = tree(hand)

        # Config flow: a YAML bridge leaves only the albums path.
        result = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'user'})
        check(result['type'] == 'form' and result['step_id'] == 'albums', f'YAML bridge did not lead to the albums step: {result.get("step_id")}')
        bridge = config_flow.FrameConfigFlow()
        bridge.hass, bridge.handler, bridge.flow_id, bridge.context = hass, DOMAIN, 'probe', {'source': 'user'}
        aborted = await bridge.async_step_bridge()
        check(aborted['type'] == 'abort' and aborted['reason'] == 'yaml_configured', 'bridge path did not abort yaml_configured')
        result = await hass.config_entries.flow.async_configure(result['flow_id'], {'photo_dir': 'relative/ramka'})
        check(result['type'] == 'form' and result['errors'] == {'photo_dir': 'invalid_photo_dir'}, 'relative photo_dir accepted')
        result = await hass.config_entries.flow.async_configure(result['flow_id'], {'photo_dir': str(photo_dir)})
        check(result['type'] == 'create_entry' and result['data'] == {'kind': 'albums', 'photo_dir': str(photo_dir)}, 'albums entry not created')
        entry = result['result']
        await hass.async_block_till_done()
        check(entry.state.value == 'loaded', 'albums entry not loaded')
        check(hass.data.get(DOMAIN, {}).get('source') == 'yaml', 'YAML bridge lost after albums entry')
        # pillow-heif comes from the albums entry setup, in the background, not from the manifest.
        await asyncio.wait_for(asyncio.shield(hass.data[albums.DATA].heif_install), 300)
        importlib.invalidate_caches()
        if OFFLINE:
            check(importlib.util.find_spec('pillow_heif') is None, 'pillow_heif installed without network')
            share.add(105, 'IMG_0105.HEIC', FAKE_HEIC)
        else:
            check(importlib.util.find_spec('pillow_heif') is not None, 'albums setup did not install pillow_heif')
            share.add(105, 'IMG_0105.HEIC', heic((120, 60, 200)))
        again = await hass.config_entries.flow.async_init(DOMAIN, context={'source': 'user'})
        check(again['type'] == 'abort' and again['reason'] == 'already_configured', 'second albums entry not aborted')

        def manager():
            return hass.data[albums.DATA]

        def sensor_count():
            state = hass.states.get('sensor.domosenso_frame_albums')
            check(state is not None, 'sensor.domosenso_frame_albums missing')
            return int(state.state)

        async def check_sensor():
            await hass.async_block_till_done()
            check(sensor_count() == len(manager().albums), f'sensor {sensor_count()} != {len(manager().albums)} albums')

        await check_sensor()
        check(sensor_count() == 0, 'sensor not 0 before albums')

        async def options(step):
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            check(flow['type'] == 'menu' and set(flow['menu_options']) == {'add_album', 'remove_album', 'sync_now', 'settings'}, 'options menu wrong')
            return await hass.config_entries.options.async_configure(flow['flow_id'], {'next_step_id': step})

        # Settings: host map validation, GB <-> bytes, and an options change reloads the entry.
        form = await options('settings')
        values = {'interval_min': 60, 'max_edge': 2560, 'quality': 85, 'free_floor_gb': 0.5}
        bad = await hass.config_entries.options.async_configure(form['flow_id'], {**values, 'host_map': 'photos.invalid -> 127.0.0.1'})
        check(bad['type'] == 'form' and bad['errors'] == {'host_map': 'invalid_host_map'}, 'invalid host map accepted')
        before = manager()
        host_map = f'http://photos.invalid -> http://127.0.0.1:{port}'
        done = await hass.config_entries.options.async_configure(form['flow_id'], {**values, 'host_map': host_map})
        check(done['type'] == 'create_entry', 'settings not saved')
        check(entry.options == {'interval_min': 60, 'max_edge': 2560, 'quality': 85, 'free_floor': 500_000_000,
                                'host_map': [{'from': 'http://photos.invalid', 'to': f'http://127.0.0.1:{port}'}]}, f'options wrong: {entry.options}')
        await wait_for(lambda: hass.data.get(albums.DATA) not in (None, before), 'options change did not reload the entry')
        await hass.async_block_till_done()
        check(manager().host_map == entry.options['host_map'] and manager().options.free_floor == 500_000_000, 'reloaded manager ignores options')
        form = await options('settings')
        fields = {key.schema: key for key in form['data_schema'].schema}
        check(fields['free_floor_gb'].default() == 0.5, 'free floor not shown back in GB')
        check(fields['host_map'].description['suggested_value'] == host_map, 'host map not shown back')
        hass.config_entries.options.async_abort(form['flow_id'])

        if OFFLINE:
            form = await options('add_album')
            done = await hass.config_entries.options.async_configure(form['flow_id'], {'url': SHARE_URL, 'password': PASSWORD})
            check(done['type'] == 'create_entry', f'offline options add failed: {done.get("errors")}')
            (rec,) = manager().public_list()
            check(rec['status'] == 'ok' and rec['photos'] == 5 and rec['heic'] == 'thumbnails' and rec['mode'] == 'originals',
                  f'offline HEIC did not fall back to the thumbnail: {rec}')
            with Image.open(photo_dir / rec['path'] / 'IMG_0105_105.jpg') as thumb:
                check(thumb.size == (32, 24), 'offline HEIC item is not the thumbnail')
            check(entry.state.value == 'loaded' and hass.data.get(DOMAIN, {}).get('source') == 'yaml',
                  'bridge or albums entry not loaded without pillow-heif')
            print(json.dumps({'bridge': 'yaml', 'albums_entry': 'loaded', 'pillow_heif': 'install-failed',
                              'heic': rec['heic']}))
            return

        # Options add: wrong password and a share error keep nothing; a good add does not reload the entry.
        current = manager()
        form = await options('add_album')
        wrong = await hass.config_entries.options.async_configure(form['flow_id'], {'url': SHARE_URL, 'password': 'wrong'})
        check(wrong['type'] == 'form' and wrong['errors'] == {'base': 'wrong_password'}, f'wrong password not reported: {wrong.get("errors")}')
        check(not manager().albums and not (photo_dir / '2026/06/Summer Trip-2').exists(), 'failed options add left an album')
        missing = await hass.config_entries.options.async_configure(wrong['flow_id'], {'url': 'http://photos.invalid/photo/mo/sharing/Missing3'})
        check(missing['type'] == 'form' and missing['errors'] == {'base': 'share_error'}, f'share error not reported: {missing.get("errors")}')
        check(not manager().albums, 'options add with a share error left an album')
        added = await hass.config_entries.options.async_configure(missing['flow_id'], {'url': SHARE_URL2})
        check(added['type'] == 'create_entry', f'options add failed: {added.get("errors")}')
        await hass.async_block_till_done()
        check(manager() is current, 'album action reloaded the entry')
        (winter_id, winter), = manager().albums.items()
        check(winter['status'] == 'ok' and winter['path'] == '2026/01/Winter Walk' and winter['photos'] == 2, f'options add record wrong: {winter}')
        await check_sensor()

        admin = await hass.auth.async_create_user('Demo admin', group_ids=['system-admin'])
        viewer = await hass.auth.async_create_user('Demo viewer', group_ids=['system-users'])
        as_admin = Context(user_id=admin.id)

        async def call(service, user=as_admin, **data):
            return await hass.services.async_call(DOMAIN, service, data, blocking=True, return_response=True, context=user)

        try:
            await call('album_sync', Context(user_id=viewer.id))
        except Unauthorized:
            pass
        else:
            raise AssertionError('non-admin album_sync was not refused')
        for url, code in (('http://photos.invalid/not-a-share', 'invalid_link'), (SHARE_URL2, 'already_added')):
            try:
                await call('album_add', url=url)
            except ServiceValidationError as err:
                check(err.translation_key == code, f'album_add {url}: {err.translation_key} != {code}')
            else:
                raise AssertionError(f'album_add {url} not refused')

        # album_add takes no password (service data is recorded); a password link comes back auth_failed.
        try:
            await call('album_add', url=SHARE_URL, password=PASSWORD)
        except vol.Invalid as err:
            check('password' in str(err), f'album_add password refused for another reason: {err}')
        else:
            raise AssertionError('album_add accepted a password')
        response = await call('album_add', url=SHARE_URL)
        locked = response['album']
        check(locked['status'] == 'auth_failed' and locked['error'] == 'wrong_password', f'password link via album_add: {locked}')
        check('password' not in locked and 'url' not in locked, 'album_add response leaks credentials')
        check(await call('album_remove', album_id=locked['id']) == {'removed': True}, 'auth_failed album not removed')

        # Options add with the password: the hand folder holds the wanted path, so the album takes -2.
        form = await options('add_album')
        done = await hass.config_entries.options.async_configure(form['flow_id'], {'url': SHARE_URL, 'password': PASSWORD})
        check(done['type'] == 'create_entry', f'options add with password failed: {done.get("errors")}')
        aid, = (rid for rid in manager().albums if rid != winter_id)
        album = manager().public(manager().albums[aid])
        check(album['status'] == 'ok' and album['path'] == '2026/06/Summer Trip-2' and album['photos'] == 5, f'album_add result wrong: {album}')
        check(album['mode'] == 'originals' and album['heic'] == 'converted' and album['skipped_count'] == 0, f'album_add mode/heic wrong: {album}')
        folder = photo_dir / album['path']
        names = photos(folder)
        check(names == ['IMG_0101_101.jpg', 'IMG_0102_102.jpg', 'IMG_0103_103.jpg', 'IMG_0104_104.jpg', 'IMG_0105_105.jpg'], f'album files wrong: {names}')
        for name in names:
            check((folder / name).read_bytes()[:2] == b'\xff\xd8', f'{name} is not JPEG')
        with Image.open(folder / 'IMG_0105_105.jpg') as converted:
            check(converted.format == 'JPEG', 'HEIC not converted to JPEG')
            exif = converted.getexif()
            check(exif.get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL) == HEIC_TAKEN, 'HEIC DateTimeOriginal lost')
            check(GPS_IFD not in exif and not exif.get_ifd(GPS_IFD), 'HEIC GPS kept')
        check(tree(hand) == hand_before, 'hand folder changed')
        check(issues.async_get_issue(DOMAIN, f'album_{aid}_path_collision') is not None, 'path collision issue missing')
        check(fake.agents == {'domosenso-frame/0.6.0'}, f'unexpected User-Agent {fake.agents}')
        await check_sensor()
        check(sensor_count() == 2, 'sensor not 2 after two adds')

        # Options remove / sync_now offer both albums.
        for step in ('remove_album', 'sync_now'):
            form = await options(step)
            fields = {key.schema: value for key, value in form['data_schema'].schema.items()}
            offered = {o['value']: o['label'] for o in fields['album_id'].config['options']}
            check(offered == {winter_id: 'Winter Walk', aid: 'Summer Trip'}, f'{step} choices wrong: {offered}')
            if step == 'remove_album':
                hass.config_entries.options.async_abort(form['flow_id'])
        logins = fake.logins
        done = await hass.config_entries.options.async_configure(form['flow_id'], {'album_id': winter_id})
        check(done['type'] == 'create_entry', 'sync_now not accepted')
        await hass.async_block_till_done()
        check(fake.logins == logins + 1 and manager() is current, 'sync_now did not sync once without reload')

        # A second sync changes nothing.
        files = tree(photo_dir)
        stable = {k: v for k, v in manager().albums[aid].items() if k not in ('last_sync', 'last_full_list')}
        result = (await call('album_sync', album_id=aid))['albums']
        check([a['status'] for a in result] == ['ok'], f'second sync: {result}')
        after = tree(photo_dir)
        changed = sorted(k for k in set(files) | set(after) if files.get(k) != after.get(k))
        # Each full sync rewrites the marker's last_sync; everything else must stay byte-identical.
        check(changed in ([], [f'{album["path"]}/.sync.json']), f'second sync changed files: {changed}')
        def marker(raw):
            return {k: v for k, v in json.loads(raw).items() if k != 'last_sync'}
        check(marker(after[f'{album["path"]}/.sync.json'][2]) == marker(files[f'{album["path"]}/.sync.json'][2]), 'second sync changed the marker')
        check({k: v for k, v in manager().albums[aid].items() if k not in ('last_sync', 'last_full_list')} == stable, 'second sync changed the record')

        # One remote removal deletes one file.
        share.remove(101)
        await call('album_sync', album_id=aid)
        check(photos(folder) == names[1:], f'one removal: {photos(folder)}')

        # Removing 3 of 4 holds; confirm_deletions clears the hold and the issue.
        share.remove(102, 103, 104)
        result = (await call('album_sync', album_id=aid))['albums'][0]
        check(result['status'] == 'awaiting_confirmation', f'hold not applied: {result["status"]}')
        check(photos(folder) == names[1:], 'held sync deleted files')
        hold_issue = f'album_{aid}_awaiting_confirmation'
        check(issues.async_get_issue(DOMAIN, hold_issue) is not None, 'awaiting_confirmation issue missing')
        # confirm_deletions on all albums: only the hold that existed before the call is released;
        # Winter Walk empties now, enters a hold in the same run and keeps it.
        winter_items = dict(share2.items)
        share2.remove(201, 202)
        winter_folder = photo_dir / '2026/01/Winter Walk'
        winter_files = tree(winter_folder)
        results = {a['id']: a for a in (await call('album_sync', confirm_deletions=True))['albums']}
        result = results[aid]
        check(result['status'] == 'ok' and photos(folder) == ['IMG_0105_105.jpg'], f'confirm failed: {result["status"]} {photos(folder)}')
        check(issues.async_get_issue(DOMAIN, hold_issue) is None, 'awaiting_confirmation issue not removed')
        check(results[winter_id]['status'] == 'awaiting_confirmation', f'new hold confirmed in the same call: {results[winter_id]}')
        check(photos(winter_folder) == ['IMG_0201_201.jpg', 'IMG_0202_202.jpg'], 'new hold deleted files')
        share2.items.update(winter_items)
        share2.version += 1
        result = (await call('album_sync', album_id=winter_id))['albums'][0]
        check(result['status'] == 'ok' and tree(winter_folder).keys() == winter_files.keys(), f'winter not back to ok: {result}')

        # Wrong password: auth_failed, files kept.
        share.password = 'changed'
        result = (await call('album_sync', album_id=aid))['albums'][0]
        check(result['status'] == 'auth_failed' and photos(folder) == ['IMG_0105_105.jpg'], f'auth failure: {result}')
        share.password = PASSWORD

        # Downloads disabled (804): new photos come as thumbnails.
        share.no_download = True
        share.add(107, 'IMG_0107.JPG', jpeg((250, 120, 0)))
        share.add(108, 'IMG_0108.JPG', jpeg((0, 120, 250)))
        result = (await call('album_sync', album_id=aid))['albums'][0]
        check(result['status'] == 'ok' and result['mode'] == 'thumbnails', f'804 not handled: {result}')
        check(photos(folder) == ['IMG_0105_105.jpg', 'IMG_0107_107.jpg', 'IMG_0108_108.jpg'], f'thumbnail files: {photos(folder)}')
        with Image.open(folder / 'IMG_0107_107.jpg') as thumb:
            check(thumb.size == (32, 24), 'thumbnail mode did not use the thumbnail')
        share.no_download = False

        # Remote rename moves the folder; the hand folder stays and the collision clears.
        share.name, share.version = 'Summer Trip Renamed', share.version + 1
        result = (await call('album_sync', album_id=aid))['albums'][0]
        check(result['status'] == 'ok' and result['path'] == '2026/06/Summer Trip Renamed', f'rename: {result}')
        check(not folder.exists(), 'old folder left after rename')
        folder = photo_dir / result['path']
        check(len(photos(folder)) == 3 and tree(hand) == hand_before, 'rename lost photos or touched the hand folder')
        check(issues.async_get_issue(DOMAIN, f'album_{aid}_path_collision') is None, 'collision issue kept after rename')

        # Two concurrent syncs: one reports syncing.
        fake.delay = 0.3
        first, second = await asyncio.gather(call('album_sync', album_id=aid), call('album_sync', album_id=aid))
        statuses = sorted(r['albums'][0]['status'] for r in (first, second))
        check(statuses == ['ok', 'syncing'], f'concurrent syncs: {statuses}')
        # Two albums at once: the second waits for the first (one sync at a time), and both finish.
        fake.max_in_flight = 0
        first, second = await asyncio.gather(call('album_sync', album_id=aid), call('album_sync', album_id=winter_id))
        check([r['albums'][0]['status'] for r in (first, second)] == ['ok', 'ok'], f'two albums: {first} {second}')
        check(fake.max_in_flight == 1, f'two albums synced in parallel: {fake.max_in_flight} requests at once')
        fake.delay = 0.0

        def leaks():
            found = []
            for path in photo_dir.rglob('*'):
                if path.is_file():
                    data = path.read_bytes()
                    found += [f'{path.relative_to(photo_dir)}: {word.decode()}' for word in (PASSWORD.encode(), PASSPHRASE.encode()) if word in data]
            return found

        check(not leaks(), f'secrets under photo_dir: {leaks()}')

        # Startup sync settles an interrupted record.
        check(await hass.config_entries.async_unload(entry.entry_id), 'unload failed')
        data = json.loads(store_path.read_text())['data']
        for rec in data['albums']:
            if rec['id'] == aid:
                rec['status'] = 'syncing'
        await Store(hass, 1, albums.STORE_KEY).async_save(data)
        albums.STARTUP_DELAY_S = 1
        check(await hass.config_entries.async_setup(entry.entry_id), 'setup after unload failed')
        albums.STARTUP_DELAY_S = 3600
        check(manager().albums[aid]['status'] == 'error' and manager().albums[aid]['error'] == 'interrupted', 'syncing record not marked interrupted')
        await wait_for(lambda: manager().albums[aid]['status'] == 'ok', 'startup sync did not bring the record back to ok')

        # Unload during a running sync leaves the store unchanged.
        share.add(109, 'IMG_0109.JPG', jpeg((60, 60, 160)))
        fake.delay = 1.0
        stored = store_path.read_bytes()

        async def background_sync():
            try:
                return await call('album_sync', album_id=aid)
            except BaseException as err:  # noqa: BLE001 - cancelled by the unload
                return err

        running = asyncio.ensure_future(background_sync())
        await wait_for(lambda: manager().locks.get(aid) is not None and manager().locks[aid].locked(), 'sync did not start')
        check(await hass.config_entries.async_unload(entry.entry_id), 'unload during sync failed')
        outcome = await running
        check(isinstance(outcome, BaseException), f'sync finished instead of being cancelled: {outcome}')
        check(store_path.read_bytes() == stored, 'store changed by a sync cancelled on unload')
        fake.delay = 0.0
        check(await hass.config_entries.async_setup(entry.entry_id), 'setup after cancelled sync failed')
        await hass.async_block_till_done()
        await check_sensor()

        # album_remove deletes only its own folder.
        winter_folder = photo_dir / '2026/01/Winter Walk'
        winter_before = tree(winter_folder)
        response = await call('album_remove', album_id=aid)
        check(response == {'removed': True}, f'album_remove: {response}')
        check(not folder.exists() and tree(winter_folder) == winter_before and tree(hand) == hand_before, 'album_remove touched other folders')
        await check_sensor()
        check(sensor_count() == 1, 'sensor not 1 after remove')

        # Options remove of the last album, without reload.
        current = manager()
        form = await options('remove_album')
        done = await hass.config_entries.options.async_configure(form['flow_id'], {'album_id': winter_id})
        check(done['type'] == 'create_entry' and not winter_folder.exists() and manager() is current, 'options remove failed')
        await check_sensor()
        check(sensor_count() == 0, 'sensor not 0 after removing all')
        check(not leaks(), f'secrets under photo_dir: {leaks()}')

        # Removing the entry deletes the registry with the passwords; photo folders stay.
        check(store_path.exists(), 'album store missing before entry removal')
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        check(not store_path.exists(), 'album store kept after entry removal')
        check(tree(hand) == hand_before, 'entry removal touched the hand folder')
        print(json.dumps({'bridge': 'yaml', 'config_flow': 'ok', 'options': 'ok', 'pillow_heif': 'installed-by-albums-entry',
                          'sync': 'ok', 'holds': 'ok', 'errors': 'ok', 'lifecycle': 'ok'}))
    finally:
        await hass.async_stop(force=True)
        await runner.cleanup()


if __name__ == '__main__':
    try:
        asyncio.run(main())
        print('albums smoke ok')
    except AssertionError as err:
        print(f'albums smoke failed: {err}')
        raise SystemExit(1) from None
    except Exception:
        traceback.print_exc()
        print('albums smoke failed: unexpected exception (fictional test data only)')
        raise SystemExit(1) from None
