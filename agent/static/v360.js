(() => {
  'use strict';
  const input=document.querySelector('[data-assistant-file]');
  if (!input) return;
  const form=document.querySelector('form input[name="attachment_id"]')?.form;
  const hidden=form?.querySelector('input[name="attachment_id"]');
  const status=document.querySelector('[data-assistant-status]');
  const csrf=document.querySelector('meta[name="axiorhub-csrf"]')?.content || '';
  if (!form || !hidden || !status || !csrf) return;
  const prefix=location.pathname.split('/assistant')[0];
  let pending=false;
  form.addEventListener('submit',event => {
    if (pending || (input.files.length && !hidden.value)) {
      event.preventDefault();
      status.textContent=pending?'Attendez la fin de l’extraction du fichier.':'Fichier non extrait : retirez-le ou réessayez.';
    }
  });
  input.addEventListener('change',async () => {
    hidden.value='';
    const file=input.files[0];
    if (!file) {status.textContent='Aucun fichier joint.';return;}
    if (file.size<1 || file.size>8000000) {
      status.textContent='Fichier refusé : 8 Mo maximum.';return;
    }
    pending=true;status.textContent='Extraction locale du document en cours…';
    try {
      const response=await fetch(prefix+'/assistant/attachment',{
        method:'POST',credentials:'same-origin',body:file,
        headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':csrf,
          'X-Attachment-Name':encodeURIComponent(file.name),
          'X-Attachment-Matter':form.elements.matter?.value || '',
          'X-Attachment-Key':form.elements.key?.value || ''}});
      const result=await response.json();
      if (!response.ok || !result.attachment_id) throw new Error(result.error || 'extraction_impossible');
      hidden.value=result.attachment_id;
      status.textContent='Document prêt : '+result.name+' ('+result.extracted_characters+
        ' caractères extraits, '+(result.parts || 1)+' section(s) à analyser'+
        (result.partial?', extrait partiel':'')+'). Envoyez la demande après relecture.';
    } catch(error) {status.textContent='Import impossible : '+error.message;}
    finally {pending=false;}
  });
})();
