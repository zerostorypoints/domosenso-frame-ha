import test from 'node:test';
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window=new Window();
Object.assign(globalThis,{window,document:window.document,HTMLElement:window.HTMLElement,customElements:window.customElements});
const file=new URL('../custom_components/domosenso_frame/timer-panel.js',import.meta.url);
const tick=()=>new Promise(r=>setTimeout(r,0));
async function panel(language='en',failure=false){
 try{await import(file);}catch(e){assert.fail('Timer mapping panel missing: '+e.message);}
 const calls=[];const el=document.createElement('domosenso-frame-timers');document.body.append(el);
 const result={endpoint:'frame',revision:'one',mappings:{timers:{writeEnabled:false,items:[]}},candidates:{timers:[{entity:'timer.demo',label:'<b>Demo</b>'}]},timerDevices:[{deviceId:'tablet',name:'Demo tablet'}]};
 el.hass={language,callWS:async msg=>{calls.push(structuredClone(msg));if(msg.type.endsWith('/profile'))return {endpoints:[{id:'frame',name:'Frame',displayVersion:1}]};if(msg.type.endsWith('_update')){if(failure)throw Error('conflict');return {...result,revision:'two',mappings:{timers:msg.patch.timers}};}return result;}};
 await tick();return {el,calls};
}
test('vanilla helper selection requires explicit write grant and alarm device; opening saves nothing',async()=>{
 const {el,calls}=await panel();assert.equal(calls.filter(c=>c.patch).length,0);
 el.querySelector('[data-add]').click();
 assert.equal(el.querySelector('[data-write]').checked,false);
 const target=el.querySelector('[data-target]');assert.equal(target.value,'');target.value='tablet';target.dispatchEvent(new window.Event('change'));
 el.querySelector('[data-write]').click();el.querySelector('[data-save]').click();await tick();
 const saved=calls.find(c=>c.patch);assert.deepEqual(saved.patch,{timers:{writeEnabled:true,items:[{entity:'timer.demo',label:'<b>Demo</b>',alarmDeviceId:'tablet'}]}});
 assert.equal(saved.revision,'one');assert.equal(el.querySelector('b'),null);el.remove();
});
test('all supported languages explain foreground fallback; failed save retains draft',async()=>{
 for(const [language,term] of [['en','foreground'],['de','Vordergrund'],['pl','pierwszym planie']]){
  const {el,calls}=await panel(language,true);assert.match(el.textContent,new RegExp(term));
  el.querySelector('[data-add]').click();el.querySelector('[data-save]').click();await tick();
  assert.equal(el.draft.items.length,1);assert.equal(calls.filter(c=>c.patch).length,1);assert.ok(el.querySelector('[role=status]'));el.remove();
 }
});
test('late responses cannot replace a newly selected endpoint',async()=>{
 const {el}=await panel();let finish;
 el._hass.callWS=msg=>msg.endpoint==='old'?new Promise(r=>{finish=r;}):Promise.resolve({endpoint:'new',revision:'two',mappings:{timers:{writeEnabled:false,items:[]}},candidates:{timers:[]},timerDevices:[]});
 const old=el.load('old');await el.load('new');finish({endpoint:'old',mappings:{timers:{writeEnabled:true,items:[]}}});await old;
 assert.equal(el.endpoint,'new');assert.equal(el.draft.writeEnabled,false);el.remove();
});
