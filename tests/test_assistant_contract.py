"""Isolated contract tests: no Home Assistant installation or household data."""
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / 'custom_components/domosenso_frame/assistant_contract.py'
spec = importlib.util.spec_from_file_location('assistant_contract', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def bundle():
    cap = {'id': 'lights.status', 'revision': 1, 'operation': 'read', 'query_type': 'light-query.v1', 'answer_type': 'lights.v1', 'title_key': 'meta.assistant.status.title', 'description_key': 'meta.assistant.status.description', 'examples_key': 'meta.assistant.status.examples', 'required_facts': [{'role': 'state', 'type': 'state', 'units': [], 'source': {'relation': 'upstream', 'id': 'light.test'}}], 'optional_facts': [], 'policy': {'evaluator': 'lights.status'}, 'freshness': {'basis': 'reported_state', 'max_age_seconds': None}, 'limitations': []}
    language = {'meta': {'assistant': {'status': {'title': 'Lights', 'description': 'Reported lights', 'examples': ['Which lights are on?']}}}}
    return {'schema': 1, 'component': {'id': 'lights', 'version': '1.0.0', 'commit': '123456789abc', 'dev': False},
            'definitions': {'capabilities': [cap]}, 'translations': {'en': language, 'de': language, 'pl': language},
            'bindings': {'subjects': [], 'policies': []}}


def test_identity_requires_explicit_matching_installed_provenance():
    b = bundle()
    assert module.identity_matches(b, {'id': 'lights', 'version': '1.0.0', 'commit': '123456789abc', 'dev': False, 'installed': True, 'package': ''})
    for change in ({'installed': False}, {'commit': 'abcdefabcdef'}, {'version': '2'}, {'dev': True}):
        inventory = {'id': 'lights', 'version': '1.0.0', 'commit': '123456789abc', 'dev': False, 'installed': True, 'package': ''} | change
        assert not module.identity_matches(b, inventory)
    assert not module.identity_matches(b, None)


def test_safe_loader_rejects_symlinks_and_invalid_yaml(tmp_path):
    import yaml
    directory = tmp_path / 'domosenso' / 'assistant'
    directory.mkdir(parents=True)
    (directory / 'lights.yaml').write_text(yaml.safe_dump(bundle()))
    (directory / 'bad.yaml').write_text('!!python/object/apply:os.system ["false"]')
    (directory / 'link.yaml').symlink_to(directory / 'lights.yaml')
    loaded, errors = module.load_directory(tmp_path)
    assert set(loaded) == {'lights'}
    assert set(errors) == {'bad.yaml', 'link.yaml'}


def test_rejects_unknown_fields_and_unsafe_field_values():
    b = bundle()
    b['bindings']['subjects'] = [{'key': 'lamp', 'kind': 'light', 'label': 'Lamp', 'fields': {'state': {'entity': 'light.lamp', 'execute': 'anything'}}}]
    with pytest.raises(ValueError):
        module.validate_bundle(b)


def test_rejects_non_finite_numbers():
    b = bundle()
    b['bindings']['policies'] = [{'levels': [float('nan')]}]
    with pytest.raises(ValueError):
        module.validate_bundle(b)


def test_real_inventory_short_commit_and_package_only():
    b = bundle()
    b['component']['commit'] = '123456789abc123456789abc123456789abc1234'
    record = {'id': 'lights', 'version': '1.0.0', 'commit': '123456789abc', 'dev': False, 'package': ''}
    assert module.identity_matches(b, record)
    assert not module.identity_matches(b, record | {'package': 'device.yaml', 'commit': ''})
    assert not module.identity_matches(b, record | {'commit': '1234567'})
    assert not module.identity_matches(b, record | {'commit': '123456789abd'})


def test_compiler_field_accepts_localized_label_and_freshness_override():
    b = bundle()
    b['bindings']['subjects'] = [{'key': 'room-1', 'kind': 'room', 'label': {'en': 'Room', 'pl': 'Pokój'}, 'fields': {'state': {'entity': 'light.example', 'maxAgeSeconds': 900}}}]
    assert module.validate_bundle(b) == b


def test_rejects_unsupported_evaluator_and_undeclared_role():
    b = bundle()
    b['definitions']['capabilities'][0]['policy']['evaluator'] = 'run_arbitrary_code'
    with pytest.raises(ValueError):
        module.validate_bundle(b)
    b = bundle()
    b['bindings']['subjects'] = [{'key': 'x', 'kind': 'light', 'label': 'Lamp', 'fields': {'unknown': {'entity': 'light.test'}}}]
    with pytest.raises(ValueError):
        module.validate_bundle(b)


def test_rejects_bounded_yaml_alias_expansion():
    b = bundle()
    child = 'value'
    for _ in range(8):
        child = [child] * 10
    b['translations']['en']['meta']['assistant']['status']['examples'] = child
    with pytest.raises(ValueError):
        module.validate_bundle(b)


def test_inventory_with_unknown_shape_does_not_match():
    b=bundle()
    assert not module.identity_matches(b, {'id':'lights','version':'1.0.0','commit':'123456789abc','dev':False})


def test_subject_accepts_translated_room_label_separate_from_key():
    b=bundle()
    b['bindings']['subjects']=[{'key':'lamp','kind':'light','label':'Lamp','roomKey':'stable-room','roomLabel':{'en':'Study','pl':'Gabinet'},'fields':{'state':{'entity':'light.test'}}}]
    assert module.validate_bundle(b)['bindings']['subjects'][0]['roomKey']=='stable-room'
