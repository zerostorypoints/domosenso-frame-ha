"""Validate the pinned canonical private artifact and derive runtime descriptors.

The private artifact never grants access. Generic reads still pass exact source,
registry, inventory, pairing and HA permission checks in assistant_metadata.
"""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from .assistant_contract import ROUTES, _bounded

UNITS = frozenset({'°C', '°F', '%', 'ppm', 'µg/m³', 'dB', 'W', 'V', 'lx', 'index'})
TEXT = re.compile(r'^[^\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]+$')


@lru_cache(maxsize=1)
def validator():
    return Draft202012Validator(json.loads(Path(__file__).with_name('frame-assistant-metadata.schema.json').read_text()))


def _text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or not TEXT.fullmatch(value):
        raise ValueError('Invalid canonical text')
    return value


def _lookup(bundle, key, maximum=120, phrases=False):
    result = {}
    for locale in ('en', 'de', 'pl'):
        value = bundle['translations'][locale]
        try:
            for part in key.split('.'):
                value = value[part]
        except (KeyError, TypeError) as exc:
            raise ValueError('Missing canonical translation') from exc
        if phrases:
            if not isinstance(value, list) or not 1 <= len(value) <= (8 if maximum == 240 else 16) or len(set(value)) != len(value):
                raise ValueError('Invalid canonical phrases')
            result[locale] = [_text(phrase, maximum) for phrase in value]
        else:
            result[locale] = _text(value, maximum)
    return result


def _local(value):
    values = {'en': value} if isinstance(value, str) else value
    return {locale: _text(text, 120) for locale, text in values.items()}


def normalize(bundle):
    _bounded(bundle)
    if len(json.dumps(bundle, ensure_ascii=False, allow_nan=False).encode()) > 256 * 1024 or not validator().is_valid(bundle):
        raise ValueError('Invalid canonical metadata schema')
    owner = bundle['component']['id']
    definitions = bundle['definitions']['capabilities']
    ids, roles = set(), set()
    generic, annotations = [], {}
    for cap in definitions:
        cid = cap['id']
        if not cid.startswith(owner + '.') or cid in ids or 'client' not in cap or cap['revision'] != 1:
            raise ValueError('Invalid canonical capability identity')
        ids.add(cid)
        facts = cap['required_facts'] + cap['optional_facts']
        available = {fact['role'] for fact in facts}
        roles.update(available)
        client = cap['client']; presentation = client['presentation']
        used = set(presentation['summary'])
        for section in presentation['details']:
            used.update(section['roles']); _lookup(bundle, section['label_key'])
        if used - available:
            raise ValueError('Undeclared presentation role')
        title = _lookup(bundle, cap['title_key'])
        description = _lookup(bundle, cap['description_key'], 300)
        anchors = _lookup(bundle, client['anchors_key'], phrases=True)
        examples = _lookup(bundle, cap['examples_key'], 240, phrases=True)
        for values in examples.values():
            for value in values:
                slots = re.findall(r'\{([^{}]*)\}', value)
                if set(slots) - set(client['selectors']) or any(char in re.sub(r'\{[^{}]*\}', '', value) for char in '{}'):
                    raise ValueError('Unsupported example selector')
        annotations[cid] = {'definition': deepcopy(cap), 'title': title, 'description': description,
                            'anchors': anchors, 'examples': examples}
        if cap['policy']['evaluator'] == 'measurement.v1':
            required = cap['required_facts']
            if (cap['query_type'] != 'measurement-query.v1' or cap['answer_type'] != 'measurement.v1'
                    or len(required) != 1 or cap['optional_facts'] or required[0]['type'] != 'number'
                    or len(required[0]['units']) != 1 or required[0]['units'][0] not in UNITS
                    or cap['freshness']['basis'] not in ('measurement', 'reported_state')
                    or cap['freshness']['max_age_seconds'] is None or 'runtime_source' in cap):
                raise ValueError('Unsupported canonical measurement')
            source = required[0]['source']
            generic.append({'id': cid, 'revision': cap['revision'], 'title': title, 'description': description,
                            'anchors': anchors, 'examples': examples, 'selectors': client['selectors'],
                            'source': {'role': required[0]['role'], **deepcopy(source)},
                            'evaluator': {'kind': 'measurement', 'unit': required[0]['units'][0],
                                          'aggregate': 'none', 'maxAgeSeconds': cap['freshness']['max_age_seconds']},
                            'canonical': deepcopy(cap)})
        else:
            route = ('component-query.v1', 'component-description.v1') if cid == owner + '.explain' else ROUTES.get(cid)
            if route != (cap['query_type'], cap['answer_type']) or cap['policy']['evaluator'] != cid:
                raise ValueError('Unsupported canonical evaluator')
    seen, areas, subjects = set(), {}, []
    for row in bundle['bindings']['subjects']:
        if row['key'] in seen or set(row['fields']) - roles:
            raise ValueError('Invalid canonical subject roles')
        seen.add(row['key'])
        subject = {'key': row['key'], 'kind': row['kind'], 'label': _local(row['label']),
                   'aliases': deepcopy(row.get('aliases', {})), 'fields': deepcopy(row['fields'])}
        if 'roomKey' in row:
            area = {'key': row['roomKey'], 'label': _local(row.get('roomLabel', row['roomKey'])),
                    'aliases': deepcopy(row.get('roomAliases', {}))}
            if areas.setdefault(row['roomKey'], area) != area:
                raise ValueError('Conflicting canonical area aliases or labels')
            subject['area'] = area
        subjects.append(subject)
    if any(policy['owner'] != owner for policy in bundle['bindings']['policies']):
        raise ValueError('Invalid canonical policy owner')
    return {'schema': 2, 'component': deepcopy(bundle['component']), 'definitions': generic,
            'bindings': {'subjects': subjects}, 'canonical': deepcopy(bundle), 'annotations': annotations}
