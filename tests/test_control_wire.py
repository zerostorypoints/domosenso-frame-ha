import json
from pathlib import Path
from jsonschema import Draft202012Validator


def test_native_protocol_examples_are_closed_and_cover_required_cases():
    root = Path(__file__).parents[1]
    fixture = root / 'tests/fixtures/shared-controls.json'
    assert fixture.exists(), 'Export native control protocol fixture'
    data = json.loads(fixture.read_text())
    schema = json.loads((root / 'custom_components/domosenso_frame/control-wire.schema.json').read_text())
    for case in data['cases'].values():
        Draft202012Validator(schema).validate(case['request'])
        Draft202012Validator(schema).validate(case['response'])
    assert {'accepted', 'ambiguity', 'partialScope', 'staleRevision', 'windBlock', 'unknown'} <= data['cases'].keys()
    assert 'entity_id' not in fixture.read_text()


def test_export_is_generated_by_actual_routes_and_executor():
    from generate_control_wire import build
    fixture=json.loads((Path(__file__).parent/'fixtures/shared-controls.json').read_text())
    assert fixture==build()
    cases=fixture['cases']
    assert cases['awningAccepted']['response']['status']=='accepted'
    assert cases['coverAccepted']['response']['status']=='accepted'
    assert cases['partialDispatch']['response']['reason']=='partial_failure'
    assert [o['status'] for o in cases['partialDispatch']['response']['outcomes']]==['accepted','failed']
    assert cases['partialScopeDenied']['response']['reason']=='partial_scope'
    assert cases['partialScopeChosen']['response']['status']=='accepted'


def test_cover_group_and_missing_wind_exports_use_current_routes():
    from generate_control_wire import build
    cases=build()['cases']
    assert cases['coverGroupAccepted']['response']['status']=='accepted'
    assert len(cases['coverGroupAccepted']['response']['outcomes'])==1
    assert cases['coverGroupExcluded']['response']['status']=='failed'
    assert cases['missingWindRetract']['response']['status']=='accepted'
    assert cases['missingWindExtend']['response']['reason']=='wind_block'
