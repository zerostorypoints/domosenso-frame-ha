"""Exact control sources, current group graph, and administrator grant proposals.

Discovery never grants writes. A proposal hashes the actual binding and registry
identity, including upstream group membership and safety inputs.
"""
import copy
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from jsonschema import Draft202012Validator
from . import assistant_contract as reads

ACTIONS = {
    'lights.turn_on': ('light', 'turn_on', 0),
    'lights.turn_off': ('light', 'turn_off', 0),
    'covers.open': ('cover', 'open_cover', 1),
    'covers.close': ('cover', 'close_cover', 2),
    'covers.stop': ('cover', 'stop_cover', 8),
    'awnings.extend': ('cover', 'open_cover', 1),
    'awnings.retract': ('cover', 'close_cover', 2),
    'awnings.stop': ('cover', 'stop_cover', 8),
}
WINDOW = {'blind', 'curtain', 'shade', 'shutter'}
EXCLUDED = {'garage', 'gate', 'door', 'damper'}
DEFAULT = {'enabled': False, 'grants': [], 'mappings': []}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def validator():
    return Draft202012Validator(json.loads(Path(__file__).with_name('control-actions-profile.schema.json').read_text()))


def validate_bundle(bundle):
    reads._bounded(bundle)
    if len(json.dumps(bundle, allow_nan=False).encode()) > reads._MAX_BYTES or next(validator().iter_errors(bundle), None):
        raise ValueError('Invalid control artifact')
    owner = bundle['component']['id']
    if owner not in ('lights', 'awnings'): raise ValueError('Unsupported control owner')
    seen = set()
    for action in bundle['definitions']['actions']:
        name = action['id']
        if (name not in ACTIONS or not name.startswith(owner + '.') or name in seen
                or type(action['revision']) is not int or action['revision'] != 1
                or action['effect'] != name.split('.')[1] or action['parameters'] != {}
                or action['confirmation'] != 'none' or action['operation'] != 'write'
                or action['action_type'] != 'control-action.v1' or action['result_type'] != 'control-result.v1'):
            raise ValueError('Unsupported control action')
        seen.add(name)
    keys = set()
    for row in bundle['bindings']['subjects']:
        role = 'light' if owner == 'lights' else 'cover'
        if (row['key'] in keys or row['kind'] != ('light' if owner == 'lights' else 'awning')
                or set(row['fields']) != {role} or set(row['fields'][role]) != {'entity'}
                or not row['fields'][role]['entity'].startswith(role + '.')):
            raise ValueError('Invalid control binding')
        keys.add(row['key'])
    if bundle['bindings']['policies']: raise ValueError('Unsupported control policy')
    return bundle


def load_directory(config_dir):
    loaded, errors = reads.load_directory(config_dir, 'assistant-actions', validate_bundle)
    return loaded, {k:v for k,v in errors.items() if k != 'timers.yaml'}


def active(artifacts, records):
    if not isinstance(records, list) or len(records) > 256: return {}
    result = {}
    for cid, bundle in artifacts.items():
        matches = [r for r in records if isinstance(r, dict) and r.get('id') == cid]
        if len(matches) == 1 and reads.identity_matches(bundle, matches[0], validate_bundle): result[cid] = bundle
    return result


def validate_mapping(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT) or type(value['enabled']) is not bool:
        raise ValueError('Invalid controls settings')
    def text(value, limit=256):
        if not isinstance(value, str) or not 1 <= len(value) <= limit or any(ord(c) < 32 for c in value): raise ValueError('Invalid control identity')
    def entity(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+', value): raise ValueError('Invalid control source')
    for key in ('grants', 'mappings'):
        rows = value[key]
        if not isinstance(rows, list) or len(rows) > 100: raise ValueError('Too many controls')
        seen = set()
        for row in rows:
            if not isinstance(row, dict): raise ValueError('Invalid control row')
            text(row.get('source'))
            if row['source'] in seen: raise ValueError('Duplicate control source')
            seen.add(row['source']); entity(row.get('entity'))
            if key == 'grants':
                required={'source','entity','bindingRevision','capabilities'}
                if not required <= row.keys() or set(row)-required-{'safetyRevision'}: raise ValueError('Invalid control grant')
                if 'safetyRevision' in row and (not isinstance(row['safetyRevision'],str) or not re.fullmatch('[a-f0-9]{64}',row['safetyRevision'])):raise ValueError('Invalid safety revision')
                if not isinstance(row['bindingRevision'],str) or not re.fullmatch('[a-f0-9]{64}',row['bindingRevision']): raise ValueError('Invalid binding revision')
                caps = row['capabilities']
                if not isinstance(caps,dict) or not caps or any(c not in ACTIONS or type(v) is not int or v != 1 for c,v in caps.items()): raise ValueError('Invalid action grants')
            else:
                if set(row) - {'source','entity','area','coverClass','guard'}: raise ValueError('Invalid control mapping')
                if 'area' in row: text(row['area'],80)
                if 'coverClass' in row and row['coverClass'] not in WINDOW | {'awning'}: raise ValueError('Invalid covering class')
                if 'guard' in row: validate_guard(row['guard'])
    return copy.deepcopy(value)


def validate_guard(guard):
    if not isinstance(guard,dict) or set(guard) != {'wind','last_heard','max_age_seconds'}: raise ValueError('Invalid wind guard')
    for role in ('wind','last_heard'):
        field=guard[role]
        if not isinstance(field,dict) or set(field) != {'entity'} or not isinstance(field['entity'],str): raise ValueError('Invalid wind source')
        domains = 'binary_sensor' if role == 'wind' else '(?:sensor|input_datetime)'
        if not re.fullmatch(domains+r'\.[a-z0-9_]+',field['entity']): raise ValueError('Invalid wind source domain')
    age=guard['max_age_seconds']
    if type(age) not in (int,float) or not math.isfinite(age) or not 0 < age <= 604800: raise ValueError('Invalid wind age')


class Profiles:
    def __init__(self, state_get, all_states, artifacts, registry_get, areas):
        self.state_get, self.all_states, self.artifacts = state_get, all_states, artifacts
        self.registry_get, self.areas = registry_get, areas

    def identity(self, entity):
        entry = self.registry_get(entity)
        if not entry or not entry.get('id') or not entry.get('platform') or not entry.get('unique_id'): raise ValueError('unverified_source')
        return {k:entry.get(k) for k in ('id','platform','unique_id','device_id')}

    def graph(self, entity, upstream=None):
        # Resolution-local memoization: visiting detects cycles; completed nodes
        # avoid rewalking a DAG. Bound edges as well as nodes before iterating.
        domain=entity.partition('.')[0]
        if domain not in ('light','cover') or upstream and domain!='light':raise ValueError('unknown_membership')
        graph, leaves, visiting, completed = {}, set(), set(), set()
        edges = 0
        def visit(eid, require_group=False):
            nonlocal edges
            if eid in visiting or not eid.startswith(domain+'.'): raise ValueError('unknown_membership')
            if eid in completed:
                if require_group and not graph[eid]: raise ValueError('unknown_membership')
                return
            if len(graph) >= 128: raise ValueError('unknown_membership')
            state = self.state_get(eid)
            if not state: raise ValueError('unknown_membership')
            self.identity(eid)
            members = state.attributes.get('entity_id')
            platform = self.registry_get(eid).get('platform')
            if members is None:
                if require_group or platform in ('group','template') or state.attributes.get('component') == 'lights': raise ValueError('unknown_membership')
                graph[eid] = []; leaves.add(eid); completed.add(eid); return
            if not isinstance(members,(list,tuple)) or not 1 <= len(members) <= 128 or edges + len(members) > 512:
                raise ValueError('unknown_membership')
            if any(not isinstance(e,str) for e in members) or len(set(members)) != len(members): raise ValueError('unknown_membership')
            edges += len(members)
            graph[eid] = sorted(members); visiting.add(eid)
            for member in members: visit(member)
            visiting.remove(eid); completed.add(eid)
        if upstream:
            if not self.state_get(entity): raise ValueError('unavailable')
            self.identity(entity);graph[entity]=[upstream];visiting.add(entity);edges=1
            visit(upstream,True)
        else: visit(entity)
        return sorted(leaves), graph

    def sources(self, endpoint):
        sources = []; owned = set()
        for cid,bundle in sorted(self.artifacts().items()):
            for row in bundle['bindings']['subjects']:
                role='light' if row['kind']=='light' else 'cover'
                entity=row['fields'][role]['entity'];owned.add(entity)
                sources.append({**copy.deepcopy(row),'source':f'component:{cid}:{row["key"]}','entity':entity,'provenance':bundle['component'], 'declared':[a['id'] for a in bundle['definitions']['actions']]})
        # Component metadata owns its source even when the artifact is stale/missing.
        # Never fall through a component-tagged proxy to a vanilla mapping.
        for row in endpoint.get('assistant',{}).get('subjects',[]):
            if row['kind'] not in ('light','awning'): continue
            role='light' if row['kind']=='light' else 'cover'
            field=row['fields'].get(role) or row['fields'].get('state',{})
            entity=field.get('entity')
            if not entity or entity in owned or 'attribute' in field: continue
            owned.add(entity);sources.append({**copy.deepcopy(row),'source':'manual:'+row['key'],'entity':entity})
        for domain in ('lights','covers'):
            for row in endpoint.get('studio',{}).get(domain,[]):
                if row['entity'] in owned: continue
                owned.add(row['entity']);sources.append({**copy.deepcopy(row),'source':'studio:'+row['entity'],'kind':'light' if domain=='lights' else 'cover'})
        return sources[:100]

    def resolve(self, row, endpoint):
        row=copy.deepcopy(row);eid=row['entity'];state=self.state_get(eid)
        if not state: raise ValueError('unavailable')
        if not row.get('provenance') and state.attributes.get('component') in ('lights','awnings'): raise ValueError('unverified_source')
        entry=self.registry_get(eid) or {};identity=self.identity(eid)
        mapping=next((m for m in endpoint.get('assistant',{}).get('controls',{}).get('mappings',[]) if m['source']==row['source'] and m['entity']==eid),{})
        areas=self.areas();area=entry.get('area_id') or entry.get('device_area_id');room_key=row.get('roomKey')
        if mapping.get('area'): area=mapping['area']
        elif room_key:
            # Keys/registry IDs are identities; labels never infer membership.
            if room_key not in areas or area and area != room_key: raise ValueError('room_conflict')
            area=room_key
        if area and area not in areas: raise ValueError('room_conflict')
        kind=row['kind'];guard=row.get('guard');cover_traits={}
        if kind=='light':
            members,graph=self.graph(eid,row.get('group',{}).get('entity'))
            caps=['lights.turn_on','lights.turn_off']
        else:
            actual=state.attributes.get('device_class')
            if actual in EXCLUDED: raise ValueError('excluded_class')
            chosen=mapping.get('coverClass')
            if actual in WINDOW | {'awning'} and chosen and chosen != actual: raise ValueError('class_conflict')
            actual=actual if actual in WINDOW | {'awning'} else chosen
            if actual not in WINDOW | {'awning'}: raise ValueError('unverified_class')
            if kind=='awning' and actual != 'awning': raise ValueError('class_conflict')
            kind='awning' if actual=='awning' else 'cover'
            caps=['awnings.extend','awnings.retract','awnings.stop'] if kind=='awning' else ['covers.open','covers.close','covers.stop']
            members,graph=self.graph(eid)
            for member in graph:
                sample=self.state_get(member)
                cls=actual if member==eid else sample.attributes.get('device_class')
                if cls in EXCLUDED:raise ValueError('excluded_class')
                if cls not in WINDOW | {'awning'}:raise ValueError('unverified_class')
                if (cls=='awning') != (kind=='awning'):raise ValueError('class_conflict')
                features=sample.attributes.get('supported_features',0)
                features=int(features) if isinstance(features,int) and not isinstance(features,bool) else 0
                cover_traits[member]={'class':cls,'features':features}
                caps=[c for c in caps if features & ACTIONS[c][2]]
            if guard is None: guard=mapping.get('guard')
        if row.get('declared') is not None: caps=[c for c in caps if c in row['declared']]
        if not caps: raise ValueError('unsupported')
        safety={};safety_revision=None
        if guard:
            try:
                validate_guard(guard)
                for role in ('wind','last_heard'):safety[role]=self.identity(guard[role]['entity'])
                safety_revision=digest({'guard':guard,'registrations':safety})
            except ValueError:
                # Missing/disabled/replaced safety providers must not remove the
                # independently granted unchanged actuator's safe directions.
                safety={}
        graph_areas={e:(self.registry_get(e).get('area_id') or self.registry_get(e).get('device_area_id')) for e in graph}
        if not mapping.get('area') and area and any(a and a!=area for a in graph_areas.values()):raise ValueError('room_conflict')
        binding={'source':row['source'],'entity':eid,'kind':kind,'identity':identity,'provenance':row.get('provenance'),
                 'graph':graph,'graphAreas':graph_areas,'registrations':{e:self.identity(e) for e in graph},'area':area,'guard':guard,'safety':safety,'capabilities':caps}
        if cover_traits:binding['coverTraits']=cover_traits
        if kind=='awning':
            binding.pop('guard');binding.pop('safety')
        grant={'source':row['source'],'entity':eid,'bindingRevision':digest(binding),'capabilities':{c:1 for c in caps}}
        if kind=='awning' and safety_revision:grant['safetyRevision']=safety_revision
        return {**row,'kind':kind,'members':members,'graph':graph,'guard':guard,'area':area,
                'room':{'key':area,'label':areas[area]['name'],'aliases':sorted(areas[area].get('aliases',[]))} if area else None,
                'aliases':sorted(entry.get('aliases',[])), 'grant':grant}

    def catalog(self, endpoint):
        sources=[]
        for row in self.sources(endpoint):
            try: sources.append(self.resolve(row,endpoint))
            except ValueError as error: sources.append({**row,'reason':str(error),'grant':None})
        return {'sources':sources,'areas':[{'id':key,**value} for key,value in sorted(self.areas().items())]}

    def validate_change(self, endpoint, proposed):
        validate_mapping(proposed)
        effective=copy.deepcopy(endpoint);effective.setdefault('assistant',{})['controls']=proposed
        available={r['source']:r for r in self.catalog(effective)['sources']}
        previous=endpoint.get('assistant',{}).get('controls',DEFAULT)
        for grant in proposed['grants']:
            if grant in previous['grants']: continue  # Stale grants may be retained, never inherited.
            row=available.get(grant['source'],{});current=row.get('grant')
            if not current or any(grant[k]!=current[k] for k in ('source','entity','bindingRevision')) or not grant['capabilities'].items() <= current['capabilities'].items():
                raise ValueError('Control sources changed. Reload and review the current binding.')
            if 'awnings.extend' in grant['capabilities'] and grant.get('safetyRevision')!=current.get('safetyRevision'):
                raise ValueError('Wind sources changed. Reload and review the extension binding.')

    def authorized(self, endpoint, permitted):
        controls=endpoint.get('assistant',{}).get('controls',DEFAULT)
        if not controls.get('enabled'): return []
        result=[]
        for row in self.catalog(endpoint)['sources']:
            current=row.get('grant')
            if not current: continue
            grant=next((g for g in controls['grants'] if all(g[k]==current[k] for k in ('source','entity','bindingRevision'))),None)
            if not grant or any(not permitted(e,'read') or not permitted(e,'control') for e in row['graph']): continue
            row['capabilities']=[c for c in current['capabilities'] if grant['capabilities'].get(c)==1]
            row['extensionAuthorized']=bool(current.get('safetyRevision') and current.get('safetyRevision')==grant.get('safetyRevision'))
            if row['capabilities']: result.append(row)
        return result

    def known_room_members(self, area, kind):
        members=set()
        for state in self.all_states():
            eid=state.entity_id;entry=self.registry_get(eid) or {}
            if (entry.get('area_id') or entry.get('device_area_id')) != area: continue
            if kind=='light' and eid.startswith('light.'):
                try: leaves,_=self.graph(eid);members.update(leaves)
                except ValueError:
                    # A separately resolved proxy contributes its leaves below; unknowns
                    # remain visible to the partial-scope calculation.
                    members.add(eid)
            elif eid.startswith('cover.'):
                cls=state.attributes.get('device_class')
                if kind=='awning' and cls=='awning' or kind=='cover' and cls not in EXCLUDED | {'awning'}:
                    try:leaves,_=self.graph(eid);members.update(leaves)
                    except ValueError:members.add(eid)
        return members
