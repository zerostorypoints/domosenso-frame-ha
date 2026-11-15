import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
const window=new Window();
globalThis.window=window;globalThis.document=window.document;globalThis.HTMLElement=window.HTMLElement;globalThis.customElements=window.customElements;
await import('../custom_components/domosenso_frame/hifisync-panel.js');
test('administrator explicitly enables sharing for a selected endpoint', async()=>{
 const calls=[];
 const element=document.createElement('domosenso-frame-hifisync');document.body.append(element);
 element.hass={callWS:async msg=>{calls.push(msg);if(msg.type.endsWith('/profile'))return {endpoints:[{id:'frame',name:'Frame',studioVersion:2}]};if(msg.type.endsWith('/studio_mappings'))return {revision:'abc',mappings:{hifisync:false}};return {revision:'def',mappings:{hifisync:true}};}};
 await element.load();
 const checkbox=element.querySelector('input');assert.equal(checkbox.checked,false);checkbox.checked=true;
 await element.save();
 assert.deepEqual(calls.at(-1),{type:'domosenso_frame/studio_mappings_update',version:1,endpoint:'frame',revision:'abc',patch:{hifisync:true}});
 assert.match(element.textContent,/saved/i);
});
