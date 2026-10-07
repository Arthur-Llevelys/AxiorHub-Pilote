(() => {
  'use strict';
  const cfg = JSON.parse(document.querySelector('#ws-ai-config')?.textContent || '{}');
  if (!cfg.mission_api) return;
  const prefix = cfg.prefix || '', csrf = cfg.csrf || '';
  const output = document.querySelector('#proactive568-status');
  let refreshing = false, stateSignature = '', timer = null;
  const el = (tag, text, cls) => {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;};
  const label = {detected:'Détecté', conditional:'Condition à confirmer', clarify:'À préciser', confirmed:'Confirmé', ready:'Préparé',response_detected:'Réponse reçue · pièces à vérifier',
    satisfied:'Satisfait avec confirmation', cancelled:'Annulé', decision:'Votre précision est nécessaire', planned:'Suite préparée',
    active:'En cours', blocked:'Une étape attend une décision ou une reprise', verified:'Résultats disponibles', waiting:'Étape suivante',
    suggested:'Plan proposé', paused:'Suspendu', dismissed:'Écarté', error:'À relancer', superseded:'Remplacé par une nouvelle version'};
  async function api(name, data, signal) {
    const r = await fetch(prefix + '/api440/m568/' + name, {method:data === undefined ? 'GET':'POST', credentials:'same-origin', signal,
      headers:data === undefined ? {} : {'Content-Type':'application/json','X-CSRF-Token':csrf}, body:data === undefined ? undefined : JSON.stringify(data)});
    const v = await r.json(); if (!r.ok) throw new Error(v.message || v.error || 'Demande refusée.'); return v;
  }
  function note(text) {if (output) output.textContent = text;}
  function button(text, action, id, type='event') {
    const b=el('button',text);b.type='button';b.dataset.control568=action;b.dataset.object568=id;b.dataset.type568=type;return b;
  }
  function planCard(p) {
    const a=el('article',undefined,'mission567-card');a.dataset.plan568=p.id;a.append(el('div',label[p.state]||p.state,'mission567-state'),el('h3',p.title),el('p',p.matter_label||p.matter||'Dossier à préciser'));
    const list=el('ol',undefined,'proactive568-steps');
    p.steps.forEach(s=>{const li=el('li');li.append(el('strong',s.role+' · '+(s.mission?.label||label[s.state]||s.state)),el('p',s.instruction.split('\n')[0]));
      const result=s.mission?.result||{};
      if(result.text){const d=el('details');d.append(el('summary','Lire ce résultat'),el('pre',result.text));li.append(d);}
      if(result.open_url && result.open_url.startsWith(prefix+'/') && !result.open_url.startsWith('//')){const link=el('a','Ouvrir le livrable','btn');link.href=result.open_url;li.append(link);}
      if(s.mission?.job?.error)li.append(el('p',s.mission.job.error,'warn'));list.append(li);});a.append(list);
    if (['suggested','blocked','paused'].includes(p.state)) a.append(button(p.state==='suggested'?'Préparer ce plan':'Reprendre les étapes','resume',p.id,'plan'));
    if (p.state==='active')a.append(button('Suspendre','pause',p.id,'plan'));
    const d=el('details');d.append(el('summary','Autres actions'),button('Annuler les étapes restantes','cancel',p.id,'plan'));a.append(d);return a;
  }
  function eventCard(ev,matters) {
    const a=el('article',undefined,'mission567-card');a.dataset.event568=ev.id;
    a.append(el('strong',label[ev.state]||ev.state),el('h3',ev.payload.subject||ev.payload.title||ev.payload.expected||ev.kind),el('p',ev.matter_label||ev.matter||'Dossier à préciser'),el('p',ev.explanation||'Source détectée, traitement à venir.'));
    const d=el('details');d.append(el('summary','Source et provenance'),el('pre',ev.payload.quote||''),el('pre',JSON.stringify(ev.payload.evidence||{source:ev.source,detected:ev.detected},null,2)));a.append(d);
    if(ev.talk?.state==='verified'&&ev.talk.url){const link=el('a','Ouvrir le salon Talk privé','btn');link.href=ev.talk.url;link.target='_blank';link.rel='noopener noreferrer';a.append(link,el('p','Salon relu sur Nextcloud · aucune invitation envoyée.'));}
    if(ev.state==='decision'){
      const select=el('select');select.setAttribute('aria-label','Dossier de cette suite');select.dataset.matter568='';
      select.append(new Option('Choisir le dossier',''));matters.forEach(o=>select.append(new Option(o.text,o.value,false,o.value===ev.matter)));a.append(select);
      if(ev.payload.condition){const l=el('label','La condition a été réalisée '),c=el('input');c.type='checkbox';c.dataset.condition568='';l.append(c);a.append(l);}
      if(!['E04','E12','E17','E18','E19','E20'].includes(ev.kind))a.append(button('Préciser et préparer','resolve',ev.id));
    }
    if(ev.state==='error')a.append(button('Relancer','retry',ev.id));
    a.append(button(ev.state==='dismissed'?'Rétablir':'Écarter',ev.state==='dismissed'?'restore':'dismiss',ev.id));return a;
  }
  function commitmentCard(c) {
    const a=el('article',undefined,'mission567-card');a.append(el('strong',label[c.state]||c.state),el('p',c.quote),el('p',(c.due?'Échéance proposée : '+c.due:'Échéance à préciser')+' · '+(c.matter_label||c.matter||'Dossier à préciser')));
    if(c.condition_text)a.append(el('p','Condition : '+c.condition_text));
    const d=el('details');d.append(el('summary','Provenance et confirmation'),el('pre',c.evidence));
    if(!['satisfied','cancelled'].includes(c.state)){
      const input=el('textarea');input.dataset.proof568='';input.placeholder='Confirmation ou référence d’une preuve d’exécution';input.rows=2;d.append(input,button('Confirmer l’exécution','satisfy',c.id,'commitment'),button('Annuler cet engagement','cancel',c.id,'commitment'));
      if(c.state==='response_detected')d.append(button('Reprendre : pièces encore manquantes','restore',c.id,'commitment'));
    } else if(c.state==='cancelled')d.append(button('Rétablir le suivi','restore',c.id,'commitment'));
    if(c.proof)d.append(el('pre',c.proof));a.append(d);return a;
  }
  async function refresh() {
    if(refreshing||document.hidden||!document.querySelector('#plans568-list'))return;
    refreshing=true;
    try {
      const v=await api('state'),sig=JSON.stringify(v);if(sig===stateSignature)return;stateSignature=sig;
      const matters=[...(document.querySelector('#ws-ai-matter')?.options||[])].filter(o=>o.value);
      const plans=document.querySelector('#plans568-list'),events=document.querySelector('#events568-list'),commitments=document.querySelector('#commitments568-list');
      // Do not overwrite a correction/proof being typed during a live update.
      if(events.contains(document.activeElement)||commitments.contains(document.activeElement)){stateSignature='';return;}
      const expanded=new Set([...document.querySelectorAll('[data-event568] details[open]')].map(d=>d.closest('[data-event568]').dataset.event568));
      const expandedPlans=new Set([...plans.querySelectorAll('details[open]')].map(d=>d.closest('[data-plan568]').dataset.plan568+'|'+d.querySelector('summary')?.textContent));
      const recent=v.plans.slice(0,v.profile.max_suggestions),more=el('details');more.append(el('summary','Toutes les autres suites'),...v.plans.slice(v.profile.max_suggestions).map(planCard));
      plans.replaceChildren(...recent.map(planCard),...(v.plans.length>recent.length?[more]:[]));events.replaceChildren(...v.events.map(x=>eventCard(x,matters)));commitments.replaceChildren(...v.commitments.map(commitmentCard));
      expanded.forEach(id=>events.querySelector('[data-event568="'+id+'"] details')?.setAttribute('open',''));
      plans.querySelectorAll('[data-plan568] details').forEach(d=>{if(expandedPlans.has(d.closest('[data-plan568]').dataset.plan568+'|'+d.querySelector('summary')?.textContent))d.open=true;});
      if(!v.plans.length)plans.append(el('p','Aucune suite préparée. Réglez l’identité observée, puis vérifiez les envois.','notice'));
      if(!v.commitments.length)commitments.append(el('p','Aucun engagement trouvé dans la fenêtre des envois contrôlés.'));
      document.querySelector('#proactive568-summary').textContent=(v.profile.enabled?'Suivi actif':'Suivi désactivé')+' · '+v.commitments.filter(c=>!['satisfied','cancelled'].includes(c.state)).length+' engagements suivis · '+v.plans.length+' suites récentes.';
    } catch(e){note(e.message);} finally{refreshing=false;}
  }
  document.addEventListener('click',async event=>{
    const b=event.target.closest('[data-control568]');if(!b||b.disabled)return;
    const old=b.textContent;b.disabled=true;b.textContent='Demande enregistrée…';
    try{
      const type=b.dataset.type568,data={id:b.dataset.object568,action:b.dataset.control568};
      if(type==='event') {const card=b.closest('article');data.matter=card.querySelector('[data-matter568]')?.value||'';data.condition_confirmed=!!card.querySelector('[data-condition568]')?.checked;}
      if(type==='commitment')data.proof=b.closest('article').querySelector('[data-proof568]')?.value||'';
      await api(type+'/control',data);note('Décision enregistrée.');stateSignature='';await refresh();
    }catch(e){note(e.message);}finally{if(b.isConnected){b.disabled=false;b.textContent=old;}}
  });
  document.querySelectorAll('[data-run568]').forEach(b=>b.addEventListener('click',async()=>{
    const old=b.textContent;b.disabled=true;b.textContent='En cours…';
    try{const v=await api(b.dataset.run568,{});note(v.message||(v.job?'Demande en file n° '+v.job+'. Les résultats seront actualisés.':JSON.stringify(v)));}
    catch(e){note(e.message);}finally{b.disabled=false;b.textContent=old;}
  }));
  const form=document.querySelector('#proactive568-settings');
  form?.addEventListener('submit',async event=>{
    event.preventDefault();const f=new FormData(form),data={};
    ['enabled','talk_enabled','news_enabled'].forEach(k=>{data[k]=f.has(k);});
    ['primary_address','timezone','autonomy','briefing_time','quiet_start','quiet_end','vacation_until'].forEach(k=>{data[k]=String(f.get(k)||'');});
    ['aliases','legal_fields','news_sources'].forEach(k=>{data[k]=String(f.get(k)||'').split('\n').map(x=>x.trim()).filter(Boolean);});
    ['roles','excluded_matters'].forEach(k=>{data[k]=f.getAll(k);});data.weekdays=f.getAll('weekdays').map(Number);
    ['lookback_days','followup_days','daily_plan_limit','max_suggestions','meeting_days','retention_days'].forEach(k=>{data[k]=Number(f.get(k));});
    data.matter_modes={};for(const line of String(f.get('matter_modes')||'').split('\n').filter(x=>x.trim())){const at=line.indexOf('=');if(at<1){note('Une exception doit avoir la forme référence = observe/prepare/organize.');return;}data.matter_modes[line.slice(0,at).trim()]=line.slice(at+1).trim();}
    const b=form.querySelector('button:last-of-type');b.disabled=true;
    try{await api('profile',data);note('Réglages enregistrés. Le watcher les reprend au prochain contrôle.');}catch(e){note(e.message);}finally{b.disabled=false;}
  });
  if(document.querySelector('#news568-list'))api('news').then(v=>{
    const list=document.querySelector('#news568-list');v.items.forEach(n=>{const a=el('article',undefined,'mission567-card'),link=el('a',n.title);link.href=n.url;link.target='_blank';link.rel='noopener noreferrer';a.append(link,el('p',n.published+' · '+n.source),el('p',n.summary));list.append(a);});
    if(!v.items.length)list.append(el('p','Aucune nouveauté datée correspondant aux mots-clés dans les flux configurés.'));
    if(v.last_run.errors?.length)note('Certaines sources sont indisponibles : '+v.last_run.errors.map(x=>x.reason).join(', '));
  }).catch(e=>note(e.message));

  const pulse=el('a','Suites de missions : lecture de l’état…','proactive568-pulse');pulse.href=prefix+'/engagements';
  const main=document.querySelector('#ws-main');main?.append(pulse);let pulseBusy=false;
  async function refreshPulse(){
    if(pulseBusy||document.hidden)return;pulseBusy=true;
    try{const v=await api('pulse');pulse.textContent=(v.enabled?'Suivi des engagements actif':'Suivi des engagements désactivé')+' · '+v.active+' suite(s) en cours · '+v.ready+' résultat(s) disponible(s)'+(v.quiet?' · mode discret':'');
      pulse.title=v.last_fallback?.reason?'Dernière lecture revenue au local : '+v.last_fallback.reason:'Ouvrir les engagements et les suites de missions';}
    catch(e){pulse.textContent='Suivi des engagements : état indisponible';pulse.title=e.message;}finally{pulseBusy=false;}
  }

  // Voice sessions use the same authenticated mission API. Audio never reaches a provider key in the browser.
  // 5.6.12 : un seul panneau Pilote. La conversation vocale vit dans le panneau de l'assistant (bouton « Dialoguer ») ;
  // le bouton flottant n'existe plus que sur une page sans panneau.
  const dockVoice=document.querySelector('#ws-ai-voice'),talkButton=document.querySelector('#ws-ai-talk'),dockEl=document.querySelector('#ws-ai-dock');
  const launch=talkButton||el('button','🎙','voice568-launch');
  if(!talkButton){launch.type='button';launch.title='Conversation vocale avec l’assistant IA';launch.setAttribute('aria-label',launch.title);launch.setAttribute('aria-expanded','false');launch.setAttribute('aria-controls','voice568-panel');}
  const pane=el('aside',undefined,'voice568-panel');pane.id='voice568-panel';pane.hidden=true;pane.setAttribute('aria-label','Conversation vocale');
  const header=el('header'),name=el('strong','Pilote · assistant IA vocal'),close=el('button','×');close.type='button';close.setAttribute('aria-label','Arrêter et replier la conversation');header.append(name,close);
  const mode=el('select');mode.setAttribute('aria-label','Mode vocal');mode.append(new Option('Discuter et obtenir une réponse','conversation'),new Option('Préparer les missions internes demandées','mission'));
  const context=el('p',undefined,'muted'),status=el('p','Microphone arrêté.','voice568-status');status.setAttribute('role','status');
  const transcript=el('textarea');transcript.rows=3;transcript.setAttribute('aria-label','Dernière transcription, modifiable');
  const answer=el('div',undefined,'voice568-answer');answer.setAttribute('aria-live','polite');
  const transmission=el('details'),transmissionTitle=el('summary','Données de lecture vocale'),transmissionText=el('pre');transmission.append(transmissionTitle,transmissionText);
  const start=el('button','Démarrer le dialogue'),stop=el('button','Arrêter'),send=el('button','Envoyer le texte corrigé');[start,stop,send].forEach(b=>{b.type='button';});
  pane.append(header,context,mode,el('p','La discussion donne des réponses. Le mode mission autorise les préparations internes ; aucun envoi ni invitation automatique.'),status,transcript,send,answer,transmission,start,stop);if(dockVoice){pane.classList.add('voice568-embedded');dockVoice.append(pane);}else document.body.append(launch,pane);
  let running=false,stream=null,rec=null,chunks=[],bytes=0,audioContext=null,analyser=null,vad=null,lastSound=0,turnStart=0,voiced=false,processing=false,speaking=null,audioURL='',scope='',parent='',generation=0;
  let controller=null,lastPayload=null;
  const token=()=>crypto.randomUUID();
  function voiceNote(text){status.textContent=text;}
  let speechGeneration=0,playResolve=null;
  // 5.6.11 : une interruption (parole, Arrêter, fermeture) résout aussi l'attente de lecture en cours : la conversation revient à l'écoute.
  function stopSpeech(){speechGeneration++;speaking?.pause();speaking=null;if(audioURL)URL.revokeObjectURL(audioURL);audioURL='';const r=playResolve;playResolve=null;if(r)r();}
  // 5.6.9 : lecture phrase par phrase. La première phrase est lue pendant que les suivantes se préparent ; une interruption
  // (parole, Arrêter, fermeture) vide la file. Le texte complet reste visible.
  function sentences(text){
    const out=[];let buffer='';
    for(const part of text.replace(/\s+/g,' ').split(/(?<=[.!?…;:])\s+/)){
      if(buffer&&(buffer+' '+part).length>240){out.push(buffer.trim());buffer=part;}else buffer=(buffer+' '+part);
    }
    if(buffer.trim())out.push(buffer.trim());
    return out.length?out:[text];
  }
  async function fetchSpeech(chunk,signal){
    const r=await fetch(prefix+'/api440/m568/speech',{method:'POST',credentials:'same-origin',signal,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({text:chunk,matter:scope})});
    if(!r.ok){const v=await r.json();throw new Error(v.message||v.error);}
    return r.blob();
  }
  function playBlob(blob,g,sg){
    return new Promise(resolve=>{
      if(g!==generation||sg!==speechGeneration||!running){resolve();return;}
      playResolve=resolve;
      audioURL=URL.createObjectURL(blob);speaking=new Audio(audioURL);
      speaking.onended=()=>{if(sg===speechGeneration){URL.revokeObjectURL(audioURL);audioURL='';speaking=null;}playResolve=null;resolve();};
      speaking.onerror=()=>{playResolve=null;resolve();};speaking.play().catch(()=>{playResolve=null;resolve();});
    });
  }
  function stopSession(){window.axiorhubPilot?.setMic(false);running=false;generation++;processing=false;controller?.abort();controller=null;clearInterval(vad);vad=null;
    if(rec&&rec.state!=='inactive')rec.stop();rec=null;stream?.getTracks().forEach(t=>t.stop());stream=null;audioContext?.close().catch(()=>{});audioContext=null;stopSpeech();mode.disabled=false;start.disabled=false;voiceNote('Microphone et lecture arrêtés. Une mission déjà confiée garde son état dans Missions.');}
  async function read(text,g){
    if(!text||g!==generation||!running)return;stopSpeech();
    const preview=await api('speech/preview',{text:text.slice(0,7000),matter:scope},controller?.signal);
    if(g!==generation||!running)return;
    transmissionTitle.textContent=preview.external?'Lecture ElevenLabs · estimation '+(preview.estimated_usd??'indisponible')+' USD'+(preview.pseudonymized?' · pseudonymes':''):'Lecture locale · '+preview.provider;
    transmissionText.textContent=preview.external?(preview.allowed?preview.transmitted_text:'Externe refusé : '+preview.reason+'. Le secours local configuré peut lire le texte.'):'Aucun texte transmis à un service vocal externe.';
    const sg=speechGeneration,parts=sentences(text.slice(0,7000));let next=fetchSpeech(parts[0],controller?.signal);
    voiceNote('Lecture phrase par phrase. Parlez pour interrompre, ou appuyez sur Arrêter.');
    for(let i=0;i<parts.length;i++){
      const blob=await next;if(g!==generation||sg!==speechGeneration||!running)return;
      if(i+1<parts.length)next=fetchSpeech(parts[i+1],controller?.signal).catch(()=>null);
      await playBlob(blob,g,sg);
      if(g!==generation||sg!==speechGeneration||!running)return;
    }
    stopSpeech();voiceNote('À vous. Le microphone reste actif.');
  }
  async function submit(text,g){
    if(!text.trim()||g!==generation){if(g===generation){processing=false;if(running&&!rec)recordTurn();}return;}
    if(lastPayload&&lastPayload.instruction!==text.trim()){voiceNote('Une demande précédente attend un accusé. Réessayez son texte avant de confier une autre demande, ou consultez Missions.');processing=false;if(running&&!rec)recordTurn();return;}
    processing=true;controller=new AbortController();stopSpeech();
    // 5.6.13 : même état de conversation que le texte — dossier, pièces jointes téléversées, documents sélectionnés, courriel, mission courante
    const pilot=window.axiorhubPilot;
    const selectedDocs=pilot?pilot.selectedDocuments():[...document.querySelectorAll('input[data-document-path]:checked')].map(x=>x.dataset.documentPath).slice(0,20);
    const currentMission=pilot?.current();const parentId=parent||((currentMission&&currentMission.matter===scope)?currentMission.id:'');
    const payload=lastPayload||{request_key:token(),instruction:text.trim(),matter:scope,mode:mode.value,autonomy:'prepare',channel:'voice',attachments:pilot?pilot.attachments():[],
      context:{page:cfg.path||'/',selected_documents:selectedDocs,mission_id:parentId,mail_key:pilot?pilot.mailKey():''}};
    lastPayload=payload;
    try{
      voiceNote('Demande transmise · '+(scope||'cabinet'));let m=await api('voice/turn',payload,controller.signal);lastPayload=null;parent=m.id;
      while(running&&g===generation&&['creating','queued','running'].includes(m.state)){
        voiceNote(m.label+(m.job?.progress?' · '+m.job.progress:''));
        await new Promise(resolve=>setTimeout(resolve,1200));if(!running||g!==generation)return;
        const r=await fetch(prefix+'/api440/m567/mission?id='+encodeURIComponent(m.id),{credentials:'same-origin',signal:controller.signal});m=await r.json();if(!r.ok)throw new Error(m.message||m.error);
      }
      if(g!==generation)return;
      const text=m.result?.text||((m.exceptions||[]).map(x=>x.message).join(' '))||m.label;answer.replaceChildren(el('pre',text));
      // 5.6.12 / 5.6.13 : le tour vocal rejoint le fil commun et devient la mission courante du panneau (continuité texte / voix)
      if(window.axiorhubPilot){window.axiorhubPilot.addTurn(payload.instruction,text,'voice');try{window.axiorhubPilot.show(m);}catch(e){}}
      else{const thread=document.querySelector('#ws-ai-result');if(thread){const turn=el('article',undefined,'ws-ai-turn');turn.append(el('p','🎙 '+payload.instruction,'ws-ai-turn-me'),el('pre',text));thread.prepend(turn);}}
      const link=el('a','Ouvrir cette mission');link.href=prefix+'/missions';answer.append(link);voiceNote(m.label);
      if(running)await read(text,g);
    }catch(e){if(e.name!=='AbortError')voiceNote(e.message+' Le texte reste disponible.');}
    finally{if(g===generation){processing=false;if(running&&!rec)recordTurn();}}
  }
  function recordTurn(){
    if(!running||processing||!stream)return;
    const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(x=>MediaRecorder.isTypeSupported(x));
    const r=new MediaRecorder(stream,mime?{mimeType:mime}:{}),turnChunks=[],turnGeneration=generation;rec=r;chunks=turnChunks;bytes=0;voiced=false;lastSound=Date.now();turnStart=Date.now();
    r.ondataavailable=e=>{chunks.push(e.data);bytes+=e.data.size;};
    r.onstop=async()=>{
      if(rec===r)rec=null;const blob=new Blob(turnChunks,{type:r.mimeType}),g=turnGeneration;
      if(g!==generation||r.skipTranscript)return;
      if(!running||!voiced||bytes>8000000){if(running&&!processing)recordTurn();return;}
      processing=true;controller=new AbortController();voiceNote('Transcription locale…');
      try{
        const response=await fetch(prefix+'/dictation',{method:'POST',credentials:'same-origin',signal:controller.signal,body:blob,headers:{'Content-Type':r.mimeType.split(';')[0],'X-CSRF-Token':csrf}});
        const v=await response.json();if(!response.ok)throw new Error(v.message||v.error||'Transcription indisponible.');
        if(g!==generation||!running)return;
        transcript.value=v.text;await submit(v.text,g);
      }catch(e){if(g===generation){processing=false;if(e.name!=='AbortError')voiceNote(e.message);if(running&&!rec)recordTurn();}}
    };r.start(250);
  }
  async function startSession(){
    if(running)return;if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder||!window.AudioContext){voiceNote('La conversation requiert HTTPS, microphone, MediaRecorder et AudioContext.');return;}
    const g=++generation;start.disabled=true;scope=(window.axiorhubPilot?window.axiorhubPilot.matter():document.querySelector('#ws-ai-matter')?.value)||cfg.selected_matter||'';parent='';lastPayload=null;
    context.textContent='Contexte de la session : '+(scope||'tout le cabinet')+'. Arrêtez puis redémarrez pour changer de dossier.';
    try{
      const capabilities=await api('capabilities');if(!capabilities.dictation)throw new Error('Configurez d’abord la transcription locale dans Paramètres › Connexions › Voix.');
      const s=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      if(g!==generation){s.getTracks().forEach(t=>t.stop());return;}
      stream=s;audioContext=new AudioContext();await audioContext.resume();analyser=audioContext.createAnalyser();analyser.fftSize=1024;audioContext.createMediaStreamSource(s).connect(analyser);
      running=true;mode.disabled=true;start.disabled=true;window.axiorhubPilot?.setMic(true);voiceNote('Microphone actif · parlez, puis laissez une courte pause.');recordTurn();
      const data=new Float32Array(analyser.fftSize);
      vad=setInterval(()=>{
        if(!running||!analyser)return;analyser.getFloatTimeDomainData(data);const rms=Math.sqrt(data.reduce((sum,x)=>sum+x*x,0)/data.length),now=Date.now();
        if(rms>0.035){lastSound=now;voiced=true;if(speaking&&rms>0.09){stopSpeech();voiceNote('Lecture interrompue. À vous.');}}
        if(rec?.state==='recording'&&!processing&&((voiced&&now-lastSound>800&&now-turnStart>400)||now-turnStart>45000||bytes>7500000))rec.stop();
      },100);
    }catch(e){if(g===generation){stopSession();voiceNote(e.message);}}
  }
  function showVoice(visible){pane.hidden=!visible;if(dockVoice)dockVoice.hidden=!visible;launch.setAttribute('aria-expanded',String(visible));}
  // 5.6.13 : la fermeture générale du panneau (×, Échap, bouton « Arrêter » de l'indicateur Micro actif) arrête le micro et la lecture
  window.axiorhubVoice={stop:()=>{stopSession();showVoice(false);},running:()=>running};
  launch.addEventListener('click',()=>{
    if(dockEl&&dockEl.hidden)document.querySelector('#ws-ai-launcher')?.click();   // même panneau que le texte et les documents
    const visible=pane.hidden;showVoice(visible);if(visible)startSession();else stopSession();
  });
  close.addEventListener('click',()=>{stopSession();showVoice(false);launch.focus();});
  start.addEventListener('click',startSession);stop.addEventListener('click',stopSession);
  send.addEventListener('click',()=>{if(processing)return;if(!running){voiceNote('Démarrez le dialogue avant d’envoyer le texte corrigé.');return;}if(rec){rec.skipTranscript=true;rec.stop();rec=null;}submit(transcript.value,generation);});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!pane.hidden){stopSession();pane.hidden=true;launch.setAttribute('aria-expanded','false');}});
  window.addEventListener('pagehide',stopSession);document.addEventListener('visibilitychange',()=>{if(document.hidden&&running)stopSession();});
  (window.axiorhubProfile567||fetch(prefix+'/api440/m567/profile',{credentials:'same-origin'}).then(r=>r.ok?r.json():null)).then(p=>{if(p?.name)name.textContent=p.name+' · assistant IA vocal';}).catch(()=>{});
  document.addEventListener('axiorhub:activity',()=>{clearTimeout(timer);timer=setTimeout(()=>{refresh();refreshPulse();},350);});
  (function tick(){setTimeout(()=>{if(!document.hidden){refresh();refreshPulse();}tick();},window.axiorhubLive?.connected?60000:10000);})();refresh();refreshPulse();
})();
