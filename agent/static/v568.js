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
  const liveBtn=document.querySelector('#ws-ai-live');   // 5.6.79 : bouton « vague » flottant (conversation en temps réel)
  const liveState=on=>{if(liveBtn){liveBtn.classList.toggle('is-live',on);liveBtn.setAttribute('aria-pressed',String(on));}};
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
  pane.append(header,context,mode,el('p','La discussion donne des réponses. Le mode mission autorise les préparations internes ; aucun envoi ni invitation automatique.'),status,answer,transcript,send,transmission,start,stop);if(dockVoice){pane.classList.add('voice568-embedded');dockVoice.append(pane);}else document.body.append(launch,pane);
  let running=false,stream=null,rec=null,chunks=[],bytes=0,audioContext=null,analyser=null,vad=null,lastSound=0,turnStart=0,voiced=false,processing=false,scope='',parent='',generation=0;
  let controller=null,lastPayload=null,micSession=null,cueReady=false;   // 5.6.94 (V07) : capture Alkove (Focus, Cue)
  const token=()=>crypto.randomUUID();
  let dictationFailures=0;
  // 5.6.81 : ni affichés ni lus à voix haute : les balises de mise en forme de la réponse
  function plain(text){return String(text).replace(/\*\*|__|`/g,'').replace(/^\s{0,3}#{1,6}\s+/gm,'').replace(/^\s*[*-]\s+/gm,'• ');}
  function voiceNote(text){status.textContent=text;}
  // 5.6.11 : une interruption (parole, Arrêter, fermeture) rend la main tout de suite : la conversation revient à l'écoute.
  // 5.6.96 (V03) : motif 'voix' (parole détectée) ou 'utilisateur' ; la lecture commune (v5694-voix.js) annule aussi ses requêtes audio.
  function stopSpeech(motif){window.axiorhubVoix?.stop(motif==='voix'?'voix':'utilisateur');}
  // 5.6.96 (V02, V03) : « Interrompre la lecture » toujours visible pendant la lecture ; « Reprendre au segment n » après un échec
  const interrompre=el('button','Interrompre la lecture','v96-interrompre'),reprise=el('button','','v96-reprise');
  [interrompre,reprise].forEach(b=>{b.type='button';b.hidden=true;});
  interrompre.addEventListener('click',()=>{stopSpeech('utilisateur');});
  // 5.6.96 (V03, V05, V06) : options de la discussion, enregistrées dans le profil du compte (m567/profile) ; ouvrir les options ne démarre pas le micro
  const options=el('details',undefined,'v96-options'),optVoix=el('select'),optInter=el('input'),optAvatar=el('select'),optEtat=el('p','','v96-options-etat');
  optEtat.setAttribute('role','status');optInter.type='checkbox';
  optVoix.append(new Option('Français posé (par défaut)','posee'),new Option('Brindille originale','originale'));
  optAvatar.append(new Option('Aucun avatar','aucun'),new Option('Portrait A','portrait_a'),new Option('Portrait B','portrait_b'));
  const champ=(texte,input)=>{const l=el('label',undefined,'v96-option');l.append(el('span',texte),input);return l;};
  const diag=el('details',undefined,'v96-diagnostic'),diagTexte=el('pre');diag.append(el('summary','Diagnostic de lecture (sans texte ni son)'),diagTexte);
  diag.addEventListener('toggle',()=>{if(!diag.open)return;   // V04 : tampon local du lecteur : moteur, segment, durée, erreur, interruption
    const lignes=(window.axiorhubVoix?.diagnostic?.()||[]).slice(-8).map(l=>[l.heure,l.tour,l.statut||l.evenement,'demandé '+(l.moteur_demande||'?'),'joué '+(l.moteur_effectif||'?'),
      'segment '+(l.segment||0)+'/'+(l.segments||0),(l.duree_ms||0)+' ms',l.erreur||'',l.interruption?'interruption : '+l.interruption:'',l.repli?'repli : '+l.repli:''].filter(Boolean).join(' · '));
    diagTexte.textContent=lignes.join('\n')||'Aucune lecture depuis l’ouverture de la page.';});
  options.append(el('summary','Options'),champ('Voix ',optVoix),champ('Interrompre en parlant ',optInter),champ('Avatar ',optAvatar),optEtat,diag);
  const portraitZone=el('div',undefined,'v96-portrait-zone');portraitZone.hidden=true;
  header.after(options,portraitZone);status.after(interrompre,reprise);   // placés après leur déclaration (le volet est déjà construit)
  let prefsVoix={},portrait=null,avatarChoisi='aucun',chargementAvatar=null,animation=0,lecture=false,detecteurVoix=null;
  const versionScript=(()=>{try{return new URL(document.currentScript?.src||'',location.href).searchParams.get('v')||'';}catch(e){return '';}})();
  const reduit=window.matchMedia?window.matchMedia('(prefers-reduced-motion: reduce)'):null;
  function appliquerOptions(p){if(!p)return;prefsVoix=p;optVoix.value=p.voix_brindille==='originale'?'originale':'posee';optInter.checked=p.interruption_vocale===true;
    const a=['portrait_a','portrait_b'].includes(p.avatar)?p.avatar:'aucun';optAvatar.value=a;montrerAvatar(a);}
  async function enregistrerOption(donnees){[optVoix,optInter,optAvatar].forEach(x=>{x.disabled=true;});
    try{const r=await fetch(prefix+'/api440/m567/profile',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(donnees)});
      const v=await r.json().catch(()=>({}));if(!r.ok)throw new Error(v.message||v.error||'HTTP '+r.status);appliquerOptions(v);optEtat.textContent='Option enregistrée pour votre compte.';}
    catch(e){appliquerOptions(prefsVoix);optEtat.textContent='Option non enregistrée : '+e.message;}
    finally{[optVoix,optInter,optAvatar].forEach(x=>{x.disabled=false;});}}
  optVoix.addEventListener('change',()=>enregistrerOption({voix_brindille:optVoix.value}));
  optInter.addEventListener('change',()=>enregistrerOption({interruption_vocale:optInter.checked}));
  optAvatar.addEventListener('change',()=>enregistrerOption({avatar:optAvatar.value}));
  // 5.6.96 (V06) : le module d'avatar n'est chargé que si un portrait est choisi ; « aucun » : aucune requête
  function chargerAvatar(){if(window.axiorhubAvatar)return Promise.resolve(window.axiorhubAvatar);
    return chargementAvatar||(chargementAvatar=new Promise((ok,ko)=>{const s=document.createElement('script');s.defer=true;
      s.src=prefix+'/static/v5696-avatar.js'+(versionScript?'?v='+encodeURIComponent(versionScript):'');
      s.onload=()=>window.axiorhubAvatar?ok(window.axiorhubAvatar):ko(new Error('avatar'));s.onerror=()=>{chargementAvatar=null;ko(new Error('avatar'));};document.head.append(s);}));}
  async function montrerAvatar(cle){if(cle===avatarChoisi&&(portrait||cle==='aucun'))return;avatarChoisi=cle;animer(false);portrait=null;portraitZone.replaceChildren();portraitZone.hidden=true;
    if(!['portrait_a','portrait_b'].includes(cle))return;
    try{const A=await chargerAvatar();if(avatarChoisi!==cle)return;portrait=A.creer(cle);if(portrait){portraitZone.append(portrait.element);portraitZone.hidden=false;animer(lecture);}}
    catch(e){optEtat.textContent='Avatar indisponible : la discussion continue sans portrait.';}}
  // Bouche animée d'après le niveau sonore local de la lecture Brindille, seulement pendant la lecture, volet visible, onglet au premier plan,
  // sans préférence « moins d'animations » ; voix du serveur : niveau nul, portrait fixe. Rien n'est enregistré ni envoyé.
  function animer(actif){clearInterval(animation);animation=0;
    const ok=Boolean(actif&&portrait&&!pane.hidden&&!document.hidden&&!(reduit&&reduit.matches));
    window.axiorhubVoix?.monitoriser?.(ok);portrait?.bouche(0);
    if(ok)animation=setInterval(()=>{portrait?.bouche(window.axiorhubVoix?.moteur?.()==='brindille'?window.axiorhubVoix.niveau():0);},80);}
  document.addEventListener('visibilitychange',()=>animer(lecture));reduit?.addEventListener?.('change',()=>animer(lecture));
  // V03 : la voix ne coupe la lecture que si la personne l'a choisi ET que la détection est sûre (Cue prêt ou annulation d'écho du micro) ;
  // sinon mode alterné : micro suspendu pendant la lecture, bouton « Interrompre la lecture » visible.
  function echoSur(){if(cueReady)return true;try{return stream?.getAudioTracks?.()[0]?.getSettings?.().echoCancellation===true;}catch(e){return false;}}
  function interrompreEnParlant(){return prefsVoix.interruption_vocale===true&&echoSur();}
  const detecteur=()=>detecteurVoix||(detecteurVoix=window.axiorhubVoix?.detecteurInterruption?.(interrompreEnParlant,0.09,4)||null);   // 4 trames de 100 ms
  function lectureEnCours(oui){lecture=oui;interrompre.hidden=!oui;detecteur()?.reinitialiser();
    if(stream){const ouvert=!oui||interrompreEnParlant();stream.getAudioTracks().forEach(t=>{t.enabled=ouvert;});}
    animer(oui);}
  // 5.6.96 (V02) : « À vous » seulement après une lecture terminée ; un échec nomme le segment, garde le texte et propose de reprendre là
  async function lire(text,g,depuis){
    reprise.hidden=true;lectureEnCours(true);
    try{const r=await window.axiorhubVoix.speak(text,{matter:scope,note:voiceNote,depuis,tour:'voix-'+g});
      if(g!==generation||!running)return;
      if(r.statut==='termine')voiceNote(((r.fallback||r.secours_serveur)?status.textContent+' ':'')+'À vous. Le microphone reste actif.');
      else if(r.statut==='interrompu_voix')voiceNote('Lecture interrompue par votre voix. À vous.');
      else if(r.statut==='interrompu_utilisateur')voiceNote('Lecture interrompue. À vous.');
    }catch(e){if(g!==generation||!running)return;voiceNote(e.message);
      const n=e.resultat?.segment||0;
      if(n){reprise.textContent='Reprendre au segment '+n;reprise.hidden=false;
        reprise.onclick=async()=>{if(processing||!running)return;processing=true;if(rec){rec.skipTranscript=true;rec.stop();rec=null;}
          try{await lire(text,generation,n);}finally{processing=false;if(running&&!rec)recordTurn();}};}}
    finally{lectureEnCours(false);}
  }
  function stopSession(){window.axiorhubPilot?.setMic(false);liveState(false);running=false;generation++;processing=false;controller?.abort();controller=null;clearInterval(vad);vad=null;
    if(rec&&rec.state!=='inactive')rec.stop();rec=null;stream?.getTracks().forEach(t=>t.stop());stream=null;micSession?.close?.();micSession=null;cueReady=false;audioContext?.close().catch(()=>{});audioContext=null;stopSpeech();mode.disabled=false;start.disabled=false;voiceNote('Microphone et lecture arrêtés. Une mission déjà confiée garde son état dans Missions.');}
  async function read(text,g){
    if(!text||g!==generation||!running)return;stopSpeech();
    // Aperçu de ce qu'une voix externe recevrait (plafond PLAFOND_EXTERNE de voix5694, annoncé par la lecture elle-même)
    const preview=await api('speech/preview',{text:text.slice(0,7000),matter:scope},controller?.signal);
    if(g!==generation||!running)return;
    transmissionTitle.textContent=preview.external?'Lecture ElevenLabs · estimation '+(preview.estimated_usd??'indisponible')+' USD'+(preview.pseudonymized?' · pseudonymes':''):'Lecture locale · '+preview.provider;
    transmissionText.textContent=preview.external?(preview.allowed?preview.transmitted_text:'Externe refusé : '+preview.reason+'. Le secours local configuré peut lire le texte.'):'Aucun texte transmis à un service vocal externe.';
    // 5.6.94 (V03, V05) puis 5.6.96 (V01, V02) : lecture commune du texte entier (Brindille dans le navigateur ou voix du serveur)
    if(!window.axiorhubVoix){voiceNote('Lecture indisponible : rechargez la page. La réponse écrite reste visible.');return;}
    await lire(text,g,1);
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
      voiceNote('Demande transmise · '+(scope||'cabinet'));
      // 5.6.80 : l'IA tarde : les passages trouvés sans modèle s'affichent ; la lecture vocale de la réponse suivra
      const quick=setTimeout(()=>{if(g===generation&&running&&processing){voiceNote('L’IA rédige sa réponse…');window.axiorhubPassages?.(payload.instruction,scope,answer);}},4000);
      let m;try{m=await api('voice/turn',payload,controller.signal);}catch(e){clearTimeout(quick);throw e;}lastPayload=null;parent=m.id;
      while(running&&g===generation&&['creating','queued','running'].includes(m.state)){
        voiceNote(m.label+(m.job?.progress?' · '+m.job.progress:''));
        await new Promise(resolve=>setTimeout(resolve,600));if(!running||g!==generation)return;
        const r=await fetch(prefix+'/api440/m567/mission?id='+encodeURIComponent(m.id),{credentials:'same-origin',signal:controller.signal});m=await r.json();if(!r.ok)throw new Error(m.message||m.error);
      }
      clearTimeout(quick);
      if(g!==generation)return;
      const text=plain(m.result?.text||((m.exceptions||[]).map(x=>x.message).join(' '))||m.label);answer.replaceChildren(el('pre',text));answer.scrollIntoView?.({block:'nearest'});
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
        dictationFailures=0;transcript.value=v.text;await submit(v.text,g);
      }catch(e){if(g===generation){processing=false;
        if(e.name==='AbortError'){if(running&&!rec)recordTurn();return;}
        // 5.6.81 : la transcription ne se rétablit pas seule : après deux échecs de suite, le micro ne reste pas ouvert pour rien
        if(++dictationFailures>=2){stopSession();voiceNote(e.message+' Micro arrêté : écrivez votre question ci-dessous.');transcript.focus();return;}
        voiceNote(e.message+' Nouvel essai au prochain tour de parole, ou écrivez votre question ci-dessous.');if(running&&!rec)recordTurn();}}
    };r.start(250);
  }
  async function startSession(){
    if(running)return;if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder||!window.AudioContext){voiceNote('La conversation requiert HTTPS, microphone, MediaRecorder et AudioContext.');return;}
    const g=++generation;start.disabled=true;scope=(window.axiorhubPilot?window.axiorhubPilot.matter():document.querySelector('#ws-ai-matter')?.value)||cfg.selected_matter||'';parent='';lastPayload=null;
    context.textContent='Contexte de la session : '+(scope||'tout le cabinet')+'. Arrêtez puis redémarrez pour changer de dossier.';
    try{
      const capabilities=await api('capabilities');if(!capabilities.dictation)throw new Error('Configurez d’abord la transcription locale dans Paramètres › Connexions › Voix.');
      const constraints={audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}};
      const cap=window.axiorhubMic?await window.axiorhubMic.capture('conversation',constraints):{stream:await navigator.mediaDevices.getUserMedia(constraints)};
      const s=cap.stream;
      if(g!==generation){cap.close?.();s.getTracks().forEach(t=>t.stop());return;}
      // 5.6.94 (V07) : « Arrêter quand je me tais » : fin de prise de parole détectée par Cue dès que les modèles tournent ; avant, ou sans Alkove, le volume
      micSession=cap;cueReady=false;
      if(cap.stopOnSilence&&cap.ready)cap.ready.then(()=>{if(micSession===cap)cueReady=true;}).catch(()=>{});
      cap.on?.('speechstart',()=>{voiced=true;lastSound=Date.now();if(cueReady&&lecture&&interrompreEnParlant())stopSpeech('voix');});   // 5.6.96 (V03) : Cue, sur choix
      cap.on?.('turnend',()=>{if(micSession===cap&&cueReady&&rec?.state==='recording'&&!processing&&voiced)rec.stop();});
      stream=s;audioContext=new AudioContext();await audioContext.resume();analyser=audioContext.createAnalyser();analyser.fftSize=1024;audioContext.createMediaStreamSource(s).connect(analyser);
      running=true;liveState(true);mode.disabled=true;start.disabled=true;window.axiorhubPilot?.setMic(true);voiceNote('Microphone actif · parlez, puis laissez une courte pause.'+(cap.note?' '+cap.note:''));recordTurn();
      const data=new Float32Array(analyser.fftSize);
      vad=setInterval(()=>{
        if(!running||!analyser)return;analyser.getFloatTimeDomainData(data);const rms=Math.sqrt(data.reduce((sum,x)=>sum+x*x,0)/data.length),now=Date.now();
        if(window.axiorhubVoix?.speaking()){   // 5.6.96 (V03) : pendant la lecture, la voix n'interrompt que sur choix, après 4 trames stables (400 ms)
          if(detecteur()?.trame(rms))stopSpeech('voix');return;}
        detecteur()?.reinitialiser();if(rms>0.035){lastSound=now;voiced=true;}
        if(rec?.state==='recording'&&!processing&&((!cueReady&&voiced&&now-lastSound>800&&now-turnStart>400)||now-turnStart>45000||bytes>7500000))rec.stop();
      },100);
    }catch(e){if(g===generation){stopSession();voiceNote(e.message);}}
  }
  function showVoice(visible){pane.hidden=!visible;animer(visible&&lecture);if(dockVoice){dockVoice.hidden=!visible;if(visible)dockEl?.scrollTo?.({top:0});}launch.setAttribute('aria-expanded',String(visible));}
  // 5.6.13 : la fermeture générale du panneau (×, Échap, bouton « Arrêter » de l'indicateur Micro actif) arrête le micro et la lecture
  window.axiorhubVoice={stop:()=>{stopSession();showVoice(false);},running:()=>running};
  launch.addEventListener('click',()=>{
    if(dockEl&&dockEl.hidden)document.querySelector('#ws-ai-launcher')?.click();   // même panneau que le texte et les documents
    const visible=pane.hidden;showVoice(visible);if(visible)startSession();else stopSession();
  });
  close.addEventListener('click',()=>{stopSession();showVoice(false);launch.focus();});
  liveBtn?.addEventListener('click',()=>{if(running){stopSession();showVoice(false);return;}if(!pane.hidden){startSession();return;}launch.click();});
  start.addEventListener('click',startSession);stop.addEventListener('click',stopSession);
  send.addEventListener('click',()=>{if(processing)return;if(!running){voiceNote('Démarrez le dialogue avant d’envoyer le texte corrigé.');return;}if(rec){rec.skipTranscript=true;rec.stop();rec=null;}submit(transcript.value,generation);});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!pane.hidden){stopSession();pane.hidden=true;launch.setAttribute('aria-expanded','false');}});
  window.addEventListener('pagehide',stopSession);document.addEventListener('visibilitychange',()=>{if(document.hidden&&running)stopSession();});
  // 5.6.80 : clic sur la vague pendant le chargement : la conversation démarre dès que le script est prêt
  window.axiorhubPilotReady=true;
  document.querySelector('#ws-ai-launcher')?.classList.remove('is-waiting');liveBtn?.classList.remove('is-waiting');
  if(window.__axPending==='ws-ai-live'&&pane.hidden)launch.click();
  window.__axPending='';
  (window.axiorhubProfile567||fetch(prefix+'/api440/m567/profile',{credentials:'same-origin'}).then(r=>r.ok?r.json():null)).then(p=>{if(p?.name)name.textContent=p.name+' · assistant IA vocal';appliquerOptions(p);}).catch(()=>{});
  document.addEventListener('axiorhub:activity',()=>{clearTimeout(timer);timer=setTimeout(()=>{refresh();refreshPulse();},350);});
  (function tick(){setTimeout(()=>{if(!document.hidden){refresh();refreshPulse();}tick();},window.axiorhubLive?.connected?60000:10000);})();refresh();refreshPulse();
})();
