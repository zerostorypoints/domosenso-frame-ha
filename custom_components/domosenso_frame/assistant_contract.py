"""Bounded, non-executable assistant artifact loading primitives.

This module deliberately does not authorize or activate artifacts. A caller must
verify provenance and apply endpoint mappings and per-user entity permissions.
"""
import math
import json
from functools import lru_cache
from jsonschema import Draft202012Validator
import os
import re
import stat
from pathlib import Path

import yaml

_ID = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
_ENTITY = re.compile(r'^[a-z_]+\.[a-z0-9_]+$')
_MAX_BYTES = 1024 * 1024


def _shape(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('Invalid artifact fields')


def _bounded(value, depth=0, budget=None):
    if budget is None:
        budget = [10000]
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError('Artifact expansion too large')
    if depth > 16:
        raise ValueError('Artifact nesting too deep')
    if isinstance(value, dict):
        if len(value) > 256 or any(not isinstance(k, str) or len(k) > 128 for k in value):
            raise ValueError('Invalid artifact object')
        for item in value.values():
            _bounded(item, depth + 1, budget)
    elif isinstance(value, list):
        if len(value) > 256:
            raise ValueError('Artifact list too long')
        for item in value:
            _bounded(item, depth + 1, budget)
    elif isinstance(value, str):
        if len(value) > 4096:
            raise ValueError('Artifact string too long')
    elif type(value) is float and not math.isfinite(value):
        raise ValueError('Non-finite artifact value')
    elif value is not None and type(value) not in (bool, int, float):
        raise ValueError('Invalid artifact value')


ROUTES = {'climate-air.temperature': ('temperature-query.v1', 'temperature.v1'), 'climate-air.air_readings': ('air-query.v1', 'air.v1'), 'climate-air.air_assessment': ('air-query.v1', 'air.v1'), 'climate-air.humidity': ('environment-query.v1', 'environment.v1'), 'climate-air.status': ('environment-query.v1', 'environment.v1'), 'plants.watering_need': ('plant-query.v1', 'plant-watering.v1'), 'plants.status': ('plant-query.v1', 'plant-status.v1'), 'plants.last_watered': ('plant-query.v1', 'plant-event.v1'), 'plants.water_in': ('plant-query.v1', 'plant-estimate.v1'), 'media.now_playing': ('media-query.v1', 'media.v1'), 'media.status': ('media-query.v1', 'media.v1'), 'media.mains': ('reading-query.v1', 'reading.v1'), 'lights.status': ('light-query.v1', 'lights.v1'), 'calendar.agenda': ('calendar-query.v1', 'calendar.v1'), 'weather.current': ('weather-query.v1', 'weather.v1'), 'weather.forecast': ('weather-query.v1', 'weather.v1'), 'bermuda-presence.observations': ('presence-query.v1', 'presence.v1'), 'awnings.status': ('awning-query.v1', 'awnings.v1'), 'awnings.wind': ('awning-query.v1', 'awnings.v1'), 'audio-sensor.level': ('audio-query.v1', 'audio-level.v1'), 'backup.status': ('status-query.v1', 'backup.v1'), 'ramka.status': ('status-query.v1', 'frame.v1')}

@lru_cache(maxsize=1)
def _validator():
    return Draft202012Validator(json.loads(Path(__file__).with_name('assistant-profile.schema.json').read_text(encoding='utf-8')))


def validate_bundle(bundle):
    _bounded(bundle)
    try:
        if len(json.dumps(bundle, allow_nan=False, ensure_ascii=False).encode('utf-8')) > _MAX_BYTES:
            raise ValueError('Expanded artifact too large')
    except (TypeError, ValueError, RecursionError) as err:
        raise ValueError('Invalid artifact data') from err
    if next(_validator().iter_errors(bundle), None):
        raise ValueError('Invalid artifact schema')
    component = bundle['component']['id']
    roles, ids = set(), set()
    for cap in bundle['definitions']['capabilities']:
        cid = cap['id']
        route = ('component-query.v1', 'component-description.v1') if cid == component + '.explain' else ROUTES.get(cid)
        if not cid.startswith(component + '.') or cid in ids or cap['revision'] != 1 or cap['operation'] != 'read' or cap['policy']['evaluator'] != cid or route != (cap['query_type'], cap['answer_type']):
            raise ValueError('Unsupported capability')
        ids.add(cid)
        roles.update(f['role'] for f in cap['required_facts'] + cap['optional_facts'])
        for lang in ('en', 'de', 'pl'):
            for name in ('title_key', 'description_key', 'examples_key'):
                value = bundle['translations'][lang]
                for key in cap[name].split('.'):
                    if not isinstance(value, dict) or key not in value:
                        raise ValueError('Missing translation')
                    value = value[key]
    keys = set()
    for subject in bundle['bindings']['subjects']:
        if subject['key'] in keys or set(subject['fields']) - roles:
            raise ValueError('Invalid binding')
        keys.add(subject['key'])
    if any(p['owner'] != component for p in bundle['bindings']['policies']):
        raise ValueError('Invalid policy owner')
    return bundle


def identity_matches(bundle, inventory):
    """Compare a *normalized*, explicitly installed inventory record; unknown shapes fail closed."""
    if not isinstance(inventory, dict) or inventory.get('package') != '' or inventory.get('installed') is False:
        return False
    try:
        component = validate_bundle(bundle)['component']
        actual, expected = inventory.get('commit'), component['commit']
        if not isinstance(actual, str) or not re.fullmatch(r'[0-9a-f]{12,40}', actual) or not re.fullmatch(r'[0-9a-f]{12,40}', expected):
            return False
        return (all(type(inventory.get(k)) is type(component[k]) and inventory[k] == component[k] for k in ('id', 'version', 'dev'))
                and (actual.startswith(expected) or expected.startswith(actual)))
    except (KeyError, ValueError, TypeError):
        return False



def load_directory(config_dir):
    """Read fixed-directory regular YAML using directory-relative no-follow opens."""
    loaded, errors = {}, {}
    descriptors = []
    try:
        flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        root_fd = os.open(Path(config_dir), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        descriptors.append(root_fd)
        try:
            domosenso_fd = os.open('domosenso', flags, dir_fd=root_fd)
            descriptors.append(domosenso_fd)
            directory_fd = os.open('assistant', flags, dir_fd=domosenso_fd)
            descriptors.append(directory_fd)
        except FileNotFoundError:
            return loaded, errors
        except OSError:
            return {}, {'directory': 'Invalid assistant directory'}
        for name in sorted(os.listdir(directory_fd)):
            if not name.endswith('.yaml'):
                continue
            try:
                stem = name[:-5]
                if not _ID.fullmatch(stem):
                    raise ValueError('Invalid filename')
                fd = os.open(name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory_fd)
                with os.fdopen(fd, 'rb') as file:
                    if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                        raise ValueError('Not a regular file')
                    raw = file.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES:
                    raise ValueError('Artifact too large')
                bundle = validate_bundle(yaml.safe_load(raw))
                if bundle['component']['id'] != stem:
                    raise ValueError('Filename identity mismatch')
                loaded[stem] = bundle
            except (OSError, ValueError, TypeError, yaml.YAMLError, RecursionError):
                errors[name] = 'Invalid assistant artifact'
        return loaded, errors
    except OSError:
        return {}, {'directory': 'Invalid assistant directory'}
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
