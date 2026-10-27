"""Evaluate configured Ramka display bands, never a medical/legal air standard."""
import math

METRICS=('score','co2','pm2_5','voc','nox')
STATUSES=('good','raised','high')
UNITS={'score':{'','/100','%'},'co2':{'ppm'},'pm2_5':{'µg/m³','μg/m³','ug/m3','µg/m3','μg/m3'},'voc':{'','index'},'nox':{'','index'}}

def validate_policy(policy):
    if not isinstance(policy,dict) or set(policy)!={'higher_is_better','levels','source'}:raise ValueError('Invalid policy fields')
    if not isinstance(policy['higher_is_better'],bool):raise ValueError('Direction required')
    if not isinstance(policy['source'],str) or not 1<=len(policy['source'])<=200:raise ValueError('Policy source required')
    levels=policy['levels']
    if not isinstance(levels,list) or len(levels)!=3:raise ValueError('Exactly three ordered bands required')
    for item in levels:
        if not isinstance(item,list) or len(item)!=2 or not isinstance(item[1],str) or len(item[1])>80:raise ValueError('Invalid band')
        if item[0] is not None and (isinstance(item[0],bool) or not isinstance(item[0],(float,int)) or not math.isfinite(item[0])):raise ValueError('Finite threshold required')
    bounds=[v[0] for v in levels]
    if policy['higher_is_better']:
        if any(v is None for v in bounds) or not bounds[0]>bounds[1]>bounds[2]:raise ValueError('Descending thresholds required')
    elif bounds[0] is None or bounds[1] is None or bounds[2] is not None or not bounds[0]<bounds[1]:raise ValueError('Ascending thresholds with final fallback required')
    return policy

def assess(readings):
    evidence=[]
    for index,item in enumerate(readings):
        metric=item.get('metric');value=item.get('value');policy=item.get('policy')
        if metric not in METRICS or not policy or not item.get('available') or item.get('stale'):continue
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<0 or (metric=='score' and value>100):continue
        if item.get('unit') not in UNITS[metric]:continue
        validate_policy(policy)
        for band,(limit,label) in enumerate(policy['levels']):
            if (value>=limit if policy['higher_is_better'] else limit is None or value<limit):
                evidence.append({'sourceIndex':index,'metric':metric,'status':STATUSES[band],'ruleLabel':label,
                  'policySource':policy['source'],'threshold':limit,'higherIsBetter':policy['higher_is_better']});break
    count=len(evidence);total=len(readings)
    return {'version':1,'status':max((e['status'] for e in evidence),key=STATUSES.index) if count else 'unknown',
      'coverage':'complete' if count and count==total else 'partial' if count else 'none',
      'evaluated':count,'total':total,'evidence':evidence}
