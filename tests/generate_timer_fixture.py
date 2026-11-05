"""Generate fictional shared native/HA wire examples from the actual adapters."""
import asyncio
import itertools
import json
import uuid
from pathlib import Path
from unittest.mock import patch
from test_timer_api import api_rig, module
from test_timers import Rig
import importlib


async def generate():
    api=module(); r,h,c,grant=api_rig(); reads=importlib.import_module('timer_package.assistant_api')
    supported=[{'queryType':'timer-query.v1','answerType':'timer-status.v1'},{'queryType':'time-query.v1','answerType':'time.v1'}]
    reads.capabilities(h,c,{'id':1,'version':1,'endpoint':'frame','supported':supported},grant,'read',lambda:r.now)
    output={'capabilities':c.results[-1]}
    api.actions(h,c,{'id':2,'version':1,'endpoint':'frame','supported':[{'actionType':'timer-action.v1','resultType':'timer-result.v1'}]},grant,'read','control')
    output['actions']=c.results[-1]
    async def snapshot(cap='timers.status',answer='timer-status.v1'):
        await reads.query_async(h,c,{'id':3,'version':1,'endpoint':'frame','capability':cap,'revision':1,'answerType':answer,'arguments':{}},grant,'read',lambda:r.now)
        return c.results[-1]
    output['time']=await snapshot('ramka.current_time','time.v1')
    output['idle']=await snapshot()
    request=r.request();output['startRequest']=request
    output['applied']=await r.execute(request);output['active']=await snapshot()
    output['conflict']=await r.execute(request|{'requestId':'stale'})
    output['confirmationRequired']=await r.execute(r.request(requestId='replace'))
    output['pausedResult']=await r.execute(r.request('pause',requestId='pause'));output['paused']=await snapshot()
    output['resumedResult']=await r.execute(r.request('resume',requestId='resume'))
    output['cancelledResult']=await r.execute(r.request('cancel',requestId='cancel'));output['cancelled']=await snapshot()
    await r.execute(r.request(requestId='finish-run'))
    r.now=r.now.replace(minute=1)
    r.states['timer.demo']=r.state('idle');r.engine.observe('timer.demo',r.states['timer.demo'],event='timer.finished')
    output['completed']=await snapshot()
    fact=r.fact();output['acknowledgeRequest']={'version':1,'endpoint':'frame','subjectId':fact['subjectId'],'runId':fact['runId']}
    output['acknowledged']=await r.engine.acknowledge(r.endpoint,fact['subjectId'],fact['runId'],lambda _:True,r.devices)
    unknown=Rig(); req=unknown.request()
    async def interrupted(*_): raise asyncio.CancelledError()
    try: await unknown.execute(req,interrupted)
    except asyncio.CancelledError: pass
    restarted=Rig(unknown.saved);output['outcomeUnknown']=await restarted.execute(req)
    r.states['timer.demo'].state = 'unavailable'
    output['unavailable'] = await snapshot()
    output['invalidation']={'kind':'timers'}
    return output


def build():
    counter=itertools.count()
    with patch('uuid.uuid4',side_effect=lambda:uuid.uuid5(uuid.NAMESPACE_URL,'fictional-timer-'+str(next(counter)))):
        return asyncio.run(generate())


if __name__=='__main__':
    Path(__file__).with_name('fixtures').joinpath('shared-timers.json').write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n')
