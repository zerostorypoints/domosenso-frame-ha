import {validateConfig} from './ui/0.1.0/frame-ui.js';
const copy=x=>JSON.parse(JSON.stringify(x));
function validate(c){validateConfig(c);const fallback=c.screens.filter(s=>!s.requires?.length).map(s=>s.id);if(c.modes.some(m=>!m.screens.some(s=>fallback.includes(s))))throw Error('fallback');return c;}
const fresh=(items,prefix)=>{let n=1;while(items.some(x=>x.id===prefix+n))n++;return prefix+n;};
function move(array,value,offset){const i=array.indexOf(value),j=i+offset;if(i>=0&&j>=0&&j<array.length)[array[i],array[j]]=[array[j],array[i]];}
export class Draft {
 constructor(config){this.config=copy(validateConfig(config));}
 change(fn){const next=copy(this.config);fn(next);validate(next);this.config=next;}
 addScreen(mode){const id=fresh(this.config.screens,'screen');this.change(c=>{c.screens.push({id,title:'Screen',widgets:[{id:'clock',type:'clock',size:'small'}]});c.modes.find(m=>m.id===mode).screens.push(id);});return id;}
 removeScreen(id){this.change(c=>{c.screens=c.screens.filter(s=>s.id!==id);for(const m of c.modes)m.screens=m.screens.filter(x=>x!==id);});}
 addMode(id){this.change(c=>c.modes.push({id,screens:[c.screens.find(s=>!s.requires?.length).id],rotationSeconds:20}));}
 removeMode(id){this.change(c=>c.modes=c.modes.filter(m=>m.id!==id));}
 assign(mode,id,on){this.change(c=>{const m=c.modes.find(m=>m.id===mode);m.screens=on?[...new Set([...m.screens,id])]:m.screens.filter(x=>x!==id);});}
 moveScreen(mode,id,offset){this.change(c=>move(c.modes.find(m=>m.id===mode).screens,id,offset));}
 addWidget(screen,kind){this.change(c=>{const s=c.screens.find(s=>s.id===screen);s.widgets.push({id:fresh(s.widgets,'widget'),type:['air','temperature'].includes(kind)?'measurements':kind,size:'wide',...(['air','temperature'].includes(kind)?{topic:kind}:{})});});}
 removeWidget(screen,id){this.change(c=>{const s=c.screens.find(s=>s.id===screen);s.widgets=s.widgets.filter(w=>w.id!==id);});}
 moveWidget(screen,id,offset){this.change(c=>{const a=c.screens.find(s=>s.id===screen).widgets;const item=a.find(w=>w.id===id);move(a,item,offset);});}
 visible(id,capabilities){return (this.config.screens.find(s=>s.id===id)?.requires||[]).every(c=>capabilities.includes(c));}
}
