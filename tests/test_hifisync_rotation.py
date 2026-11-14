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
