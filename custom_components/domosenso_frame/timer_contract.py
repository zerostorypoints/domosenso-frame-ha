"""Closed compiler artifact adapter. Metadata is descriptive and never a grant."""
import json
from functools import lru_cache
from pathlib import Path
from jsonschema import Draft202012Validator
from . import assistant_contract as reads
from .timers import ACTIONS


@lru_cache(maxsize=1)
def validator():
    return Draft202012Validator(json.loads(Path(__file__).with_name('assistant-actions-profile.schema.json').read_text()))


def validate_bundle(bundle):
    reads._bounded(bundle)
    if len(json.dumps(bundle, allow_nan=False).encode()) > reads._MAX_BYTES or next(validator().iter_errors(bundle), None): raise ValueError('Invalid action artifact')
    if bundle['component']['id'] != 'timers': raise ValueError('Invalid action owner')
    ids = set()
    for action in bundle['definitions']['actions']:
        name = action['id']
        if name not in ACTIONS or name in ids or type(action['revision']) is not int or action['effect'] != name.split('.')[1]: raise ValueError('Unsupported action')
        ids.add(name)
        expected = {'durationSeconds':{'type':'integer','minimum':1,'maximum':86400},'confirmReplace':{'type':'boolean'}} if name == 'timers.start' else {}
        if action['parameters'] != expected or action['confirmation'] != ('replace_active' if name == 'timers.start' else 'none'): raise ValueError('Invalid action parameters')
        for lang in ('en','de','pl'):
            for field in ('title_key','description_key','examples_key'):
                value = bundle['translations'][lang]
                for key in action[field].split('.'):
                    if not isinstance(value, dict) or key not in value: raise ValueError('Missing translation')
                    value = value[key]
                if field == 'examples_key':
                    if not isinstance(value, list) or not value or any(not isinstance(v,str) or not v.strip() for v in value): raise ValueError('Invalid examples')
                elif not isinstance(value,str) or not value.strip(): raise ValueError('Invalid translation')
    keys = set()
    for subject in bundle['bindings']['subjects']:
        if subject['key'] in keys or subject['kind'] != 'timer' or set(subject['fields']) != {'timer'}: raise ValueError('Invalid timer binding')
        keys.add(subject['key'])
        field = subject['fields']['timer']
        if not field['entity'].startswith('timer.') or 'attribute' in field: raise ValueError('Invalid timer source')
    if bundle['bindings']['policies']: raise ValueError('Timer actions have no policies')
    return bundle


def identity_matches(bundle, record):
    return reads.identity_matches(bundle, record, validate_bundle)


def load_directory(config_dir):
    loaded, errors = reads.load_directory(config_dir, 'assistant-actions', validate_bundle)
    # These separately versioned control owners are not timer artifacts. Their
    # validity is handled by control_profiles; never relax timer validation.
    return loaded, {k:v for k,v in errors.items() if k not in ('lights.yaml','awnings.yaml')}


def active(artifacts, records):
    if not isinstance(records, list) or len(records) > 256: return {}
    result = {}
    for key, bundle in artifacts.items():
        matching = [r for r in records if isinstance(r,dict) and r.get('id') == key]
        if len(matching) == 1 and identity_matches(bundle, matching[0]): result[key] = bundle
    return result
