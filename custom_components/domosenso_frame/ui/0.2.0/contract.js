import {REGISTRY,BUILTINS} from './registry.js';
export {REGISTRY,BUILTINS};
export const UI_VERSION='0.2.0';
export const STATES=['controls','ambient','media'];
const caps=['temperature','air','weather','status','rooms','plants','media','mains','history','calendar','photos','lights','covers'];
const fail=()=>{throw new Error('Invalid template configuration');};
const object=(v,keys,required=keys)=>{if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).some(k=>!keys.includes(k))||required.some(k=>!(k in v)))fail();};
const text=(v,max=80)=>{if(typeof v!=='string'||!v.length||v.length>max)fail();};
const id=v=>{if(typeof v!=='string'||!/^[a-z][a-z0-9_-]{0,39}$/.test(v))fail();};
const list=(v,min,max)=>{if(!Array.isArray(v)||v.length<min||v.length>max)fail();};
const unique=xs=>{if(new Set(xs).size!==xs.length)fail();};
export function intersects(a,b){return a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y;}
export function positions(screen,portrait=false){
 if(!portrait)return screen.widgets.map(w=>({...w.layout}));
 const widgets=screen.widgets,n=widgets.length,total=widgets.reduce((a,w)=>a+w.layout.w*w.layout.h,0);let remaining=24-2*n;
 const extra=widgets.map(w=>Math.floor((24-2*n)*w.layout.w*w.layout.h/total));remaining-=extra.reduce((a,b)=>a+b,0);
 for(let i=0;i<remaining;i++)extra[i%n]++;
 let y=0;return widgets.map((w,i)=>{const h=2+extra[i],r={x:0,y,w:12,h};y+=h;return {...(w.portrait||r)};});
}
export function validRect(r,type,portrait){
 const min=portrait?[3,2]:REGISTRY[type]?.min;
 return min&&r&&Object.keys(r).length===4&&['x','y','w','h'].every(k=>Number.isInteger(r[k]))&&r.x>=0&&r.y>=0&&r.w>=min[0]&&r.h>=min[1]&&r.x+r.w<=12&&r.y+r.h<=(portrait?24:12);
}
export function validateConfig(config){
 object(config,['version','name','locale','theme','modes']);if(config.version!==2||!['en','de','pl'].includes(config.locale)||!['classic','modern'].includes(config.theme))fail();text(config.name);list(config.modes,3,3);unique(config.modes.map(m=>m.id));
 for(const mode of config.modes){object(mode,['id','screens']);if(!STATES.includes(mode.id))fail();list(mode.screens,1,16);unique(mode.screens.map(s=>s.id));
  for(const screen of mode.screens){object(screen,['id','title','duration','requires','widgets']);id(screen.id);text(screen.title);if(screen.duration!==0&&(!Number.isInteger(screen.duration)||screen.duration<10||screen.duration>600))fail();list(screen.requires,0,16);unique(screen.requires);if(screen.requires.some(x=>!caps.includes(x)))fail();list(screen.widgets,1,12);unique(screen.widgets.map(w=>w.id));
   for(const w of screen.widgets){object(w,['id','type','layout','portrait','options'],['id','type','layout']);id(w.id);if(!REGISTRY[w.type])fail();object(w.layout,['x','y','w','h']);if(w.portrait)object(w.portrait,['x','y','w','h']);const options=w.options||{},rules=REGISTRY[w.type].options;object(options,Object.keys(rules),[]);for(const [k,v] of Object.entries(options)){const rule=rules[k];if(rule.enum?!rule.enum.includes(v):(!Number.isInteger(v)||v<rule.integer[0]||v>rule.integer[1]))fail();}}
   for(const portrait of [false,true]){const ps=positions(screen,portrait);for(let i=0;i<ps.length;i++){if(!validRect(ps[i],screen.widgets[i].type,portrait)||ps.slice(0,i).some(r=>intersects(r,ps[i])))fail();}}
  }
  if(!mode.screens.some(s=>!s.requires.length))fail();
 }
 return config;
}
export function place(screen,widgetId,rect,portrait=false,type){
 const index=screen.widgets.findIndex(w=>w.id===widgetId);type=type||screen.widgets[index]?.type;
 const r=Object.fromEntries(['x','y','w','h'].map(k=>[k,Math.round(rect[k])]));
 if(!validRect(r,type,portrait)||positions(screen,portrait).some((p,i)=>i!==index&&intersects(p,r)))return null;return r;
}
export function migrate(old,name='Imported layout'){
 const modes=STATES.map(mode=>{const src=old.modes.find(m=>m.id===mode)||old.modes[0],screens=[],used=new Set();for(const sid of src.screens){const s=old.screens.find(s=>s.id===sid),chunks=[[]];let x=0,y=0;for(const w of s.widgets){const width={small:4,wide:8,full:12}[w.size];if(x+width>12){y+=3;x=0;}if(y+3>12||chunks.at(-1).length>=12){chunks.push([]);x=y=0;}chunks.at(-1).push({id:w.id,type:w.type==='measurements'?(w.topic==='air'?'air':'rooms'):w.type,layout:{x,y,w:width,h:3}});x+=width;}chunks.forEach((widgets,i)=>{let id=i?s.id.slice(0,30)+'-part'+(i+1):s.id;let collision=0;while(used.has(id)){collision++;id=s.id.slice(0,24)+'-part'+(i+1)+'-'+collision;}used.add(id);screens.push({id,title:s.title.slice(0,70)+(i?' · '+(i+1):''),duration:src.rotationSeconds===0?0:Math.max(10,Math.min(600,src.rotationSeconds)),requires:s.requires||[],widgets});});}return {id:mode,screens};});return validateConfig({version:2,name,locale:old.locale||'en',theme:'modern',modes});
}
