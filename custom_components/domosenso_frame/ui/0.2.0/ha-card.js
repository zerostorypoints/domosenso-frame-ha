import './frame-ui.js';
export class StudioCard extends HTMLElement {
 constructor(){super();this.frame=document.createElement('domosenso-studio-frame');this.frame.style.cssText='display:block;width:100%;height:100%;min-height:400px';this.append(this.frame);this.generation=0;}
 setConfig(config){if(!config.endpoint)throw Error('Frame endpoint required');this.endpoint=config.endpoint;this.style.cssText='display:block;height:calc(100dvh - 80px)';}
 set hass(value){this._hass=value;this.refresh();}
 connectedCallback(){this.timer=setInterval(()=>this.refresh(),2000);}
 disconnectedCallback(){clearInterval(this.timer);this.generation++;this.busy=false;}
 async refresh(){if(!this._hass||!this.endpoint||this.busy||!this.isConnected)return;this.busy=true;const generation=this.generation;try{const snapshot=await this._hass.callWS({type:'domosenso_frame/studio_dashboard',endpoint:this.endpoint,offset:0});if(generation===this.generation)this.frame.snapshot=snapshot;}catch{if(this.frame.snapshot&&generation===this.generation)this.frame.snapshot={...this.frame.snapshot,connected:false};}finally{if(generation===this.generation)this.busy=false;}}
 getCardSize(){return 10;}
}
if(!customElements.get('domosenso-studio-card'))customElements.define('domosenso-studio-card',StudioCard);
