(() => {
  'use strict';
  const ruleForm=document.querySelector('#doc568-rule'),runList=document.querySelector('#doc568-runs'),calForm=document.querySelector('#doc568-calendar'),caseForm=document.querySelector('#doc568-case');
  if(!ruleForm&&!runList&&!calForm)return;
  const prefix=document.querySelector('meta[name="axiorhub-prefix"]')?.content||'',csrf=document.querySelector('meta[name="axiorhub-csrf"]')?.content||'';
  const status=document.querySelector('#doc568-status');let rules=[],busy=false,runSignature='',compiledText='',generation=0,refreshTimer=null;
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const note=text=>{status.textContent=text;};
  const labels={active:'Actif',draft:'À relire',paused:'Suspendu',queued:'En attente',running:'Analyse en cours',verified:'Résultats vérifiés',waiting:'Conclusions en préparation',decision:'Votre précision est nécessaire',error:'Incident : reprise possible',abstained:'Abstention expliquée',superseded:'Déjà traité',dismissed:'Écarté'};
  async function api(route,data){
    const r=await fetch(prefix+'/api440/m568/'+route,{method:data===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:data===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    const v=await r.json();if(!r.ok)throw new Error(v.message||v.error||'Demande non terminée.');return v;
  }
  function button(text,work){const b=el('button',text);b.type='button';b.addEventListener('click',async()=>{if(b.disabled)return;const old=b.textContent;b.disabled=true;b.textContent='Enregistrement…';try{await work();}catch(e){note(e.message);}finally{b.disabled=false;b.textContent=old;}});return b;}
  function set(form,key,value){const f=form.elements.namedItem(key);if(!f)return;if(f.type==='checkbox')f.checked=Boolean(value);else if(f.tagName==='SELECT'&&f.multiple)[...f.options].forEach(x=>{x.selected=(value||[]).includes(x.value);});else f.value=Array.isArray(value)?value.join('\n'):String(value??'');}
  function fillRule(row){
    generation++;ruleForm.reset();['id','revision','name','instruction'].forEach(k=>set(ruleForm,k,row[k]));
    compiledText=row.instruction;fillRecipe(row.recipe);ruleForm.elements.approved.checked=false;
    document.querySelector('#doc568-warnings').textContent='Vérifiez la portée et les effets avant activation. Le plafond du profil reste prioritaire.';
    ruleForm.elements.instruction.focus({preventScroll:true});ruleForm.scrollIntoView({block:'nearest',behavior:'smooth'});
  }
  function fillRecipe(r){
    ['names','types','age_days','clock','scope','mode','recipient','folder'].forEach(k=>set(ruleForm,k,r[k]));
    ruleForm.querySelectorAll('[name="actions"]').forEach(x=>{x.checked=r.actions.includes(x.value);});
    document.querySelector('#doc568-recipe').hidden=false;
  }
  function recipe(){
    const f=new FormData(ruleForm);if(compiledText!==String(f.get('instruction')||''))throw new Error('La mission a changé. Interprétez sa nouvelle version avant activation.');
    return {names:String(f.get('names')||'').split('\n').map(x=>x.trim()).filter(Boolean),types:f.getAll('types'),age_days:Number(f.get('age_days')),clock:f.get('clock'),scope:f.get('scope'),mode:f.get('mode'),recipient:f.get('recipient'),folder:f.get('folder')||'PROCEDURE',actions:['analyze',...f.getAll('actions').filter(x=>x!=='analyze')],guidance:compiledText};
  }
  async function renderRules(){
    const v=await api('rules/list');rules=v.rules;const list=document.querySelector('#doc568-rules');if(!list)return;
    list.replaceChildren();rules.forEach(r=>{
      const card=el('article',undefined,'mission567-card doc568-rule-card');card.append(el('h3',r.name),el('p',(labels[r.state]||r.state)+' · version '+r.revision),el('p',r.summary.trigger),el('small',r.summary.age));
      const actions=el('ul');r.summary.actions.forEach(x=>actions.append(el('li',x)));card.append(actions);
      const bar=el('div',undefined,'actions');bar.append(button(r.state==='active'?'Modifier':'Relire et activer',()=>fillRule(r)));
      if(r.state==='active')bar.append(button('Suspendre',async()=>{await api('rules/control',{id:r.id,action:'pause'});note('Agent suspendu, suites en cours également suspendues.');await renderRules();}));
      bar.append(button('Supprimer cet agent',async()=>{await api('rules/control',{id:r.id,action:'delete'});note('Agent supprimé. L’historique et les preuves restent disponibles.');await renderRules();}));card.append(bar);list.append(card);
    });
  }
  if(ruleForm){
    document.querySelector('#doc568-compile').addEventListener('click',async event=>{
      const b=event.currentTarget,banner=document.querySelector('#doc568-compile-status'),g=++generation,text=ruleForm.elements.instruction.value;compiledText='';document.querySelector('#doc568-recipe').hidden=true;b.disabled=true;
      try{
        let v=await api('rules/compile',{instruction:text});banner.textContent='Interprétation en attente, tâche n° '+v.job+'.';
        for(let i=0;i<150;i++){
          await new Promise(resolve=>setTimeout(resolve,1200));if(g!==generation)return;
          const state=await api('rules/job?id='+encodeURIComponent(v.job));banner.textContent=state.progress||'Interprétation '+(labels[state.state]||state.state)+'…';
          if(state.state==='error')throw new Error(state.result.erreur||'Interprétation non terminée.');
          if(state.state==='done'){
            const r=state.result;if(!r.recipe)throw new Error((r.warnings||[]).join(' ')+' '+(r.unsupported||[]).join(', '));
            if(ruleForm.elements.instruction.value!==text)throw new Error('Le texte a changé. Relancez l’interprétation.');
            compiledText=text;fillRecipe(r.recipe);document.querySelector('#doc568-warnings').textContent=(r.warnings||[]).join(' ');banner.textContent='Recette prête à relire. Aucune action distante effectuée.';return;
          }
        }
        throw new Error('L’interprétation continue en arrière-plan. Consultez l’activité puis recommencez pour retrouver la même tâche si elle est encore active.');
      }catch(e){banner.textContent=e.message;}finally{b.disabled=false;}
    });
    ruleForm.addEventListener('submit',async event=>{
      event.preventDefault();const b=ruleForm.querySelector('[type="submit"]');if(b.disabled)return;b.disabled=true;
      try{
        if(!ruleForm.elements.approved.checked)throw new Error('Relisez et approuvez les effets.');
        const f=new FormData(ruleForm),r=await api('rules/save',{...(f.get('id')?{id:f.get('id'),revision:Number(f.get('revision'))}:{}),name:f.get('name'),instruction:f.get('instruction'),recipe:recipe()});
        set(ruleForm,'id',r.id);set(ruleForm,'revision',r.revision);
        await api('rules/control',{id:r.id,revision:r.revision,approved:true,action:'activate'});note('Agent activé. La surveillance utilise cette recette et le niveau d’autonomie du profil.');await renderRules();
      }catch(e){note(e.message);}finally{b.disabled=false;}
    });
    document.querySelector('#doc568-new').addEventListener('click',()=>{generation++;ruleForm.reset();compiledText='';document.querySelector('#doc568-recipe').hidden=true;ruleForm.elements.instruction.focus();});
    document.querySelector('#doc568-simulate').addEventListener('click',async()=>{const out=document.querySelector('#doc568-simulation');try{const f=new FormData(ruleForm),v=await api('rules/simulate',{recipe:recipe(),filename:f.get('sample_filename'),created:f.get('sample_date'),modified:f.get('sample_date'),text:f.get('sample_text')});out.textContent=v.match+' · type '+v.document_type+' · '+v.message;}catch(e){out.textContent=e.message;}});
    renderRules().catch(e=>note(e.message));
  }
  async function renderRuns(){
    if(!runList||busy||document.hidden)return;busy=true;
    try{
      const v=await api('document/state'),sig=JSON.stringify(v);if(sig===runSignature||(runList.contains(document.activeElement)&&document.activeElement.matches('input,textarea,select')))return;runSignature=sig;
      const opened=new Set([...runList.querySelectorAll('details[open]')].map(x=>x.dataset.run));runList.replaceChildren();
      if(!v.runs.length)runList.append(el('p','Aucune exécution : activez un agent et le profil, puis attendez la surveillance ou lancez une vérification.'));
      v.runs.forEach(r=>{
        const card=el('article',undefined,'mission567-card');card.append(el('h3',r.rule_name+' · '+r.matter_label),el('p',labels[r.state]||r.state),el('p',r.path),el('p',r.explanation||r.result.message||r.result.abstention||'Source réservée, traitement persistant.'));
        if(r.open_url){const a=el('a','Ouvrir le dossier');a.href=r.open_url;card.append(a);}
        const d=el('details');d.dataset.run=r.id;d.open=opened.has(r.id);d.append(el('summary','Étapes, sources et preuves de dépôt'));
        r.effects.forEach(x=>{d.append(el('h4',(x.step.replaceAll('_',' '))+' · '+(labels[x.state]||x.state)),el('pre',JSON.stringify(x.proof,null,2)));});
        if(r.result.summary)d.append(el('h4','Synthèse de l’analyse'),el('p',r.result.summary));card.append(d);
        const actions=el('div',undefined,'actions');
        if(r.result.draft_verified?.uid){const a=el('a','Ouvrir le brouillon vérifié');a.href=prefix+'/courriels?'+new URLSearchParams({uid:r.result.draft_verified.uid,validity:r.result.draft_verified.uidvalidity});actions.append(a);}
        if(r.effects.some(x=>x.step==='file_procedure'&&x.state==='verified')&&r.result.destination){const a=el('a','Ouvrir le document classé');a.href=prefix+'/documents/edit?'+new URLSearchParams({path:r.result.destination,mode:'view',matter:r.matter});actions.append(a);}
        if(['error','decision','paused','waiting'].includes(r.state)){
          actions.append(button('Relancer sans dupliquer',async()=>{await api('document/control',{id:r.id,action:'retry'});runSignature='';note('Reprise demandée ; les dépôts existants seront relus.');await renderRuns();}));
          const resolve=el('details');resolve.append(el('summary','Préciser le dossier ou la création'));
          const select=el('select');select.append(el('option','Dossier actuel'));
          // 5.6.9 : dossiers plausibles relevés par l'agent proposés en premier (jamais choisis d'office)
          const suggested=(r.metadata&&r.metadata.candidates)||[];
          suggested.forEach(id=>{const o=document.querySelector('#ws-ai-matter option[value="'+CSS.escape(id)+'"]');if(o){const n=new Option(o.textContent+' (suggéré)',id);select.append(n);}});
          document.querySelector('#ws-ai-matter')?.querySelectorAll('option').forEach(o=>{if(o.value&&!suggested.includes(o.value)){const n=o.cloneNode(true);n.selected=o.value===r.matter;select.append(n);}});
          const date=el('input');date.placeholder='Création ISO avec fuseau';const proof=el('input');proof.placeholder='Preuve de création ou explication';
          resolve.append(select,date,proof,button('Confirmer et reprendre',async()=>{await api('document/control',{id:r.id,action:'resolve',matter:select.value||r.matter,...(date.value?{created:date.value,proof:proof.value}:{})});runSignature='';note('Précision enregistrée.');await renderRuns();}));actions.append(resolve);
        }
        if(!['verified','dismissed','superseded','abstained'].includes(r.state))actions.append(button('Écarter',async()=>{await api('document/control',{id:r.id,action:'dismiss'});runSignature='';note('Exécution écartée et suite suspendue.');await renderRuns();}));
        card.append(actions);runList.append(card);
      });
    }catch(e){note(e.message);}finally{busy=false;}
  }
  document.querySelector('[data-doc568-scan]')?.addEventListener('click',async event=>{const b=event.currentTarget;b.disabled=true;try{const v=await api('document/scan',{});note(v.message+' Tâches '+v.jobs.join(', ')+'.');await renderRuns();}catch(e){note(e.message);}finally{b.disabled=false;}});
  if(runList){renderRuns();(function tick(){refreshTimer=setTimeout(()=>{renderRuns();tick();},window.axiorhubLive?.connected?60000:12000);})();document.addEventListener('axiorhub:activity',renderRuns);document.addEventListener('visibilitychange',renderRuns);window.addEventListener('pagehide',()=>clearTimeout(refreshTimer));}
  async function renderCalendars(){
    const v=await api('calendars/list'),list=document.querySelector('#doc568-calendars');list.replaceChildren();
    list.append(el('p',v.google_connected?'Compte Google autorisé.':'Google non connecté.'));
    v.targets.forEach(r=>{const card=el('article',undefined,'mission567-card');card.append(el('h3',r.label),el('p',r.provider+' · '+(r.enabled?'Activé':'Désactivé')+' · '+(r.config.url||r.config.calendar_id||'')));
      card.append(button('Modifier',()=>{calForm.reset();Object.entries({...r.config,id:r.id,label:r.label,provider:r.provider,enabled:r.enabled}).forEach(([k,v])=>set(calForm,k,v));set(calForm,'password','');calForm.scrollIntoView({block:'nearest',behavior:'smooth'});}),button('Tester la lecture',async()=>{const v=await api('calendars/test',{id:r.id});note(v.message);}),button('Désactiver',async()=>{await api('calendars/save',{id:r.id,enabled:false});await renderCalendars();note('Cette cible est désactivée.');}));list.append(card);});
    const d=el('details');d.append(el('summary','Cibles effectivement utilisées'));v.effective.forEach(r=>d.append(el('p',r.label+' · '+r.provider)));list.append(d);
  }
  if(calForm){
    calForm.addEventListener('submit',async event=>{event.preventDefault();const b=calForm.querySelector('button');b.disabled=true;try{const f=new FormData(calForm),data={label:f.get('label'),provider:f.get('provider'),enabled:f.has('enabled'),external_approved:f.has('external_approved'),include_details:f.has('include_details')};['id','url','username','password','calendar_id'].forEach(k=>{if(f.get(k))data[k]=f.get(k);});await api('calendars/save',data);calForm.elements.password.value='';note('Agenda enregistré. Les secrets ne sont pas réaffichés.');await renderCalendars();}catch(e){note(e.message);}finally{b.disabled=false;}});
    document.querySelector('#doc568-calendar-new').addEventListener('click',()=>{calForm.reset();set(calForm,'id','');});
    document.querySelector('[data-google568-connect]').replaceWith(button('Autoriser Google Calendar',async()=>{const v=await api('google/start',{});const u=new URL(v.url);if(u.origin!=='https://accounts.google.com')throw new Error('Adresse d’autorisation inattendue.');window.location.assign(v.url);}));
    document.querySelector('[data-google568-disconnect]').replaceWith(button('Révoquer Google Calendar',async()=>{await api('google/disconnect',{});note('Autorisation révoquée et agendas Google désactivés.');await renderCalendars();}));
    document.querySelector('[data-google568-discover]').replaceWith(button('Lister mes agendas Google',async()=>{const v=await api('calendars/test',{}),list=document.querySelector('#doc568-google-list');list.replaceChildren();v.google_calendars.forEach(x=>{const row=el('p',x.label+' · '+x.access+' ');if(['writer','owner'].includes(x.access))row.append(button('Choisir',()=>{calForm.reset();set(calForm,'provider','google');set(calForm,'label',x.label);set(calForm,'calendar_id',x.id);calForm.scrollIntoView({block:'nearest'});}));list.append(row);});}));
    renderCalendars().catch(e=>note(e.message));
  }
  caseForm?.elements.matter.addEventListener('change',async()=>{if(!caseForm.elements.matter.value)return;try{const v=await api('procedure/get?matter='+encodeURIComponent(caseForm.elements.matter.value));Object.entries(v).forEach(([k,v])=>set(caseForm,k,v));}catch(e){note(e.message);}});
  caseForm?.addEventListener('submit',async event=>{event.preventDefault();const b=caseForm.querySelector('button');b.disabled=true;try{const f=new FormData(caseForm),data={};['matter','lawyer_name','role','partner_name','partner_email','circuit','side','declaration_date','distance','opponent_constituted'].forEach(k=>{data[k]=f.get(k);});['ordinary_civil','no_interruption_or_shortening','confirmed'].forEach(k=>{data[k]=f.has(k);});await api('procedure/save',data);note('Profil du dossier enregistré ; les prochaines analyses le prendront en compte.');}catch(e){note(e.message);}finally{b.disabled=false;}});
})();
