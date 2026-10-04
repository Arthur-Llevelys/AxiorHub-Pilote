/* Local recording only: never SpeechRecognition, never auto-submit. */
(() => {
  'use strict';
  const meta = name => document.querySelector(`meta[name="${name}"]`)?.content || '';
  const prefix = meta('axiorhub-prefix');
  const audioEnabled = meta('axiorhub-audio') === 'enabled';
  let active = null, pending = false;
  document.querySelectorAll('.job-progress[data-job]').forEach(banner=>{
    let polls=0;
    const poll=async()=>{
      if(document.hidden || active || pending){setTimeout(poll,5000);return;}
      try{
        const response=await fetch(prefix+'/api/v1/jobs/'+encodeURIComponent(banner.dataset.job),{credentials:'same-origin'});
        if(!response.ok) throw new Error('status');
        const job=await response.json();
        const states={pending:'En attente du worker',running:'Traitement en cours',cancel_requested:'Annulation demandée',done:'Action terminée',error:'Action interrompue',cancelled:'Action annulée'};
        banner.textContent='Opération n° '+banner.dataset.job+' — '+(states[job.status]||job.status)+'.';
        if(['done','error','cancelled'].includes(job.status)){
          banner.textContent+=' Cliquez sur Actualiser pour voir le résultat ou le motif. Votre saisie actuelle est conservée jusque-là.';return;
        }
      }catch{banner.textContent='Suivi temporairement indisponible. Cliquez sur Actualiser ; ne renvoyez pas la même demande.';}
      if(++polls<120)setTimeout(poll,5000);
    };
    setTimeout(poll,1500);
  });
  document.querySelectorAll('.copy-prompt').forEach(button => {
    button.addEventListener('click', async () => {
      if (pending) return;
      const text = button.dataset.prompt;
      const field = document.querySelector('#guided-question');
      if (field) { field.value = text; field.dispatchEvent(new Event('input', {bubbles:true})); }
      const status = document.querySelector('#clipboard-status');
      try { await navigator.clipboard.writeText(text); if(status) status.textContent='Prompt copié. Remplacez les éléments entre crochets dans Open WebUI.'; }
      catch { if(status) status.textContent='Copie non autorisée par le navigateur. Le prompt est placé dans le champ de demande : sélectionnez-le pour le copier.'; }
    });
  });
  document.querySelectorAll('textarea:not([readonly]):not([disabled])').forEach((field, index) => {
    if (!field.id) field.id = 'dictation-field-' + index;
    const toolbar = document.createElement('div'); toolbar.className='dictation-bar';
    const button = document.createElement('button'); button.type='button';button.className='micro-button';
    button.textContent='🎙 Micro — dicter';button.setAttribute('aria-controls',field.id);button.setAttribute('aria-pressed','false');
    const status=document.createElement('span');status.setAttribute('role','status');
    status.textContent=audioEnabled?'Dictée locale. 60 secondes maximum, puis relecture.':'Micro disponible après installation de la passerelle locale.';
    toolbar.append(button,status);field.after(toolbar);
    button.addEventListener('click', async () => {
      if (active) { if(active.button===button && active.recorder.state==='recording') active.recorder.stop(); return; }
      if (!audioEnabled) {status.textContent='La passerelle doit être activée par l’administrateur. Aucune donnée envoyée.';return;}
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {status.textContent='Micro indisponible : utilisez un navigateur récent en HTTPS.';return;}
      let stream, recorder, timer; const chunks=[]; let bytes=0; let cancelled=false;
      button.disabled=true;
      pending=true;
      try {
        stream=await navigator.mediaDevices.getUserMedia({audio:true});
        const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(x=>MediaRecorder.isTypeSupported(x));
        if(!mime) throw new Error('format');
        recorder=new MediaRecorder(stream,{mimeType:mime,audioBitsPerSecond:64000});
        active={button,recorder,stream};button.disabled=false;button.textContent='⏹ Arrêter et transcrire';button.setAttribute('aria-pressed','true');
        pending=false;
        status.textContent='Enregistrement en cours… Aucun formulaire ne sera envoyé automatiquement.';
        recorder.ondataavailable=event=>{bytes+=event.data.size;if(bytes>8000000){cancelled=true;if(recorder.state==='recording')recorder.stop();}else chunks.push(event.data);};
        recorder.onerror=()=>{cancelled=true;if(recorder.state==='recording')recorder.stop();};
        recorder.onstop=async()=>{
          clearTimeout(timer);stream.getTracks().forEach(track=>track.stop());button.disabled=true;button.setAttribute('aria-pressed','false');
          try {
            if(cancelled) throw new Error('cancelled');
            status.textContent='Transcription locale en cours…';
            const response=await fetch(prefix+'/dictation',{method:'POST',credentials:'same-origin',
              headers:{'Content-Type':mime.split(';')[0],'X-CSRF-Token':meta('axiorhub-csrf')},body:new Blob(chunks,{type:mime})});
            const result=await response.json();
            if(!response.ok || typeof result.text!=='string') throw new Error('transcription');
            const added=(field.value?'\n':'')+result.text;
            if(field.maxLength>0 && field.value.length+added.length>field.maxLength){
              const recovery=document.createElement('textarea');recovery.readOnly=true;recovery.value=result.text;recovery.setAttribute('aria-label','Dictée à raccourcir');toolbar.after(recovery);
              status.textContent='Champ trop court : dictée conservée ci-dessous. Raccourcissez-la manuellement.';
            } else {field.value+=added;field.dispatchEvent(new Event('input',{bubbles:true}));status.textContent='Texte ajouté. Relisez-le puis envoyez vous-même la demande.';field.focus();}
          } catch {status.textContent='Transcription non obtenue. Vérifiez la passerelle locale ; votre texte initial est conservé.';}
          finally {chunks.length=0;active=null;button.disabled=false;button.textContent='🎙 Micro — dicter';}
        };
        recorder.start(1000);timer=setTimeout(()=>{if(recorder.state==='recording')recorder.stop();},60000);
      } catch {pending=false;stream?.getTracks().forEach(track=>track.stop());active=null;button.disabled=false;button.textContent='🎙 Micro — dicter';status.textContent='Micro non démarré. Autorisez le microphone dans le navigateur et vérifiez HTTPS.';}
    });
  });
  window.addEventListener('pagehide',()=>{if(active){active.recorder.onstop=null;active.stream.getTracks().forEach(t=>t.stop());active=null;}});
})();
