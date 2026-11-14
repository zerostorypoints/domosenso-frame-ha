"""Defensive projection of the optional local HiFiSync provider."""
import copy
import math
from datetime import datetime

KINDS = {'hifisync-system', 'hifisync-room'}


def legacy_config(config):
    result = copy.deepcopy(config)
    for mode in result['modes']:
        screens = []
        for screen in mode['screens']:
            if KINDS.intersection(screen['requires']):
                continue
            screen['widgets'] = [widget for widget in screen['widgets'] if widget['type'] not in KINDS]
            if screen['widgets']:
                screens.append(screen)
        mode['screens'] = screens
    return result


def sanitize(value, now):
    """Accept only a bounded, unexpired v1 presentation payload; never pass extras."""
    try:
        if not isinstance(value, dict) or type(value['stale']) is not bool:
            return None
        until = datetime.fromisoformat(value['validUntil'].replace('Z', '+00:00'))
        if until.tzinfo is None or until <= now:
            return None
        source = value['snapshot']
        if type(source['version']) is not int or source['version'] != 1:
            return None
        def text(v):
            if not isinstance(v, str) or not 1 <= len(v) <= 200:
                raise ValueError('Invalid text')
            return v
        def number(v, nullable=False):
            if nullable and v is None:
                return None
            if type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 10000:
                raise ValueError('Invalid number')
            return v
        system = source['system']
        components = system['components']
        rack = system['rack']
        if not isinstance(components, list) or len(components) > 200 or not isinstance(rack, list) or len(rack) > 128:
            return None
        out = []
        for item in components:
            quantity = item['quantity']
            if type(quantity) is not int or not 1 <= quantity <= 1000:
                return None
            out.append({'id': text(item['id']), 'label': text(item['label']), 'role': text(item['role']), 'quantity': quantity})
        if any(not isinstance(row, list) or len(row) > 128 for row in rack):
            return None
        clean_rack = [[text(item) for item in row] for row in rack]
        room = source.get('room')
        if room is not None:
            placements = room['placements']
            if not isinstance(placements, list) or len(placements) > 128:
                return None
            width, length = number(room['widthM']), number(room['lengthM'])
            if width <= 0 or length <= 0:
                return None
            clean_placements = []
            for item in placements:
                x, y = number(item['xM']), number(item['yM'])
                if item['role'] not in ('left', 'right', 'subwoofer', 'listening') or not 0 <= x <= width or not 0 <= y <= length:
                    return None
                clean_placements.append({'role': item['role'], 'xM': x, 'yM': y, 'rotationDeg': number(item['rotationDeg'], True)})
            room = {'name': text(room['name']), 'widthM': width, 'lengthM': length, 'heightM': number(room['heightM'], True), 'placements': clean_placements}
        snapshot = {'version': 1, 'revision': text(source['revision']), 'system': {'id': text(system['id']), 'name': text(system['name']), 'components': out, 'rack': clean_rack}, 'room': room}
        return {'snapshot': snapshot, 'stale': value['stale'], 'validUntil': until.isoformat()}
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def projection(endpoint, negotiated, permitted, provider, now):
    player = endpoint.get('studio', {}).get('media', {}).get('entity')
    if not (negotiated and endpoint.get('hifisync') is True and player and callable(provider) and permitted(player)):
        return None
    try:
        return sanitize(provider(player), now)
    except Exception:
        return None


def validate_template(config):
    has_hifisync = False
    for mode in config['modes']:
        for screen in mode['screens']:
            for widget in screen['widgets']:
                if widget['type'] in KINDS:
                    has_hifisync = True
                    if mode['id'] != 'media' or widget['type'] not in screen['requires']:
                        raise ValueError('HiFiSync widgets require their media capability')
    if has_hifisync:
        screens = next(mode['screens'] for mode in config['modes'] if mode['id'] == 'media')
        first = screens[0]
        if first['requires'] or not any(w['type'] == 'media' for w in first['widgets']):
            raise ValueError('Now Playing must be the first unconditional media screen')
