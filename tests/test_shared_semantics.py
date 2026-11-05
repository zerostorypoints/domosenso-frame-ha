"""Same versioned browser/Bridge truth table, independent of a running service."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_assistant_api import pkg

ROOT = Path(__file__).parent / 'fixtures/shared-semantics'
ASSESSMENTS = json.loads((ROOT / 'assessment.json').read_text())['cases']
OPERABILITY = json.loads((ROOT / 'operability.json').read_text())['cases']


@pytest.mark.parametrize('case', ASSESSMENTS, ids=lambda x: x['name'])
def test_assessment_matches_shared_browser_corpus(case):
    semantics = importlib.import_module(pkg.__name__ + '.shared_semantics')
    assert semantics.assessment(case['value'], case['kind'], case['limits']) == case['expected']


@pytest.mark.parametrize('case', OPERABILITY, ids=lambda x: x['name'])
def test_operability_matches_shared_browser_corpus(case):
    semantics = importlib.import_module(pkg.__name__ + '.shared_semantics')
    assert semantics.operability(case['domain'], case['state']) == case['expected']


def test_air_policy_uses_live_canonical_helpers_and_detects_conflicting_legacy_alias():
    runtime = importlib.import_module(pkg.__name__ + '.assistant_data')
    policy = {'metric': 'co2', 'entity': 'sensor.air', 'unit': 'ppm', 'levels': [800, 1200],
              'higher_is_better': False, 'helpers': {'fair': 'input_number.fair', 'poor': 'input_number.poor'}}
    values = {'input_number.fair': '850', 'input_number.poor': '1250', 'input_number.raised': '900'}
    state = lambda eid: SimpleNamespace(state=values[eid])
    selected, error = runtime.resolve_air_policy('co2', 'sensor.air', 'ppm', None, [policy], state, lambda _: True)
    assert not error and selected['levels'] == [850, 1250]
    policy['helpers'] = {'fair': 'input_number.fair', 'raised': 'input_number.raised'}
    assert runtime.resolve_air_policy('co2', 'sensor.air', 'ppm', None, [policy], state, lambda _: True) == (None, 'conflicting_policy')
