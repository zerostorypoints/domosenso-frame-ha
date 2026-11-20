"""Synology Photos share link parsing and host mapping for album sync."""
from __future__ import annotations

import re
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
    origin = f'{parts.scheme}://{parts.netloc}'
    return ShareLink(origin, origin + prefix, passphrase)


def api_base(link: ShareLink, host_map: list[dict[str, str]]) -> str:
    for entry in host_map:
        if entry.get('from') == link.origin:
            return entry['to'] + link.base[len(link.origin):]
    return link.base
