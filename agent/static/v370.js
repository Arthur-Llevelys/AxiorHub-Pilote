(() => {
  'use strict';
  const launcher=document.querySelector('#ws-ai-launcher'), dock=document.querySelector('#ws-ai-dock');
  if(!launcher||!dock)return;
  const config=JSON.parse(document.querySelector('#ws-ai-config')?.textContent||'{}');
  const prefix=config.prefix||''; const csrf=config.csrf||'';
  const question=dock.querySelector('#ws-ai-question'), matter=dock.querySelector('#ws-ai-matter');
  const due=dock.querySelector('#ws-ai-due'), autonomy=dock.querySelector('#ws-ai-autonomy');
  const file=dock.querySelector('#ws-ai-file'), status=dock.querySelector('#ws-ai-status'), result=dock.querySelector('#ws-ai-result');
  let attachmentIds=[],threadId='',recording=null;
  const open=()=>{dock.hidden=false;launcher.setAttribute('aria-expanded','true');question.focus();};
  const close=()=>{dock.hidden=true;launcher.setAttribute('aria-expanded','false');};
  launcher.addEventListener('click',()=>dock.hidden?open():close());dock.querySelector('#ws-ai-close').addEventListener('click',close);
  dock.querySelectorAll('[data-ai-example]').forEach(b=>b.addEventListener('click',()=>{question.value=b.dataset.aiExample;question.focus();}));
  file.addEventListener('change',async()=>{
    attachmentIds=[];const chosen=[...file.files];if(!chosen.length)return;
    if(chosen.length>3||chosen.some(x=>x.size<1||x.size>8000000)){status.textContent='Import refusé : trois documents maximum, 8 Mo chacun.';return;}
    status.textContent='Extraction locale de '+chosen.length+' document(s)…';
    try{let extracted=0;
      for(const current of chosen){const response=await fetch(prefix+'/assistant/attachment',{method:'POST',credentials:'same-origin',body:current,
        headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':csrf,'X-Attachment-Name':encodeURIComponent(current.name),'X-Attachment-Matter':matter.value}});
        const data=await response.json();if(!response.ok||!data.attachment_id)throw new Error(data.error||'extraction impossible');
        attachmentIds.push(data.attachment_id);extracted+=Number(data.extracted_characters||0);}
      status.textContent=chosen.length+' document(s) prêt(s) · '+extracted+' caractères extraits.';
    }catch(error){status.textContent='Import impossible : '+error.message;}
  });
  const poll=async(jobId,currentThread)=>{
    for(let i=0;i<120;i++){
      await new Promise(resolve=>setTimeout(resolve,2500));
      const response=await fetch(prefix+'/api/v1/jobs/'+jobId,{credentials:'same-origin'});if(!response.ok)continue;
      const job=await response.json();status.textContent='Traitement : '+job.status;
      if(job.status==='done'){
        const thread=await fetch(prefix+'/api/v1/threads/'+currentThread,{credentials:'same-origin'}).then(r=>r.json());
        const messages=thread.messages||[];const answer=[...messages].reverse().find(x=>x.role==='assistant');
        result.textContent=answer?.content||'Traitement terminé. Ouvrez l’Assistant pour le détail.';status.textContent='Réponse terminée — à relire.';return;
      }
      if(['error','cancelled'].includes(job.status)){let details={};try{details=JSON.parse(job.result||'{}')}catch{};result.textContent='Action non terminée : '+(details.erreur||job.status);return;}
    }
    status.textContent='Traitement toujours en cours. Vous pouvez replier la fenêtre.';
  };
  dock.querySelector('#ws-ai-send').addEventListener('click',async()=>{
    const text=question.value.trim();if(!text){status.textContent='Saisissez une instruction.';return;}
    status.textContent='Mise en file prioritaire…';result.textContent='';
    const selectedDocuments=[...document.querySelectorAll('input[data-document-path]:checked,select[data-document-path],input[name="source_path"],input[name="our_source_path"],input[name="opponent_source_path"],input[name="document_path"]')]
      .map(x=>x.dataset.documentPath||x.value||'').filter(Boolean).slice(0,20);
    const recentResults=[...document.querySelectorAll('.success,.notification-ready,.job-progress')]
      .map(x=>(x.textContent||'').trim()).filter(Boolean).slice(0,5);
    const pageContext={page:config.path||location.pathname,matter:matter.value,
      selected_documents:selectedDocuments,active_mission:(document.querySelector('.page-title h1')?.textContent||'').trim(),
      recent_results:recentResults};
    try{const response=await fetch(prefix+'/api/v1/assistant',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({question:text+(due.value?'\nÉchéance : '+due.value:'')+(autonomy.value?'\nNiveau demandé : '+autonomy.value:''),matter:matter.value,thread_id:threadId,attachment_ids:attachmentIds,page_context:pageContext})});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'demande refusée');threadId=data.thread_id;attachmentIds=[];file.value='';poll(data.job_id,threadId);
    }catch(error){status.textContent='Envoi impossible : '+error.message;}
  });
  dock.querySelector('#ws-ai-dictate').addEventListener('click',async event=>{
    const button=event.currentTarget;
    if(recording){recording.stop();return;}
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){status.textContent='Micro indisponible dans ce navigateur.';return;}
    try{const stream=await navigator.mediaDevices.getUserMedia({audio:true});const chunks=[];
      const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(x=>MediaRecorder.isTypeSupported(x));
      recording=new MediaRecorder(stream,{mimeType:mime});recording.ondataavailable=e=>chunks.push(e.data);button.textContent='⏹ Arrêter';status.textContent='Écoute locale en cours…';
      recording.onstop=async()=>{stream.getTracks().forEach(t=>t.stop());button.textContent='🎙 Parler';const current=recording;recording=null;
        try{const response=await fetch(prefix+'/dictation',{method:'POST',credentials:'same-origin',body:new Blob(chunks,{type:mime}),headers:{'Content-Type':mime.split(';')[0],'X-CSRF-Token':csrf}});
          const data=await response.json();if(!response.ok||!data.text)throw new Error();question.value+=(question.value?'\n':'')+data.text;status.textContent='Dictée ajoutée. Relisez avant envoi.';
        }catch{status.textContent='Transcription non obtenue.';}};recording.start(1000);setTimeout(()=>recording&&recording.stop(),60000);
    }catch{status.textContent='Autorisez le microphone et vérifiez la connexion HTTPS.';}
  });
})();
