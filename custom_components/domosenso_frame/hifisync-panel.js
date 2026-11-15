class HiFiSyncPanel extends HTMLElement {
 set hass(value){this._hass=value;}
 async load(){
  if(!this._hass||this.busy)return;
  this.busy=true;this.render();
  try{
   const profile=await this._hass.callWS({type:'domosenso_frame/profile'});
   this.endpoints=profile.endpoints.filter(e=>e.studioVersion===2);
   if(!this.endpoints.some(e=>e.id===this.endpoint))this.endpoint=this.endpoints[0]?.id;
   this.mapping=this.endpoint?await this._hass.callWS({type:'domosenso_frame/studio_mappings',version:1,endpoint:this.endpoint}):null;
   this.message='';
  }catch(e){this.message='Could not load settings: '+String(e.message||e);}
  finally{this.busy=false;this.render();}
 }
 async save(){
  if(this.busy||!this.mapping)return;
  const enabled=this.querySelector('input').checked,endpoint=this.endpoint,revision=this.mapping.revision;
  this.busy=true;
  try{this.mapping=await this._hass.callWS({type:'domosenso_frame/studio_mappings_update',version:1,endpoint,revision,patch:{hifisync:enabled}});this.message='Settings saved.';}
  catch(e){this.message='Could not save settings: '+String(e.message||e);}
  finally{this.busy=false;this.render();}
 }
 render(){
  const root=document.createElement('main');root.style.cssText='padding:32px;font-family:sans-serif;max-width:760px';
  const heading=document.createElement('h1');heading.textContent='HiFiSync sharing';root.append(heading);
  const hint=document.createElement('p');hint.textContent='Allow this frame to show the HiFiSync system and room for its mapped media player. Pairing and player read permission are still required.';root.append(hint);
  if(this.endpoints?.length){
   const select=document.createElement('select');select.setAttribute('aria-label','Frame');select.disabled=this.busy;
   for(const endpoint of this.endpoints){const option=document.createElement('option');option.value=endpoint.id;option.textContent=endpoint.name;select.append(option);}
   select.value=this.endpoint;select.onchange=()=>{this.endpoint=select.value;this.mapping=null;this.load();};root.append(select);
   if(this.mapping){const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=this.mapping.mappings.hifisync===true;label.append(check,document.createTextNode(' Enable HiFiSync for this frame'));root.append(label);const save=document.createElement('button');save.textContent='Save';save.disabled=this.busy;save.onclick=()=>this.save();root.append(save);}
  } else {const empty=document.createElement('p');empty.textContent='No Studio-ready frames found.';root.append(empty);}
  const reload=document.createElement('button');reload.textContent='Reload settings';reload.onclick=()=>this.load();root.append(reload);
  if(this.message){const message=document.createElement('p');message.textContent=this.message;root.append(message);}
  this.replaceChildren(root);
 }
}
customElements.define('domosenso-frame-hifisync',HiFiSyncPanel);
