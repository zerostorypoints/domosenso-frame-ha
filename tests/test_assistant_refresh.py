import asyncio
import importlib
from test_assistant_bindings import pkg


def test_refresh_detects_same_version_replacement_and_serializes():
    module=importlib.import_module(pkg.__name__+'.assistant_refresh')
    async def scenario():
        signature=[1];published=[];loads=[]
        async def executor(fn,*args):return fn(*args)
        def loader():loads.append(1);return {'plants':signature[0]}
        refresh=module.MetadataRefresh(lambda:tuple(signature),loader,published.append,executor)
        await asyncio.gather(refresh.check(),refresh.check())
        assert len(loads)==1 and published==[{'plants':1}]
        signature[0]=2;await refresh.check();assert published[-1]=={'plants':2}
        await refresh.close();signature[0]=3;await refresh.check();assert len(loads)==2
    asyncio.run(scenario())


def test_invalid_reload_disables_old_data_and_retries():
    module=importlib.import_module(pkg.__name__+'.assistant_refresh')
    async def scenario():
        current=[1];published=[]
        async def executor(fn,*args):return fn(*args)
        def loader():
            if current[0]==2:raise OSError('incomplete deploy')
            return {'plants':1}
        refresh=module.MetadataRefresh(lambda:tuple(current),loader,published.append,executor)
        await refresh.check();current[0]=2;await refresh.check()
        assert published[-1]=={}
        current[0]=3;await refresh.check();assert published[-1]=={'plants':1}
    asyncio.run(scenario())


def test_fingerprint_changes_without_component_version_and_does_not_follow_symlink(tmp_path):
    module=importlib.import_module(pkg.__name__+'.assistant_refresh')
    directory=tmp_path/'domosenso'/'assistant';directory.mkdir(parents=True)
    path=directory/'plants.yaml';path.write_text('one')
    before=module.fingerprint(tmp_path);path.write_text('two plants')
    assert module.fingerprint(tmp_path)!=before
    path.unlink();path.symlink_to(tmp_path/'missing')
    assert module.fingerprint(tmp_path)
