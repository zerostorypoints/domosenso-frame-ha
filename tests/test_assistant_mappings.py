"""Admin mappings are explicit and do not alter legacy mappings."""
import importlib
import sys
import types
from pathlib import Path

import pytest

pkg = types.ModuleType('frame_test_package')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg
mappings = importlib.import_module('frame_test_package.studio_mappings')


def test_assistant_mappings_are_explicit_and_sensitive_defaults_off():
    endpoint = {'id': 'fictional', 'temperature': [], 'air': [], 'calendars': []}
    payload = {'subjects': [{'key': 'lamp', 'kind': 'light', 'label': 'Lamp', 'fields': {'state': {'entity': 'light.lamp'}}}], 'sensitive': False}
    updated = mappings.merge_patch(endpoint, {'assistant': payload})
    assert mappings.editable(updated)['assistant'] == payload
    assert endpoint.get('assistant') is None
    assert mappings.editable(endpoint)['assistant']['sensitive'] is False
    with pytest.raises(ValueError):
        mappings.merge_patch(endpoint, {'assistant': payload | {'sensitive': 'yes'}})


def test_assistant_mapping_rejects_executable_fields_and_wrong_entity_domain():
    endpoint = {'temperature': [], 'air': [], 'calendars': []}
    base = {'key': 'x', 'kind': 'light', 'label': 'Light', 'fields': {'state': {'entity': 'light.x'}}}
    for row in (base | {'action': 'turn_on'}, base | {'fields': {'state': {'entity': 'script.turn_on'}}}):
        with pytest.raises(ValueError):
            mappings.merge_patch(endpoint, {'assistant': {'subjects': [row], 'sensitive': False}})


def test_assistant_source_discovery_never_suggests_action_domains():
    from types import SimpleNamespace
    states = {eid: SimpleNamespace(entity_id=eid, attributes={'friendly_name': eid}) for eid in ('sensor.room', 'light.lamp', 'script.change', 'automation.run')}
    discovered = mappings.discover(states)['assistant']
    assert {item['entity'] for item in discovered} == {'sensor.room', 'light.lamp'}


def test_component_explanations_need_explicit_valid_endpoint_mapping():
    endpoint={'temperature':[],'air':[],'calendars':[]}
    payload={'subjects':[],'sensitive':False,'explanations':['remote-access']}
    updated=mappings.merge_patch(endpoint,{'assistant':payload})
    assert mappings.editable(updated)['assistant']['explanations']==['remote-access']
    with pytest.raises(ValueError):
        mappings.merge_patch(endpoint,{'assistant':payload | {'explanations':['../../private']}})


def test_room_ids_persist_through_save_restart_rename_and_reorder():
    import asyncio
    endpoint={'id':'f','display':{},'temperature':[],'air':[],'calendars':[]}
    saved=[]
    async def persist(value): saved.append(value)
    store=mappings.Mappings({'f':endpoint},{},persist)
    states={e:types.SimpleNamespace(entity_id=e,attributes={'device_class':'temperature'}) for e in ('sensor.a','sensor.b')}
    async def update(rows):
        rev=store.get('f',states)['revision']
        return await store.update('f',rev,{'rooms':rows},states,lambda:None)
    first=asyncio.run(update([{'label':'A','temperature':'sensor.a'},{'label':'B','temperature':'sensor.b'}]))
    ids={x['temperature']:x['roomKey'] for x in first['mappings']['rooms']}
    second=asyncio.run(update([{'label':'Renamed B','temperature':'sensor.b'},{'label':'Renamed A','temperature':'sensor.a'}]))
    assert {x['temperature']:x['roomKey'] for x in second['mappings']['rooms']}==ids
    restored=mappings.Mappings({'f':endpoint},saved[-1],persist)
    assert {x['temperature']:x['roomKey'] for x in restored.get('f',states)['mappings']['rooms']}==ids


def test_translated_room_label_is_separate_from_identity():
    row={'key':'plant','kind':'plant','label':'Fern','roomKey':'stable','roomLabel':{'en':'Study','pl':'Gabinet'},'fields':{'moisture':{'entity':'sensor.moisture'}}}
    assert mappings.merge_patch({}, {'assistant':{'subjects':[row],'sensitive':False}})['assistant']['subjects'][0]['roomLabel']['pl']=='Gabinet'


def test_assistant_labels_accept_translations_with_required_english():
    row={'key':'plant','kind':'plant','label':{'en':'Fern','pl':'Paproć'},'roomKey':'stable','roomLabel':{'en':'Study','pl':'Gabinet'},'fields':{'moisture':{'entity':'sensor.moisture'}}}
    patch=lambda value: {'assistant':{'subjects':[value],'sensitive':False}}
    assert mappings.merge_patch({},patch(row))['assistant']['subjects'][0]['label']['pl']=='Paproć'
    for field in ('label','roomLabel'):
        with pytest.raises(ValueError): mappings.merge_patch({},patch(row | {field:{'pl':'Polski'}}))
