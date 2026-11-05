import copy
import importlib
import pytest
from test_timers import timer_module
from test_assistant_contract import bundle

timer_module()
contract = importlib.import_module('timer_package.assistant_contract')


def actions_bundle():
    b = bundle(); b['component']['id'] = 'timers'
    b['definitions'] = {'actions': []}
    b['bindings']['subjects'] = [{'key':'general','kind':'timer','label':'Demo','fields':{'timer':{'entity':'timer.demo'}}}]
    for name in ('start','pause','resume','cancel'):
        b['definitions']['actions'].append({'id':'timers.'+name,'revision':1,'operation':'timer','action_type':'timer-action.v1','result_type':'timer-result.v1',
         'title_key':'meta.assistant.status.title','description_key':'meta.assistant.status.description','examples_key':'meta.assistant.status.examples',
         'parameters':{'durationSeconds':{'type':'integer','minimum':1,'maximum':86400},'confirmReplace':{'type':'boolean'}} if name=='start' else {},'confirmation':'replace_active' if name=='start' else 'none','effect':name})
    return b


def runtime_bundle():
    b = bundle(); b['component']['id'] = 'ramka'; cap = b['definitions']['capabilities'][0]
    cap.update(id='ramka.current_time',query_type='time-query.v1',answer_type='time.v1',runtime_source='ha_clock',required_facts=[],optional_facts=[],policy={'evaluator':'ramka.current_time'},freshness={'basis':'provider_response','max_age_seconds':None})
    b['bindings']['subjects'] = [{'key':'demo','kind':'frame','label':'Demo','fields':{}}]
    return b


def test_time_runtime_source_separate_fixed_directory_and_legacy_loader_unchanged(tmp_path):
    import yaml
    runtime = importlib.import_module('timer_package.assistant_runtime_contract')
    old = bundle(); assert contract.validate_bundle(copy.deepcopy(old)) == old
    b = runtime_bundle()
    assert runtime.validate_bundle(b) == b
    with pytest.raises(ValueError): contract.validate_bundle(b)
    for change in ({'runtime_source':'template'}, {'id':'ramka.status'}, {'required_facts':old['definitions']['capabilities'][0]['required_facts']}, {'freshness':{'basis':'reported_state','max_age_seconds':None}}):
        bad = copy.deepcopy(b);bad['definitions']['capabilities'][0].update(change)
        with pytest.raises(ValueError): runtime.validate_bundle(bad)
    for change in ('owner','duplicate','binding','policy','missing_source'):
        bad = copy.deepcopy(b)
        if change == 'owner': bad['component']['id'] = 'lights'
        if change == 'duplicate': bad['definitions']['capabilities'] *= 2
        if change == 'binding': bad['bindings']['subjects'] = [{'key':'frame','kind':'frame','label':'Frame','fields':{'state':{'entity':'sensor.demo'}}}]
        if change == 'policy': bad['bindings']['policies'] = [{}]
        if change == 'missing_source': del bad['definitions']['capabilities'][0]['runtime_source']
        with pytest.raises(ValueError): runtime.validate_bundle(bad)
    record = dict(b['component'], package='')
    assert runtime.active({'ramka':b}, [record]) == {'ramka':b}
    assert runtime.active({'ramka':b}, [record,record]) == {}
    for change in ({'commit':'abcdefabcdef'}, {'version':'2'}, {'dev':True}, {'package':'ramka.yaml'}, {'installed':False}):
        assert runtime.active({'ramka':b}, [record|change]) == {}
    directory = tmp_path/'domosenso'/'assistant-runtime'; directory.mkdir(parents=True)
    (directory/'ramka.yaml').write_text(yaml.safe_dump(b))
    (directory/'link.yaml').symlink_to(directory/'ramka.yaml')
    (directory/'wrong.yaml').write_text(yaml.safe_dump(b))
    loaded,errors = runtime.load_directory(tmp_path)
    assert loaded == {'ramka':b} and set(errors) == {'link.yaml','wrong.yaml'}
    assert contract.load_directory(tmp_path) == ({},{})
    (directory/'ramka.yaml').unlink(); (directory/'link.yaml').unlink(); (directory/'wrong.yaml').unlink()
    directory.rmdir(); directory.symlink_to(tmp_path, target_is_directory=True)
    assert runtime.load_directory(tmp_path)[1] == {'directory':'Invalid assistant directory'}


def test_action_loader_rejects_unsafe_or_misbound_metadata_and_verifies_identity(tmp_path):
    import yaml
    try: loader = importlib.import_module('timer_package.timer_contract')
    except ModuleNotFoundError: pytest.fail('Action artifact loader missing')
    b=actions_bundle(); assert loader.validate_bundle(b) == b
    record=dict(b['component'],package='')
    assert loader.identity_matches(b,record)
    assert not loader.identity_matches(b,record|{'version':'2.0.0'})
    for field,value in [('id','timers.execute'),('effect','cancel'),('parameters',{}),('service','start')]:
        bad=copy.deepcopy(b);bad['definitions']['actions'][0][field]=value
        with pytest.raises(ValueError): loader.validate_bundle(bad)
    bad=copy.deepcopy(b);bad['bindings']['subjects'][0]['fields']['timer']['entity']='script.demo'
    with pytest.raises(ValueError): loader.validate_bundle(bad)
    directory=tmp_path/'domosenso'/'assistant-actions';directory.mkdir(parents=True)
    (directory/'timers.yaml').write_text(yaml.safe_dump(b));(directory/'link.yaml').symlink_to(directory/'timers.yaml')
    loaded,errors=loader.load_directory(tmp_path)
    assert loaded=={'timers':b} and 'link.yaml' in errors
