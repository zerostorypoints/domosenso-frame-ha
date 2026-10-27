"""Explicit radar bindings and bounded screen intent, independent of HA schedulers."""
import math
import re

UNITS={'m':1,'cm':0.01,'mm':0.001,'in':0.0254,'ft':0.3048}
DEFAULTS={'near_m':1.5,'off_delay_s':300,'hysteresis_m':0.25}

def eligible(state,kind):
    if not state:return False
    domain=state.entity_id.partition('.')[0]
    return domain=='binary_sensor' if kind=='radar_presence' else domain=='sensor' and state.attributes.get('unit_of_measurement') in UNITS

def validate(value,states=None):
    if not isinstance(value,dict) or set(value)-{'presence','distance',*DEFAULTS}:raise ValueError('Invalid radar fields')
    if not value:return {}
    if not value.get('presence'):raise ValueError('Select a radar presence binary sensor')
    result={**DEFAULTS,**value}
    for key,domain in [('presence','binary_sensor'),('distance','sensor')]:
        if key not in result:continue
        eid=result[key]
        if not isinstance(eid,str) or not re.fullmatch(domain+r'\.[a-z0-9_]+',eid):raise ValueError('Invalid radar source')
        if states is not None and not eligible(states.get(eid),'radar_'+key):raise ValueError('Select an existing radar source with a supported distance unit')
    for key,low,high in [('near_m',0.1,20),('off_delay_s',1,3600),('hysteresis_m',0.05,2)]:
        n=result[key]
        if type(n) not in (float,int) or not math.isfinite(n) or not low<=n<=high:raise ValueError('Invalid radar threshold or delay')
    return result

def sources(config):return [config[key] for key in ('presence','distance') if config.get(key)]

class Radar:
    def __init__(self,endpoints,states,emit,later,now):
        self.endpoints=endpoints;self.states=states;self.emit=emit;self.later=later;self.now=now;self.records={};self.closed=False
    def _cancel(self,record):
        if cancel:=record.pop('cancel',None):cancel()
    def snapshot(self,eid):
        if self.closed:return None
        config=self.endpoints.get(eid,{}).get('radar',{})
        if not config:return None
        record=self.records.setdefault(eid,{'near':False,'last':None})
        presence=self.states.get(config['presence'])
        if not presence or presence.state not in ('on','off'):
            self._cancel(record);record.pop('absent',None);record['near']=False
            # Cancel a tablet's deferred dim intent without choosing a display mode.
            return {'kind':'screen','action':'release'}
        if presence.state=='on':
            self._cancel(record);record.pop('absent',None)
            distance=self.states.get(config.get('distance')) if config.get('distance') else None
            try:metres=float(distance.state)*UNITS[distance.attributes.get('unit_of_measurement')]
            except (AttributeError,ValueError,TypeError,KeyError):metres=float('nan')
            threshold=config.get('near_m',1.5)+(config.get('hysteresis_m',0.25) if record['near'] else 0)
            record['near']=math.isfinite(metres) and metres>=0 and metres<=threshold
            return {'kind':'screen','action':'wake','mode':'controls' if record['near'] else 'ambient'}
        record['near']=False
        record.setdefault('absent',self.now())
        remaining=config.get('off_delay_s',300)-(self.now()-record['absent'])
        if remaining<=0:
            self._cancel(record)
            return {'kind':'screen','action':'dim','mode':'ambient'}
        if 'cancel' not in record:
            def expired():
                record.pop('cancel',None)
                if not self.closed and self.records.get(eid) is record:self.refresh(eid)
            record['cancel']=self.later(remaining,expired)
        return None
    def refresh(self,eid):
        event=self.snapshot(eid)
        if event is not None and event!=self.records[eid]['last']:
            self.records[eid]['last']=event;self.emit(eid,event)
    def reset(self,eid):
        old=self.records.pop(eid,None)
        if old:self._cancel(old)
        if not self.closed:
            if self.endpoints.get(eid,{}).get('radar'):self.refresh(eid)
            elif old:self.emit(eid,{'kind':'screen','action':'release','mode':'ambient'})
    def close(self):
        self.closed=True
        for record in self.records.values():self._cancel(record)
        self.records.clear()
