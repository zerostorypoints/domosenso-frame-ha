// Catalog-owned presentation core. No HA globals, network, tokens or native APIs.
export const UI_VERSION='0.1.0';
const object=v=>v&&typeof v==='object'&&!Array.isArray(v);
const fail=()=>{throw Error('Invalid Frame display contract');};
const text=(v,n=120)=>typeof v==='string'&&v.length<=n;
const id=v=>typeof v==='string'&&/^[a-z][a-z0-9_-]{0,39}$/.test(v);
const keys=(v,allowed)=>object(v)&&Object.keys(v).every(k=>allowed.includes(k));
const list=(v,max=64)=>Array.isArray(v)&&v.length<=max;
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const date=v=>text(v,60)&&Number.isFinite(Date.parse(v));
export function validateConfig(c){
 if(!keys(c,['version','locale','screens','modes'])||c.version!==1||!['en','de','pl'].includes(c.locale)||!list(c.screens,16)||!c.screens.length||!list(c.modes,3)||!c.modes.length)fail();
 const screenIds=new Set(),modeIds=new Set();
 for(const s of c.screens){
  if(!keys(s,['id','title','widgets','requires'])||!id(s.id)||screenIds.has(s.id)||!text(s.title,80)||!s.title||!list(s.widgets,24)||!s.widgets.length)fail();screenIds.add(s.id);
  if(s.requires!==undefined&&(!list(s.requires,4)||new Set(s.requires).size!==s.requires.length||s.requires.some(v=>!['weather','temperature','air','status'].includes(v))))fail();
  const widgetIds=new Set();
  for(const w of s.widgets){
   if(!keys(w,['id','type','size','title','topic'])||!id(w.id)||widgetIds.has(w.id)||!['clock','weather','measurements','status'].includes(w.type)||!['small','wide','full'].includes(w.size)||w.title!==undefined&&!text(w.title,80))fail();
   if(w.type==='measurements'?!['air','temperature'].includes(w.topic):w.topic!==undefined)fail();widgetIds.add(w.id);
  }
 }
 for(const m of c.modes){
  if(!keys(m,['id','screens','rotationSeconds'])||!['ambient','controls','media'].includes(m.id)||modeIds.has(m.id)||!list(m.screens,16)||!m.screens.length||new Set(m.screens).size!==m.screens.length||m.screens.some(s=>!screenIds.has(s))||!Number.isInteger(m.rotationSeconds)||(m.rotationSeconds!==0&&(m.rotationSeconds<10||m.rotationSeconds>600)))fail();modeIds.add(m.id);
 }
 return c;
}
function visibleScreens(s,mode){
 const capabilities=['temperature','air','status'].filter(k=>s[k]?.length);if(s.weather)capabilities.push('weather');
 return mode.screens.filter(id=>(s.config.screens.find(p=>p.id===id).requires||[]).every(k=>capabilities.includes(k)));
}
export function validateSnapshot(s){
 if(!object(s)||s.version!==1||typeof s.connected!=='boolean'||!date(s.fetchedAt)||!text(s.timezone,80))fail();validateConfig(s.config);
 try{new Intl.DateTimeFormat('en',{timeZone:s.timezone});}catch{fail();}
 const mode=s.config.modes.find(m=>m.id===s.mode);if(!mode||!mode.screens.includes(s.screen))fail();
 for(const group of ['temperature','air']){
  if(!list(s[group]))fail();for(const m of s[group])if(!object(m)||!text(m.label,80)||!text(m.unit,30)||typeof m.available!=='boolean'||typeof m.stale!=='boolean'||m.value!==null&&!finite(m.value)||m.available&&m.value===null||m.observedAt!==null&&!date(m.observedAt)||m.updatedAt!==null&&!date(m.updatedAt)||m.indoor!==undefined&&typeof m.indoor!=='boolean')fail();
 }
 if(!list(s.status))fail();for(const m of s.status)if(!object(m)||!text(m.label,80)||!text(m.value,120)||typeof m.available!=='boolean'||typeof m.stale!=='boolean')fail();
 if(s.weather!==null){const w=s.weather;if(!object(w)||!text(w.label,80)||!text(w.condition,80)||!text(w.unit,30)||w.temperature!==null&&!finite(w.temperature)||typeof w.available!=='boolean'||typeof w.stale!=='boolean')fail();}
 if(s.assessment!==null){const a=s.assessment;if(!object(a)||a.version!==1||!['good','raised','high','unknown'].includes(a.status)||!['complete','partial','none'].includes(a.coverage)||!Number.isInteger(a.evaluated)||a.evaluated<0||a.total!==s.air.length||a.evaluated>a.total)fail();}
 if(!visibleScreens(s,mode).includes(s.screen))fail();
 return s;
}
const words={
 en:{home:'Home',preview:'Frame preview',offline:'No connection · readings may be outdated',old:'Outdated',missing:'Unavailable',empty:'No readings',clock:'Time',weather:'Weather',temperature:'Temperature',air:'Air quality',status:'Home status',good:'Within configured range',raised:'Needs attention',high:'Needs attention',unknown:'No assessment',partial:'Partial assessment',source:'According to Frame settings',loading:'Waiting for Home Assistant',current:'Current conditions',settings:'Preview · UI',on:'On',off:'Off',open:'Open',closed:'Closed',locked:'Locked',unlocked:'Unlocked'},
 pl:{home:'Dom',preview:'Podgląd Ramki',offline:'Brak połączenia · pomiary mogą być nieaktualne',old:'Nieaktualne',missing:'Brak danych',empty:'Brak pomiarów',clock:'Czas',weather:'Pogoda',temperature:'Temperatury',air:'Jakość powietrza',status:'Stan domu',good:'W dobrym zakresie ustawień',raised:'Wymaga uwagi',high:'Wymaga uwagi',unknown:'Brak oceny',partial:'Ocena częściowa',source:'Według ustawień ramki',loading:'Czekam na Home Assistanta',current:'Bieżące warunki',settings:'Podgląd · UI',on:'Włączone',off:'Wyłączone',open:'Otwarte',closed:'Zamknięte',locked:'Zablokowane',unlocked:'Odblokowane'},
 de:{home:'Zuhause',preview:'Frame-Vorschau',offline:'Keine Verbindung · Messwerte eventuell veraltet',old:'Veraltet',missing:'Nicht verfügbar',empty:'Keine Messwerte',clock:'Uhrzeit',weather:'Wetter',temperature:'Temperaturen',air:'Luftqualität',status:'Hausstatus',good:'Im konfigurierten guten Bereich',raised:'Aufmerksamkeit erforderlich',high:'Aufmerksamkeit erforderlich',unknown:'Keine Bewertung',partial:'Teilweise Bewertung',source:'Gemäss Frame-Einstellungen',loading:'Warte auf Home Assistant',current:'Aktuelle Bedingungen',settings:'Vorschau · UI',on:'Ein',off:'Aus',open:'Offen',closed:'Geschlossen',locked:'Verriegelt',unlocked:'Entriegelt'}
};
const weatherWords={sunny:['Sunny','Słonecznie','Sonnig'],cloudy:['Cloudy','Pochmurno','Bewölkt'],partlycloudy:['Partly cloudy','Częściowe zachmurzenie','Teilweise bewölkt'],rainy:['Rain','Deszcz','Regen'],pouring:['Heavy rain','Ulewa','Starkregen'],snowy:['Snow','Śnieg','Schnee'],fog:['Fog','Mgła','Nebel'],'clear-night':['Clear night','Bezchmurna noc','Klare Nacht'],windy:['Windy','Wietrznie','Windig']};
const css=`
:host{display:block;box-sizing:border-box;container-type:inline-size;height:100%;min-height:320px;color:#ecf4f2;font:1rem system-ui,sans-serif;background:#101b20;color-scheme:dark}
*{box-sizing:border-box}.shell{height:100%;overflow:auto;display:flex;flex-direction:column;padding:clamp(12px,2cqw,28px);gap:16px}.header{flex-shrink:0;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.brand{font-size:.8rem;letter-spacing:.14em;color:#8ed9c8}h1{margin:4px 0 0;font-size:clamp(1.4rem,3cqw,2rem);overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}nav{display:flex;flex-wrap:nowrap;overflow-x:auto;max-width:100%;max-height:96px;gap:8px}button{flex:0 0 auto;max-width:240px;font:inherit;min-height:48px;padding:10px 16px;border:1px solid #456365;border-radius:12px;background:#172a30;color:inherit;cursor:pointer;overflow-wrap:anywhere;max-width:min(240px,100%);max-height:96px;overflow:auto}button[aria-current=true]{background:#b0ebd9;color:#10231f;border-color:#b0ebd9}button:focus-visible{outline:3px solid #fff;outline-offset:2px}.notice{background:#513b21;border:1px solid #a5844e;border-radius:12px;padding:12px;overflow-wrap:anywhere}.content{overflow:auto;min-height:160px;flex:1}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;align-content:start}.widget{min-width:0;padding:clamp(18px,2.3cqw,30px);border:1px solid #31454b;border-radius:22px;background:#1b2a30;overflow-wrap:anywhere}.wide{grid-column:span 2}.full{grid-column:1/-1}h2{font-size:1rem;line-height:1.4;color:#b5ceca;margin:0 0 20px}.big{font-size:clamp(2.2rem,6cqw,4.8rem);line-height:1.1;font-weight:600;letter-spacing:-.035em}.muted{color:#abc0bd;font-size:.9rem;margin-top:12px;line-height:1.5}.rows{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:14px}.reading{border-top:1px solid #3a5053;padding-top:14px;min-width:0}.value{font-size:clamp(1.4rem,3cqw,2.5rem);line-height:1.3;margin-top:8px;font-variant-numeric:tabular-nums}.unit{font-size:.85rem;color:#abc0bd}.assessment{margin:0 0 18px;padding:12px;border-left:4px solid #8ed9c8;background:#213935}.assessment.attention{border-color:#eab974;background:#403326}.foot{display:flex;gap:16px;flex-wrap:wrap;font-size:.75rem;color:#9ab3af}.badge{color:#b6edda}.condition{font-size:1.2rem;margin-top:16px}
@container(max-width:850px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.wide{grid-column:span 1}.widget[data-kind=measurements].wide{grid-column:span 2}}
@container(max-width:650px){.grid{grid-template-columns:minmax(0,1fr)}.wide,.widget[data-kind=measurements].wide{grid-column:span 1}.header{align-items:flex-start}nav{width:100%}.widget{padding:20px}.big{font-size:clamp(2.2rem,12cqw,4rem)}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
`;
const node=(tag,cls,value)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(value!==undefined)n.textContent=value;return n;};
export class FrameUI extends (globalThis.HTMLElement||class{}){
 constructor(){super();this.attachShadow({mode:'open'});this._selected=null;this._until=0;this._paused=false;this._interact=()=>{if(this._displayed)this._selected=this._displayed;this._until=Date.now()+45000;};}
 connectedCallback(){this.addEventListener('pointerdown',this._interact);this.addEventListener('keydown',this._interact);this._timer=setInterval(()=>this._clocks(),1000);this.render();}
 disconnectedCallback(){clearInterval(this._timer);this.removeEventListener('pointerdown',this._interact);this.removeEventListener('keydown',this._interact);}
 set paused(v){this._paused=!!v;if(v&&this._displayed)this._selected=this._displayed;if(!v)this._until=Date.now()+45000;}
 set snapshot(v){validateSnapshot(v);this._snapshot=v;this.render();}
 get snapshot(){return this._snapshot;}
 _clocks(){if(!this._snapshot)return;const s=this._snapshot;const offline=!s.connected||Date.now()-Date.parse(s.fetchedAt)>30000;if(this._offline!==offline){this.render();return;}for(const n of this.shadowRoot.querySelectorAll('[data-clock]'))n.textContent=new Intl.DateTimeFormat(s.config.locale,{timeZone:s.timezone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date());}
 render(){
  const s=this._snapshot,c=s?.config,t=words[c?.locale||'en'];const root=this.shadowRoot;const scroll=root.querySelector('.content')?.scrollTop||0;const focus=root.activeElement?.dataset?.key;
  root.replaceChildren(node('style',null,css));if(!s){root.append(node('div','shell',t.loading));return;}
  const mode=c.modes.find(m=>m.id===s.mode),pages=visibleScreens(s,mode);
  if(!pages.includes(this._selected)||(!this._paused&&Date.now()>this._until))this._selected=null;
  const selected=this._selected||s.screen;this._displayed=selected;const screen=c.screens.find(p=>p.id===selected);
  const shell=node('main','shell'),head=node('header','header'),titles=node('div');titles.append(node('div','brand','DOMOSENSO'),node('h1',null,screen.title));head.append(titles);
  const nav=node('nav');nav.setAttribute('aria-label',t.preview);
  for(const page of pages){const title=c.screens.find(p=>p.id===page).title,b=node('button',null,title);b.dataset.key=page;b.setAttribute('aria-current',String(selected===page));b.onclick=()=>{this._selected=page;this._until=Date.now()+45000;this.render();};nav.append(b);}head.append(nav);shell.append(head);
  const offline=!s.connected||Date.now()-Date.parse(s.fetchedAt)>30000;this._offline=offline;
  if(offline){const notice=node('div','notice',t.offline);notice.setAttribute('role','status');shell.append(notice);}
  const content=node('div','content'),grid=node('div','grid');
  for(const w of screen.widgets){const card=node('section','widget '+w.size);card.dataset.widget=w.id;card.dataset.kind=w.type;card.append(node('h2',null,w.title||t[w.topic||w.type]));
   if(w.type==='clock'){const clock=node('div','big');clock.dataset.clock='true';card.append(clock,node('div','muted',new Intl.DateTimeFormat(c.locale,{timeZone:s.timezone,dateStyle:'full'}).format(new Date())));}
   if(w.type==='weather'){const v=s.weather,usable=v?.available&&!v.stale&&!offline;card.append(node('div','big',usable&&v.temperature!==null?new Intl.NumberFormat(c.locale,{maximumFractionDigits:1}).format(v.temperature)+' '+v.unit:t.missing));const label=weatherWords[v?.condition]?.[{en:0,pl:1,de:2}[c.locale]]||v?.condition||t.missing;card.append(node('div','condition',usable?label:v?.stale?t.old:t.missing),node('div','muted',t.current));}
   if(w.type==='measurements'){
    const a=w.topic==='air'?s.assessment:null;if(a){const box=node('div','assessment '+(['raised','high'].includes(a.status)?'attention':''));box.append(node('div',null,offline?t.old:t[a.status]),node('div','muted',t.source+' · '+a.evaluated+'/'+a.total+(a.coverage==='partial'?' · '+t.partial:'')));card.append(box);}
    const values=s[w.topic],rows=node('div','rows');if(!values.length)rows.append(node('div','muted',t.empty));
    for(const m of values){const r=node('div','reading');r.append(node('div',null,m.label));const valid=m.available&&!m.stale&&!offline&&m.value!==null;r.append(node('div','value',valid?new Intl.NumberFormat(c.locale,{maximumFractionDigits:2}).format(m.value):m.stale||offline?t.old:t.missing));if(valid)r.append(node('div','unit',m.unit));rows.append(r);}card.append(rows);
   }
   if(w.type==='status'){const rows=node('div','rows');if(!s.status.length)rows.append(node('div','muted',t.empty));for(const m of s.status){const r=node('div','reading');r.append(node('div',null,m.label),node('div','value',offline||m.stale?t.old:!m.available?t.missing:t[m.value]||m.value));rows.append(r);}card.append(rows);}
   grid.append(card);
  }
  content.append(grid);shell.append(content);const foot=node('footer','foot');foot.append(node('span',null,t.settings+' '+UI_VERSION),node('span',null,new Intl.DateTimeFormat(c.locale,{timeZone:s.timezone,timeStyle:'medium'}).format(new Date(s.fetchedAt))));shell.append(foot);root.append(shell);content.scrollTop=scroll;this._clocks();if(focus)for(const b of root.querySelectorAll('button'))if(b.dataset.key===focus)b.focus();
 }
}
if(globalThis.customElements&&!customElements.get('domosenso-frame'))customElements.define('domosenso-frame',FrameUI);
