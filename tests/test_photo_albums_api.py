"""Photo album commands for paired frames: fictional facade, states and script; no HA needed."""
import logging
from types import SimpleNamespace
import pytest
from test_controls import module, run
from test_control_api import Connection

PASSWORD = 'pw-Velvet-Otter-42'
URL = 'https://photos.example.org/mo/sharing/AbCdEf'
CODES = ['invalid_link', 'already_added', 'wrong_password', 'unreachable', 'unknown_album']


class AlbumError(Exception):
    def __init__(self, code):
        super().__init__(code); self.code = code


class Facade:
    version = 1
    def __init__(self):
        self.fail = None; self.calls = []
        self.items = [{'id': 'a1', 'name': 'Trip', 'path': '2007/12/trip', 'state': 'synced'}]
    def albums(self): return self.items
    async def check(self, url, password):
        self.calls.append('check')
        if self.fail: raise self.fail
        return 'Trip'
    async def add(self, url, password):
        self.calls.append('add'); return {'id': 'a2', 'name': None}
    async def remove(self, album_id):
        if self.fail: raise self.fail
        return 'removed'
    async def sync(self, album_id):
        if self.fail: raise self.fail


class Rig:
    def __init__(self, opt_in=True, ramka=True, script=True):
        self.facade = Facade(); self.allowed = True; self.script_calls = []
        self.endpoint = {'id': 'frame', 'name': 'Frame'}
        if opt_in: self.endpoint['photo_albums'] = {'entity': 'sensor.ramka_photo_albums', 'script': 'script.ramka_photo_albums_set'}
        self.states = {}
        if ramka: self.states['sensor.ramka_photo_albums'] = SimpleNamespace(attributes={'available': ['2007', '2026'], 'chosen': [], 'all': True})
        self.scripts = {'ramka_photo_albums_set'} if script else set()
        async def call(domain, service, data, blocking, context):
            assert blocking and context.id == 'context'
            self.script_calls.append((domain, service, data))
        self.hass = SimpleNamespace(data={'domosenso_photos_albums': SimpleNamespace(frame_api=self.facade)},
            states=SimpleNamespace(get=self.states.get),
            services=SimpleNamespace(async_call=call, has_service=lambda d, n: d == 'script' and n in self.scripts))
        self.c = Connection()
    def grant(self, h, c):
        if not self.allowed: raise ValueError('revoked')
        return {'deviceId': 'tablet', 'endpoint': 'frame'}, self.endpoint
    def call(self, name, **fields):
        api = module('photo_albums_api')
        msg = {'id': 1, 'type': 'domosenso_frame/' + name, 'version': 1, 'endpoint': 'frame', 'deviceId': 'tablet'} | fields
        run(lambda: api.COMMANDS[name](self.hass, self.c, msg, self.grant))
        return self.c.results[-1] if self.c.results else None
    def code(self): return self.c.errors[-1][0]


def test_requires_grant_and_opt_in():
    r = Rig(); r.allowed = False; r.call('photo_albums'); assert r.code() == 'frame_denied' and not r.c.results
    r = Rig(opt_in=False); r.call('photo_albums'); assert r.code() == 'frame_denied'
    r = Rig(); r.call('photo_albums', deviceId='other'); assert r.code() == 'frame_denied'
    r = Rig(); r.call('photo_albums', endpoint='kitchen'); assert r.code() == 'frame_denied'
    r = Rig(opt_in=False); r.call('photo_album_add', url=URL); assert r.code() == 'frame_denied' and not r.facade.calls


def test_list_with_ramka():
    r = Rig(); assert r.call('photo_albums') == {'albums': r.facade.items, 'ramka': {'available': ['2007', '2026'], 'chosen': [], 'all': True}}
    r = Rig(ramka=False); assert r.call('photo_albums')['ramka'] is None


@pytest.mark.parametrize('code', CODES[:4])
def test_add_maps_codes(code):
    r = Rig(); r.facade.fail = AlbumError(code); r.call('photo_album_add', url=URL)
    assert r.code() == code and r.facade.calls == ['check']
    r = Rig(); assert r.call('photo_album_add', url=URL) == {'id': 'a2', 'name': 'Trip'} and r.facade.calls == ['check', 'add']


def test_unexpected_failure_is_photos_unavailable_without_text():
    r = Rig(); r.facade.fail = RuntimeError('secret detail'); r.call('photo_album_add', url=URL)
    assert r.code() == 'photos_unavailable' and 'secret detail' not in repr(r.c.errors)


def test_password_never_leaks(caplog):
    caplog.set_level(logging.DEBUG)
    seen = []
    for fail in [None, *[AlbumError(c) for c in CODES], RuntimeError('boom'), RuntimeError(PASSWORD)]:
        r = Rig(); r.facade.fail = fail; r.call('photo_album_add', url=URL, password=PASSWORD); seen.append((r.c.results, r.c.errors))
    r = Rig(); r.call('photo_album_add', url=URL, password=PASSWORD + 'x' * 300); seen.append((r.c.results, r.c.errors))
    assert PASSWORD not in repr(seen) and PASSWORD not in caplog.text and all(PASSWORD not in m for m in caplog.messages)


def test_overlong_input_refused_without_echo():
    r = Rig(); long = 'https://photos.example.org/' + 'x' * 2973; r.call('photo_album_add', url=long)
    assert r.code() == 'invalid_link' and long not in repr(r.c.errors) and not r.facade.calls
    r = Rig(); r.call('photo_album_add', url=URL, password='p' * 257); assert r.code() == 'wrong_password' and not r.facade.calls
    r = Rig(); r.call('photo_album_remove', albumId='a' * 65); assert r.code() == 'unknown_album'


def test_unknown_album():
    for name in ('photo_album_remove', 'photo_album_sync'):
        r = Rig(); r.facade.fail = AlbumError('unknown_album'); r.call(name, albumId='gone'); assert r.code() == 'unknown_album'
    r = Rig(); assert r.call('photo_album_remove', albumId='a1') == {'status': 'removed'}
    r = Rig(); assert r.call('photo_album_sync', albumId='a1') == {'status': 'started'}


def test_unavailable_photos():
    fields = {'photo_albums': {}, 'photo_album_add': {'url': URL}, 'photo_album_remove': {'albumId': 'a1'},
              'photo_album_sync': {'albumId': 'a1'}, 'photo_albums_choose': {'chosen': [], 'all': True}}
    for broken in ('missing', 'version'):
        for name, extra in fields.items():
            r = Rig()
            if broken == 'missing': r.hass.data.clear()
            else: r.facade.version = 2
            r.call(name, **extra); assert r.code() == 'photos_unavailable' and not r.c.results and not r.script_calls


def test_choose_validation():
    r = Rig(); assert r.call('photo_albums_choose', chosen=['2007'], all=False) == {'status': 'saved'}
    assert r.script_calls == [('script', 'ramka_photo_albums_set', {'chosen': ['2007'], 'all_albums': False})]
    r = Rig(); r.states['sensor.ramka_photo_albums'].attributes['chosen'] = ['1999']
    assert r.call('photo_albums_choose', chosen=['1999', '2026'], all=False) == {'status': 'saved'}
    r = Rig(); r.call('photo_albums_choose', chosen=['1999'], all=False); assert r.code() == 'invalid_choice' and not r.script_calls
    for bad in ({'chosen': ['x' * 201], 'all': False}, {'chosen': ['2007'] * 501, 'all': False}, {'chosen': [7], 'all': False}, {'chosen': [], 'all': 'yes'}):
        r = Rig(); r.call('photo_albums_choose', **bad); assert r.code() == 'invalid_choice' and not r.script_calls
    r = Rig(script=False); r.call('photo_albums_choose', chosen=[], all=True); assert r.code() == 'ramka_unavailable'
    r = Rig(ramka=False); r.call('photo_albums_choose', chosen=[], all=True); assert r.code() == 'ramka_unavailable' and not r.script_calls


def test_profile_flag():
    api = module('photo_albums_api')
    r = Rig(); assert api.profile_fields(r.hass, r.endpoint) == {'photoAlbumsVersion': 1}
    r = Rig(opt_in=False); assert api.profile_fields(r.hass, r.endpoint) == {}
    r = Rig(); r.facade.version = 2; assert api.profile_fields(r.hass, r.endpoint) == {}
    r = Rig(); r.hass.data.clear(); assert api.profile_fields(r.hass, r.endpoint) == {}


def test_ramka_all_missing_follows_chosen():
    r = Rig(); r.states['sensor.ramka_photo_albums'].attributes.pop('all')
    assert r.call('photo_albums')['ramka']['all'] is True
    r = Rig(); r.states['sensor.ramka_photo_albums'].attributes = {'available': ['2007', '2026'], 'chosen': ['2007']}
    assert r.call('photo_albums')['ramka'] == {'available': ['2007', '2026'], 'chosen': ['2007'], 'all': False}
    r = Rig(); r.states['sensor.ramka_photo_albums'].attributes['all'] = False
    assert r.call('photo_albums')['ramka']['all'] is False


def test_add_error_after_successful_check():
    r = Rig()
    async def taken(url, password):
        r.facade.calls.append('add'); raise AlbumError('already_added')
    r.facade.add = taken; r.call('photo_album_add', url=URL, password=PASSWORD)
    assert r.code() == 'already_added' and r.facade.calls == ['check', 'add'] and not r.c.results


def test_non_string_url_and_password_pass_schema_and_are_refused_without_echo():
    vol = pytest.importorskip('voluptuous')
    fields = module('photo_albums_api').schema_fields(vol)['photo_album_add']
    for url, password, code in ((['x', PASSWORD], 'p', 'invalid_link'), (URL, {'p': PASSWORD}, 'wrong_password'), (7, None, 'invalid_link')):
        msg = vol.Schema(fields)({'version': 1, 'endpoint': 'frame', 'deviceId': 'tablet', 'url': url, 'password': password})
        r = Rig(); r.call('photo_album_add', **{k: msg[k] for k in ('url', 'password')})
        assert r.code() == code and not r.facade.calls and PASSWORD not in repr(r.c.errors)
