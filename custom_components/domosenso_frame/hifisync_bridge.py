"""Defensive projection of the optional local HiFiSync provider."""
import copy
import math
from datetime import datetime
from uuid import UUID

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


def effective_config(config, enabled, *, has_system, has_room):
    """Compose a display-only Music playlist without editing the saved layout."""
    result = legacy_config(config)
    if not enabled:
        return result

    saved_screens = next(mode['screens'] for mode in config['modes'] if mode['id'] == 'media')
    media_mode = next(mode for mode in result['modes'] if mode['id'] == 'media')
    screens = media_mode['screens']
    anchor = next(
        (screen for screen in screens if not screen['requires'] and any(
            widget['type'] == 'media' for widget in screen['widgets']
        )),
        None,
    ) or next(screen for screen in screens if not screen['requires'])
    screens.remove(anchor)
    used_ids = {anchor['id'], *(screen['id'] for screen in screens)}
    extra = []
    for kind, title, available in (
        ('hifisync-system', 'System', has_system),
        ('hifisync-room', 'Room', has_system and has_room),
    ):
        if not available:
            continue
        saved = next(
            (screen for screen in saved_screens
             if screen['id'] not in used_ids and kind in screen['requires']
             and any(widget['type'] == kind for widget in screen['widgets'])),
            None,
        )
        if saved:
            screen = copy.deepcopy(saved)
            screen['requires'] = [kind]
            screen['widgets'] = [widget for widget in screen['widgets']
                                 if widget['type'] == kind or widget['type'] not in KINDS]
        else:
            screen_id = kind
            suffix = 1
            while screen_id in used_ids:
                screen_id = f'{kind}-{suffix}'
                suffix += 1
            screen = {
                'id': screen_id, 'title': title, 'duration': 15, 'requires': [kind],
                'widgets': [{'id': kind, 'type': kind, 'layout': {'x': 0, 'y': 0, 'w': 12, 'h': 12}}],
            }
        extra.append(screen)
        used_ids.add(screen['id'])
    if extra and not anchor['duration']:
        anchor['duration'] = 15
    media_mode['screens'] = [anchor, *extra, *screens]
    return result


def add_link_summaries(result, *, installed, provider, permitted):
    """Attach bounded labels to their media candidates, never to other players."""
    result['hifisyncInstalled'] = installed
    if not installed or not callable(provider):
        return
    for candidate in result['candidates']['media']:
        entity_id = candidate['entity']
        if not permitted(entity_id):
            continue
        try:
            link = provider(entity_id)
        except Exception:
            continue
        if not isinstance(link, dict):
            continue
        status = link.get('status')
        if status not in ('ready', 'stale', 'loading', 'select_system', 'select_player', 'unavailable', 'reauth'):
            continue

        def label(key):
            value = link.get(key)
            return value if status in ('ready', 'stale') and isinstance(value, str) and 1 <= len(value) <= 200 else None

        candidate['hifisyncLink'] = {'status': status, 'systemName': label('systemName'), 'roomName': label('roomName')}


def sanitize_drawing(value):
    """Copy a bounded drawing, or omit it when any recognized field is invalid."""
    try:
        if not isinstance(value, dict) or value['rackKind'] not in ('amp', 'phono', 'tt', 'src', 'spk', 'sub', 'dac'):
            return None
        result = {'rackKind': value['rackKind']}
        def bounded_number(v, maximum):
            if type(v) not in (int, float) or not math.isfinite(v) or not .01 <= v <= maximum:
                raise ValueError('Invalid drawing number')
            return v
        def bounded_text(v):
            if not isinstance(v, str) or not 1 <= len(v) <= 80:
                raise ValueError('Invalid drawing text')
            return v
        for key in ('widthCm', 'heightCm', 'depthCm'):
            if key in value: result[key] = bounded_number(value[key], 1000)
        for key in ('floorstanding', 'active'):
            if key in value:
                if type(value[key]) is not bool: raise ValueError('Invalid drawing boolean')
                result[key] = value[key]
        if 'bassAlignment' in value: result['bassAlignment'] = bounded_text(value['bassAlignment'])
        if 'drivers' in value:
            drivers = value['drivers']
            if not isinstance(drivers, list) or len(drivers) > 16: raise ValueError('Invalid drivers')
            result['drivers'] = []
            for driver in drivers:
                count = driver['count']
                if type(count) is not int or not 1 <= count <= 32: raise ValueError('Invalid driver count')
                clean = {'role': bounded_text(driver['role']), 'size': bounded_number(driver['size'], 100), 'count': count}
                for key in ('type', 'face'):
                    if key in driver: clean[key] = bounded_text(driver[key])
                result['drivers'].append(clean)
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

def sanitize(value, now, drawings=False, *, room_identity=False):
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
        if not isinstance(components, list) or len(components) > 64 or not isinstance(rack, list) or len(rack) > 32:
            return None
        out = []
        for item in components:
            quantity = item['quantity']
            if type(quantity) is not int or not 1 <= quantity <= 64:
                return None
            clean = {'id': text(item['id']), 'label': text(item['label']), 'role': text(item['role']), 'quantity': quantity}
            if drawings:
                drawing = sanitize_drawing(item.get('drawing'))
                if drawing is not None: clean['drawing'] = drawing
            out.append(clean)
        if any(not isinstance(row, list) or len(row) > 32 for row in rack):
            return None
        ids = {item['id'] for item in out}
        if len(ids) != len(out) or any(item not in ids for row in rack for item in row):return None
        clean_rack = [[text(item) for item in row] for row in rack]
        room = source.get('room')
        if room is not None:
            placements = room['placements']
            if not isinstance(placements, list) or len(placements) > 16:
                return None
            width, length = number(room['widthM']), number(room['lengthM'])
            if not .01 <= width <= 200 or not .01 <= length <= 200:
                return None
            clean_placements = []
            for item in placements:
                x, y = number(item['xM']), number(item['yM'])
                if item['role'] not in ('left', 'right', 'subwoofer', 'listening') or not 0 <= x <= width or not 0 <= y <= length:
                    return None
                rotation=number(item['rotationDeg'], True)
                if rotation is not None and not -360 <= rotation <= 360:return None
                clean_placements.append({'role': item['role'], 'xM': x, 'yM': y, 'rotationDeg': rotation})
            height=number(room['heightM'], True)
            if height is not None and not .01 <= height <= 200:return None
            room_id = {}
            if room_identity and 'id' in room:
                raw_id = room['id']
                if not isinstance(raw_id, str) or len(raw_id) != 36 or str(UUID(raw_id)) != raw_id.lower():
                    return None
                room_id['id'] = raw_id.lower()
            room = {**room_id, 'name': text(room['name']), 'widthM': width, 'lengthM': length, 'heightM': height, 'placements': clean_placements}
        snapshot = {'version': 1, 'revision': text(source['revision']), 'system': {'id': text(system['id']), 'name': text(system['name']), 'components': out, 'rack': clean_rack}, 'room': room}
        return {'snapshot': snapshot, 'stale': value['stale'], 'validUntil': until.isoformat()}
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def projection(endpoint, negotiated, permitted, provider, now, drawings=False, *, room_identity=False):
    player = endpoint.get('studio', {}).get('media', {}).get('entity')
    if not (negotiated and endpoint.get('hifisync') is True and player and callable(provider) and permitted(player)):
        return None
    try:
        return sanitize(provider(player), now, drawings=drawings, room_identity=room_identity)
    except Exception:
        return None


def room_signature(value):
    if value and (room := value['snapshot']['room']):
        system_id = value['snapshot']['system']['id']
        if 'id' in room:
            return (system_id, 'id', room['id'])
        return (system_id, 'name', room['name'])
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


def music_active(negotiated, enabled, has_helper, helper_active, player_state):
    return helper_active if has_helper else bool(negotiated and enabled and player_state == 'playing')
