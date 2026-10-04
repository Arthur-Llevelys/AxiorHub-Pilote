/* AxiorHub 4.3: same-origin SSE + genuine HTMX forms; no CDN or eval. */
(() => {
  'use strict';
  const prefix=document.querySelector('meta[name="axiorhub-prefix"]')?.content||'';
  const summary=document.querySelector('#ws-live-summary'), panel=document.querySelector('#ws-live-panel');
  const toggle=document.querySelector('#ws-live-toggle'), toast=document.querySelector('#ws-live-toast');
  let cursor=0, stream, queued=false, connected=false, boardTimer, lastBoard=0;
  const tracked=new Map();
  function notify(text){toast.textContent=text;toast.hidden=false;setTimeout(()=>toast.hidden=true,7000);}
  let panelBusy=false;
  function refreshPanel(){
    if(panel.hidden||!window.htmx||panelBusy)return;panelBusy=true;
    const opened=new Set([...panel.querySelectorAll('[data-live-job] details[open]')].map(x=>x.closest('[data-live-job]').dataset.liveJob+'|'+x.querySelector('summary')?.textContent));
    const top=panel.scrollTop;
    htmx.ajax('GET',prefix+'/live/panel',{target:'#ws-live-panel-content',swap:'outerHTML'}).then(()=>{
      panel.querySelectorAll('[data-live-job] details').forEach(x=>{if(opened.has(x.closest('[data-live-job]').dataset.liveJob+'|'+x.querySelector('summary')?.textContent))x.open=true;});
      panel.scrollTop=top;
    }).finally(()=>panelBusy=false);
  }
  toggle?.addEventListener('click',()=>{panel.hidden=!panel.hidden;toggle.setAttribute('aria-expanded',String(!panel.hidden));refreshPanel();});
  document.querySelector('#ws-live-close')?.addEventListener('click',()=>{panel.hidden=true;toggle.setAttribute('aria-expanded','false');});
  function receipt(form,job,target){
    if(!form||!form.isConnected)return;
    const button=form.querySelector('button[type="submit"],button:not([type])');
    let state=form.querySelector('.live-receipt');
    if(!state){state=document.createElement('span');state.className='live-receipt';state.setAttribute('role','status');form.append(state);}
    if(!job){if(button){button.textContent='Enregistré ✓';button.disabled=true;}state.textContent='Modification enregistrée.';return;}
    const active=['pending','running','cancel_requested'].includes(job.status);
    const action=form.querySelector('[name="action"]')?.value;
    if(button){button.disabled=active||job.status==='done'||job.status==='cancelled';
      button.textContent=job.status==='pending'?'En attente'+(job.position?' · position '+job.position:''):
        job.status==='running'?(job.progress||'Analyse en cours…'):
        job.status==='done'?(action==='review_action380'?(job.review_state==='dismissed'?'Écarté ✓':job.review_state==='restored'?'Rétabli ✓':'Vu ✓'):job.outcome==='verified'?(job.open_label||'Ouvrir le livrable'):'Terminé ✓'):
        job.status==='error'?'Échec · consulter le motif':job.label;
      // Errors do not silently replay a possibly applied mutation. Use the
      // explicit server retry check in the activity panel instead.
      if(job.status==='error')button.disabled=true;
      if(job.status==='done'&&job.outcome==='verified'){
        button.disabled=false;button.dataset.liveHref=prefix+job.href;
      }
    }
    state.textContent=job.error||job.message||job.progress||job.label;
    let link=form.querySelector('.live-open');
    if(!active){if(!link){link=document.createElement('a');link.className='live-open';form.append(link);}link.href=job.outcome==='verified'?prefix+job.href:target;link.textContent=job.status==='error'?'Voir le diagnostic':job.outcome==='verified'?'Ouvrir le livrable':'Voir le résultat';}
    if(job.status==='done'&&job.review_state==='dismissed'&&!form.querySelector('.live-restore')){
      const restore=document.createElement('button');restore.type='button';restore.className='live-restore';restore.textContent='Rétablir';
      restore.addEventListener('click',()=>{
        const copy=form.cloneNode(true);copy.querySelectorAll('.live-receipt,.live-open,.live-restore').forEach(x=>x.remove());
        copy.querySelector('[name="state"]').value='restored';delete copy.dataset.liveKey;delete copy.dataset.liveToken;
        copy.querySelector('button').disabled=false;copy.querySelector('button').textContent='Rétablir';
        form.replaceWith(copy);htmx.process(copy);copy.requestSubmit();
      });form.append(restore);
    }
  }
  document.body.addEventListener('click',event=>{
    const button=event.target.closest('button[data-live-href]');if(!button)return;
    event.preventDefault();window.location.assign(button.dataset.liveHref);
  });
  async function snapshot(){
    if(queued||document.hidden)return;queued=true;
    try{
      const res=await fetch(prefix+'/live/snapshot?after='+cursor,{credentials:'same-origin',cache:'no-store'});
      if(!res.ok)throw new Error('connexion');
      const data=await res.json(),byId=new Map(data.jobs.map(j=>[j.id,j]));
      if(cursor===0)cursor=data.cursor||0;
      const watcher=data.services.find(x=>x.name==='surveillance');
      const mail=data.services.find(x=>x.name==='courriels');
      const idle=data.services.find(x=>x.name==='imap_idle');
      const active=watcher&&!watcher.stale&&watcher.status==='active';
      const next=watcher?.next_check?Math.max(0,Math.round(watcher.next_check-Date.now()/1000)):null;
      summary.textContent=(active?'Surveillance active':watcher?.status==='paused'?'Surveillance en pause':'Surveillance non confirmée')+
        ' · '+data.running+' en cours · '+data.pending+' en attente'+
        (mail?' · courriels contrôlés il y a '+Math.max(0,mail.age_seconds)+' s':'')+
        (next!==null?' · prochain contrôle dans '+next+' s':'')+
        (data.mode==='observe'?' · mode observation, pas de dépôt':'')+
        (!data.mail_drafts_enabled?' · dépôts automatiques désactivés':'');
      if(idle?.status==='error')summary.textContent+=' · IMAP en reconnexion';
      else if(idle?.status==='polling')summary.textContent+=' · contrôle périodique, IDLE indisponible';
      document.querySelector('.live-dot')?.classList.toggle('active',active);
      const seen=new Set();
      for(const item of data.requests){
        if(seen.has(item.fingerprint))continue;seen.add(item.fingerprint);
        document.querySelectorAll('form[data-live-key="'+item.fingerprint+'"]').forEach(form=>{
          receipt(form,byId.get(item.job_id),prefix+item.target);tracked.set(form,item);
        });
      }
      for(const [form,item] of tracked){if(!form.isConnected){tracked.delete(form);continue;}receipt(form,byId.get(item.job_id),prefix+item.target);}
      refreshPanel();
    }catch(_error){summary.textContent='Connexion interrompue · reconnexion automatique';}
    finally{queued=false;}
  }
  function refreshBoard(){
    if(!document.querySelector('#ws-live-board')||document.hidden||!window.htmx)return;
    clearTimeout(boardTimer);
    boardTimer=setTimeout(()=>{
      if(document.querySelector('#ws-live-board')?.contains(document.activeElement)&&document.activeElement?.matches('input,textarea,select'))return;
      const y=window.scrollY;lastBoard=Date.now();
      const opened=new Set([...document.querySelectorAll('#ws-live-board [data-live-card]')].filter(x=>x.querySelector('details[open]')).map(x=>x.dataset.liveCard));
      htmx.ajax('GET',prefix+'/live/board',{target:'#ws-live-board',swap:'innerHTML'}).then(()=>{
        document.querySelectorAll('#ws-live-board [data-live-card]').forEach(x=>{if(opened.has(x.dataset.liveCard))x.querySelectorAll('details').forEach(d=>d.open=true);});
        window.scrollTo({top:y,behavior:'instant'});snapshot();
      });
    },Math.max(1000,10000-(Date.now()-lastBoard)));
  }
  document.body.addEventListener('htmx:configRequest',event=>{
    const form=event.detail.elt.closest('form');if(!form)return;
    if(!form.dataset.liveToken)form.dataset.liveToken=crypto.randomUUID();
    event.detail.parameters.live_token=form.dataset.liveToken;
  });
  document.body.addEventListener('htmx:beforeRequest',event=>{
    const form=event.detail.elt.closest('form');if(!form)return;
    const button=form.querySelector('button');if(button){button.dataset.liveOriginal ||= button.textContent;button.disabled=true;button.textContent='Enregistrement…';}
  });
  document.body.addEventListener('input',event=>{
    const form=event.target.closest('form');if(!form||!tracked.has(form))return;
    tracked.delete(form);delete form.dataset.liveToken;delete form.dataset.liveKey;
    const button=form.querySelector('button');if(button){button.disabled=false;delete button.dataset.liveHref;button.textContent=button.dataset.liveOriginal||'Enregistrer';}
    form.querySelector('.live-receipt')?.remove();form.querySelector('.live-open')?.remove();
  });
  document.body.addEventListener('live-action',event=>{
    const form=event.target.closest('form'), data=event.detail;
    if(form){form.dataset.liveKey=data.fingerprint;tracked.set(form,{job_id:data.job_id,target:data.url.startsWith(prefix)?data.url.slice(prefix.length):data.url});
      receipt(form,data.job_id?{status:'pending',label:'Demande enregistrée',id:data.job_id}:null,data.url);}
    snapshot();refreshBoard();
  });
  document.body.addEventListener('htmx:afterRequest',event=>{
    if(event.detail.successful)return;
    const form=event.detail.elt.closest('form');if(!form)return;
    const button=form.querySelector('button');if(button){button.disabled=false;button.textContent='Réessayer la demande';}
    if(event.detail.xhr?.status===400)delete form.dataset.liveToken;
    notify(event.detail.xhr?.status===400?'Demande refusée : vérifiez le formulaire ou rechargez votre session.':'Connexion interrompue. Vérifiez l’activité avant de relancer.');
  });
  function connect(){
    if(stream)stream.close();if(document.hidden)return;
    stream=new EventSource(prefix+'/live/events?after='+cursor);
    stream.onopen=()=>{connected=true;snapshot();};
    stream.onerror=()=>{connected=false;};
    stream.addEventListener('activity',event=>{
      const item=JSON.parse(event.data);cursor=Math.max(cursor,item.id);snapshot();
      if(item.kind==='produced')notify(item.message);
      if(['produced','done','error','queued','detected','calendar','documents'].includes(item.kind))refreshBoard();
    });
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden){stream?.close();connected=false;}else{snapshot();connect();}});
  document.addEventListener('htmx:afterSwap',event=>{if(event.detail.target?.id==='ws-live-board')snapshot();});
  window.addEventListener('pagehide',()=>stream?.close());
  snapshot().then(connect);setInterval(()=>{snapshot();if(!connected)refreshBoard();},15000);
})();
