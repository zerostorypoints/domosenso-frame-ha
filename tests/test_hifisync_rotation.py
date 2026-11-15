import importlib.util
from pathlib import Path
import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'custom_components/domosenso_frame' / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

runtime = load('studio_runtime')
sources = load('studio_sources')


def configuration(system=15, room=15):
    def screen(id, requires, duration):
        return {'id': id, 'requires': requires, 'duration': duration, 'widgets': []}
    return {'modes': [{'id': 'media', 'screens': [screen('playing', [], 15), screen('system', ['hifisync-system'], system), screen('room', ['hifisync-room'], room)]}, {'id': 'ambient', 'screens': [screen('ambient', [], 10), screen('other', [], 10)]}]}


def select(rt, now, caps=('hifisync-system','hifisync-room'), paused=False, config=None, mode='media'):
    return rt.select('frame', config or configuration(), mode, '', mode=='media', caps, 'rev', now, paused=paused)[1]


def test_rotation_wrap_and_missing_room_and_capability_loss():
    rt=runtime.Runtime()
    assert select(rt, 0)=='playing'
    assert select(rt, 15)=='system'
    assert select(rt, 30)=='room'
    assert select(rt, 45)=='playing'
    assert select(rt, 60)=='system'
    assert select(rt, 61, ('hifisync-system',))=='playing'
    assert select(rt, 76, ('hifisync-system',))=='system'
    assert select(rt, 91, ('hifisync-system',))=='playing'


def test_manual_step_restarts_duration_and_wraps():
    rt=runtime.Runtime();assert select(rt, 0)=='playing'
    rt.step('frame', 1, 10)
    assert select(rt, 24)=='system'
    assert select(rt, 25)=='room'
    rt.step('frame', 1, 26)
    assert select(rt, 40)=='playing'
    assert select(rt, 41)=='system'


def test_pause_survives_manual_step_and_resumes_with_full_duration():
    rt=runtime.Runtime();assert select(rt, 0)=='playing'
    assert select(rt, 5, paused=True)=='playing'
    rt.step('frame', 1, 20)
    assert select(rt, 100, paused=True)=='system'
    assert select(rt, 110, paused=False)=='system'
    assert select(rt, 124)=='system'
    assert select(rt, 125)=='room'


def test_zero_duration_private_screens_remain_manual_only_and_other_modes_ignore_pause():
    rt=runtime.Runtime();config=configuration(0,0)
    assert select(rt, 0, config=config)=='playing'
    assert select(rt, 16, config=config)=='playing'
    rt.step('frame', 1, 17)
    assert select(rt, 100, config=config)=='system'
    rt=runtime.Runtime()
    assert select(rt, 0, paused=True, mode='ambient', config=config)=='ambient'
    assert select(rt, 10, paused=True, mode='ambient', config=config)=='other'


def test_pause_mapping_requires_input_boolean_and_is_permission_checked():
    mapping={'runtime': {'paused': 'input_boolean.demo_paused'}}
    assert sources.validate_sources(mapping)==mapping
    assert 'input_boolean.demo_paused' in sources.source_entities(mapping)
    with pytest.raises(ValueError): sources.validate_sources({'runtime': {'paused': 'sensor.demo'}})


def test_resume_gives_current_page_its_full_duration_and_off_never_rotates():
    rt=runtime.Runtime();assert select(rt, 0)=='playing'
    assert select(rt, 5, paused=True)=='playing'
    assert select(rt, 105, paused=True)=='playing'
    assert select(rt, 110)=='playing'
    assert select(rt, 124)=='playing'
    assert select(rt, 125)=='system'
    rt=runtime.Runtime();config=configuration()
    assert select(rt, 0, config=config, mode='off')=='ambient'
    assert select(rt, 100, config=config, mode='off')=='ambient'


def test_zero_duration_pages_remain_manual_after_long_poll_gap():
    rt=runtime.Runtime();config=configuration(0,0)
    assert select(rt, 0, config=config)=='playing'
    assert select(rt, 200, config=config)=='playing'


def studio_select(rt, now, *, caps=('hifisync-system','hifisync-room'), paused=False, room_signature=('demo-system', 'Room')):
    return rt.select('frame', configuration(), 'media', '', True, caps, 'rev', now, paused=paused, studio_music=True, room_signature=room_signature)[1]


def start_room(rt, now=0):
    assert studio_select(rt, now) == 'playing'
    assert studio_select(rt, now+30) == 'room'
    return rt.room_visit('frame')


def test_room_hold_renews_and_resumes_remaining_page_time():
    rt = runtime.Runtime()
    visit = start_room(rt)
    client = object()
    assert rt.interact_room('frame', client, visit, 'touch', now=44)
    assert studio_select(rt, 73) == 'room'
    assert rt.interact_room('frame', client, visit, 'touch', now=73)
    assert studio_select(rt, 103) == 'room'
    assert rt.room_visit('frame') == visit
    assert studio_select(rt, 104) == 'playing'


def test_room_held_gesture_renews_before_end_and_watchdog_expires_if_end_is_lost():
    rt = runtime.Runtime()
    visit = start_room(rt)
    client = object()
    assert rt.interact_room('frame', client, visit, 'begin', 'drag', now=44)
    assert rt.interact_room('frame', client, visit, 'progress', 'drag', now=71)
    assert studio_select(rt, 89) == 'room'
    assert not rt.interact_room('frame', client, visit, 'end', 'earlier', now=89)
    assert rt.interact_room('frame', client, visit, 'end', 'drag', now=89)
    assert studio_select(rt, 119) == 'room'
    assert studio_select(rt, 120) == 'playing'

    rt = runtime.Runtime()
    visit = start_room(rt)
    assert rt.interact_room('frame', client, visit, 'begin', 'drag', now=44)
    assert studio_select(rt, 75) == 'playing'


@pytest.mark.parametrize('delta,next_page', [(1, 'playing'), (-1, 'system')])
def test_manual_swipe_clears_hold_and_invalidates_room_visit(delta, next_page):
    rt = runtime.Runtime()
    visit = start_room(rt)
    client = object()
    assert rt.interact_room('frame', client, visit, 'touch', now=40)
    rt.step('frame', delta, 41)
    assert studio_select(rt, 55) == next_page
    assert rt.room_visit('frame') is None
    rt.step('frame', -delta, 55)
    assert studio_select(rt, 56) == 'room'
    assert rt.room_visit('frame') != visit
    assert not rt.interact_room('frame', client, visit, 'touch', now=57)


def test_manual_pause_and_disconnect_do_not_extend_or_override_one_another():
    rt = runtime.Runtime()
    visit = start_room(rt)
    client = object()
    assert rt.interact_room('frame', client, visit, 'touch', now=44)
    assert studio_select(rt, 45, paused=True) == 'room'
    rt.disconnect(client, now=46)
    assert studio_select(rt, 100, paused=True) == 'room'
    assert studio_select(rt, 100) == 'room'
    assert studio_select(rt, 115) == 'playing'

    rt = runtime.Runtime()
    visit = start_room(rt)
    assert rt.interact_room('frame', client, visit, 'begin', 'drag', now=44)
    rt.disconnect(client, now=46)
    assert studio_select(rt, 46) == 'room'
    assert studio_select(rt, 47) == 'playing'


def test_removed_room_and_reselected_projection_invalidate_old_visit():
    rt = runtime.Runtime()
    visit = start_room(rt)
    client = object()
    assert rt.interact_room('frame', client, visit, 'touch', now=40)
    assert studio_select(rt, 41, caps=('hifisync-system',), room_signature=None) == 'playing'
    assert not rt.interact_room('frame', client, visit, 'touch', now=42)
    assert studio_select(rt, 43) == 'playing'
    assert studio_select(rt, 73) == 'room'
    assert rt.room_visit('frame') != visit
    assert studio_select(rt, 74, room_signature=('different-system', 'Room')) == 'playing'
    assert rt.room_visit('frame') is None
