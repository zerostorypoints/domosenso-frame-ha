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


def test_room_identity_is_scoped_to_studio_capability_and_supports_legacy_previews():
    value = sample()
    room_id = '12345678-1234-1234-1234-123456789abc'
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fallback = bridge.sanitize(value, now, room_identity=True)
    assert bridge.room_signature(fallback) == ('s', 'name', 'Room')
    value['snapshot']['room']['id'] = room_id.upper()
    old = bridge.sanitize(value, now, drawings=True)
    assert 'id' not in old['snapshot']['room']
    studio = bridge.sanitize(value, now, room_identity=True)
    assert studio['snapshot']['room']['id'] == room_id
    assert bridge.room_signature(studio) == ('s', 'id', room_id)
    value['snapshot']['room']['id'] = 'malformed'
    assert bridge.sanitize(value, now, room_identity=True) is None


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


def page(id, *types, requires=(), duration=0):
    return {
        'id': id,
        'title': id,
        'duration': duration,
        'requires': list(requires),
        'widgets': [{'id': kind, 'type': kind, 'layout': {'x': 0, 'y': 0, 'w': 12, 'h': 12}} for kind in types],
    }


def test_effective_playlist_holds_saved_template_revision_and_keeps_all_sixteen_pages():
    import copy

    pages = [page('custom-before', 'clock'), page('music', 'media')]
    pages.extend(page(f'custom-{i}', 'clock') for i in range(14))
    template = {'modes': [{'id': 'media', 'screens': pages}]}
    before = copy.deepcopy(template)
    result = bridge.effective_config(template, True, has_system=True, has_room=True)
    screens = result['modes'][0]['screens']
    assert [s['id'] for s in screens] == ['music', 'hifisync-system', 'hifisync-room', 'custom-before', *[f'custom-{i}' for i in range(14)]]
    assert [p['duration'] for p in screens[:3]] == [15, 15, 15]
    assert template == before


def test_effective_playlist_reuses_saved_pilot_page_once_and_missing_room_does_not_show():
    now_playing = page('music', 'media', duration=42)
    system = page('pilot-system', 'hifisync-system', requires=['hifisync-system'], duration=34)
    room = page('pilot-room', 'hifisync-room', requires=['hifisync-room'], duration=37)
    template = {'modes': [{'id': 'media', 'screens': [now_playing, system, room, page('duplicate', 'hifisync-system', requires=['hifisync-system'])]}]}
    assert bridge.effective_config(template, True, has_system=True, has_room=True)['modes'][0]['screens'] == [now_playing, system, room]
    assert bridge.effective_config(template, True, has_system=True, has_room=False)['modes'][0]['screens'] == [now_playing, system]
    assert bridge.effective_config(template, False, has_system=True, has_room=True)['modes'][0]['screens'] == [now_playing]


def test_effective_playlist_avoids_custom_page_id_collisions():
    custom = [page('hifisync-system', 'clock', requires=['weather']), page('custom-first', 'clock')]
    template = {'modes': [{'id': 'media', 'screens': custom}]}
    screens = bridge.effective_config(template, True, has_system=True, has_room=False)['modes'][0]['screens']
    assert screens[0]['widgets'][0]['type'] == 'media'
    assert screens[1]['widgets'][0]['type'] == 'hifisync-system'
    assert screens[1]['id'] != 'hifisync-system'
    assert screens[2:] == custom
    assert template['modes'][0]['screens'] == custom


def test_effective_playlist_preserves_custom_pages_with_available_hifisync_requirements():
    music = page('music', 'media', duration=42)
    clock = page('room-clock', 'clock', requires=['hifisync-room'])
    custom = page('custom', 'weather')
    template = {'modes': [{'id': 'media', 'screens': [clock, music, custom]}]}

    result = bridge.effective_config(template, True, has_system=True, has_room=True)
    assert [p['id'] for p in result['modes'][0]['screens']] == [
        'music', 'hifisync-system', 'hifisync-room', 'room-clock', 'custom',
    ]
    assert result['modes'][0]['screens'][-2] == clock
    without_room = bridge.effective_config(template, True, has_system=True, has_room=False)
    assert [p['id'] for p in without_room['modes'][0]['screens']] == ['music', 'hifisync-system', 'custom']
    assert bridge.effective_config(template, False, has_system=True, has_room=True)['modes'][0]['screens'] == [music, custom]


def test_filtering_an_unconditional_hifisync_only_page_keeps_playback_available():
    template = {'modes': [{'id': 'media', 'screens': [page('custom', 'hifisync-room')]}]}
    off = bridge.effective_config(template, False, has_system=True, has_room=True)
    assert off['modes'][0]['screens'][0]['widgets'][0]['type'] == 'media'
    on = bridge.effective_config(template, True, has_system=True, has_room=True)
    assert [p['widgets'][0]['type'] for p in on['modes'][0]['screens']] == [
        'media', 'hifisync-system', 'hifisync-room',
    ]
    assert template['modes'][0]['screens'][0]['widgets'][0]['type'] == 'hifisync-room'


def test_negotiated_summary_is_bounded_by_player_permission_and_hides_extra_data():
    result = {'candidates': {'media': [{'entity': 'media_player.one'}, {'entity': 'media_player.forbidden'}, {'entity': 'media_player.unmatched'}]}}
    calls = []

    def provider(entity):
        calls.append(entity)
        return {'status': 'ready', 'systemName': 'System', 'roomName': 'Room', 'token': 'secret'} if entity == 'media_player.one' else None

    bridge.add_link_summaries(result, installed=True, provider=provider, permitted=lambda id: id != 'media_player.forbidden')
    assert result['hifisyncInstalled'] is True
    assert result['candidates']['media'][0]['hifisyncLink'] == {'status': 'ready', 'systemName': 'System', 'roomName': 'Room'}
    assert all('hifisyncLink' not in row for row in result['candidates']['media'][1:])
    assert calls == ['media_player.one', 'media_player.unmatched']
    absent = {'candidates': {'media': [{'entity': 'media_player.one'}]}}
    bridge.add_link_summaries(absent, installed=False, provider=None, permitted=lambda _: True)
    assert absent == {'hifisyncInstalled': False, 'candidates': {'media': [{'entity': 'media_player.one'}]}}


def test_opted_in_imported_layout_without_player_page_starts_with_now_playing():
    home = page('home', 'clock', duration=20)
    existing_id = page('now-playing', 'rooms', duration=20)
    template = {'modes': [{'id': 'media', 'screens': [home, existing_id]}]}
    screens = bridge.effective_config(template, True, has_system=True, has_room=True)['modes'][0]['screens']

    assert [screen['widgets'][0]['type'] for screen in screens] == [
        'media', 'hifisync-system', 'hifisync-room', 'clock', 'rooms',
    ]
    assert len({screen['id'] for screen in screens}) == len(screens)
    assert screens[0]['title'] == 'Now Playing'
    assert screens[-2:] == [home, existing_id]
    assert template['modes'][0]['screens'] == [home, existing_id]
    assert bridge.effective_config(template, False, has_system=True, has_room=True) == template
