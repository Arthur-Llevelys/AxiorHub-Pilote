(() => {
  'use strict';
  const scope=document.querySelector('[data-hearing-matter]');
  if (!scope) return;
  const csrf=document.querySelector('meta[name="axiorhub-csrf"]')?.content || '';
  const prefix=document.querySelector('meta[name="axiorhub-prefix"]')?.content || '';
  const matter=scope.dataset.hearingMatter || '';
  const form=document.querySelector('form input[name="our_source_path"]')?.form;
  if (!csrf || !prefix || !matter || !form) return;
  let pending=0;
  form.addEventListener('submit',event => {
    if (pending) {event.preventDefault();scope.querySelectorAll('[data-hearing-status]').forEach(node => {
      node.textContent='Patientez jusqu’à la fin du téléversement avant de lancer l’analyse.';
    });}
  });
  scope.querySelectorAll('[data-hearing-upload]').forEach(input => {
    input.addEventListener('change',async () => {
      const file=input.files[0],side=input.dataset.hearingUpload;
      const status=scope.querySelector('[data-hearing-status="'+side+'"]');
      if (!file) return;
      if (!/\.(docx|pdf)$/i.test(file.name) || !file.size || file.size>20000000) {
        status.textContent='Fichier DOCX ou PDF de 20 Mo maximum requis.';return;
      }
      ++pending;input.disabled=true;status.textContent='Vérification et dépôt dans le dossier Nextcloud en cours…';
      try {
        const response=await fetch(prefix+'/hearing/upload',{method:'POST',credentials:'same-origin',body:file,
          headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':csrf,
            'X-Hearing-Name':encodeURIComponent(file.name),'X-Hearing-Matter':matter,'X-Hearing-Side':side}});
        const result=await response.json();
        if (!response.ok || result.matter!==matter || !result.path) throw new Error(result.error || 'depot_impossible');
        form.elements[side==='ours'?'our_source_path':'opponent_source_path'].value=result.path;
        status.textContent='Dépôt vérifié : '+result.path+' · '+result.text_characters+' caractères de texte lisible.';
      } catch(error) {status.textContent='Dépôt impossible : '+error.message;}
      finally {--pending;input.disabled=false;}
    });
  });
})();
