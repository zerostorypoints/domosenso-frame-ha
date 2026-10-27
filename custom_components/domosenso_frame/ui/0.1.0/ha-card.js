import './frame-ui.js';
export async function withTimeout(promise,ms=15000){let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('HA display timeout')),ms);})]);}finally{clearTimeout(timer);}}
// Normal HA dashboard access, independent of a paid app/device grant. Backend still checks
// HA user's read access to every endpoint source; this adapter cannot invoke services.
class FrameDashboardCard extends HTMLElement {
 constructor(){super();this.attachShadow({mode:'open'});this._generation=0;}
 setConfig(config){if(typeof config.endpoint!=='string'||!/^[a-z][a-z0-9_-]{0,39}$/.test(config.endpoint))throw Error('Frame endpoint required');this._config=config;this._generation++;this._request=null;this._last=0;this._build();}
 getCardSize(){return 12;}
 getGridOptions(){return {columns:'full',rows:'auto'};}
 set hass(hass){const changed=this._hass?.connection!==hass?.connection;this._hass=hass;if(changed){this._generation++;this._request=null;this._last=0;}if(Date.now()-(this._last||0)>2000)void this._fetch();}
 connectedCallback(){this._build();this._timer=setInterval(()=>void this._fetch(),2000);}
 disconnectedCallback(){clearInterval(this._timer);this._generation++;this._request=null;}
 _build(){if(this._view)return;this.shadowRoot.innerHTML='<style>:host{display:block;height:max(420px,calc(100dvh - 110px))}domosenso-frame{height:100%}</style><domosenso-frame></domosenso-frame>';this._view=this.shadowRoot.querySelector('domosenso-frame');}
 async _fetch(){if(!this.isConnected||!this._hass||!this._config||this._request)return;const token={};this._request=token;const epoch=this._generation;this._last=Date.now();try{const data=await withTimeout(this._hass.callWS({type:'domosenso_frame/dashboard',endpoint:this._config.endpoint}));if(epoch===this._generation)this._view.snapshot=data;}catch{if(epoch===this._generation&&this._view.snapshot)this._view.snapshot={...this._view.snapshot,connected:false};}finally{if(this._request===token)this._request=null;}}
}
if(!customElements.get('domosenso-frame-card'))customElements.define('domosenso-frame-card',FrameDashboardCard);
