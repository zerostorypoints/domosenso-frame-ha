"""Reproducible fictional v1-to-v2 migration corpus; never contacts Home Assistant."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import yaml
from datetime import datetime, timezone
import types

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('frame_acceptance_package')
pkg.__path__ = [str(ROOT / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg
projection = importlib.import_module(pkg.__name__ + '.assistant_metadata_legacy')


def export(catalog_root=None):
    cases = []
    hashes = {}
    for file in sorted((ROOT / 'tests/fixtures').glob('assistant-*.json')):
        if file.name == 'assistant-fixture.json':
            continue  # The standalone temperature answer also occurs in the package fixture.
        hashes[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
        value = json.loads(file.read_text())
        if 'capabilities' in value:
            caps = {cap['id']: cap for cap in value['capabilities']['capabilities']}
            pairs = [(caps[key], answer) for key, answer in value['answers'].items()]
        else:
            pairs = [(item['capability'], item['answer']) for item in value.values()]
        for cap, answer in pairs:
            descriptor = projection.describe(cap)
            catalog = {'version': 2, 'catalogRevision': 'fixture-revision', 'asOf': answer['asOf'],
                       'capabilities': [descriptor]}
            result = {'version': 2, 'catalogRevision': catalog['catalogRevision'],
                      'capability': cap['id'], 'revision': cap['revision'],
                      'asOf': answer['asOf'], 'timezone': answer.get('timezone', 'UTC'),
                      **projection.project(answer, cap)}
            cases.append({'fixture': file.name, 'v1': {'capability': cap, 'answer': answer},
                          'catalog': catalog, 'answer': result})
    if catalog_root:
        runtime_class = importlib.import_module(pkg.__name__ + '.assistant_extended').ExtendedRuntime
        artifacts = {}
        for file in sorted((catalog_root / 'components').glob('*/assistant.yaml')):
            manifest = yaml.safe_load((file.parent / 'component.yaml').read_text())
            artifacts[file.parent.name] = {'component': {'id': file.parent.name, 'version': manifest['version']},
                'definitions': yaml.safe_load(file.read_text()), 'bindings': {'subjects': [], 'policies': []},
                'translations': {lang: json.loads((file.parent / f'translations/{lang}.json').read_text())
                                 for lang in ('en', 'de', 'pl')}}
            hashes[str(file.relative_to(catalog_root))] = hashlib.sha256(file.read_bytes()).hexdigest()
        rt = runtime_class('00000000-0000-0000-0000-000000000001', lambda _: None, lambda _: True,
                           lambda: datetime(2026, 11, 5, 12, tzinfo=timezone.utc), artifacts=artifacts)
        endpoint = {'id': 'frame', 'assistant': {'explanations': sorted(artifacts)}}
        for cap in rt.capabilities(endpoint, [{'queryType': 'component-query.v1', 'answerType': 'component-description.v1'}]):
            answer = rt.query(endpoint, cap['id'], 1, cap['answerType'], {})
            add_case(cases, 'canonical-catalog', cap, answer)
    timers_file = ROOT / 'tests/fixtures/shared-timers.json'
    timers = json.loads(timers_file.read_text())
    hashes[timers_file.name] = hashlib.sha256(timers_file.read_bytes()).hexdigest()
    timer_caps = {c['id']: c for c in timers['capabilities']['capabilities']}
    for key in ('time', 'idle', 'active', 'paused', 'completed', 'cancelled', 'unavailable'):
        answer = timers[key]
        add_case(cases, 'shared-timers.' + key, timer_caps[answer['capability']], answer)
    return {'sourceFiles': hashes, 'cases': cases}


def add_case(cases, name, cap, answer):
    descriptor = projection.describe(cap)
    catalog = {'version': 2, 'catalogRevision': 'fixture-revision', 'asOf': answer['asOf'], 'capabilities': [descriptor]}
    result = {'version': 2, 'catalogRevision': catalog['catalogRevision'], 'capability': cap['id'],
              'revision': cap['revision'], 'asOf': answer['asOf'], 'timezone': answer.get('timezone', 'UTC'),
              **projection.project(answer, cap)}
    cases.append({'fixture': name, 'v1': {'capability': cap, 'answer': answer}, 'catalog': catalog, 'answer': result})


if __name__ == '__main__':
    Path(sys.argv[1]).write_text(json.dumps(export(Path(sys.argv[2]) if len(sys.argv) > 2 else None), indent=2, ensure_ascii=False) + '\n')

    if len(sys.argv) > 3:
        catalog = json.loads(Path(sys.argv[3]).read_text())
        for cap in catalog['capabilities']:
            if cap['id'] in projection.ANCHORS:
                en, pl = projection.ANCHORS[cap['id']]
                cap['anchors'] = {'en': en.split('|'), 'pl': pl.split('|')}
                cap['options'] = projection._options(cap['id'])
        Path(sys.argv[1]).with_name('candidate-catalog.json').write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + '\n')
