"""Request-local display projection from the same resolved read subjects."""
import copy
from .assistant_data import merge_verified_bindings, resolve_air_policy


def project(endpoint,artifacts,permitted,language,state_get=None):
    out=copy.deepcopy(endpoint);studio=out.setdefault('studio',{})
    def label(value):return value if isinstance(value,str) else value.get(language,value.get('en',''))
    for subject in merge_verified_bindings(endpoint,artifacts):
        fields={r:f for r,f in subject['fields'].items() if permitted(f['entity'])}
        if not fields:continue
        name=label(subject.get('label',subject['key']))
        if subject['kind']=='plant':
            primary=fields.get('plant') or fields.get('moisture')
            if not primary:continue
            rows=studio.setdefault('plants',[])
            if any(r['entity']==primary['entity'] for r in rows):continue
            limits=subject.get('limits',{});bands={}
            for role in ('moisture','conductivity','brightness','temperature','battery'):
                low,high=limits.get('min_'+role),limits.get('max_'+role)
                if type(low) in (int,float) and type(high) in (int,float) and low<high:bands[role]=[low,high]
            rows.append({'entity':primary['entity'],'label':name,'limits':bands,'_fields':copy.deepcopy(fields),
                         '_plant_entity':fields.get('plant',{}).get('entity')})
        elif subject['kind']=='room':
            # Attribute-backed numeric facts need a dedicated display adapter; don't silently
            # substitute an entity state for its attribute.
            measurements={r:f['entity'] for r,f in fields.items() if r in ('temperature','humidity') and 'attribute' not in f}
            if measurements:
                rows=studio.setdefault('rooms',[{'label':r['label'],'temperature':r['entity']} for r in out.get('temperature',[])])
                if not any(any(r.get(k)==v for k,v in measurements.items()) for r in rows):
                    rows.append({'label':name,**measurements,**({'roomKey':subject['roomKey']} if subject.get('roomKey') else {})})
                if 'temperature' in measurements and not any(r['entity']==measurements['temperature'] for r in out.get('temperature',[])):
                    out.setdefault('temperature',[]).append({'label':name,'entity':measurements['temperature'],'metric':'temperature','indoor':subject.get('indoor',True)})
            for metric in ('co2','pm2_5','voc','nox','score'):
                field=fields.get(metric)
                if field and 'attribute' not in field and not any(r['entity']==field['entity'] and r.get('metric')==metric for r in out.get('air',[])):
                    out.setdefault('air',[]).append({'entity':field['entity'],'label':name,'metric':metric})
    if state_get:
        policies=[p for bundle in artifacts.values() for p in bundle['bindings']['policies']]
        for row in out.get('air',[]):
            if row.get('policy') or not permitted(row['entity']):continue
            sample=state_get(row['entity']);unit=getattr(sample,'attributes',{}).get('unit_of_measurement','')
            policy,reason=resolve_air_policy(row.get('metric'),row['entity'],unit,None,policies,state_get,permitted)
            if policy and not reason:
                a,b=policy['levels'];higher=policy['higher_is_better']
                row['policy']={'higher_is_better':higher,'levels':[[a,'good'],[b,'raised'],[min(0,b-1) if higher else None,'high']],'source':policy['source']}
        if any(row.get('policy') for row in out.get('air',[])):out['semantic_version']=1
    return out


def still_current(snapshot,endpoint,artifacts,permitted,language,state_get=None):
    return snapshot==project(endpoint,artifacts,permitted,language,state_get)
