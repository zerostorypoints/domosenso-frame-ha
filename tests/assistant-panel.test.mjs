import test from 'node:test';
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window=new Window();
Object.assign(globalThis,{window,document:window.document,HTMLElement:window.HTMLElement,customElements:window.customElements,CustomEvent:window.CustomEvent});
await import('../custom_components/domosenso_frame/assistant-panel.js');
const selection={component:'plants',subject:'plant.fern',fields:{plant:{entity:'plant.fern'},moisture:{entity:'sensor.moisture'}}};
const original={subjects:[{key:'existing',kind:'light',label:'Lamp',fields:{state:{entity:'light.lamp'}}}],sensitive:false,calendar_sensitive:false};
const result=()=>({version:1,endpoint:'frame',revision:'revision-1',mappings:{assistant:structuredClone(original)},assistantCatalog:{components:[{id:'plants',status:'verified',subjects:[{key:'plant.fern',kind:'plant',label:{en:'Fern',pl:'Paproć'},selection,missing:0}]}]}});
const tick=()=>new Promise(r=>setTimeout(r,0));
async function panel(language='en',saveError=false){
 const calls=[];const el=document.createElement('domosenso-frame-assistant');document.body.append(el);
 el.hass={language,callWS:async msg=>{calls.push(structuredClone(msg));if(msg.type.endsWith('/profile'))return {endpoints:[{id:'frame',name:'Frame',displayVersion:1}]};if(msg.type.endsWith('_update')){if(saveError)throw new Error('mapping_conflict');return {...result(),revision:'revision-2',mappings:{assistant:msg.patch.assistant}};}return result();}};
 await tick();return {el,calls};
}
test('opening proposals does not save; selection preserves manual sources and sensitive flags',async()=>{
 const {el,calls}=await panel();assert.equal(calls.filter(x=>x.type.endsWith('_update')).length,0);
 el.querySelector('input[type=checkbox]').click();
 assert.equal(calls.filter(x=>x.type.endsWith('_update')).length,0);
 el.querySelector('[data-save]').click();await tick();
 const saved=calls.find(x=>x.type.endsWith('_update'));assert.deepEqual(saved.patch.assistant,{...original,component_sources:[selection]});
 assert.equal(saved.revision,'revision-1');assert.equal(saved.endpoint,'frame');el.remove();
});
test('Polish metadata labels render as text and failed save retains selection',async()=>{
 const {el,calls}=await panel('pl',true);assert.match(el.textContent,/Paproć/);
 el.querySelector('input[type=checkbox]').click();el.querySelector('[data-save]').click();await tick();
 assert.equal(el.querySelector('input[type=checkbox]').checked,true);assert.match(el.textContent,/Nie udało/);
 assert.equal(calls.filter(x=>x.type.endsWith('_update')).length,1);el.remove();
});
test('missing component metadata is actionable and never proposes a source',async()=>{
 const {el}=await panel();el.result.assistantCatalog={components:[{id:'plants',status:'missing_metadata',subjects:[]}]};el.render();
 assert.match(el.textContent,/metadata/i);assert.equal(el.querySelectorAll('input[type=checkbox]').length,0);el.remove();
});
test('component removal still allows revoking a stored selection',async()=>{
 const {el}=await panel();el.result.assistantCatalog={components:[{id:'plants',status:'missing_metadata',subjects:[]}]};el.draft.component_sources=[selection];el.render();
 const remove=el.querySelector('[data-remove]');assert.ok(remove);remove.click();assert.deepEqual(el.draft.component_sources,[]);el.remove();
});
test('HA state updates cannot reopen a hidden assistant view',async()=>{
 const {el}=await panel();el.hidden=true;el.style.display='none';el.render();assert.equal(el.style.display,'none');el.remove();
});
test('a late load response cannot replace the selected frame',async()=>{
 const {el}=await panel();let finish;
 el._hass.callWS=msg=>msg.endpoint==='old'?new Promise(r=>{finish=r;}):Promise.resolve({...result(),endpoint:'new'});
 const old=el.load('old');await el.load('new');finish({...result(),endpoint:'old'});await old;
 assert.equal(el.endpoint,'new');assert.equal(el.result.endpoint,'new');el.remove();
});
test('component descriptions require an explicit choice without enabling sensitive data',async()=>{
 const {el,calls}=await panel();el.result.assistantCatalog.components[0].explanation=true;el.render();
 el.querySelector('[data-explanation]').click();el.querySelector('[data-save]').click();await tick();
 const saved=calls.find(x=>x.type.endsWith('_update')).patch.assistant;
 assert.deepEqual(saved.explanations,['plants']);assert.equal(saved.sensitive,false);assert.equal(saved.calendar_sensitive,false);assert.equal(saved.component_sources,undefined);el.remove();
});
test('ordinary HA updates preserve source disclosure and focused controls',async()=>{
 const {el}=await panel();const details=el.querySelector('details');details.open=true;
 const box=el.querySelector('input');box.focus();el.hass={...el._hass,states:{'sensor.example':{state:'20'}}};
 assert.ok(el.querySelector('details')===details);assert.equal(details.open,true);assert.ok(document.activeElement===box);el.remove();
});
test('manual mapping handoff saves to the frame selected in assistant sources',async()=>{
 await import('../custom_components/domosenso_frame/panel.js');
 const host=document.createElement('domosenso-frame-panel');host.mount();
 const p=host.assistant,editor=host.editor,calls=[];
 const response=id=>({...result(),endpoint:id});
 p._hass={language:'en',callWS:async msg=>response(msg.endpoint)};p.language='en';p.endpoints=[{id:'alpha',name:'Alpha'},{id:'beta',name:'Beta'}];await p.load('beta');
 editor.endpoint='alpha';editor.mapping=response('alpha');editor.render=()=>{};editor.poll=async()=>{};
 editor.endpoints=[{id:'alpha',capabilitiesVersion:1},{id:'beta',capabilitiesVersion:1}];
 editor.library={applied:{},templates:{}};editor.selectTemplate=()=>{};
 editor._hass={callWS:async msg=>{calls.push(msg);return response(msg.endpoint);}};
 [...p.querySelectorAll('button')].find(b=>b.textContent==='Manual sources / vanilla HA').click();await tick();
 editor.mappingDraft.assistant.sensitive=true;await editor.saveMapping();
 assert.equal(editor.endpoint,'beta');assert.equal(calls.find(c=>c.type.endsWith('_update')).endpoint,'beta');
});
