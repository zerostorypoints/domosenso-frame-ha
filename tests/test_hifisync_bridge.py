import importlib.util
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location('hifisync_bridge', Path(__file__).parents[1] / 'custom_components/domosenso_frame/hifisync_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def sample():
    return {'snapshot': {'version': 1, 'revision': 'rev', 'system': {'id': 's', 'name': 'System', 'components': [{'id': 'a', 'label': 'Amp', 'role': 'amplifier', 'quantity': 1, 'secret': 'no'}], 'rack': [['a']]}, 'room': {'name': 'Room', 'widthM': 4, 'lengthM': 5, 'heightM': None, 'placements': []}, 'secret': 'no'}, 'stale': False, 'validUntil': '2099-01-01T00:00:00+00:00', 'secret': 'no'}


def test_provider_projection_is_bounded_and_strips_unrecognized_data():
    value = bridge.sanitize(sample(), datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert value['snapshot']['system']['components'] == [{'id': 'a', 'label': 'Amp', 'role': 'amplifier', 'quantity': 1}]
    assert 'secret' not in str(value)
    assert bridge.sanitize({**sample(), 'validUntil': '2020-01-01T00:00:00+00:00'}, datetime(2026, 1, 1, tzinfo=timezone.utc)) is None


def test_legacy_projection_removes_new_screens_without_mutating_saved_template():
    config = {'modes': [{'id': 'media', 'screens': [{'id': 'playing', 'requires': [], 'widgets': [{'type': 'media'}]}, {'id': 'system', 'requires': ['hifisync-system'], 'widgets': [{'type': 'hifisync-system'}]}]}]}
    projected = bridge.legacy_config(config)
    assert [s['id'] for s in projected['modes'][0]['screens']] == ['playing']
    assert len(config['modes'][0]['screens']) == 2


def test_provider_called_only_for_opted_in_mapped_and_permitted_player():
    calls = []
    provider = lambda entity: calls.append(entity) or sample()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    endpoint = {'hifisync': True, 'studio': {'media': {'entity': 'media_player.one'}}}
    assert bridge.projection(endpoint, True, lambda _: True, provider, now)['snapshot']['system']['name'] == 'System'
    assert calls == ['media_player.one']
    for ep, negotiated, permitted in [(endpoint, False, True), ({**endpoint, 'hifisync': False}, True, True), (endpoint, True, False), ({'hifisync': True}, True, True)]:
        assert bridge.projection(ep, negotiated, lambda _: permitted, provider, now) is None
    assert calls == ['media_player.one']


def test_manual_hifisync_screens_do_not_rotate_into_view():
    runtime_spec = importlib.util.spec_from_file_location('runtime', Path(__file__).parents[1] / 'custom_components/domosenso_frame/studio_runtime.py')
    runtime = importlib.util.module_from_spec(runtime_spec)
    runtime_spec.loader.exec_module(runtime)
    rt = runtime.Runtime()
    screens = [{'id': 'playing', 'requires': [], 'duration': 10, 'widgets': [{'type': 'media'}]}, {'id': 'system', 'requires': ['hifisync-system'], 'duration': 0, 'widgets': [{'type': 'hifisync-system'}]}]
    config = {'modes': [{'id': 'media', 'screens': screens}]}
    assert rt.select('frame', config, 'media', '', True, ['hifisync-system'], '1', 0)[1] == 'playing'
    assert rt.select('frame', config, 'media', '', True, ['hifisync-system'], '1', 11)[1] == 'playing'
    rt.step('frame', 1, 12)
    assert rt.select('frame', config, 'media', '', True, ['hifisync-system'], '1', 13)[1] == 'system'
    assert rt.select('frame', config, 'media', '', True, [], '1', 14)[1] == 'playing'


def test_legacy_projection_keeps_unconditional_mixed_screen_usable():
    config = {'modes': [{'id': 'media', 'screens': [{'id': 'playing', 'requires': [], 'widgets': [{'type': 'media'}, {'type': 'hifisync-system'}]}]}]}
    assert bridge.legacy_config(config)['modes'][0]['screens'][0]['widgets'] == [{'type': 'media'}]


def test_hifisync_widgets_require_gates_and_first_now_playing_screen():
    import pytest
    config = {'modes': [{'id': 'media', 'screens': [{'id': 'playing', 'requires': [], 'widgets': [{'type': 'media'}]}, {'id': 'system', 'requires': ['hifisync-system'], 'widgets': [{'type': 'hifisync-system'}]}]}]}
    bridge.validate_template(config)
    config['modes'][0]['screens'][1]['requires'] = []
    with pytest.raises(ValueError): bridge.validate_template(config)
    config['modes'][0]['screens'][1]['requires'] = ['hifisync-system']
    config['modes'][0]['screens'].reverse()
    with pytest.raises(ValueError): bridge.validate_template(config)


def test_projection_rejects_values_exceeding_packaged_renderer_limits():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    value = sample(); value['snapshot']['system']['components'][0]['quantity'] = 65
    assert bridge.sanitize(value, now) is None
    value = sample(); value['snapshot']['room']['widthM'] = 201
    assert bridge.sanitize(value, now) is None
    value = sample(); value['snapshot']['system']['rack'] = [['unknown']]
    assert bridge.sanitize(value, now) is None


def test_music_session_uses_player_when_no_helper_for_opted_in_client():
    assert bridge.music_active(True, True, False, False, 'playing')
    assert not bridge.music_active(False, True, False, False, 'playing')
    assert not bridge.music_active(True, True, True, False, 'playing')
    assert bridge.music_active(True, True, True, True, 'paused')


def test_legacy_reads_and_previews_do_not_reset_modern_navigation():
    runtime_spec = importlib.util.spec_from_file_location('runtime_interleaved', Path(__file__).parents[1] / 'custom_components/domosenso_frame/studio_runtime.py')
    runtime = importlib.util.module_from_spec(runtime_spec); runtime_spec.loader.exec_module(runtime)
    rt = runtime.Runtime()
    modern = runtime.view_key('frame', True)
    legacy = runtime.view_key('frame', False)
    preview = runtime.view_key('frame', True, preview=True)
    config = {'modes': [{'id': 'ambient', 'screens': [{'id': 'climate', 'requires': [], 'duration': 0, 'widgets': []}]}, {'id': 'media', 'screens': [{'id': 'playing', 'requires': [], 'duration': 0, 'widgets': [{'type': 'media'}]}, {'id': 'system', 'requires': ['hifisync-system'], 'duration': 0, 'widgets': [{'type': 'hifisync-system'}]}]}]}
    assert rt.select(modern, config, 'ambient', '', True, ['hifisync-system'], '1', 0)[1] == 'playing'
    assert rt.select(legacy, config, 'ambient', '', False, [], '1', 1)[1] == 'climate'
    assert rt.select(preview, config, 'ambient', '', True, [], '1', 2)[1] == 'playing'
    rt.step(modern, 1, 3)
    assert rt.select(modern, config, 'ambient', '', True, ['hifisync-system'], '1', 4)[1] == 'system'
    rt.reset('frame')
    assert rt.select(modern, config, 'ambient', '', True, ['hifisync-system'], '1', 5)[1] == 'playing'


def test_action_view_is_bound_to_pairing_and_ignores_legacy_interleaving():
    runtime_spec = importlib.util.spec_from_file_location('runtime_binding', Path(__file__).parents[1] / 'custom_components/domosenso_frame/studio_runtime.py')
    runtime = importlib.util.module_from_spec(runtime_spec); runtime_spec.loader.exec_module(runtime)
    views = runtime.SessionViews(); first = object(); second = object()
    proof = {'deviceId': 'one', 'endpoint': 'frame', 'state': 'approved'}
    views.remember(first, proof, True, True)
    views.remember(first, proof, False, True)
    assert views.key(first, proof, True) == runtime.view_key('frame', True)
    assert views.key(second, proof, True) == runtime.view_key('frame', False)
    assert views.key(first, {**proof, 'endpoint': 'other'}, True) == runtime.view_key('other', False)
    assert views.key(first, proof, False) == runtime.view_key('frame', False)
    views.forget(first)
    assert views.key(first, proof, True) == runtime.view_key('frame', False)


def test_drawing_requires_handshake_and_malformed_data_does_not_discard_snapshot():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    value = sample()
    component = value['snapshot']['system']['components'][0]
    component['drawing'] = {'rackKind':'spk', 'widthCm':20, 'drivers':[{'role':'woofer','size':16.5,'count':2,'face':'front','secret':'x'}], 'secret':'x'}
    assert 'drawing' not in bridge.sanitize(value, now)['snapshot']['system']['components'][0]
    drawing = bridge.sanitize(value, now, drawings=True)['snapshot']['system']['components'][0]['drawing']
    assert drawing == {'rackKind':'spk', 'widthCm':20, 'drivers':[{'role':'woofer','size':16.5,'count':2,'face':'front'}]}
    for invalid in [{'rackKind':'other'}, {'rackKind':'spk','widthCm':1001}, {'rackKind':'spk','drivers':[{'role':'x','size':True,'count':1}]}, {'rackKind':'spk','floorstanding':1}]:
        component['drawing'] = invalid
        assert 'drawing' not in bridge.sanitize(value, now, drawings=True)['snapshot']['system']['components'][0]
