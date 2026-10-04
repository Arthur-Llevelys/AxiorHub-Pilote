(() => {
  'use strict';
  const form = document.getElementById('word-template-upload');
  if (!form) return;
  const status = document.getElementById('word-upload-result');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const file = form.elements.model.files?.[0];
    if (!file || !file.name.toLowerCase().endsWith('.docx') || file.size > 8000000 || file.size < 1) {
      status.textContent = 'Choisir un fichier .docx de 8 Mo maximum.'; return;
    }
    const button = form.querySelector('button'); button.disabled = true;
    status.textContent = 'Contrôle et prévisualisation de toutes les pages en cours…';
    try {
      const csrf = document.querySelector('meta[name="axiorhub-csrf"]')?.content || '';
      const response = await fetch(form.dataset.uploadUrl, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                 'X-CSRF-Token':csrf,'X-Template-Name':encodeURIComponent(file.name)}, body:file});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Import impossible');
      window.location.assign(window.location.pathname + '?template=' + encodeURIComponent(result.template_id));
    } catch (error) { status.textContent = 'Import refusé : ' + error.message; button.disabled = false; }
  });
})();
