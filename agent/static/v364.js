document.addEventListener('DOMContentLoaded',()=>{
  const prefix=document.querySelector('meta[name="axiorhub-prefix"]')?.content||'';
  const csrf=document.querySelector('meta[name="axiorhub-csrf"]')?.content||'';
  document.querySelectorAll('[data-extension-upload]').forEach(input=>{
    input.addEventListener('change',async()=>{
      const file=input.files&&input.files[0];if(!file)return;
      const id=input.dataset.extensionUpload;
      const status=document.querySelector('[data-extension-status="'+CSS.escape(id)+'"]');
      if(status)status.textContent='Inspection locale de l’archive…';
      input.disabled=true;
      try{
        const response=await fetch(prefix+'/extensions/upload',{method:'POST',body:file,credentials:'same-origin',headers:{
          'Content-Type':'application/zip','X-CSRF-Token':csrf,
          'X-Extension-Id':id,'X-Extension-Name':encodeURIComponent(file.name)}});
        const result=await response.json();
        if(!response.ok)throw new Error(result.error||'import_extension_echoue');
        if(status)status.textContent='Archive inspectée. SHA-256 : '+result.archive_sha256+'. Rechargez la page pour examiner le rapport.';
      }catch(error){if(status)status.textContent='Import refusé : '+error.message;}
      finally{input.disabled=false;input.value='';}
    });
  });
});
