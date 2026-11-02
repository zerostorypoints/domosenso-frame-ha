"""Optional HA clock metadata, isolated from legacy strict read profiles."""
from . import assistant_contract as reads


def validate_bundle(bundle):
    reads.validate_bundle(bundle, _runtime=True)
    caps = bundle['definitions']['capabilities']
    if bundle['component']['id'] != 'ramka' or len(caps) != 1:
        raise ValueError('Invalid runtime artifact owner or capabilities')
    cap = caps[0]
    if (cap['id'] != 'ramka.current_time' or cap.get('runtime_source') != 'ha_clock'
            or cap['freshness'] != {'basis': 'provider_response', 'max_age_seconds': None}
            or bundle['bindings']['policies']
            or any(s['kind'] != 'frame' or s['fields'] for s in bundle['bindings']['subjects'])):
        raise ValueError('Unsupported runtime artifact')
    for lang in ('en', 'de', 'pl'):
        for name in ('title', 'description', 'examples'):
            value = bundle['translations'][lang]
            for part in cap[name + '_key'].split('.'): value = value[part]
            if name == 'examples':
                if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
                    raise ValueError('Invalid runtime examples')
            elif not isinstance(value, str) or not value.strip():
                raise ValueError('Invalid runtime text')
    return bundle


def identity_matches(bundle, record):
    return reads.identity_matches(bundle, record, validate_bundle)


def load_directory(config_dir):
    return reads.load_directory(config_dir, 'assistant-runtime', validate_bundle)


def active(artifacts, records):
    if not isinstance(records, list) or len(records) > 256: return {}
    result = {}
    for key, bundle in artifacts.items():
        matches = [r for r in records if isinstance(r, dict) and r.get('id') == key]
        if len(matches) == 1 and identity_matches(bundle, matches[0]): result[key] = bundle
    return result
