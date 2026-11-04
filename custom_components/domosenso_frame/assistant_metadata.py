"""Resolve the v2 read catalogue and report facts from authorized HA sources."""

from datetime import datetime
from functools import lru_cache
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
from statistics import mean

import yaml
from jsonschema import Draft202012Validator

from .assistant_contract import _bounded, identity_matches, load_directory

DIRECTORY = 'assistant-metadata'
MAX_CATALOG_BYTES = 256 * 1024
MAX_ANSWER_BYTES = 128 * 1024
PAGE_SIZE = 32
SUPPORTED = frozenset({'measurement.v1', 'selector.v1'})


def encoded(value):
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()


def token(key, *parts):
    return hmac.new(key, encoded(parts), hashlib.sha256).hexdigest()


@lru_cache(maxsize=1)
def _validator():
    path = Path(__file__).with_name('frame-assistant-metadata.schema.json')
    return Draft202012Validator(json.loads(path.read_text(encoding='utf-8')))


class MetadataLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError('Invalid metadata alias')
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        keys = [key.value for key, _ in node.value if isinstance(key, yaml.ScalarNode)]
        if len(keys) != len(node.value) or len(keys) != len(set(keys)):
            raise ValueError('Invalid metadata keys')
        return super().construct_mapping(node, deep)


def validate_bundle(bundle):
    _bounded(bundle)
    if len(encoded(bundle)) > MAX_CATALOG_BYTES or not _validator().is_valid(bundle):
        raise ValueError('Invalid metadata schema')
    owner = bundle['component']['id']
    definitions = bundle['definitions']
    ids = {cap['id'] for cap in definitions}
    if len(ids) != len(definitions) or any(not name.startswith(owner + '.') for name in ids):
        raise ValueError('Invalid capability owner or identity')
    patterns = {}
    for cap in definitions:
        source = cap['source']
        pattern = re.sub(r'<[^>]+>', '[a-z0-9_]+', re.escape(source['id']))
        patterns.setdefault(source['role'], []).append(re.compile(pattern))
    subjects = bundle['bindings']['subjects']
    if len({row['key'] for row in subjects}) != len(subjects):
        raise ValueError('Duplicate metadata subject')
    areas = {}
    for row in subjects:
        area = row.get('area')
        if area and areas.setdefault(area['key'], area) != area:
            raise ValueError('Conflicting metadata area')
        for role, field in row['fields'].items():
            if not any(rule.fullmatch(field['entity']) for rule in patterns.get(role, ())):
                raise ValueError('Invalid metadata binding')
    return bundle


def load_metadata(config_dir):
    return load_directory(config_dir, DIRECTORY, validate_bundle,
                          parser=lambda raw: yaml.load(raw, Loader=MetadataLoader), limit=MAX_CATALOG_BYTES)


def active_metadata(artifacts, records):
    if not isinstance(records, list) or len(records) > 256:
        return {}
    verified = {}
    for cid, bundle in artifacts.items():
        matches = [row for row in records if isinstance(row, dict) and row.get('id') == cid]
        if len(matches) == 1 and identity_matches(bundle, matches[0], lambda value: value):
            verified[cid] = bundle
    return verified


@lru_cache(maxsize=256)
def _entity_pattern(source_id):
    return re.compile(re.sub(r'<[^>]+>', '[a-z0-9_]+', re.escape(source_id)))


def _declared_field(cap, subject):
    source = cap['source']
    field = subject['fields'].get(source['role'])
    return field if field and _entity_pattern(source['id']).fullmatch(field['entity']) else None


def resolve(artifacts, grants, permitted, registry_id, secret, scope, area_id_for):
    """Use an explicit capability revision, source binding, registry identity and HA READ."""
    if not isinstance(grants, list) or len(grants) > 512:
        return {}
    catalog = {}
    for owner, bundle in sorted(artifacts.items()):
        subjects = {row['key']: row for row in bundle['bindings']['subjects']}
        for cap in bundle['definitions']:
            cap_id = cap['id']
            rows = {}
            for grant in grants:
                if (not isinstance(grant, dict) or grant.get('capability') != cap_id
                        or type(grant.get('revision')) is not int or grant['revision'] != cap['revision']
                        or grant.get('role') != cap['source']['role']):
                    continue
                subject_key = grant.get('subject')
                subject = subjects.get(subject_key) if isinstance(subject_key, str) else None
                field = _declared_field(cap, subject) if subject else None
                if not field or 'attribute' in grant or field != {'entity': grant.get('entity')}:
                    continue
                eid = field['entity']
                identity = registry_id(eid)
                if not identity or identity != grant.get('registryId') or not permitted(eid):
                    continue
                subject_id = token(secret, scope, owner, 'subject', subject_key)
                area = subject.get('area')
                if 'area' in cap['selectors'] and area and area_id_for(eid) != area['key']:
                    continue
                area_id = token(secret, scope, owner, 'area', area['key']) if area else None
                rows[subject_id] = (subject, field, identity, area_id)
            if rows:
                catalog[cap_id] = (cap, rows)
    if len(catalog) > 64:
        raise ValueError('Metadata catalogue too large')
    return catalog


def describe(catalog):
    descriptors = []
    for cap, rows in catalog.values():
        selectors = {}
        for subject_id, (subject, _, _, area_id) in rows.items():
            if 'subject' in cap['selectors']:
                selectors[subject_id] = ('subject', subject)
            if 'area' in cap['selectors'] and area_id:
                selectors[area_id] = ('area', subject['area'])
        descriptors.append({
            **{name: cap[name] for name in ('id', 'revision', 'title', 'description', 'anchors', 'examples')},
            'operation': 'read', 'availability': 'configured',
            'arguments': cap['selectors'],
            'answer': {'kind': 'measurement.v1', 'unit': cap['evaluator']['unit'],
                       'aggregate': cap['evaluator']['aggregate']},
            'selectors': [{'id': identifier, 'type': kind, 'label': value['label'], 'aliases': value.get('aliases', {})}
                          for identifier, (kind, value) in selectors.items()],
        })
    if len(encoded(descriptors)) > MAX_CATALOG_BYTES - 1024:
        raise ValueError('Metadata catalogue too large')
    return descriptors


def evaluate(cap, rows, arguments, state_get, verified, now):
    if (not isinstance(arguments, dict) or len(arguments) > 1
            or set(arguments) - set(cap['selectors']) or any(not isinstance(v, str) for v in arguments.values())):
        raise ValueError('Invalid metadata arguments')
    if 'subject' in arguments:
        rows = {key: value for key, value in rows.items() if key == arguments['subject']}
    if 'area' in arguments:
        rows = {key: value for key, value in rows.items() if value[3] == arguments['area']}
    if not rows:
        raise ValueError('Unknown metadata selector')
    policy = cap['evaluator']
    facts = []
    current = []
    for key, (subject, field, _, _) in rows.items():
        eid = field['entity']
        state = state_get(eid)
        attrs = getattr(state, 'attributes', {})
        report = getattr(state, 'last_reported', None)
        report = report if isinstance(report, datetime) and report.utcoffset() is not None else None
        status = 'unavailable'
        value = None
        if state and not attrs.get('restored'):
            if not verified(eid) or report is None or report > now:
                status = 'unverified'
            elif (now - report).total_seconds() > policy['maxAgeSeconds']:
                status = 'stale'
            elif attrs.get('unit_of_measurement') != policy['unit']:
                status = 'invalid_unit'
            else:
                try:
                    candidate = float(state.state)
                    if math.isfinite(candidate):
                        status, value = 'current', candidate
                        current.append(candidate)
                except (TypeError, ValueError, OverflowError):
                    pass
        facts.append({'subjectId': key, 'label': subject['label'], 'unit': policy['unit'],
                      'status': status, 'value': value, 'sourceRef': key,
                      'reportedAt': report.isoformat() if report else None,
                      'freshnessBasis': 'source_report' if status in ('current', 'stale') else 'unknown'})
    status = 'complete' if len(current) == len(rows) else 'partial' if current else 'unknown'
    summary = None
    if status == 'complete' and policy['aggregate'] == 'mean':
        summary = {'kind': 'measurement', 'value': mean(current), 'unit': policy['unit']}
    if policy['aggregate'] == 'count' and current:
        summary = {'kind': 'count', 'value': len(current), 'unit': 'subject'}
    return {'facts': facts[:64], 'coverage': {'eligible': len(rows), 'evaluated': len(current),
                                            'omitted': max(0, len(facts) - 64)},
            'assessment': {'status': status, 'summary': summary}}


def source_candidates(artifacts, registry_id):
    """Admin-only proposals for exact existing sources. Discovery never grants access."""
    candidates = []
    for bundle in artifacts.values():
        for cap in bundle['definitions']:
            for subject in bundle['bindings']['subjects']:
                field = _declared_field(cap, subject)
                identity = registry_id(field['entity']) if field else None
                if identity:
                    candidates.append({
                        'title': cap['title'], 'label': subject['label'],
                        'grant': {'capability': cap['id'], 'revision': cap['revision'],
                                  'subject': subject['key'], 'role': cap['source']['role'], 'registryId': identity, **field},
                    })
                if len(candidates) > 512:
                    return []
    return candidates


def validate_sources(rows):
    required = {'capability', 'revision', 'subject', 'entity', 'role', 'registryId'}
    if not isinstance(rows, list) or len(rows) > 512:
        raise ValueError('Invalid metadata read selections')
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != required
                or type(row['revision']) is not int or not 1 <= row['revision'] <= 65535
                or any(not isinstance(row[key], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', row[key])
                       for key in set(row) - {'revision'})
                or not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+', row['entity'])):
            raise ValueError('Invalid metadata read selection')
        identity = (row['capability'], row['subject'])
        if identity in seen:
            raise ValueError('Duplicate metadata read selection')
        seen.add(identity)


def validate_source_change(previous, proposed, candidates):
    validate_sources(proposed)
    allowed = {encoded(item['grant']) for item in candidates} | {encoded(item) for item in previous}
    if any(encoded(item) not in allowed for item in proposed):
        raise ValueError('Metadata sources changed. Reload before granting access.')
