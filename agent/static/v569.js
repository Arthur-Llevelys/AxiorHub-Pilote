/* AxiorHub 5.6.9 : bloc « À décider » (Aujourd'hui) et page « Mise en service ». Mêmes API authentifiées que les autres pages. */
(() => {
  'use strict';
  const box = document.querySelector('#c569-decisions');
  const meta = name => document.querySelector('meta[name="' + name + '"]')?.content || '';
  const prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  const status = document.querySelector('#c569-status');
  const note = text => { if (status) status.textContent = text; };
  async function call(route, data) {
    const r = await fetch(prefix + '/api440/m568/' + route, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)});
    const v = await r.json();
    if (!r.ok) throw new Error(v.message || v.error || 'Demande refusée.');
    return v;
  }
  async function call567(route, data) {   // 5.6.13 : missions et règles (même authentification, préfixe m567)
    const r = await fetch(prefix + '/api440/m567/' + route, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)});
    const v = await r.json();
    if (!r.ok) throw new Error(v.message || v.error || 'Demande refusée.');
    return v;
  }
  const recette = document.querySelector('[data-recette5613-run]');
  recette?.addEventListener('click', async () => {
    const out = document.querySelector('#recette5613-status');
    if (recette.dataset.confirm && !window.confirm(recette.dataset.confirm)) return;
    recette.disabled = true; out.textContent = 'Recette en cours (dépôt Nextcloud, brouillon IMAP)…';
    try {
      const v = await call('readiness/production', {});
      out.textContent = v.ok ? 'Recette réussie.' : 'Recette avec échec(s) : voir le rapport.';
      const list = document.createElement('ul'); list.className = 'r569-list';
      v.steps.forEach(s => { const li = document.createElement('li'); li.className = 'r569-' + (s.ok === true ? 'ok' : s.ok === false ? 'bad' : 'muted'); li.textContent = s.label + ' — ' + s.message; list.append(li); });
      recette.closest('section').querySelector('ul.r569-list')?.remove(); recette.closest('section').append(list); recette.disabled = false;
    }
    catch (e) { out.textContent = e.message; recette.disabled = false; }
  });
  const runButton = document.querySelector('[data-readiness569-run]');
  runButton?.addEventListener('click', async () => {
    const out = document.querySelector('#readiness569-status');
    runButton.disabled = true; out.textContent = 'Contrôles en cours…';
    try { await call('readiness/run', {}); location.reload(); }
    catch (e) { out.textContent = e.message; runButton.disabled = false; }
  });
  const mic = document.querySelector('#readiness569-mic');
  mic?.querySelector('[data-mic-test]')?.addEventListener('click', async () => {
    const out = mic.querySelector('[data-mic-result]');
    if (!window.isSecureContext) { out.textContent = 'Page non sécurisée : le navigateur refuse le micro hors HTTPS (sauf localhost).'; return; }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !window.AudioContext) { out.textContent = 'Navigateur sans getUserMedia, MediaRecorder ou AudioContext.'; return; }
    let stream = null, context = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: true});
      context = new AudioContext(); await context.resume();
      const analyser = context.createAnalyser(); context.createMediaStreamSource(stream).connect(analyser);
      const data = new Float32Array(analyser.fftSize); let peak = 0; const started = Date.now();
      out.textContent = 'Parlez…';
      await new Promise(resolve => { const timer = setInterval(() => {
        analyser.getFloatTimeDomainData(data);
        for (const v of data) peak = Math.max(peak, Math.abs(v));
        if (Date.now() - started > 2500) { clearInterval(timer); resolve(); }
      }, 100); });
      out.textContent = peak > 0.01 ? 'Micro fonctionnel : niveau capté ' + peak.toFixed(2) + '. Aucun son transmis.'
        : 'Micro autorisé mais aucun son capté en 2,5 s : vérifiez l’entrée audio du système.';
    } catch (e) { out.textContent = 'Micro refusé ou indisponible : ' + e.message; }
    finally { stream?.getTracks().forEach(t => t.stop()); context?.close().catch(() => {}); }
  });
  if (!box || document.querySelector('#c5614-panel')) return;   // 5.6.14 : le centre « À décider » compact est pris en charge par v5614.js
  box.addEventListener('click', async event => {
    const button = event.target.closest('button[data-act]');
    if (!button) return;
    const item = button.closest('.c569-item'), act = button.dataset.act, id = item.dataset.id;
    const field = name => item.querySelector('[data-field="' + name + '"]')?.value?.trim() || '';
    button.disabled = true;
    try {
      if (act === 'plan-resume') await call('plan/control', {id, action: 'resume'});
      else if (act === 'mission-resolve') {
        if (!field('matter')) throw new Error('Choisissez le dossier de cette mission.');
        await call567('mission/control', {id, action: 'resolve', matter: field('matter')});
      } else if (act === 'mission-resume') await call567('mission/control', {id, action: 'resume'});
      else if (act === 'docreq-resolve') await call('docreq/control', {id, matter: field('matter')});
      else if (act.startsWith('rule-')) await call567('rule/decide', {id, action: act === 'rule-adopt' ? 'adopt' : act === 'rule-adopt-matter' ? 'adopt_matter' : 'ignore'});
      else if (act === 'resolve') {
        const payload = {id, action: 'resolve'};
        if (field('matter')) payload.matter = field('matter');
        if (field('created')) { payload.created = field('created'); payload.proof = field('proof'); }
        if (!payload.matter && !payload.created) throw new Error('Choisissez un dossier ou indiquez la date de création avec sa preuve.');
        await call('document/control', payload);
      } else await call('document/control', {id, action: act});
      note('Décision enregistrée ; l’agent reprend.');
      item.remove();
      const badge = box.querySelector('#c569-title .c530-badge'), left = box.querySelectorAll('.c569-item').length;
      if (badge) badge.textContent = String(left);
      if (!left) setTimeout(() => box.remove(), 800);   // 5.6.11 : plus de rechargement complet
    } catch (e) { note(e.message); button.disabled = false; }
  });
})();
