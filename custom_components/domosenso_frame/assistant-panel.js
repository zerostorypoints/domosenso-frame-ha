// The integration owns exposure; the catalog owns meanings and the app consumes typed answers.
const copy=value=>structuredClone(value);
const text=(tag,value)=>{const node=document.createElement(tag);node.textContent=value;return node;};
const sameSource=(a,b)=>a?.entity===b?.entity&&a?.attribute===b?.attribute;
const sameSelection=(a,b)=>a?.component===b?.component&&a?.subject===b?.subject&&Object.keys(a.fields).length===Object.keys(b.fields).length&&Object.entries(a.fields).every(([role,field])=>sameSource(field,b.fields[role]));
const words={
 en:{scopeChanged:'Some approved readings are no longer compatible. Review the current sources before enabling the updated scope.',incomplete:'Incomplete',future:'All current and future sources',futureHint:'Includes configured replacement sensors for the approved readings. New capabilities still require a new selection. Uncheck an item to exclude it.',discovered:'Discovered',selected:'Selected',excluded:'Excluded',scopeRemove:'Disable future sources',manualHint:'Manual sources are configured once: shared labels, readings and limits are used for display and answers. Unmapped sensors are not automatically shared.',explain:'Allow component description',title:'Assistant sources',intro:'Choose which component data this frame may use. Names and assessment rules stay linked to the installed component. Saving does not change dashboards or HA helpers.',frame:'Frame',load:'Loading…',save:'Save selections',reload:'Discard draft and reload',manual:'Manual sources / vanilla HA',empty:'No component metadata is installed. Update components using DomoSenso, or use manual sources for vanilla HA.',missing:'Metadata is missing or does not match the installed component. Render and deploy matching component metadata, then reload the integration.',verified:'Verified component metadata',sources:'Review sources',unavailable:'Some sources do not currently exist.',sensitive:'Calendar and presence require separate explicit exposure in Manual sources.',remove:'Remove selection',saved:'Selections saved. The app will use them on its next question.',error:'Could not save or load. The draft is retained. Reload if sources changed.',stale:'Stored selections (including unavailable components)',loadingError:'Could not load frames.'},
 pl:{scopeChanged:'Część zatwierdzonych pomiarów nie jest już zgodna. Sprawdź bieżące źródła przed włączeniem zaktualizowanego zakresu.',incomplete:'Niekompletne',future:'Wszystkie obecne i przyszłe źródła',futureHint:'Obejmuje także wymianę skonfigurowanych czujników dla zatwierdzonych pomiarów. Nowe możliwości wymagają osobnego wyboru. Odznacz element, aby go wykluczyć.',discovered:'Odkryte',selected:'Wybrane',excluded:'Wykluczone',scopeRemove:'Wyłącz przyszłe źródła',manualHint:'Źródła ręczne konfigurujesz raz: ekran i odpowiedzi korzystają ze wspólnych nazw, pomiarów i progów. Nieprzypisane sensory nie są udostępniane automatycznie.',explain:'Udostępnij opis komponentu',title:'Źródła asystenta',intro:'Wybierz dane komponentów udostępnione tej ramce. Nazwy i reguły oceny pozostają powiązane z zainstalowanym komponentem. Zapis nie zmienia dashboardów ani helperów HA.',frame:'Ramka',load:'Wczytywanie…',save:'Zapisz wybór',reload:'Odrzuć szkic i wczytaj ponownie',manual:'Źródła ręczne / vanilla HA',empty:'Brak zainstalowanych metadanych komponentów. Zaktualizuj komponenty przez DomoSenso lub wybierz źródła ręczne dla vanilla HA.',missing:'Brakuje metadanych albo nie pasują do zainstalowanego komponentu. Wygeneruj i wdróż zgodne metadane, następnie przeładuj integrację.',verified:'Zweryfikowane metadane komponentu',sources:'Sprawdź źródła',unavailable:'Część źródeł obecnie nie istnieje.',sensitive:'Kalendarz i obecność wymagają osobnego udostępnienia w Źródłach ręcznych.',remove:'Usuń wybór',saved:'Wybór zapisany. Aplikacja użyje go przy następnym pytaniu.',error:'Nie udało się zapisać lub wczytać danych. Szkic zachowany. Jeśli źródła się zmieniły, wczytaj je ponownie.',stale:'Zapisane wybory (także dla niedostępnych komponentów)',loadingError:'Nie udało się wczytać ramek.'}
};
class AssistantPanel extends HTMLElement {
 constructor(){super();this.epoch=0;this.busy=false;this.notice='';}
 set hass(value){const language=value.language?.startsWith('pl')?'pl':'en',changed=language!==this.language;this._hass=value;this.language=language;if(!this.started){this.started=true;void this.start();}else if(changed)this.render();}
 disconnectedCallback(){this.epoch++;this.started=false;}
 label(value){return typeof value==='string'?value:value?.[this.language]||value?.en||'';}
 async start(){const epoch=++this.epoch;this.busy=true;this.render();try{const profile=await this._hass.callWS({type:'domosenso_frame/profile'});if(epoch!==this.epoch)return;this.endpoints=profile.endpoints.filter(e=>e.displayVersion===1);if(this.endpoints.length){await this.load(this.endpoints[0].id);return;}}catch{if(epoch===this.epoch)this.notice='loadingError';}if(epoch===this.epoch){this.busy=false;this.render();}}
 async load(endpoint){const epoch=++this.epoch;this.busy=true;this.notice='';this.render();try{const result=await this._hass.callWS({type:'domosenso_frame/studio_mappings',version:1,endpoint});if(epoch!==this.epoch)return;this.endpoint=endpoint;this.result=result;this.draft=copy(result.mappings.assistant||{subjects:[],sensitive:false});}catch{if(epoch===this.epoch)this.notice='error';}finally{if(epoch===this.epoch){this.busy=false;this.render();}}}
 async save(){if(this.busy||!this.result)return;const epoch=this.epoch;this.busy=true;this.notice='';this.render();try{const result=await this._hass.callWS({type:'domosenso_frame/studio_mappings_update',version:1,endpoint:this.endpoint,revision:this.result.revision,patch:{assistant:copy(this.draft)}});if(epoch!==this.epoch)return;this.result=result;this.draft=copy(result.mappings.assistant);this.notice='saved';}catch{if(epoch===this.epoch)this.notice='error';}finally{if(epoch===this.epoch){this.busy=false;this.render();}}}
 button(label,action){const b=text('button',label);b.type='button';b.disabled=this.busy;b.onclick=action;return b;}
 selectSource(selection,enabled){const entries=this.draft.component_sources||[];this.draft.component_sources=entries.filter(s=>s.component!==selection.component||s.subject!==selection.subject);if(enabled)this.draft.component_sources.push(copy(selection));this.notice='';this.render();}
 scope(component,kind){return this.draft?.source_policy?.scopes.find(s=>s.component===component&&s.kind===kind);}
 setFuture(component,proposal,enabled){
  const policy=this.draft.source_policy||{version:1,scopes:[]},previous=this.scope(component.id,proposal.kind);
  policy.scopes=policy.scopes.filter(s=>s.component!==component.id||s.kind!==proposal.kind);
  const subjects=component.subjects.filter(s=>s.kind===proposal.kind);
  const independent=copy(this.draft.component_sources||[]);
  this.draft.component_sources=independent.filter(s=>s.component!==component.id||!subjects.some(r=>r.key===s.subject));
  if(enabled)policy.scopes.push(copy(proposal));
  else for(const subject of subjects){
   const exact=independent.find(s=>s.component===component.id&&s.subject===subject.key);
   const fields={...(exact?.fields||{})};
   if(!previous?.exclude.includes(subject.key))for(const [role,field] of Object.entries(subject.selection.fields))if(previous?.roles.includes(role)&&!fields[role])fields[role]=copy(field);
   if(Object.keys(fields).length)this.draft.component_sources.push({...copy(subject.selection),fields});
  }
  this.draft.source_policy=policy;this.notice='';this.render();
 }
 toggleSubject(component,subject,enabled){
  const scope=this.scope(component.id,subject.kind);
  if(!scope){this.selectSource(subject.selection,enabled);return;}
  scope.exclude=scope.exclude.filter(k=>k!==subject.key);if(!enabled)scope.exclude.push(subject.key);
  this.notice='';this.render();
 }
 controlsDraft(){return this.draft.controls||={enabled:false,grants:[],mappings:[]};}
 controlMapping(source,key,value){
  const controls=this.controlsDraft();let row=controls.mappings.find(m=>m.source===source.source);
  if(!row){row={source:source.source,entity:source.entity};controls.mappings.push(row);}
  if(value===''||value==null)delete row[key];else row[key]=value;
  controls.grants=controls.grants.filter(g=>g.source!==source.source);this.notice='';this.render();
 }
 renderControls(root){
  const catalog=this.result?.controlCatalog;if(!catalog)return;
  const pl=this.language==='pl',say=(en,polish)=>pl?polish:en;
  const section=document.createElement('fieldset');section.append(text('legend',say('Assistant controls','Sterowanie asystentem')));
  section.append(text('p',say('Review exact sources and actions. New or replaced sources never inherit consent. Save mapping changes first, then review and grant the current binding.','Sprawdź dokładne źródła i działania. Nowe lub wymienione źródła wymagają osobnej zgody. Najpierw zapisz mapowania, następnie sprawdź bieżące powiązanie i nadaj uprawnienia.')));
  const current=this.draft.controls||{enabled:false,grants:[],mappings:[]};
  const enabled=document.createElement('input');enabled.type='checkbox';enabled.dataset.controlsEnabled='';enabled.checked=current.enabled;enabled.disabled=this.busy;
  enabled.onchange=()=>{this.controlsDraft().enabled=enabled.checked;this.render();};
  const enableLabel=text('label',say('Enable explicitly granted controls','Włącz zatwierdzone sterowanie'));enableLabel.prepend(enabled);section.append(enableLabel);
  for(const source of catalog.sources){
   const row=document.createElement('fieldset');row.append(text('legend',this.label(source.label)),text('p',source.entity));
   if(source.members?.length)row.append(text('p',say('Current members: ','Bieżące elementy: ')+source.members.join(', ')));
   if(source.reason)row.append(text('p',source.reason));
   const mapping=current.mappings.find(m=>m.source===source.source)||{};
   const saved=(this.result.mappings.assistant?.controls?.mappings||[]).find(m=>m.source===source.source)||{};
   const changed=JSON.stringify(mapping)!==JSON.stringify(saved);
   const area=document.createElement('select');area.dataset.controlArea='';area.disabled=this.busy;area.setAttribute('aria-label',say('Explicit HA area','Jawny obszar HA'));
   const automatic=text('option',say('Use source / HA area','Użyj obszaru źródła / HA'));automatic.value='';area.append(automatic);
   for(const a of catalog.areas){const option=text('option',a.name);option.value=a.id;area.append(option);}area.value=mapping.area||'';
   area.onchange=()=>this.controlMapping(source,'area',area.value);row.append(area);
   if(source.kind!=='light'){
    const type=document.createElement('select');type.dataset.controlClass='';type.disabled=this.busy;type.setAttribute('aria-label',say('Verified covering class','Potwierdzony typ osłony'));
    for(const value of ['', 'blind','curtain','shade','shutter','awning']){const option=text('option',value||say('Use HA device class','Użyj typu urządzenia HA'));option.value=value;type.append(option);}
    type.value=mapping.coverClass||'';type.onchange=()=>this.controlMapping(source,'coverClass',type.value);row.append(type);
    if(source.kind==='awning'||mapping.coverClass==='awning'){
     if(source.provenance&&source.guard)row.append(text('p',say('Wind policy supplied by installed component','Polityka wiatru z zainstalowanego komponentu')));
     else{
      const guard=mapping.guard||{wind:{entity:''},last_heard:{entity:''},max_age_seconds:3600};
      for(const [role,domains] of [['wind',['binary_sensor']],['last_heard',['input_datetime','sensor']]]){
       row.append(text('label',role==='wind'?say('Wind alarm (off = safe report)','Alarm wiatru (off = bezpieczny odczyt)'):say('Last report timestamp','Czas ostatniego raportu')));
       const picker=document.createElement('ha-selector');picker.hass=this._hass;picker.selector={entity:{filter:{domain:domains}}};picker.value=guard[role].entity;picker.disabled=this.busy;
       picker.addEventListener('value-changed',event=>{const next=copy(guard);next[role]={entity:event.detail.value};this.controlMapping(source,'guard',next);});row.append(picker);
      }
      const age=document.createElement('input');age.type='number';age.min='1';age.max='604800';age.value=guard.max_age_seconds;age.disabled=this.busy;age.setAttribute('aria-label',say('Maximum report age (seconds)','Maksymalny wiek raportu (sekundy)'));
      age.onchange=()=>this.controlMapping(source,'guard',{...copy(guard),max_age_seconds:Number(age.value)});row.append(age);
      row.append(this.button(say('Remove wind mapping','Usuń mapowanie wiatru'),()=>this.controlMapping(source,'guard',null)));
     }
    }
   }
   if(source.grant&&!changed){
    const chosen=current.grants.find(g=>g.source===source.source),valid=chosen?.bindingRevision===source.grant.bindingRevision;
    if(source.kind==='awning'&&(!source.grant.safetyRevision||(valid&&chosen.capabilities['awnings.extend']&&chosen.safetyRevision!==source.grant.safetyRevision))){
     row.append(text('p',say('Wind binding is unavailable or needs review. Granted retraction and STOP remain available.','Powiązanie wiatru jest niedostępne lub wymaga sprawdzenia. Zatwierdzone zwijanie i STOP pozostają dostępne.')));
     if(valid&&source.grant.safetyRevision&&chosen.capabilities['awnings.extend']){
      const review=this.button(say('Review current wind binding for extension','Zatwierdź bieżące powiązanie wiatru dla wysuwania'),()=>{chosen.safetyRevision=source.grant.safetyRevision;this.render();});review.dataset.controlWindReview='';row.append(review);
     }
    }
    const all=document.createElement('input');all.type='checkbox';all.dataset.controlGrant='';all.disabled=this.busy;all.checked=!!valid;
    all.onchange=()=>{const c=this.controlsDraft();c.grants=c.grants.filter(g=>g.source!==source.source);if(all.checked)c.grants.push(copy(source.grant));this.render();};
    const label=text('label',say('Grant reviewed actions: ','Zatwierdź działania: ')+Object.keys(source.grant.capabilities).join(', '));label.prepend(all);row.append(label);
    if(valid)for(const cap of Object.keys(source.grant.capabilities)){
     const check=document.createElement('input');check.type='checkbox';check.disabled=this.busy;check.checked=!!chosen.capabilities[cap];
     check.onchange=()=>{if(check.checked)chosen.capabilities[cap]=1;else delete chosen.capabilities[cap];if(!Object.keys(chosen.capabilities).length)this.controlsDraft().grants=this.controlsDraft().grants.filter(g=>g!==chosen);this.render();};
     const label=text('label',cap);label.prepend(check);row.append(label);
    }
   }
   section.append(row);
  }
  for(const grant of current.grants){const row=text('div',grant.source+' ');row.append(this.button(say('Remove control grant','Usuń zgodę na sterowanie'),()=>{this.controlsDraft().grants=this.controlsDraft().grants.filter(g=>g!==grant);this.render();}));section.append(row);}
  root.append(section);
 }
 render(){if(!this._hass)return;const t=words[this.language||'en'];this.style.cssText=(this.style.display?'display:'+this.style.display+';':'')+'padding:24px;max-width:900px;font:16px system-ui;line-height:1.5;color:var(--primary-text-color)';const root=document.createElement('section');root.append(text('h1',t.title),text('p',t.intro));
 const controls=document.createElement('div');controls.style.cssText='display:flex;gap:12px;flex-wrap:wrap;align-items:center';
 const selector=document.createElement('select');selector.setAttribute('aria-label',t.frame);selector.disabled=this.busy;for(const frame of this.endpoints||[]){const option=text('option',frame.name);option.value=frame.id;option.selected=frame.id===this.endpoint;selector.append(option);}selector.onchange=()=>void this.load(selector.value);controls.append(selector);
 const manual=this.button(t.manual,()=>this.dispatchEvent(new CustomEvent('open-manual-assistant',{bubbles:true,detail:{endpoint:this.endpoint}})));controls.append(manual);root.append(controls,text('p',t.manualHint));
 if(this.notice){const note=text('p',t[this.notice]||t.error);note.setAttribute('role','status');root.append(note);}if(this.busy)root.append(text('p',t.load));
 const components=this.result?.assistantCatalog?.components||[];
 if(this.result&&!components.length)root.append(text('p',t.empty));
 for(const component of components){const section=document.createElement('fieldset');section.style.cssText='margin:16px 0;padding:16px;border:1px solid #888;border-radius:10px';section.append(text('legend',component.id));
  if(component.status!=='verified'){section.append(text('p',t.missing));root.append(section);continue;}
  section.append(text('p',t.verified));
  if(component.coverage?.scopeChanged)section.append(text('p',t.scopeChanged));
  if(component.coverage?.incomplete)section.append(text('p',t.incomplete+': '+component.coverage.incomplete));
  for(const proposal of component.scopes||[]){
   const label=text('label',t.future+' · '+proposal.kind),box=document.createElement('input');box.type='checkbox';box.dataset.future='';box.disabled=this.busy;box.checked=!!this.scope(component.id,proposal.kind);
   box.onchange=()=>this.setFuture(component,proposal,box.checked);label.prepend(box);section.append(label,text('p',t.futureHint));
  }
  const isSelected=subject=>{const scope=this.scope(component.id,subject.kind);return scope?!scope.exclude.includes(subject.key):(this.draft?.component_sources||[]).some(s=>sameSelection(s,subject.selection));};
  const excluded=(this.draft?.source_policy?.scopes||[]).filter(s=>s.component===component.id).reduce((n,s)=>n+s.exclude.length,0);
  section.append(text('p',`${t.discovered}: ${component.subjects.length} · ${t.selected}: ${component.subjects.filter(isSelected).length} · ${t.excluded}: ${excluded}`));
  if(component.explanation){const label=text('label',t.explain),box=document.createElement('input');box.type='checkbox';box.dataset.explanation='';box.disabled=this.busy;box.checked=(this.draft.explanations||[]).includes(component.id);box.onchange=()=>{this.draft.explanations=(this.draft.explanations||[]).filter(id=>id!==component.id);if(box.checked)this.draft.explanations.push(component.id);this.notice='';this.render();};label.prepend(box);section.append(label);}
  for(const subject of component.subjects){const row=document.createElement('div');row.style.marginBottom='16px';const label=text('label',this.label(subject.label));const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.disabled=this.busy;checkbox.dataset.subject='';checkbox.checked=isSelected(subject);checkbox.onchange=()=>this.toggleSubject(component,subject,checkbox.checked);label.prepend(checkbox);row.append(label);
   const details=document.createElement('details');details.append(text('summary',t.sources));for(const [role,field] of Object.entries(subject.selection.fields))details.append(text('div',`${role}: ${field.entity}${field.attribute?' · '+field.attribute:''}`));row.append(details);
   if(subject.missing)row.append(text('p',t.unavailable));if(['calendar','presence_source'].includes(subject.kind))row.append(text('p',t.sensitive));section.append(row);
  }root.append(section);
 }
 for(const scope of this.draft?.source_policy?.scopes||[]){const row=text('div',scope.component+' · '+scope.kind+' ');row.append(this.button(t.scopeRemove,()=>{this.draft.source_policy.scopes=this.draft.source_policy.scopes.filter(s=>s!==scope);this.render();}));root.append(row);}
 if(this.draft?.component_sources?.length){root.append(text('h2',t.stale));for(const selected of this.draft.component_sources){const row=text('div',selected.component+' · '+selected.subject+' ');const remove=this.button(t.remove,()=>this.selectSource(selected,false));remove.dataset.remove='';row.append(remove);root.append(row);}}
 this.renderControls(root);
 if(this.result){const save=this.button(t.save,()=>void this.save());save.dataset.save='';save.disabled=this.busy||JSON.stringify(this.draft)===JSON.stringify(this.result.mappings.assistant);root.append(save,this.button(t.reload,()=>void this.load(this.endpoint)));}
 for(const control of root.querySelectorAll('button,select'))control.style.cssText='font:inherit;padding:10px 16px;margin:6px;border:1px solid #888;border-radius:8px;min-height:44px;max-width:100%;color:inherit;background:var(--card-background-color,#fff);cursor:pointer';this.replaceChildren(root);
 }
}
customElements.define('domosenso-frame-assistant',AssistantPanel);
