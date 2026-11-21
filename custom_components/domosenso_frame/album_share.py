"""Synology Photos share link parsing and host mapping for album sync."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

_MARKER = '/mo/sharing/'
_PASSPHRASE = re.compile(r'[A-Za-z0-9]+')


@dataclass(frozen=True)
class ShareLink:
    origin: str
    base: str
    passphrase: str


def parse_share_url(url: str) -> ShareLink:
    parts = urlsplit(str(url).strip())
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise ValueError('Share link must be an http(s) URL')
    prefix, marker, rest = parts.path.partition(_MARKER)
    passphrase = rest.split('/', 1)[0]
    if not marker or not _PASSPHRASE.fullmatch(passphrase):
        raise ValueError('Not a Synology Photos share link')
    if parts.username is not None or parts.password is not None or '@' in parts.netloc:
        raise ValueError('Share link must not contain credentials')
    origin = f'{parts.scheme}://{parts.netloc.lower()}'
    return ShareLink(origin, origin + prefix, passphrase)


def api_base(link: ShareLink, host_map: list[dict[str, str]]) -> str:
    for entry in host_map:
        if entry.get('from') == link.origin:
            return entry['to'] + link.base[len(link.origin):]
    return link.base


USER_AGENT = 'domosenso-frame/0.6.0'
PAGE_SIZE = 500
MAX_BODY = 200_000_000  # larger responses are refused by the transport
Transport = Callable[..., Awaitable[tuple[int, str, bytes]]]


class ShareError(Exception):
    def __init__(self, code: int | None = None):
        super().__init__(f'Synology share error {code}')
        self.code = code


class AuthFailed(ShareError):
    pass


class NoDownload(ShareError):
    pass


class Unreachable(Exception):
    """Network failure or a reply that is not the share API; `code` names a known cause."""

    def __init__(self, message: str = '', code: str | None = None):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RemoteAlbum:
    id: int
    name: str
    item_count: int
    start_time: int
    version: int


@dataclass(frozen=True)
class RemoteItem:
    id: int
    filename: str
    filesize: int
    time: int
    type: str
    cache_key: str


def _raise_for(code) -> None:
    if code == 1001:
        raise AuthFailed(code)
    if code == 804:
        raise NoDownload(code)
    raise ShareError(code)


class ShareClient:
    def __init__(self, transport: Transport, base: str, passphrase: str, password: str | None,
                 user_agent: str = USER_AGENT):
        self._transport = transport
        self._url = f'{base}/mo/sharing/webapi/entry.cgi'
        self._passphrase = passphrase
        self._password = password
        self._agent = user_agent
        self._sid: str | None = None

    def _headers(self, session: bool = True) -> dict[str, str]:
        headers = {'User-Agent': self._agent}
        if session:
            headers['X-SYNO-SHARING'] = self._passphrase
            if self._sid:
                headers['Cookie'] = f'sharing_sid={self._sid}'
        return headers

    async def _send(self, method, params=None, data=None, session=True) -> tuple[str, bytes]:
        try:
            status, ctype, body = await self._transport(method, self._url, params=params, data=data,
                                                        headers=self._headers(session))
        except (OSError, asyncio.TimeoutError) as err:
            raise Unreachable(str(err) or type(err).__name__) from err
        if 300 <= status < 400:  # redirects are not followed: map the final address in the host map
            raise Unreachable(f'HTTP {status}', 'redirect')
        if status != 200:
            raise Unreachable(f'HTTP {status}')
        return ctype, body

    async def _call(self, data: dict, session: bool = True) -> dict:
        _, body = await self._send('POST', data=data, session=session)
        try:
            reply = json.loads(body)
        except ValueError as err:
            raise Unreachable('Response is not JSON') from err
        if not reply.get('success'):
            _raise_for((reply.get('error') or {}).get('code'))
        return reply.get('data') or {}

    async def _fetch(self, params: dict) -> bytes:
        ctype, body = await self._send('GET', params=params)
        is_json = ctype.lower().startswith('application/json')
        if is_json or body[:1] == b'{':
            try:
                reply = json.loads(body)
            except ValueError:
                reply = None
            if not isinstance(reply, dict):
                reply = None
            if is_json or (reply is not None and reply.get('success') is False):
                _raise_for(((reply or {}).get('error') or {}).get('code'))
        return body

    async def login(self) -> None:
        data = await self._call({
            'api': 'SYNO.Core.Sharing.Login', 'method': 'login', 'version': '1',
            'sharing_id': json.dumps(self._passphrase), 'password': json.dumps(self._password or ''),
        }, session=False)
        self._sid = data.get('sharing_sid')

    async def album(self) -> RemoteAlbum:
        data = await self._call({
            'api': 'SYNO.Foto.Browse.Album', 'method': 'get', 'version': '1',
            'passphrase': json.dumps(self._passphrase),
        })
        a = data['list'][0]
        return RemoteAlbum(a['id'], a['name'], a['item_count'], a['start_time'], a['version'])

    async def items(self) -> list[RemoteItem]:
        items: list[RemoteItem] = []
        while True:
            data = await self._call({
                'api': 'SYNO.Foto.Browse.Item', 'method': 'list', 'version': '1',
                'offset': str(len(items)), 'limit': str(PAGE_SIZE),
                'additional': '["thumbnail","resolution","orientation"]',
                'passphrase': json.dumps(self._passphrase),
            })
            page = data.get('list') or []
            items += [RemoteItem(i['id'], i['filename'], i['filesize'], i['time'], i['type'],
                                 ((i.get('additional') or {}).get('thumbnail') or {}).get('cache_key', ''))
                      for i in page]
            if len(page) < PAGE_SIZE:
                return items

    async def original(self, item: RemoteItem) -> bytes:
        return await self._fetch({
            'api': 'SYNO.Foto.Download', 'method': 'download', 'version': '2',
            'force_download': 'true', 'unit_id': json.dumps([item.id]), 'passphrase': self._passphrase,
        })

    async def thumbnail(self, item: RemoteItem) -> bytes:
        return await self._fetch({
            'api': 'SYNO.Foto.Thumbnail', 'method': 'get', 'version': '2', 'id': str(item.id),
            'cache_key': item.cache_key, 'type': 'unit', 'size': 'xl', 'passphrase': self._passphrase,
        })
