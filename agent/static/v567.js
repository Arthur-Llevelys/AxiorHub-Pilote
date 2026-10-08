(() => {
  'use strict';
  const cfg = JSON.parse(document.querySelector('#ws-ai-config')?.textContent || '{}');
  if (!cfg.mission_api) return;
  const prefix = cfg.prefix || '', csrf = cfg.csrf || '';
  const dock = document.querySelector('#ws-ai-dock'), launcher = document.querySelector('#ws-ai-launcher');
  const question = dock.querySelector('#ws-ai-question'), matter = dock.querySelector('#ws-ai-matter');
  const send = dock.querySelector('#ws-ai-send'), status = dock.querySelector('#ws-ai-status');
  const result = dock.querySelector('#ws-ai-result'), file = dock.querySelector('#ws-ai-file');
  const current = dock.querySelector('#ws-ai-current') || result, history = dock.querySelector('#ws-ai-history');
  const intentSelect = dock.querySelector('#ws-ai-intent'), plan = dock.querySelector('#ws-ai-plan'), filesList = dock.querySelector('#ws-ai-files');
  const search = dock.querySelector('#ws-ai-matter-search'), wide = dock.querySelector('#ws-ai-wide');
  const pageStatus = document.querySelector('#mission567-page-status');
  const q = new URLSearchParams(location.search);
  let selected = cfg.selected_matter || q.get('matter') || '';
  if (!selected && ['/matter', '/fiche', '/chronologie'].includes(cfg.path)) selected = q.get('id') || '';
  if ([...matter.options].some(o => o.value === selected)) matter.value = selected;
  let inputChannel = 'text';
  let currentMission = null, attachments = [], uploadBusy = false, sending = false, recorder = null, liveAudio = null;
  let audioURL = '', hiddenExit = false, profile = {}, retryPayload = null, refreshTimer = null, refreshBusy = false, planTimer = null, shownStates = {};
  const note = text => { status.textContent = text; if (pageStatus) pageStatus.textContent = text; };
  const storageKey = 'axiorhub567-last-mission:' + prefix;
  const storage = {get: () => {try {return sessionStorage.getItem(storageKey);} catch {return null;}},
                   set: value => {try {sessionStorage.setItem(storageKey, value);} catch {}}};
  const token = () => crypto.randomUUID ? crypto.randomUUID() : [...crypto.getRandomValues(new Uint8Array(20))].map(x => x.toString(16).padStart(2, '0')).join('');
  async function api(name, data) {
    const response = await fetch(prefix + '/api440/m567/' + name, {method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      headers: data === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: data === undefined ? undefined : JSON.stringify(data)});
    const value = await response.json();
    if (!response.ok) throw new Error(value.message || value.error || 'Demande refusée.');
    return value;
  }
  // 5.6.13 : un seul composant Pilote. Sur « Aujourd'hui », le panneau est monté dans la page (même état, même fil, mêmes limites) ;
  // ailleurs il reste repliable. Replier arrête toujours le micro et la lecture.
  const slot = document.querySelector('#c530-pilot-slot');
  const embedded = !!slot;
  if (embedded) {
    slot.querySelector('[data-pilot-fallback]')?.remove(); slot.append(dock); dock.hidden = false; dock.classList.add('ws-ai-dock--embedded');
    launcher.hidden = true; document.querySelector('[data-legacy-composer]')?.setAttribute('hidden', '');
  }
  function stopVoice() { try { window.axiorhubVoice?.stop(); } catch {} stopAudio(); if (recorder?.state !== 'inactive') recorder?.stop(); }
  function open() {dock.hidden = false; launcher.setAttribute('aria-expanded', 'true'); question.focus(); refreshCurrent(); loadMatters();}
  function close() {stopVoice(); if (embedded) return; dock.hidden = true; launcher.setAttribute('aria-expanded', 'false'); launcher.focus();}
  launcher.addEventListener('click', () => dock.hidden ? open() : close());
  dock.querySelector('#ws-ai-close').addEventListener('click', close);
  wide?.addEventListener('click', () => {const on = dock.classList.toggle('ws-ai-dock--wide'); wide.setAttribute('aria-pressed', String(on));});
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !dock.hidden && !embedded) close();
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && document.activeElement === question) {event.preventDefault(); send.click();}
  });
  dock.querySelectorAll('[data-ai-example]').forEach(b => b.addEventListener('click', () => {question.value = b.dataset.aiExample; question.focus(); schedulePlan();}));
  // 5.6.14 (U04) : commandes de contexte (Préparer audience, Répondre, Comparer, Réviser, Assignation, Devis)
  dock.querySelectorAll('[data-ai-chip]').forEach(b => b.addEventListener('click', () => {
    question.value = b.dataset.aiChip; if (intentSelect && b.dataset.aiIntent) intentSelect.value = b.dataset.aiIntent; else if (intentSelect) intentSelect.value = 'auto';
    question.focus(); schedulePlan();
  }));
  document.querySelectorAll('[data-mission-open]').forEach(b => b.addEventListener('click', open));
  // 5.6.13 : sélection du dossier — recherche par client, adversaire, référence, alias ; nom complet ; récents en premier
  let mattersLoaded = false, mattersData = [];
  function rebuildMatters(filter) {
    const keep = matter.value, f = (filter || '').trim().toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    const rows = mattersData.filter(m => !f || m.search.includes(f));
    matter.replaceChildren(new Option('Tout le cabinet', ''));
    const recent = rows.filter(m => m.recent), others = rows.filter(m => !m.recent);
    const group = (label, items) => { if (!items.length) return; const g = document.createElement('optgroup'); g.label = label;
      items.forEach(m => g.append(new Option(m.label + (m.adversaries?.length ? ' · c/ ' + m.adversaries.join(', ') : ''), m.id))); matter.append(g); };
    group('Dossiers récents', recent); group('Autres dossiers', others);
    if ([...matter.options].some(o => o.value === keep)) matter.value = keep; else if (f && rows.length === 1) matter.value = rows[0].id;
  }
  async function loadMatters() {
    if (mattersLoaded) return; mattersLoaded = true;
    try { mattersData = (await api('matters')).matters; rebuildMatters(search?.value); } catch { mattersLoaded = false; }
  }
  search?.addEventListener('input', () => { loadMatters().then(() => rebuildMatters(search.value)); });
  search?.addEventListener('focus', loadMatters);
  matter.addEventListener('change', () => {
    if (attachments.length) {attachments = []; file.value = ''; renderFiles(); note('Dossier changé : joignez à nouveau les documents pour ce contexte.');}
    schedulePlan();
  });
  // 5.6.13 : liste commune texte–voix des pièces jointes (nom, extraction, pages lisibles, retrait)
  async function renderFiles() {
    if (!filesList) return;
    if (!attachments.length) { filesList.hidden = true; filesList.replaceChildren(); return; }
    try {
      const data = await api('attachments?ids=' + encodeURIComponent(attachments.join(',')) + '&matter=' + encodeURIComponent(matter.value) + '&key=' + encodeURIComponent(q.get('key') || ''));
      filesList.replaceChildren(...data.attachments.map(a => {
        const li = el('li', undefined, a.readable ? '' : 'ko');
        li.append(el('span', (a.name || a.id) + (a.error ? ' — ' + a.error : ' — ' + a.pages + ' page(s) lisible(s), ' + a.chars + ' caractères extraits' + (a.partial ? ' (partiel)' : ''))));
        const b = el('button', 'Retirer'); b.type = 'button'; b.addEventListener('click', () => { attachments = attachments.filter(x => x !== a.id); renderFiles(); schedulePlan(); }); li.append(b); return li;
      }));
      filesList.hidden = false;
    } catch (e) { note(e.message); }
  }
  file.addEventListener('change', async () => {
    const files = [...file.files], scope = matter.value;
    if (!files.length) return;
    if (attachments.length + files.length > 3 || files.some(x => x.size < 1 || x.size > 8000000)) {note('Trois documents maximum, 8 Mo chacun.'); return;}
    uploadBusy = true; send.disabled = true; note('Extraction locale des documents…');
    try {
      for (const f of files) {
        const r = await fetch(prefix + '/assistant/attachment', {method: 'POST', credentials: 'same-origin', body: f,
          headers: {'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrf,
                    'X-Attachment-Name': encodeURIComponent(f.name), 'X-Attachment-Matter': scope}});
        const data = await r.json(); if (!r.ok || !data.attachment_id) throw new Error(data.error || 'Extraction impossible.');
        if (scope !== matter.value) throw new Error('Dossier changé pendant l’import : recommencez.');
        attachments.push(data.attachment_id);
      }
      note(files.length + ' document(s) prêt(s). L’analyse progressive reprendra toutes les pages extraites.');
    } catch (e) {note(e.message);} finally {uploadBusy = false; send.disabled = sending; file.value = ''; renderFiles(); schedulePlan();}
  });
  const el = (tag, text, cls) => {const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node;};
  // 5.6.13 : résultat attendu annoncé avant de démarrer (Question / Analyse / Word / Brouillon), modifiable
  async function showPlan() {
    if (!plan) return;
    const text = question.value.trim(); if (text.length < 3) { plan.hidden = true; return; }
    try {
      const p = await api('mission/intent', {instruction: text, matter: matter.value, intent: intentSelect?.value || 'auto', attachments,
        context: {mail_key: q.get('key') || '', selected_documents: selectedDocuments()}});
      plan.replaceChildren(el('strong', '→ ' + p.label + (p.parcours ? ' · ' + p.parcours : '') + (p.facturation && p.facturation.missing?.length ? ' · manque : ' + p.facturation.missing.join(', ') : '')), el('span', ' · ' + (p.matter_label || (p.needs_matter || p.ambiguous ? 'dossier à préciser' : 'tout le cabinet'))
        + ' · sources : ' + p.sources.selected_documents + ' document(s) du dossier, ' + p.sources.attachments + ' pièce(s) jointe(s)' + (p.sources.mail ? ', courriel sélectionné' : '')),
        el('small', ' ' + (p.reasons || []).join(' ')));
      plan.hidden = false; plan.dataset.intent = p.intent;
    } catch (e) { plan.textContent = e.message; plan.hidden = false; }
  }
  function schedulePlan() { clearTimeout(planTimer); planTimer = setTimeout(showPlan, 450); }
  question.addEventListener('input', schedulePlan); intentSelect?.addEventListener('change', showPlan);
  function selectedDocuments() {
    return [...document.querySelectorAll('input[data-document-path]:checked, input[name="source_path"], input[name="our_source_path"], input[name="opponent_source_path"]')]
      .map(x => x.dataset.documentPath || x.value).filter(Boolean).slice(0, 20);
  }
  function checksLine(c) {
    const p = el('p', undefined, 'ws-ai-checks');
    [[c.deposit, c.deposit_label], [c.legal_control, c.legal_control_label], [c.lawyer_validation, c.lawyer_validation_label]]
      .forEach(([ok, label]) => p.append(el('span', (ok ? '✓ ' : '○ ') + label, 'ws-ai-check ' + (ok ? 'ok' : 'ko'))));
    return p;
  }
  function card(m) {
    const c = el('article', undefined, 'mission567-card'); c.dataset.missionId = m.id;
    c.append(el('div', m.label, 'mission567-state'), el('h3', m.instruction), el('p', m.matter_label || 'Cabinet'));
    c.append(el('p', m.job?.progress || m.plan.deliverable, 'muted'));
    const r = m.result || {};
    if (r.text) {const d = el('details'); d.append(el('summary', 'Lire la réponse et ses sources'), el('pre', r.text)); c.append(d);}
    // 5.6.13 : résultat produit — fichier ou brouillon, emplacement, contrôles effectués, points à compléter
    if (r.location) c.append(el('p', 'Emplacement : ' + r.location + (r.template ? ' · modèle : ' + r.template : ''), 'ws-ai-result-meta'));
    if (r.checks) { c.append(checksLine(r.checks)); if (r.checks.legal_control_detail) c.append(el('p', 'Contrôle : ' + r.checks.legal_control_detail, 'ws-ai-result-meta')); }
    if (r.to_complete?.length) { const d = el('details'); d.append(el('summary', r.to_complete.length + ' point(s) à compléter')); const ul = el('ul'); r.to_complete.forEach(x => ul.append(el('li', x))); d.append(ul); c.append(d); }
    if (r.revision_diff) { const d = el('details', undefined, 'ws-ai-diff'); d.append(el('summary', 'Comparaison des versions : ' + r.revision_diff.summary));
      const ul = el('ul'); r.revision_diff.added.slice(0, 10).forEach(x => ul.append(el('li', '+ ' + x))); r.revision_diff.removed.slice(0, 10).forEach(x => ul.append(el('li', '− ' + x))); d.append(ul); c.append(d); }
    if (r.rule_proposal && r.rule_proposal.state === 'proposed') {
      const p = el('p', 'Règle proposée : ' + r.rule_proposal.instruction + ' ', 'ws-ai-result-meta');
      const a = el('button', 'Adopter'), i = el('button', 'Ignorer'); [a, i].forEach(b => { b.type = 'button'; b.dataset.ruleDecide = r.rule_proposal.id; }); a.dataset.ruleAction = 'adopt'; i.dataset.ruleAction = 'ignore';
      p.append(a, ' ', i); c.append(p);
    }
    if (r.open_url && r.open_url.startsWith(prefix + '/') && !r.open_url.startsWith('//')) {
      const a = el('a', m.kind === 'mail' ? 'Relire le brouillon' : m.complex ? 'Ouvrir la mission complexe' : m.kind === 'facturation' ? 'Ouvrir Honoraires' : 'Ouvrir le projet', 'btn'); a.href = r.open_url; c.append(a);
    }
    if (r.missing?.length) c.append(el('p', 'À renseigner : ' + r.missing.join(', '), 'warn'));
    if (m.kind === 'document' && r.checks && !r.checks.lawyer_validation && r.path) {
      const b = el('button', 'Valider ce projet (avocat)'); b.type = 'button'; b.dataset.missionControl = 'validate'; c.append(' ', b);
    }
    if (m.state === 'decision') {
      const choices = new Map(); (m.exceptions || []).forEach(x => {(x.candidates || []).forEach(y => choices.set(y.id, y.label)); c.append(el('p', x.message));});
      if (choices.size) {
        const s = el('select', undefined, 'mission567-choice'); s.setAttribute('aria-label', 'Dossier de cette mission');
        choices.forEach((label, id) => {const o = el('option', label); o.value = id; s.append(o);});
        const b = el('button', 'Préciser et démarrer'); b.type = 'button'; b.dataset.missionControl = 'resolve'; c.append(s, b);
      }
    }
    if (m.state === 'error') c.append(el('p', m.job?.error || (m.exceptions || []).map(x => x.message).join(' '), 'warn'));
    if (['creating', 'queued', 'running', 'error', 'paused','suggested'].includes(m.state)) {
      const d = el('details'), active = ['creating', 'queued', 'running'].includes(m.state), b = el('button', active ? 'Suspendre' : (m.state==='suggested' ? 'Démarrer la préparation' : 'Reprendre'));
      b.type = 'button'; b.dataset.missionControl = active ? 'pause' : 'resume'; if (m.state==='suggested' || m.state==='error') c.append(b); else {d.append(el('summary', 'Autres actions'), b); c.append(d);}
    }
    const d = el('details'), list = el('ol'); m.plan.steps.forEach(x => list.append(el('li', x)));
    d.append(el('summary', 'Plan, sources et limites'), list, el('p', m.plan.restrictions.join(' · ')));
    if (m.plan.intent_reasons?.length) d.append(el('p', 'Résultat attendu : ' + m.plan.deliverable + ' — ' + m.plan.intent_reasons.join(' ')));
    const man = r.manifest;
    if (man && (man.fichiers_total || man.pieces_selectionnees?.length)) {
      const m2 = el('details'); m2.append(el('summary', 'Manifeste des sources'));
      const ul = el('ul');
      (man.conclusions_retenues || []).forEach(x => ul.append(el('li', 'Conclusions retenues : ' + x.fichier + (x.version ? ' (version du ' + x.version + ')' : ''))));
      (man.pieces_selectionnees || []).forEach(x => ul.append(el('li', 'Pièce sélectionnée : ' + x)));
      (man.extraits_utilises || []).forEach(x => ul.append(el('li', 'Extrait lu : ' + x)));
      ul.append(el('li', (man.courriels || 0) + ' courriel(s), ' + (man.faits || 0) + ' fait(s) validé(s), ' + (man.agenda || 0) + ' événement(s)'));
      if (man.non_lus_total) ul.append(el('li', man.non_lus_total + ' fichier(s) du dossier non lu(s) : ' + (man.non_lus || []).slice(0, 8).join(', ') + (man.non_lus_total > 8 ? '…' : '')));
      m2.append(ul); d.append(m2);
    }
    (r.sources || []).slice(0, 30).forEach(x => d.append(el('p', typeof x === 'string' ? x : JSON.stringify(x))));
    (r.notes || []).forEach(x => d.append(el('p', x, 'muted')));
    if (r.proof) d.append(el('p', 'Relecture : ' + r.proof)); c.append(d); return c;
  }
  // 5.6.13 : fil partagé texte–voix. Le rafraîchissement du résultat courant ne remplace jamais l'historique.
  function addTurn(instruction, text, channel) {
    if (!history) return;
    const turn = el('article', undefined, 'ws-ai-turn'); turn.append(el('p', (channel === 'voice' ? '🎙 ' : '✎ ') + instruction, 'ws-ai-turn-me'), el('pre', text));
    history.prepend(turn); while (history.children.length > 30) history.lastChild.remove();
  }
  function show(m) {
    currentMission = m; storage.set(m.id); current.replaceChildren(card(m)); note(m.label);
    const final = ['answered', 'verified', 'prepared', 'abstained', 'error'].includes(m.state);
    if (final && shownStates[m.id] !== m.state) { shownStates[m.id] = m.state; if (m.context?.channel !== 'voice') addTurn(m.instruction, (m.result?.text || m.result?.message || m.label), 'text'); }
    if (['answered', 'verified', 'prepared', 'abstained'].includes(m.state)) send.textContent = 'Confier une autre mission';
    else send.textContent = 'Démarrer une nouvelle mission';
  }
  async function refreshCurrent() {
    const id = currentMission?.id || storage.get(); if (!id || document.hidden) return;
    try {show(await api('mission?id=' + encodeURIComponent(id)));} catch { /* Un autre compte ne récupère pas cette mission. */ }
  }
  async function refreshList() {
    const list = document.querySelector('#mission567-list'); if (!list || document.hidden || refreshBusy) return;
    refreshBusy = true;
    try {const data = await api('missions'); list.replaceChildren(...data.missions.map(card));} catch (e) {note(e.message);} finally {refreshBusy = false;}
  }
  send.addEventListener('click', async () => {
    if (sending || uploadBusy) return;
    const text = question.value.trim(); if (!text) {note('Décrivez le résultat attendu.'); return;}
    const payload = retryPayload || {request_key: token(), instruction: text, matter: matter.value, attachments: [...attachments], intent: intentSelect?.value || 'auto',
      autonomy:dock.querySelector('#ws-ai-autonomy').value, due: dock.querySelector('#ws-ai-due').value, context: {page: cfg.path || '/', selected_documents: selectedDocuments(), mission_id: currentMission?.matter === matter.value ? currentMission.id : '',
      mail_key: q.get('key') || ''}, channel: inputChannel};
    sending = true; send.disabled = true; send.textContent = 'Demande enregistrée…'; note('Votre mission est en cours d’enregistrement.');
    try {
      const m = await api('mission/create', payload); retryPayload = null; attachments = []; file.value = ''; renderFiles(); if (plan) plan.hidden = true; show(m); refreshList();
    } catch (e) {retryPayload = payload; send.textContent = 'Vérifier / reprendre cette demande'; note(e.message + ' Le même identifiant sera réutilisé pour éviter un doublon.');}
    finally {sending = false; send.disabled = uploadBusy;}
  });
  document.addEventListener('click', async event => {
    const rule = event.target.closest('[data-rule-decide]');
    if (rule) { rule.disabled = true; try { const v = await api('rule/decide', {id: rule.dataset.ruleDecide, action: rule.dataset.ruleAction}); note(v.message); rule.closest('p')?.remove(); } catch (e) { note(e.message); rule.disabled = false; } return; }
    const b = event.target.closest('[data-mission-control]'); if (!b) return;
    const c = b.closest('[data-mission-id]'); b.disabled = true;
    try {const m = await api('mission/control', {id: c.dataset.missionId, action: b.dataset.missionControl,
      matter: c.querySelector('.mission567-choice')?.value || ''}); if (currentMission?.id === m.id) show(m); await refreshList();}
    catch (e) {note(e.message);} finally {b.disabled = false;}
  });
  function stopAudio() {if (liveAudio) {liveAudio.pause(); liveAudio.src = ''; liveAudio = null;} if (audioURL) {URL.revokeObjectURL(audioURL); audioURL = '';}}
  async function read(text) {
    stopAudio(); if (!text) {note('Aucun texte à lire.'); return;} note('Préparation de la lecture vocale…');
    try {
      const response = await fetch(prefix + '/api440/m567/speech', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify({text,matter:matter.value})});
      if (!response.ok) {const error = await response.json(); throw new Error(error.message || error.error);}
      audioURL = URL.createObjectURL(await response.blob()); liveAudio = new Audio(audioURL);
      liveAudio.onended = stopAudio; await liveAudio.play(); note('Lecture vocale. Pause, reprise et arrêt sont disponibles.');
    } catch (e) {stopAudio(); note(e.message);}
  }
  document.querySelectorAll('[data-briefing567]').forEach(b => b.addEventListener('click', async () => {
    b.disabled = true; try {const brief = await api('briefing'); current.replaceChildren(el('pre', brief.text)); await read(brief.text);} catch (e) {note(e.message);} finally {b.disabled = false;}
  }));
  dock.querySelector('[data-read567]').addEventListener('click', () => read(currentMission?.result?.text || current.textContent));
  dock.querySelector('[data-audio-pause567]').addEventListener('click', () => {if (liveAudio) liveAudio.paused ? liveAudio.play().catch(e => note(e.message)) : liveAudio.pause();});
  dock.querySelector('[data-audio-stop567]').addEventListener('click', stopAudio);
  dock.querySelector('#ws-ai-dictate').addEventListener('click', async event => {
    const button = event.currentTarget; stopAudio();
    if (recorder && recorder.state !== 'inactive') {recorder.stop(); return;}
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {note('Microphone indisponible dans ce navigateur.'); return;}
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: true});
      const mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(m => MediaRecorder.isTypeSupported(m));
      const rec = new MediaRecorder(stream, mime ? {mimeType: mime} : {}), chunks = []; let bytes = 0;
      recorder = rec; const timeout = setTimeout(() => {if (rec.state !== 'inactive') rec.stop();}, 60000);
      rec.ondataavailable = e => {chunks.push(e.data); bytes += e.data.size; if (bytes > 8000000 && rec.state !== 'inactive') rec.stop();};
      rec.onstop = async () => {
        clearTimeout(timeout); stream.getTracks().forEach(t => t.stop()); recorder = null; button.textContent = '🎙 Dicter'; window.axiorhubPilot.setMic(false);
        if (hiddenExit || bytes > 8000000) {if (bytes > 8000000) note('Dictée trop volumineuse : recommencez plus brièvement.'); return;}
        try {
          note('Transcription locale…'); const r = await fetch(prefix + '/dictation', {method: 'POST', credentials: 'same-origin',
            body: new Blob(chunks, {type: rec.mimeType}), headers: {'Content-Type': rec.mimeType.split(';')[0], 'X-CSRF-Token': csrf}});
          const data = await r.json(); if (!r.ok || !data.text) throw new Error(data.error || 'Transcription non obtenue.');
          let text = data.text, corrections = 0;
          for (const term of profile.lexicon || []) {
            const escaped = term.heard.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const pattern = new RegExp('(?<![\\p{L}\\p{N}])' + escaped + '(?![\\p{L}\\p{N}])', 'giu');
            text = text.replace(pattern, () => {corrections++; return term.written;});
          }
          question.value += (question.value ? '\n' : '') + text; inputChannel = 'voice'; question.focus(); schedulePlan();
          note('Transcription visible, à rectifier si nécessaire. ' + (corrections ? corrections + ' correction(s) du lexique appliquée(s). ' : '') + 'La mission ne démarre qu’à votre demande.');
        } catch (e) {note(e.message);}
      };
      rec.start(1000); button.textContent = '⏹ Arrêter la dictée'; window.axiorhubPilot.setMic(true); note('Microphone actif. La voix utilise les mêmes droits que le texte.');
    } catch (e) {stream?.getTracks().forEach(t => t.stop()); note('Microphone non démarré : ' + e.message);}
  });
  // 5.6.13 : état de conversation partagé entre le texte et la voix (dossier, pièces jointes, courriel, mission courante, fil)
  const mic = dock.querySelector('#ws-ai-mic');
  window.axiorhubPilot = {
    matter: () => matter.value, attachments: () => [...attachments], mailKey: () => q.get('key') || '',
    selectedDocuments, current: () => currentMission, addTurn, show, note,
    setMic(active) { if (mic) mic.hidden = !active; }
  };
  mic?.querySelector('#ws-ai-mic-stop')?.addEventListener('click', stopVoice);
  const preferences = document.querySelector('#assistant567-preferences');
  preferences?.addEventListener('submit', async event => {
    event.preventDefault(); const f = new FormData(preferences), output = document.querySelector('#assistant567-preferences-status');
    const lexicon = String(f.get('lexicon') || '').split('\n').filter(x => x.trim()).map(x => {const at = x.indexOf('='); if(at<1) return {heard:'',written:''}; return {heard: x.slice(0, at).trim(), written: x.slice(at + 1).trim()};});
    try {profile = await api('profile', {name: f.get('name'), tone: f.get('tone'), length: f.get('length'), initiative:f.get('initiative'), speech_enabled: f.has('speech_enabled'),
      discreet: f.has('discreet'), speech_rate: Number(f.get('speech_rate')), lexicon}); output.textContent = 'Préférences enregistrées.';}
    catch (e) {output.textContent = e.message;}
  });
  window.axiorhubProfile567 = api('profile');   // 5.6.9 : une seule lecture du profil par page (v568.js la réutilise)
  window.axiorhubProfile567.then(p => {profile = p; dock.querySelector('header strong').textContent = p.name + ' · Pilote'; dock.querySelector('#ws-ai-autonomy').value=p.initiative==='proposer' ? 'suggest' : 'prepare';}).catch(() => {});
  const connections = document.querySelector('#connections567-form');
  let catalog = JSON.parse(document.querySelector('#config567-catalog')?.textContent || 'null');
  connections?.addEventListener('submit', async event => {
    event.preventDefault(); const output = document.querySelector('#connections567-status'), values = {};
    for (const field of catalog.fields) {
      const input = [...connections.querySelectorAll('[data-config567]')].find(x => x.dataset.config567 === field.key);
      let value = field.type === 'bool' ? input.checked : input.value;
      if (['port', 'integer'].includes(field.type)) value = Number(value);
      if (['lines', 'paths', 'urls'].includes(field.type)) value = value.split('\n').map(x => x.trim()).filter(Boolean);
      if (field.type === 'secret' ? value.trim() : JSON.stringify(value) !== JSON.stringify(field.value)) values[field.key] = value;
    }
    if (!Object.keys(values).length) {output.textContent = 'Aucun changement à enregistrer.'; return;}
    const button = connections.querySelector('button'); button.disabled = true;
    try {
      const answer = await api('config/save', {revision: catalog.revision, values}); output.textContent = answer.message;
      catalog = await api('config/catalog'); connections.querySelectorAll('input[type=password]').forEach(x => {x.value = ''; x.placeholder = 'Secret configuré ; laisser vide pour le conserver';});
    } catch (e) {output.textContent = e.message;} finally {button.disabled = false;}
  });
  document.querySelectorAll('[data-connector567]').forEach(b => b.addEventListener('click', async () => {
    b.disabled = true; const output = document.querySelector('#connections567-status'); output.textContent = 'Test en cours…';
    try {const value = await api('config/test', {connector: b.dataset.connector567}); output.textContent = (value.ok ? 'Test validé : ' : 'Test non validé : ') + value.message;
      if (value.calendars) {
        const list = document.createElement('ul'); value.calendars.forEach(x => list.append(el('li', (x.name || x.displayname || 'Agenda') + ' — ' + x.url))); output.append(list);
      }
    } catch (e) {output.textContent = e.message;} finally {b.disabled = false;}
  }));
  document.querySelector('[data-docker-plan567]')?.addEventListener('click', async () => {
    const values = {}; document.querySelectorAll('[data-docker567]').forEach(x => {values[x.dataset.docker567] = x.value;});
    try {const plan = await api('config/docker-plan', {values}); document.querySelector('#docker567-plan').textContent = plan.env_example;}
    catch (e) {document.querySelector('#docker567-plan').textContent = e.message;}
  });
  function schedule() {clearTimeout(refreshTimer); refreshTimer = setTimeout(() => {refreshCurrent(); refreshList();}, 350);}
  document.addEventListener('axiorhub:activity', schedule);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) schedule();});
  (function tick() {setTimeout(() => {if (!document.hidden) {refreshCurrent(); refreshList();} tick();}, window.axiorhubLive?.connected ? 60000 : 10000);})();
  window.addEventListener('pagehide', () => {hiddenExit = true; stopAudio(); if (recorder?.state !== 'inactive') recorder?.stop();});
  if (embedded) loadMatters();
  refreshCurrent();
})();
