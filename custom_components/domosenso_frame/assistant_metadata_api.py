"""Session-bound v2 read operations. No operation in this module writes HA state."""

from datetime import datetime, timezone
import hmac
import re
from secrets import token_bytes

from . import assistant_metadata as metadata

DOMAIN = 'domosenso_frame'
LOCALES = {'en', 'pl', 'de'}
REVISION = re.compile(r'^(en|pl|de)\.[0-9a-f]{64}$')
CURSOR = re.compile(r'^(1[0-5]|[1-9])\.[0-9a-f]{64}$')


class StaleMetadata(ValueError):
    pass


def _request(msg, required, optional=()):
    keys = set(msg) - {'id', 'type'}
    if (not set(required) <= keys or keys - set(required) - set(optional)
            or type(msg['version']) is not int or msg['version'] != 2
            or not isinstance(msg['endpoint'], str) or not 1 <= len(msg['endpoint']) <= 40):
        raise ValueError('Invalid metadata request')


def _catalog(hass, connection, endpoint_id, locale, grant, policy_read):
    proof, endpoint = grant(hass, connection)
    state = hass.data[DOMAIN]
    if not proof or not endpoint or endpoint['id'] != endpoint_id or not state.get('assistant_metadata_read_enabled'):
        raise ValueError('Metadata unavailable')
    inventory = hass.states.get('sensor.domosenso_components')
    records = getattr(inventory, 'attributes', {}).get('components')
    artifacts = metadata.active_metadata(state['assistant_metadata_artifacts'], records)
    permitted = lambda entity: connection.user.permissions.check_entity(entity, policy_read)
    scope = (id(connection), connection.user.id, endpoint_id)
    secret = state['assistant_metadata_key']
    catalog = metadata.resolve(artifacts, endpoint.get('assistant', {}).get('metadata_sources', []), permitted,
                               state['assistant_registry_id'], secret, scope)
    revision = locale + '.' + metadata.token(secret, scope, proof, locale, catalog)
    return state, catalog, revision


def discover(hass, connection, msg, grant, policy_read, now=lambda: datetime.now(timezone.utc)):
    try:
        _request(msg, {'endpoint', 'version', 'supported', 'locale'}, {'cursor'})
        locale, supported = msg['locale'], msg['supported']
        if (not isinstance(locale, str) or locale not in LOCALES or not isinstance(supported, list)
                or len(supported) != len(metadata.SUPPORTED) or any(not isinstance(item, str) for item in supported)
                or set(supported) != metadata.SUPPORTED):
            raise ValueError('Unsupported metadata primitives')
        state, catalog, revision = _catalog(hass, connection, msg['endpoint'], locale, grant, policy_read)
        descriptors = metadata.describe(catalog)
        cursor = msg.get('cursor')
        page = 0
        if cursor is not None:
            match = CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
            if not match:
                raise ValueError('Invalid metadata cursor')
            page = int(match[1])
            expected = f"{page}." + metadata.token(state['assistant_metadata_key'], revision, page)
            if not hmac.compare_digest(cursor, expected):
                raise StaleMetadata
        start = page * metadata.PAGE_SIZE
        end = start + metadata.PAGE_SIZE
        result = {'version': 2, 'catalogRevision': revision, 'asOf': now().isoformat(), 'capabilities': [
            {**cap, 'selectors': cap['selectors'][start:end]} for cap in descriptors if page == 0 or len(cap['selectors']) > start
        ]}
        if any(len(cap['selectors']) > end for cap in descriptors):
            result['nextCursor'] = f"{page + 1}." + metadata.token(state['assistant_metadata_key'], revision, page + 1)
        connection.send_result(msg['id'], result)
    except StaleMetadata:
        connection.send_error(msg['id'], 'frame_metadata_stale', 'Metadata changed')
    except (ValueError, TypeError, KeyError):
        connection.send_error(msg['id'], 'frame_denied', 'Metadata unavailable or access denied')


def query(hass, connection, msg, grant, policy_read, now=lambda: datetime.now(timezone.utc)):
    try:
        _request(msg, {'endpoint', 'version', 'catalogRevision', 'capability', 'revision', 'arguments'})
        candidate = msg['catalogRevision']
        match = REVISION.fullmatch(candidate) if isinstance(candidate, str) else None
        if not match or not isinstance(msg['capability'], str) or type(msg['revision']) is not int:
            raise ValueError('Invalid metadata query')
        state, catalog, revision = _catalog(hass, connection, msg['endpoint'], match[1], grant, policy_read)
        if not hmac.compare_digest(candidate, revision):
            raise StaleMetadata
        cap, rows = catalog[msg['capability']]
        if cap['revision'] != msg['revision']:
            raise ValueError('Unknown capability revision')
        as_of = now()
        answer = metadata.evaluate(cap, rows, msg['arguments'], state['assistant_state_get'],
                                   state['assistant_source_verified'], as_of)
        if _catalog(hass, connection, msg['endpoint'], match[1], grant, policy_read)[2] != revision:
            raise StaleMetadata
        result = {'version': 2, 'catalogRevision': revision, 'capability': cap['id'], 'revision': cap['revision'],
                  'asOf': as_of.isoformat(), 'timezone': hass.config.time_zone, **answer}
        if len(metadata.encoded(result)) > metadata.MAX_ANSWER_BYTES:
            raise ValueError('Metadata answer too large')
        connection.send_result(msg['id'], result)
    except StaleMetadata:
        connection.send_error(msg['id'], 'frame_metadata_stale', 'Metadata changed')
    except (ValueError, TypeError, KeyError):
        connection.send_error(msg['id'], 'frame_denied', 'Metadata unavailable or access denied')


def setup(hass, grant, register_command, policy_read):
    import voluptuous as vol
    from homeassistant.components import websocket_api
    from homeassistant.core import callback

    hass.data[DOMAIN]['assistant_metadata_key'] = token_bytes(32)
    command = websocket_api.websocket_command

    @command({vol.Required('type'): DOMAIN + '/assistant_metadata', vol.Required('endpoint'): str,
              vol.Required('version'): int, vol.Required('supported'): list, vol.Required('locale'): str,
              vol.Optional('cursor'): str})
    @callback
    def discover_command(h, connection, msg):
        discover(h, connection, msg, grant, policy_read)

    @command({vol.Required('type'): DOMAIN + '/assistant_metadata_query', vol.Required('endpoint'): str,
              vol.Required('version'): int, vol.Required('catalogRevision'): str, vol.Required('capability'): str,
              vol.Required('revision'): int, vol.Required('arguments'): dict})
    @callback
    def query_command(h, connection, msg):
        query(h, connection, msg, grant, policy_read)

    register_command(hass, discover_command)
    register_command(hass, query_command)
