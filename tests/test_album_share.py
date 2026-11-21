"""Share link parsing and host map for album sync (no Home Assistant import)."""
import asyncio
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

pkg = types.ModuleType('album_share_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg


class _Share:
    def __getattr__(self, name):
        if name.startswith('__') or name == 'pytestmark':
            raise AttributeError(name)
        try:
            return getattr(importlib.import_module('album_share_pkg.album_share'), name)
        except ModuleNotFoundError:
            pytest.fail('album_share module is missing')


share = _Share()


def test_parse_photo_prefix():
    link = share.parse_share_url('https://photos.example.org/photo/mo/sharing/blAbC123')
    assert link == share.ShareLink('https://photos.example.org', 'https://photos.example.org/photo', 'blAbC123')


def test_parse_ignores_trailing_slash_query_fragment():
    assert share.parse_share_url('http://nas.example:5000/photo/mo/sharing/Xy9/?x=1#/').passphrase == 'Xy9'


@pytest.mark.parametrize('bad', ['https://example.org/photo/', 'ftp://h/photo/mo/sharing/a', 'https://h/mo/sharing/a-b', ''])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        share.parse_share_url(bad)


def test_host_map_rewrites_matching_origin_only():
    link = share.parse_share_url('https://photos.example.org/photo/mo/sharing/a1')
    m = [{'from': 'https://other.example', 'to': 'http://10.0.0.2:5000'}, {'from': 'https://photos.example.org', 'to': 'http://10.0.0.9:5000'}]
    assert share.api_base(link, m) == 'http://10.0.0.9:5000/photo'
    assert share.api_base(link, []) == 'https://photos.example.org/photo'


def test_parse_rejects_userinfo():
    with pytest.raises(ValueError):
        share.parse_share_url('https://user:pw@photos.example.org/photo/mo/sharing/a1')


def test_parse_lowercases_host():
    link = share.parse_share_url('https://Photos.Example.org/photo/mo/sharing/a1')
    assert (link.origin, link.base) == ('https://photos.example.org', 'https://photos.example.org/photo')


BASE = 'http://10.0.0.9:5000/photo'
CGI = BASE + '/mo/sharing/webapi/entry.cgi'
OK_LOGIN = (200, 'application/json', json.dumps({'success': True, 'data': {'sharing_sid': 'S'}}).encode())


def _json(data=None, error=None):
    body = {'success': error is None}
    body['error' if error else 'data'] = {'code': error} if error else data
    return (200, 'application/json', json.dumps(body).encode())


class FakeTransport:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, method, url, params=None, data=None, headers=None):
        self.calls.append({'method': method, 'url': url, 'params': params, 'data': data, 'headers': headers or {}})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _client(*replies, password='pw'):
    t = FakeTransport(*replies)
    return t, share.ShareClient(t, BASE, 'p1', password, share.USER_AGENT)


def test_login_quotes_values_and_keeps_sid():
    async def run():
        t, c = _client(OK_LOGIN, _json({'list': [{'id': 8, 'name': 'x', 'item_count': 0, 'start_time': 1, 'version': 2}]}))
        await c.login()
        await c.album()
        return t.calls
    login, album = asyncio.run(run())
    assert (login['method'], login['url']) == ('POST', CGI)
    assert login['data'] == {'api': 'SYNO.Core.Sharing.Login', 'method': 'login', 'version': '1',
                             'sharing_id': '"p1"', 'password': '"pw"'}
    assert album['headers']['X-SYNO-SHARING'] == 'p1'
    assert album['headers']['Cookie'] == 'sharing_sid=S'
    assert album['data']['passphrase'] == '"p1"'


def test_login_has_no_sharing_header():
    async def run():
        t, c = _client(OK_LOGIN)
        await c.login()
        return t.calls[0]['headers']
    headers = asyncio.run(run())
    assert 'X-SYNO-SHARING' not in headers and 'Cookie' not in headers


def test_every_call_sends_user_agent():
    async def run():
        t, c = _client(OK_LOGIN, _json({'list': [{'id': 1, 'name': 'x', 'item_count': 0, 'start_time': 1, 'version': 2}]}),
                       _json({'list': []}), (200, 'image/jpeg', b'x'), (200, 'image/jpeg', b'y'))
        item = share.RemoteItem(1, 'a.jpg', 1, 1, 'photo', 'k')
        await c.login(); await c.album(); await c.items(); await c.original(item); await c.thumbnail(item)
        return t.calls
    calls = asyncio.run(run())
    assert len(calls) == 5
    assert all(call['headers']['User-Agent'] == 'domosenso-frame/0.6.0' for call in calls)


def test_wrong_password_raises_auth_failed():
    async def run():
        _, c = _client(_json(error=1001))
        await c.login()
    with pytest.raises(share.AuthFailed):
        asyncio.run(run())


def test_album_without_session_raises_share_error():
    async def run():
        _, c = _client(_json(error=101))
        await c.album()
    with pytest.raises(share.ShareError) as e:
        asyncio.run(run())
    assert e.value.code == 101 and not isinstance(e.value, share.AuthFailed)


def test_album_fields():
    album = {'id': 8, 'name': 'trip', 'item_count': 1, 'start_time': 1198695901, 'end_time': 1198695999, 'version': 463772, 'passphrase': 'p1'}
    async def run():
        t, c = _client(_json({'list': [album]}))
        return await c.album(), t.calls[0]
    result, call = asyncio.run(run())
    assert result == share.RemoteAlbum(8, 'trip', 1, 1198695901, 463772)
    assert call['data']['api'] == 'SYNO.Foto.Browse.Album' and call['data']['method'] == 'get' and call['data']['version'] == '1'


def _page(n, start):
    return _json({'list': [{'id': start + i, 'filename': f'{start + i}.jpg', 'filesize': 10, 'time': 5, 'type': 'photo',
                            'additional': {'thumbnail': {'cache_key': f'k{start + i}'}}} for i in range(n)]})


def test_items_pages_until_short_page():
    async def run():
        t, c = _client(_page(500, 0), _page(500, 500), _page(7, 1000))
        return await c.items(), t.calls
    items, calls = asyncio.run(run())
    assert len(items) == 1007 and items[0] == share.RemoteItem(0, '0.jpg', 10, 5, 'photo', 'k0')
    assert [(c['data']['offset'], c['data']['limit']) for c in calls] == [('0', '500'), ('500', '500'), ('1000', '500')]
    assert calls[0]['data']['additional'] == '["thumbnail","resolution","orientation"]'
    assert calls[0]['data']['api'] == 'SYNO.Foto.Browse.Item'


def _item():
    return share.RemoteItem(42, 'a.jpg', 3, 5, 'photo', 'ck')


def test_original_804_raises_no_download():
    async def run():
        _, c = _client(_json(error=804))
        await c.original(_item())
    with pytest.raises(share.NoDownload):
        asyncio.run(run())


def test_original_returns_bytes():
    async def run():
        t, c = _client((200, 'application/force-download', b'JPEGDATA'))
        return await c.original(_item()), t.calls[0]
    body, call = asyncio.run(run())
    assert body == b'JPEGDATA' and call['method'] == 'GET'
    assert call['params'] == {'api': 'SYNO.Foto.Download', 'method': 'download', 'version': '2',
                              'force_download': 'true', 'unit_id': '[42]', 'passphrase': 'p1'}


def test_thumbnail_params():
    async def run():
        t, c = _client((200, 'image/jpeg', b'T'))
        return await c.thumbnail(_item()), t.calls[0]
    body, call = asyncio.run(run())
    assert body == b'T'
    assert call['params'] == {'api': 'SYNO.Foto.Thumbnail', 'method': 'get', 'version': '2', 'id': '42',
                              'cache_key': 'ck', 'type': 'unit', 'size': 'xl', 'passphrase': 'p1'}


def test_network_error_is_unreachable():
    async def run():
        _, c = _client(OSError('down'))
        await c.login()
    with pytest.raises(share.Unreachable):
        asyncio.run(run())


def test_non_200_is_unreachable():
    async def run():
        _, c = _client((502, 'text/html', b'bad'))
        await c.login()
    with pytest.raises(share.Unreachable):
        asyncio.run(run())


@pytest.mark.parametrize('reply', [
    (200, 'Application/JSON; charset=utf-8', json.dumps({'success': False, 'error': {'code': 804}}).encode()),
    (200, 'application/octet-stream', json.dumps({'success': False, 'error': {'code': 804}}).encode()),
])
def test_original_error_reply_detected_by_type_or_body(reply):
    async def run():
        _, c = _client(reply)
        await c.original(_item())
    with pytest.raises(share.NoDownload):
        asyncio.run(run())


def test_original_body_starting_with_brace_but_not_an_error_is_kept():
    async def run():
        _, c = _client((200, 'application/force-download', b'{not json at all'),
                       (200, 'application/force-download', b'{"success": true}'))
        return await c.original(_item()), await c.original(_item())
    assert asyncio.run(run()) == (b'{not json at all', b'{"success": true}')


def test_redirect_is_unreachable_with_code():
    async def run():
        _, c = _client((302, 'text/html', b''))
        await c.login()
    with pytest.raises(share.Unreachable) as e:
        asyncio.run(run())
    assert e.value.code == 'redirect'
