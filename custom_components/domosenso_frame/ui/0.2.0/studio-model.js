import {validateConfig,REGISTRY,positions,place} from './contract.js';
const copy=x=>structuredClone(x);
export const fresh=(items,prefix)=>{let n=1;while(items.some(x=>x.id===prefix+n))n++;return prefix+n;};
export class Draft {
 constructor(config){this.config=copy(validateConfig(config));this.past=[];this.future=[];}
 change(fn){const next=copy(this.config);fn(next);validateConfig(next);if(JSON.stringify(next)===JSON.stringify(this.config))return;this.past.push(this.config);if(this.past.length>80)this.past.shift();this.future=[];this.config=next;}
 undo(){if(this.past.length){this.future.push(this.config);this.config=this.past.pop();}}
 redo(){if(this.future.length){this.past.push(this.config);this.config=this.future.pop();}}
 screen(mode,id,config=this.config){return config.modes.find(m=>m.id===mode).screens.find(s=>s.id===id);}
 move(mode,sid,wid,rect,portrait=false){const s=this.screen(mode,sid),r=place(s,wid,rect,portrait);if(!r)return false;this.change(c=>{const s=this.screen(mode,sid,c);if(portrait)positions(s,true).forEach((p,i)=>s.widgets[i].portrait=p);s.widgets.find(w=>w.id===wid)[portrait?'portrait':'layout']=r;});return true;}
 freezePortrait(mode,sid){this.change(c=>{const s=this.screen(mode,sid,c);positions(s,true).forEach((p,i)=>s.widgets[i].portrait=p);});}
 resetPortrait(mode,sid){this.change(c=>this.screen(mode,sid,c).widgets.forEach(w=>delete w.portrait));}
 addWidget(mode,sid,type,rect){
  const s=this.screen(mode,sid);if(s.widgets.length>=12)return null;const id=fresh(s.widgets,'widget'),[w,h]=REGISTRY[type].min;let found=rect&&place(s,id,rect,false,type);
  if(!rect)for(let y=0;!found&&y<=12-h;y++)for(let x=0;!found&&x<=12-w;x++)found=place(s,id,{x,y,w,h},false,type);
  if(!found)return null;
  const overridden=s.widgets.some(w=>w.portrait);let portrait=null;
  if(overridden){for(let y=0;!portrait&&y<24;y++)for(let x=0;!portrait&&x<12;x++)portrait=place(s,id,{x,y,w:Math.max(3,w),h:2},true,type);if(!portrait)return null;}
  this.change(c=>{const target=this.screen(mode,sid,c);if(overridden)positions(target,true).forEach((p,i)=>target.widgets[i].portrait=p);target.widgets.push({id,type,layout:found,...(portrait?{portrait}:{})});});return id;
 }

 removeWidget(mode,sid,id){this.change(c=>{const s=this.screen(mode,sid,c);if(s.widgets.some(w=>w.portrait))positions(s,true).forEach((p,i)=>s.widgets[i].portrait=p);s.widgets=s.widgets.filter(w=>w.id!==id);});}
 addScreen(mode){const m=this.config.modes.find(m=>m.id===mode),id=fresh(m.screens,'screen');this.change(c=>c.modes.find(m=>m.id===mode).screens.push({id,title:'New screen',duration:0,requires:[],widgets:[{id:'clock',type:'clock',layout:{x:0,y:0,w:4,h:3}}]}));return id;}
 copyScreen(from,id,to){const original=copy(this.screen(from,id)),target=this.config.modes.find(m=>m.id===to),newId=fresh(target.screens,'screen');original.id=newId;this.change(c=>c.modes.find(m=>m.id===to).screens.push(original));return newId;}
 removeScreen(mode,id){this.change(c=>{const m=c.modes.find(m=>m.id===mode);m.screens=m.screens.filter(s=>s.id!==id);});}
 reorderScreen(mode,id,offset){this.change(c=>{const a=c.modes.find(m=>m.id===mode).screens,i=a.findIndex(s=>s.id===id),j=i+offset;if(j>=0&&j<a.length)[a[i],a[j]]=[a[j],a[i]];});}
}

export function widgetAvailability(widgets,type,options={}){
 const base=widgets?.[type]||{ready:type==='clock',reasons:type==='clock'?[]:['Source discovery is unavailable. Reload mappings.']};
 if(type==='history'&&base.metrics){const metric=options.metric||'temperature';return {...base,ready:!!base.metrics[metric],reasons:base.metrics[metric]?[]:['Map '+metric+' measurements for this chart. Recorder statistics must be available.']};}
 if(type==='navigation'&&base.targets){const target=options.target||'controls';return {...base,ready:!!base.targets[target],reasons:base.targets[target]?[]:['Configure navigation to '+target+'.']};}
 return base;
}
