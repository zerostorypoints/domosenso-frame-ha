"""Options opt-in for photo albums on UI-configured panels: pure functions, fictional entities."""
import pytest
from test_controls import module

ENDPOINT = {'id': 'panel', 'name': 'Frame', 'dashboard': '/lovelace'}
ON = {'photo_albums_enabled': True, 'photo_albums_entity': 'sensor.demo_photo_albums', 'photo_albums_script': 'script.demo_photo_albums_set'}


class States:
    def __init__(self, *ids): self.ids = ids
    def async_entity_ids(self): return list(self.ids)


def test_options_enable_adds_key():
    result = module('onboarding').endpoint_with_options(ENDPOINT, ON)
    assert result['photo_albums'] == {'entity': 'sensor.demo_photo_albums', 'script': 'script.demo_photo_albums_set'}
    assert 'photo_albums' not in ENDPOINT


def test_options_disabled_removes_key():
    endpoint = {**ENDPOINT, 'photo_albums': {'entity': 'sensor.a', 'script': 'script.b'}}
    assert 'photo_albums' not in module('onboarding').endpoint_with_options(endpoint, {**ON, 'photo_albums_enabled': False})
    assert 'photo_albums' not in module('onboarding').endpoint_with_options(endpoint, {})


def test_options_missing_entity_rejected():
    with pytest.raises(ValueError):
        module('onboarding').endpoint_with_options(ENDPOINT, {**ON, 'photo_albums_script': ''})


def test_suggests_single_pair():
    suggest = module('onboarding').suggest_photo_albums
    assert suggest(States('sensor.demo_photo_albums', 'script.demo_photo_albums_set', 'sensor.other')) == ('sensor.demo_photo_albums', 'script.demo_photo_albums_set')
    assert suggest(States('sensor.demo_photo_albums')) == (None, None)
    assert suggest(States('sensor.a_photo_albums', 'script.a_photo_albums_set', 'sensor.b_photo_albums', 'script.b_photo_albums_set')) == (None, None)


def test_options_error_uses_endpoint_validation():
    vol = pytest.importorskip('voluptuous')
    validate = vol.Schema({vol.Optional('photo_albums'): vol.Schema({'entity': vol.Match(r'^sensor\.'), 'script': vol.Match(r'^script\.')})}, extra=vol.ALLOW_EXTRA)
    error = module('onboarding').options_error
    assert error(ENDPOINT, ON, validate) is None
    assert error(ENDPOINT, {}, validate) is None
    assert error(ENDPOINT, {**ON, 'photo_albums_script': ''}, validate) == 'albums_required'
    assert error(ENDPOINT, {**ON, 'photo_albums_entity': 'light.demo_photo_albums'}, validate) == 'invalid_albums'
    assert error(ENDPOINT, {**ON, 'photo_albums_script': 'sensor.demo_photo_albums_set'}, validate) == 'invalid_albums'
