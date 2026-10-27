import './ui/0.2.0/studio.js';
class DomoSensoFramePanel extends HTMLElement {
 set hass(value){this._hass=value;this.mount();this.editor.hass=value;if(!this.started){this.started=true;this.refresh();}}
 mount(){
  if(this.editor)return;
  this.grants=document.createElement('div');this.editor=document.createElement('domosenso-frame-studio');this.welcome=document.createElement('section');
  this.welcome.style.cssText='padding:24px;max-width:760px;font-family:sans-serif;line-height:1.6';
  const title=document.createElement('h1');title.textContent='Get started with DomoSenso Frame';this.welcome.append(title);
  for(const text of ['1. Choose sources for this frame. In Template Studio, Map sources shows eligible sensors and entities; Suggested is a hint, not automatic permission. Empty mappings are allowed. Air measurements have no inferred quality thresholds.', '2. Open the Android app, enter this Home Assistant address and sign in. Select your frame. The app includes its display; a custom HA dashboard is not required.', '3. Open Devices and pairing here. Compare the six-digit code shown on the tablet before approving. Approval is specific to the device, HA user and frame.', '4. Use Template Studio to arrange widgets and apply a template after the tablet connects. Sources, templates and device grants are managed separately.']){const p=document.createElement('p');p.textContent=text;this.welcome.append(p);}
  const show=view=>{this.editor.hidden=view!=='studio';this.editor.style.display=view==='studio'?'block':'none';this.grants.hidden=view!=='devices';this.welcome.hidden=view!=='welcome';};
  const makeButton=(label,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=label;b.style.cssText='padding:10px 18px;border:1px solid #b4c6d4;border-radius:8px;cursor:pointer';b.onclick=fn;return b;};
  const tabs=document.createElement('nav');tabs.style.cssText='display:flex;flex-wrap:wrap;gap:8px;padding:12px 24px;background:#e6edf2';
  for(const [label,view] of [['Get started','welcome'],['Template Studio','studio'],['Devices and pairing','devices']])tabs.append(makeButton(label,()=>show(view)));
  const links=document.createElement('div');links.style.cssText='display:flex;flex-wrap:wrap;gap:8px';
  for(const [label,group] of [['Map radar','radar'],['Map temperature','rooms'],['Map air','air'],['Map weather','weather'],['Map calendars','calendars'],['Map media','media']])links.append(makeButton(label,async()=>{show('studio');if(!this.editor.mapping)await this.editor.loadMappings();if(this.editor.mapping)this.editor.openMapping(group);}));
  this.welcome.append(links);this.replaceChildren(tabs,this.welcome,this.editor,this.grants);show('welcome');
 }
 connectedCallback(){this.timer=setInterval(()=>this.refresh(),5000);}
 disconnectedCallback(){clearInterval(this.timer);}
 async refresh(){
  if(!this._hass||this.loading)return;this.loading=true;
  try{
   const devices=await this._hass.callWS({type:'domosenso_frame/devices'});
   const root=document.createElement('main');root.style.cssText='padding:32px;font-family:sans-serif';
   const title=document.createElement('h1');title.textContent='DomoSenso Frame';root.append(title);
   const help=document.createElement('p');help.textContent='Approve only when the six-digit code matches the code shown on your tablet. Grants bind this device, HA user and endpoint.';root.append(help);
   if(!devices.length){const p=document.createElement('p');p.textContent='No pairing requests. Open the Android app to begin.';root.append(p);}
   for(const d of devices){const row=document.createElement('section');row.style.cssText='padding:16px;margin:12px 0;border:1px solid #888;border-radius:8px';
    const text=document.createElement('p');text.textContent=`${d.name} · ${d.endpoint} · ${d.state} · ${d.deviceId.slice(0,12)}${d.code?' · Code: '+d.code:''}`;row.append(text);
    if(d.state==='pending'||d.state==='approved'){const button=document.createElement('button');button.textContent=d.state==='pending'?'Approve matching code':'Revoke access';button.onclick=async()=>{button.disabled=true;try{await this._hass.callWS({type:'domosenso_frame/'+(d.state==='pending'?'approve':'revoke'),deviceId:d.deviceId,...(d.code?{code:d.code}:{})});await this.refresh();}catch(e){button.textContent=String(e.message||e.code);}};row.append(button);}
    if(d.state==='revoked'){const button=document.createElement('button');button.textContent='Remove revoked registration';button.onclick=async()=>{if(!window.confirm('Remove this revoked registration? The tablet must pair again and receive a new matching-code approval.'))return;button.disabled=true;try{await this._hass.callWS({type:'domosenso_frame/remove_device',deviceId:d.deviceId});await this.refresh();}catch(e){button.textContent=String(e.message||e.code);}};row.append(button);}root.append(row);
   }this.grants.replaceChildren(root);
  }catch(e){this.grants.textContent='Could not load device grants: '+String(e.message||e.code);}finally{this.loading=false;}
 }
}
customElements.define('domosenso-frame-panel',DomoSensoFramePanel);
