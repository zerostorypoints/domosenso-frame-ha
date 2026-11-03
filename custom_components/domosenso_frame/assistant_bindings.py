"""Explicit component exposure. A stored reference is not a copied manual adapter."""
import copy
import re


def source_identity(field):
    return {key: field[key] for key in ('entity', 'attribute') if key in field}


def validate_selections(value):
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError('Invalid component sources')
    seen = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {'component', 'subject', 'fields'}:
            raise ValueError('Invalid component source fields')
        cid, key = row['component'], row['subject']
        if not isinstance(cid, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', cid):
            raise ValueError('Invalid component source')
        if not isinstance(key, str) or not 1 <= len(key) <= 160 or any(ord(c) < 32 for c in key) or (cid, key) in seen:
            raise ValueError('Invalid component subject')
        seen.add((cid, key))
        fields = row['fields']
        if not isinstance(fields, dict) or not 1 <= len(fields) <= 32:
            raise ValueError('Invalid component roles')
        for role, field in fields.items():
            if not isinstance(role, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,79}', role):
                raise ValueError('Invalid component role')
            if not isinstance(field, dict) or set(field) - {'entity', 'attribute'} or 'entity' not in field:
                raise ValueError('Invalid component field')
            eid = field['entity']
            if not isinstance(eid, str) or not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+', eid) or eid.partition('.')[0] in {'script','automation','scene','button','service'}:
                raise ValueError('Invalid component entity')
            if 'attribute' in field and (not isinstance(field['attribute'], str) or not re.fullmatch(r'[A-Za-z0-9_]{1,80}', field['attribute'])):
                raise ValueError('Invalid component attribute')
    return value


def selected_subjects(endpoint, artifacts, capability=None):
    """Resolve only exact role/source matches in *currently verified* artifacts."""
    result = []
    for chosen in endpoint.get('assistant', {}).get('component_sources', []):
        bundle = artifacts.get(chosen['component'])
        if not bundle:
            continue
        subject = next((row for row in bundle['bindings']['subjects'] if row['key'] == chosen['subject']), None)
        if not subject:
            continue
        fields = {role: copy.deepcopy(field) for role, field in subject['fields'].items()
                  if chosen['fields'].get(role) == source_identity(field)}
        if fields:
            result.append({**copy.deepcopy(subject), 'fields': fields, '_component': chosen['component']})
    from .assistant_selection import dynamic_subjects
    for subject in dynamic_subjects(endpoint, artifacts, capability):
        match=next((s for s in result if (s['_component'],s['key'])==(subject['_component'],subject['key'])),None)
        if match:match['fields'].update(subject['fields'])
        else:result.append(subject)
    return result


def validate_new_selections(previous, proposed, artifacts):
    """An unchanged stale grant may be retained, but never extended from stale metadata."""
    validate_selections(proposed)
    for selection in proposed:
        if selection in previous:
            continue
        resolved = selected_subjects({'assistant': {'component_sources': [selection]}}, artifacts)
        if len(resolved) != 1 or set(resolved[0]['fields']) != set(selection['fields']):
            raise ValueError('Component sources changed or metadata is unavailable. Reload before selecting sources.')


def catalog(artifacts, errors, inventory, endpoint, states):
    """Admin-only proposals; neither discovery nor opening the panel grants access."""
    known = {r['id'] for r in inventory if isinstance(r, dict) and isinstance(r.get('id'), str)} if isinstance(inventory, list) else set()
    selections = endpoint.get('assistant', {}).get('component_sources', [])
    known.update(row['component'] for row in selections)
    result = []
    for cid in sorted(known | set(artifacts) | set(errors)):
        bundle = artifacts.get(cid)
        subjects = []
        if bundle:
            for row in bundle['bindings']['subjects']:
                if not row['fields'] or row['kind'] == 'component':
                    continue
                selection = {'component':cid,'subject':row['key'], 'fields':{role:source_identity(field) for role,field in row['fields'].items()}}
                subjects.append({'key':row['key'], 'kind':row['kind'], 'label':copy.deepcopy(row['label']),
                                 'selection':selection, 'missing':sum(states.get(f['entity']) is None for f in row['fields'].values())})
        result.append({'id':cid, 'status':'verified' if bundle else errors.get(cid, 'missing_metadata'), 'subjects':subjects, 'explanation':bool(bundle and any(c['id']==cid+'.explain' for c in bundle['definitions']['capabilities']))})
    from .assistant_selection import proposals
    for row in result:
        row['scopes']=proposals(row['id'],artifacts[row['id']]) if row['id'] in artifacts else []
    return {'components':result}
