"""Fixture is an actual wire envelope with fictional data."""
import json
from pathlib import Path


def test_fictional_fixture_has_typed_answer_and_no_entity_ids():
    fixture = json.loads((Path(__file__).parent / 'fixtures/assistant-fixture.json').read_text())
    assert fixture['version'] == 1
    assert fixture['capability'] == 'climate-air.temperature'
    assert fixture['answerType'] == 'temperature.v1'
    assert fixture['coverage'] == {'eligible': 1, 'evaluated': 1, 'missing': 0}
    assert 'sensor.' not in json.dumps(fixture)
