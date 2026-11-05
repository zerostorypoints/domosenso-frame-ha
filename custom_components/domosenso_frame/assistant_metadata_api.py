"""Session-bound v2 read operations. No operation in this module writes HA state."""

from datetime import datetime, timezone
import hmac
import re
from secrets import token_bytes

from . import assistant_metadata as metadata
from . import assistant_metadata_legacy as legacy

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
                               state['assistant_registry_id'], secret, scope, state['assistant_area_id'])
    projected = {}
    legacy_scope = None
    if state.get('assistant_metadata_legacy_enabled'):
        from .assistant_api import _engine
        from . import timer_api
        instant = lambda: datetime.now(timezone.utc)
        current, engine = _engine(hass, connection, grant, {'endpoint': endpoint_id}, policy_read, instant)
        supported = list(legacy.SUPPORTED_PAIRS)
        available = engine.capabilities(current, supported) + timer_api.read_capabilities(
            hass, connection, {'endpoint': endpoint_id, 'supported': supported}, grant, policy_read, instant)
        projected = {cap['id']: (cap, metadata.decorate_projection(cap, legacy.describe(cap), artifacts)) for cap in available}
        legacy_scope = [current, state.get('assistant_metadata_revision'), [
            {key: value for key, value in cap.items() if key not in ('availability', 'availabilityAsOf', 'limitations')}
            for cap in available], [{key: value for key, value in descriptor.items() if key != 'availability'}
                                    for _, descriptor in projected.values()]]
    revision = locale + '.' + metadata.token(secret, scope, proof, locale, catalog, legacy_scope)
    return state, catalog, revision, projected


def discover(hass, connection, msg, grant, policy_read, now=lambda: datetime.now(timezone.utc)):
    try:
        _request(msg, {'endpoint', 'version', 'supported', 'locale'}, {'cursor'})
        locale, supported = msg['locale'], msg['supported']
        if (not isinstance(locale, str) or locale not in LOCALES or not isinstance(supported, list)
                or len(supported) > 16 or any(not isinstance(item, str) or len(item) > 64 for item in supported)
                or len(set(supported)) != len(supported) or not metadata.SUPPORTED <= set(supported)):
            raise ValueError('Unsupported metadata primitives')
        state, catalog, revision, projected = _catalog(hass, connection, msg['endpoint'], locale, grant, policy_read)
        if projected and not {'records.v1', 'options.v1'} <= set(supported):
            raise ValueError('Unsupported projected read primitives')
        descriptors = [cap for cap in metadata.describe(catalog) if cap['id'] not in projected]
        descriptors.extend(descriptor for _, descriptor in projected.values())
        if len(descriptors) > 64 or len(metadata.encoded(descriptors)) > metadata.MAX_CATALOG_BYTES - 1024:
            raise ValueError('Metadata catalogue too large')
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
        state, catalog, revision, _ = _catalog(hass, connection, msg['endpoint'], match[1], grant, policy_read)
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


class _ReadReply:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def __getattr__(self, key):
        return getattr(self.connection, key)

    def send_result(self, _id, result):
        self.result = result

    def send_error(self, _id, _code, _message):
        self.result = None


async def query_with_projection(hass, connection, msg, grant, policy_read):
    if not hass.data[DOMAIN].get('assistant_metadata_legacy_enabled'):
        return query(hass, connection, msg, grant, policy_read)
    try:
        _request(msg, {'endpoint', 'version', 'catalogRevision', 'capability', 'revision', 'arguments'})
        revision = msg['catalogRevision']
        match = REVISION.fullmatch(revision) if isinstance(revision, str) else None
        if not match or not isinstance(msg['capability'], str) or type(msg['revision']) is not int:
            raise ValueError('Invalid projected query')
        _, _, current, projected = _catalog(hass, connection, msg['endpoint'], match[1], grant, policy_read)
        if not hmac.compare_digest(revision, current):
            raise StaleMetadata
        if msg['capability'] not in projected:
            return query(hass, connection, msg, grant, policy_read)
        cap, descriptor = projected[msg['capability']]
        if cap['revision'] != msg['revision']:
            raise ValueError('Unsupported projected revision')
        args = legacy.make_arguments(cap, descriptor, msg['arguments'])
        from .assistant_api import query_async
        captured = _ReadReply(connection)
        await query_async(hass, captured, {'id': msg['id'], 'endpoint': msg['endpoint'], 'version': 1,
                                         'capability': cap['id'], 'revision': cap['revision'],
                                         'answerType': cap['answerType'], 'arguments': args},
                          lambda h, _: grant(h, connection), policy_read)
        if _catalog(hass, connection, msg['endpoint'], match[1], grant, policy_read)[2] != revision:
            raise StaleMetadata
        if captured.result is None:
            raise ValueError('Projected read denied')
        result = {'version': 2, 'catalogRevision': revision, 'capability': cap['id'], 'revision': cap['revision'],
                  'asOf': captured.result['asOf'], 'timezone': hass.config.time_zone,
                  **legacy.project(captured.result, cap)}
        if len(metadata.encoded(result)) > metadata.MAX_ANSWER_BYTES:
            raise ValueError('Projected answer too large')
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
    @websocket_api.async_response
    async def query_command(h, connection, msg):
        await query_with_projection(h, connection, msg, grant, policy_read)

    register_command(hass, discover_command)
    register_command(hass, query_command)
