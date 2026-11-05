import importlib

from test_assistant_api import pkg

projection = importlib.import_module(pkg.__name__ + '.action_metadata')


def test_projection_only_uses_present_granted_keys():
    result = {'version': 1, 'targets': [{'id': 'authorized', 'kind': 'awning',
              'capabilities': [{'id': 'awnings.stop', 'revision': 1}]}]}
    value = projection.project_controls(result)
    assert result.get('intent') is None
    assert value['intent']['version'] == 2
    assert value['intent']['targets'][0]['id'] == 'authorized'
    assert value['intent']['targets'][0]['actions'][0]['key'] == 'awnings.stop'
    assert value['intent']['targets'][0]['actions'][0]['verb'] == 'stop'
    assert all(action['key'] != 'awnings.extend' for target in value['intent']['targets'] for action in target['actions'])


def test_android_fixture_tracks_all_control_noun_classes():
    import json
    from pathlib import Path

    fixtures = Path(__file__).with_name('fixtures')
    cases = json.loads((fixtures / 'shared-controls.json').read_text())['cases']
    intents = json.loads((fixtures / 'assistant-metadata/action-bindings.json').read_text())
    assert set(intents) == {'targets', 'coverTargets', 'awningTargets'}
    for name, expected in intents.items():
        assert projection.project_controls(cases[name]['response'])['intent'] == expected
