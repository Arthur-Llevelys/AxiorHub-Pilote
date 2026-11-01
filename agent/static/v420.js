/* AxiorHub 4.2 (5.6.88 : palette unique Ctrl/Cmd+K, résultats groupés calculés sur le serveur) : palette de commandes et conservation des brouillons de formulaires. */
(() => {
  'use strict';
  const prefix=document.querySelector('meta[name="axiorhub-prefix"]')?.content||'';
  const commands=[
    ['Aujourd’hui','Audiences, brouillons, échéances, décisions','/aujourdhui'],
    ['Courriels à relire','Relire et corriger les brouillons','/courriels'],
    ['Dossiers','Ouvrir les dossiers','/dossiers'],
    ['Agenda et tâches','Agenda, tâches, organiser la semaine','/planning'],
    ['Échéances','Délais de procédure','/echeances'],
    ['Produire','Demander un acte, une note, un courrier','/production'],
    ['Pièces et bordereaux','Bordereau, pièces numérotées et tamponnées','/pieces'],
    ['Cabinet','Temps, rendez-vous, conflits, prescription','/cabinet'],
    ['Pourquoi rien n’est produit ?','Services, erreurs, dossiers bloqués','/diagnostic'],
    ['État du système','Diagnostiquer et relancer','/etat-systeme'],
    ['Paramètres','Configurer AxiorHub','/parametres']
  ];
  const palette=document.createElement('div');palette.id='ws-command-palette';palette.hidden=true;
  palette.innerHTML='<section role="dialog" aria-modal="true" aria-label="Recherche et commandes"><input id="ws-command-search" type="search" autocomplete="off" aria-controls="ws-command-results" aria-label="Rechercher dans le cabinet ou aller à une page" placeholder="Dossier, contact, facture, document, courriel, fonction…"><p id="ws-command-status" class="m82-quick-status" role="status"></p><div id="ws-command-results"></div><small>Ctrl+K (⌘K) · Flèches pour parcourir · Entrée pour ouvrir · Échap pour fermer</small></section>';
  document.body.appendChild(palette);const input=palette.querySelector('input');const results=palette.querySelector('#ws-command-results');const status=palette.querySelector('#ws-command-status');
  let timer=null,seq=0,back=null;
  const norm=s=>(s||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase();
  function row(title,hint,href){const a=document.createElement('a');a.href=href;const s=document.createElement('span');const b=document.createElement('strong');b.textContent=title;const sm=document.createElement('small');sm.textContent=hint;s.append(b,document.createElement('br'),sm);const k=document.createElement('kbd');k.textContent='↵';a.append(s,k);return a;}
  function complete(raw){return row('Rechercher « '+raw+' »','Recherche complète : courriels, fichiers, agenda et notes',prefix+'/recherche?q='+encodeURIComponent(raw));}
  function groups(raw,data){results.replaceChildren();
    let n=0;(data.groupes||[]).forEach(g=>{const box=document.createElement('section');box.setAttribute('aria-label',g.titre);const h=document.createElement('p');h.className='m82-quick-group';h.textContent=g.titre;box.append(h);g.resultats.forEach(r=>{n+=1;box.append(row(r.titre,r.detail,prefix+r.url));});results.append(box);});
    results.append(complete(raw));status.textContent=(n?n+' résultat(s).':'Aucun résultat rapide.')+(data.partiel?' Recherche interrompue : la recherche complète ira plus loin.':'');}
  function search(raw){const mine=++seq;fetch(prefix+'/api440/m5688/palette?q='+encodeURIComponent(raw),{credentials:'same-origin'}).then(r=>r.ok?r.json():Promise.reject(r.status)).then(data=>{if(mine===seq)groups(raw,data);}).catch(()=>{if(mine===seq)status.textContent='Recherche rapide indisponible : utilisez la recherche complète.';});}
  function render(){const raw=input.value.trim();const q=norm(raw);clearTimeout(timer);seq+=1;status.textContent='';results.replaceChildren();
    if(raw.length>=2){results.append(complete(raw));timer=setTimeout(()=>search(raw),140);return;}
    commands.filter(x=>norm(x.join(' ')).includes(q)).forEach(x=>results.append(row(x[0],x[1],prefix+x[2])));
    if(!results.children.length){const p=document.createElement('p');p.className='empty';p.textContent='Aucune commande.';results.append(p);}}
  function open(){back=document.activeElement;palette.hidden=false;input.value='';render();requestAnimationFrame(()=>input.focus());}
  function close(){if(palette.hidden)return;palette.hidden=true;seq+=1;clearTimeout(timer);if(back&&back.focus)back.focus();}
  input.addEventListener('input',render);
  palette.addEventListener('keydown',event=>{const links=[...results.querySelectorAll('a')],i=links.indexOf(document.activeElement);
    if(event.key==='ArrowDown'&&links.length){event.preventDefault();(links[i+1]||links[0]).focus();}
    else if(event.key==='ArrowUp'&&links.length){event.preventDefault();(i>0?links[i-1]:input).focus();}
    else if(event.key==='Tab'){const stops=[input,...links],at=stops.indexOf(document.activeElement);event.preventDefault();stops[(at+(event.shiftKey?stops.length-1:1))%stops.length].focus();}
    else if(event.key==='Enter'&&event.target===input){const first=results.querySelector('a');if(first){event.preventDefault();location.href=first.href;}}});
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&!event.altKey&&event.key.toLowerCase()==='k'){event.preventDefault();palette.hidden?open():close();}else if(event.key==='Escape'&&!palette.hidden)close();});
  document.addEventListener('click',event=>{const a=event.target.closest&&event.target.closest('[data-m82-quick]');if(a&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey){event.preventDefault();open();}});
  palette.addEventListener('click',event=>{if(event.target===palette)close();});

  document.querySelectorAll('form').forEach((form,index)=>{
    if(form.method.toLowerCase()!=='post')return;
    const action=form.querySelector('[name="action"]')?.value||'form';const key='axiorhub-form-v420:'+location.pathname+':'+action+':'+index;
    const fields=[...form.querySelectorAll('textarea,input:not([type="hidden"]):not([type="file"]),select')].filter(x=>x.name&&!['csrf','confirm'].includes(x.name));
    if(!fields.length)return;
    try{const saved=JSON.parse(sessionStorage.getItem(key)||'{}');fields.forEach(x=>{if(saved[x.name]!==undefined&&!x.value){if(x.type==='checkbox')x.checked=!!saved[x.name];else x.value=saved[x.name];}});}catch(_error){}
    let marker=form.querySelector('.ws-autosave-state');if(!marker){marker=document.createElement('span');marker.className='ws-autosave-state';form.appendChild(marker);}
    form.addEventListener('input',()=>{const data={};fields.forEach(x=>data[x.name]=x.type==='checkbox'?x.checked:x.value);try{sessionStorage.setItem(key,JSON.stringify(data));marker.textContent='Brouillon local sauvegardé';}catch(_error){}});
    form.addEventListener('submit',()=>{try{sessionStorage.removeItem(key);}catch(_error){}});
  });
  document.querySelectorAll('[data-studio-choice]').forEach(button=>button.addEventListener('click',()=>{const select=document.querySelector('[data-studio-type]');if(select){select.value=button.dataset.studioChoice;select.scrollIntoView({behavior:'smooth',block:'center'});}}));
})();
