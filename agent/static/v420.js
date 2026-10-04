/* AxiorHub 4.2 (palette étendue en 5.2.0 : recherche dans le cabinet) : palette de commandes et conservation des brouillons de formulaires. */
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
  palette.innerHTML='<section role="dialog" aria-modal="true" aria-label="Recherche et commandes"><input id="ws-command-search" autocomplete="off" placeholder="Rechercher dans le cabinet ou aller à une page…"><div id="ws-command-results"></div><small>Ctrl+K (⌘K) · Entrée : rechercher · Échap pour fermer</small></section>';
  document.body.appendChild(palette);const input=palette.querySelector('input');const results=palette.querySelector('#ws-command-results');
  const norm=s=>(s||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase();
  function row(title,hint,href){const a=document.createElement('a');a.href=href;const s=document.createElement('span');const b=document.createElement('strong');b.textContent=title;const sm=document.createElement('small');sm.textContent=hint;s.append(b,document.createElement('br'),sm);const k=document.createElement('kbd');k.textContent='↵';a.append(s,k);return a;}
  function render(){const raw=input.value.trim();const q=norm(raw);results.replaceChildren();
    if(raw)results.append(row('Rechercher « '+raw+' »','Courriels, fichiers, agenda et notes du cabinet',prefix+'/recherche?q='+encodeURIComponent(raw)));
    commands.filter(x=>norm(x.join(' ')).includes(q)).forEach(x=>results.append(row(x[0],x[1],prefix+x[2])));
    if(!results.children.length){const p=document.createElement('p');p.className='empty';p.textContent='Aucune commande.';results.append(p);}}
  function open(){palette.hidden=false;input.value='';render();requestAnimationFrame(()=>input.focus());}
  function close(){palette.hidden=true;}
  input.addEventListener('input',render);
  input.addEventListener('keydown',event=>{if(event.key==='Enter'){const first=results.querySelector('a');if(first){event.preventDefault();location.href=first.href;}}});
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();palette.hidden?open():close();}else if(event.key==='Escape'&&!palette.hidden)close();});
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
