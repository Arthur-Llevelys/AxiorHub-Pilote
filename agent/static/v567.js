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
  let audioURL = '', hiddenExit = false, profile = {}, refreshTimer = null, refreshBusy = false, planTimer = null, shownStates = {};
  // 5.6.97 (B08) : une tentative au reçu incertain est gardée telle quelle (instantané immuable, même clé) ; « revision » écarte toute réponse
  // arrivée après une action plus récente ; « provisoire » : une carte d'envoi, de refus ou de reçu incertain que le suivi ne remplace pas.
  let tentativeIncertaine = null, revision = 0, provisoire = false, nCarte = 0;
  const extrait = (t, n = 80) => { t = String(t || ''); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
  const cleIncertaine = 'axiorhub5697-incertaine:' + prefix;   // la tentative au reçu incertain survit au rechargement de l'onglet
  const memoIncertaine = {get: () => {try {return JSON.parse(sessionStorage.getItem(cleIncertaine) || 'null');} catch {return null;}},
                          set: v => {try {v ? sessionStorage.setItem(cleIncertaine, JSON.stringify(v)) : sessionStorage.removeItem(cleIncertaine);} catch {}}};
  const note = text => { status.textContent = text; if (pageStatus) pageStatus.textContent = text; };
  const storageKey = 'axiorhub567-last-mission:' + prefix;
  const storage = {get: () => {try {return sessionStorage.getItem(storageKey);} catch {return null;}},
                   set: value => {try {sessionStorage.setItem(storageKey, value);} catch {}}};
  const token = () => crypto.randomUUID ? crypto.randomUUID() : [...crypto.getRandomValues(new Uint8Array(20))].map(x => x.toString(16).padStart(2, '0')).join('');
  async function api(name, data) {
    // 5.6.97 (B08) : un refus confirmé (4xx) se distingue d'un reçu incertain (réseau coupé, 5xx, réponse illisible)
    const incertain = (message, status) => Object.assign(new Error(message), {incertain: true, status: status || 0});
    let response, value;
    try {
      response = await fetch(prefix + '/api440/m567/' + name, {method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin',
        headers: data === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: data === undefined ? undefined : JSON.stringify(data)});
    } catch { throw incertain('Connexion au serveur interrompue.'); }
    try { value = await response.json(); } catch { value = null; }
    if (!response.ok) {
      if (response.status >= 500 || !value) throw incertain((value && (value.message || value.error)) || 'Le serveur n’a pas confirmé (erreur ' + response.status + ').', response.status);
      throw Object.assign(new Error(value.message || value.error || 'Demande refusée.'), {status: response.status});
    }
    if (!value) throw incertain('Réponse du serveur illisible.', response.status);
    return value;
  }
  // 5.6.13 : un seul composant Pilote. Sur « Aujourd'hui », le panneau est monté dans la page (même état, même fil, mêmes limites) ;
  // ailleurs il reste repliable. Replier arrête toujours le micro et la lecture.
  const slot = document.querySelector('#c530-pilot-slot');
  const embedded = !!slot;
  if (embedded) {
    slot.querySelector('[data-pilot-fallback]')?.remove(); slot.append(dock); dock.hidden = false; dock.classList.add('ws-ai-dock--embedded');
    document.querySelector('[data-legacy-composer]')?.setAttribute('hidden', '');
    // 5.6.17 : le bouton robot reste visible et mène au Pilote monté dans la page (la règle globale button{display} rendait « hidden » inopérant).
    launcher.title = 'Aller au Pilote'; launcher.setAttribute('aria-expanded', 'true');
  }
  function stopVoice() { try { window.axiorhubVoice?.stop(); } catch {} stopAudio(); if (recorder?.state !== 'inactive') recorder?.stop(); }
  function open() {dock.hidden = false; launcher.setAttribute('aria-expanded', 'true'); question.focus({preventScroll: true}); refreshCurrent(); loadMatters();}   // 5.6.96 (U01) : sans saut de page
  function close() {stopVoice(); if (embedded) return; dock.hidden = true; launcher.setAttribute('aria-expanded', 'false'); launcher.focus();}
  launcher.addEventListener('click', () => {
    if (embedded) { dock.scrollIntoView({behavior: 'smooth', block: 'start'}); question.focus({preventScroll: true}); refreshCurrent(); return; }
    dock.hidden ? open() : close();
  });
  dock.querySelector('#ws-ai-close').addEventListener('click', close);
  // 5.6.19 : fenêtre flottante large par défaut (mémorisée par navigateur) ; le bouton ⤢ la réduit.
  const wideKey = 'axiorhub567-wide:' + prefix; let wideOn = true;
  try { wideOn = localStorage.getItem(wideKey) !== '0'; } catch {}
  if (wideOn && !embedded) { dock.classList.add('ws-ai-dock--wide'); wide?.setAttribute('aria-pressed', 'true'); }
  wide?.addEventListener('click', () => {const on = dock.classList.toggle('ws-ai-dock--wide'); wide.setAttribute('aria-pressed', String(on)); try { localStorage.setItem(wideKey, on ? '1' : '0'); } catch {}});
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !dock.hidden && !embedded) close();
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && document.activeElement === question) {event.preventDefault(); if (!event.repeat) send.click();}   // 5.6.97 (B08) : touche maintenue : un seul envoi
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
    const kept = keep ? [...matter.options].find(o => o.value === keep) : null;   // 5.6.96 (U06) : dossier de la page, gardé même hors de la liste chargée
    const rows = mattersData.filter(m => !f || m.search.includes(f));
    matter.replaceChildren(new Option('Tout le cabinet', ''));
    const recent = rows.filter(m => m.recent), others = rows.filter(m => !m.recent);
    const group = (label, items) => { if (!items.length) return; const g = document.createElement('optgroup'); g.label = label;
      items.forEach(m => g.append(new Option(m.label + (m.adversaries?.length ? ' · c/ ' + m.adversaries.join(', ') : ''), m.id))); matter.append(g); };
    group('Dossiers récents', recent); group('Autres dossiers', others);
    if (kept && ![...matter.options].some(o => o.value === keep)) matter.append(new Option(kept.textContent, keep));
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
    if (attachments.length + files.length > 20 || files.some(x => x.size < 1 || x.size > 30000000)) {note('Vingt documents maximum, 30 Mo chacun.'); return;}
    uploadBusy = true; send.disabled = true; note('Extraction locale des documents…');
    try {
      let done = 0;
      for (const f of files) {
        note('Extraction locale… document ' + (++done) + ' sur ' + files.length);
        const r = await fetch(prefix + '/assistant/attachment', {method: 'POST', credentials: 'same-origin', body: f,
          headers: {'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrf,
                    'X-Attachment-Name': encodeURIComponent(f.name), 'X-Attachment-Matter': scope}});
        const data = await r.json(); if (!r.ok || !data.attachment_id) throw new Error(data.error || 'Extraction impossible.');
        if (scope !== matter.value) throw new Error('Dossier changé pendant l’import : recommencez.');
        attachments.push(data.attachment_id);
      }
      note(files.length + ' document(s) prêt(s). Seuls les passages utiles à votre demande iront à l’IA ; demandez une « synthèse complète » pour tout faire lire.');
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
  // 5.6.88 : carte de situation d'une société, même contenu que societe5688.carte_html ; textContent seulement, adresses https seulement
  function societeCard(s) {
    const labels = {alerte: 'Alerte', attention: 'À lire', non_verifie: 'Non vérifié', rien_trouve: 'Rien trouvé dans ces sources', incertaine: 'Identification incertaine', inconnue: 'Inconnue de l’annuaire'};
    const box = el('section', undefined, 'so88-carte so88-v-' + s.verdict); box.setAttribute('aria-label', 'Situation de la société');
    const h = el('h3', s.nom); if (s.siren) h.append(' ', el('small', 'SIREN ' + s.siren)); h.append(' ', el('span', labels[s.verdict] || s.verdict, 'so88-etat')); box.append(h);
    const link = u => {if (!/^https:\/\//.test(u || '')) return null; const a = el('a', 'Voir la source'); a.href = u; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;};
    if ((s.alertes || []).length) {
      const ul = el('ul', undefined, 'so88-alertes'); ul.setAttribute('role', 'list');
      s.alertes.forEach(a => {const li = el('li', a.texte + ' ', 'so88-' + a.niveau), u = link(a.lien); if (u) li.append(u); ul.append(li);}); box.append(ul);
    }
    if ((s.points || []).length) {
      const dl = el('dl', undefined, 'so88-points');
      s.points.forEach(p => {const row = el('div', undefined, 'so88-' + p.etat), dd = el('dd', p.texte + ' '), u = link(p.lien); dd.append(el('small', p.date || '')); if (u) dd.append(' ', u); row.append(el('dt', p.titre), dd); dl.append(row);});
      box.append(dl);
    }
    return box;
  }

  function card(m) {
    const c = el('article', undefined, 'mission567-card'); c.dataset.missionId = m.id; c.dataset.etat = m.panneau?.code || '';
    c.append(el('div', m.panneau?.libelle || m.label, 'mission567-state'), el('h3', m.title || m.instruction));   // 5.6.97 : état explicite, en mots
    if (m.panneau?.geste) c.append(el('p', m.panneau.geste, 'mission5697-geste'));
    const choix97 = new Map(); if (m.state === 'decision') (m.exceptions || []).forEach(x => (x.candidates || []).forEach(y => choix97.set(y.id, y.label)));
    if (choix97.size) c.append(choixDossier(m, choix97));   // 5.6.97 : le choix attendu en tête de carte, visible sans défiler
    if (m.subtitle) c.append(el('p', m.subtitle, 'mission567-sub'));   // 5.6.61 : action de l'agent, sous l'objet
    c.append(el('p', m.matter_label || 'Cabinet'));
    // 5.6.89 (B11) : la ligne du plan dit ce qui est demandé ; seule une preuve annonce un dépôt
    const lignes = m.lignes || {demande: m.plan.deliverable ? 'Résultat demandé : ' + m.plan.deliverable : ''};
    if (m.job?.progress) c.append(el('p', m.job.progress, 'muted'));
    if (lignes.demande) c.append(el('p', lignes.demande, 'muted'));
    if (lignes.prepare) c.append(el('p', lignes.prepare, 'ws-ai-result-meta'));
    if (lignes.depot) c.append(el('p', '✓ ' + lignes.depot, 'ws-ai-result-meta'));
    const r = m.result || {};
    if (r.text) {const d = el('details'); d.append(el('summary', 'Lire la réponse et ses sources'), el('pre', r.text)); c.append(d);}
    (r.sources || []).filter(x => x && x.kind === 'situation_societe').forEach(x => c.append(societeCard(x)));   // 5.6.88 : contrôle posé par le logiciel
    if (m.choix_ia) {   // 5.6.82 : ce qui a été choisi et pourquoi, sans raisonnement interne
      const x = m.choix_ia, d = el('details', undefined, 'm82-choix');
      d.append(el('summary', 'Choix de l’IA'), el('p', x.modele + ' · effort ' + x.effort + (x.provisoire ? ' · choix provisoire, non encore mesuré au banc' : '')), el('p', x.raison),
               el('p', (x.canal ? x.canal + ' · ' : '') + 'consommation : ' + x.consommation));
      c.append(d);
    }
    window.axiorhubIa5689?.carte(m, c);   // 5.6.89 : label IA : sources, limites, fournisseur, question regroupée
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
      (m.exceptions || []).forEach(x => c.append(el('p', x.message)));   // 5.6.97 : le choix lui-même est en tête de carte (choixDossier)
      // 5.6.96 (R03, lot affichage) : recherche publique proposée : un clic renvoie le texte exact ; rien ne part sans ce clic
      (m.exceptions || []).filter(x => x.code === 'recherche_publique_a_confirmer' && x.requete).forEach(x => {
        const b = el('button', 'Lancer la recherche publique « ' + x.requete + ' »', 'ws-ai-confirmer'); b.type = 'button';
        b.dataset.rechercheConfirmer = x.requete; b.dataset.rechercheInstruction = m.instruction || ''; c.append(b);
      });
    }
    if (m.state === 'error' || m.state === 'interrupted') c.append(el('p', lignes.echec || m.job?.error || (m.exceptions || []).map(x => x.message).join(' '), 'warn'));
    if (['creating', 'queued', 'running', 'error', 'paused','suggested','interrupted'].includes(m.state)) {
      const d = el('details'), active = ['creating', 'queued', 'running'].includes(m.state), b = el('button', active ? 'Suspendre' : (m.state==='suggested' ? 'Démarrer la préparation' : (m.state==='error' ? 'Relancer' : 'Reprendre')));
      b.type = 'button'; b.dataset.missionControl = active ? 'pause' : 'resume'; if (['suggested', 'error', 'interrupted'].includes(m.state)) c.append(b); else {d.append(el('summary', 'Autres actions'), b); c.append(d);}
    }
    const d = el('details'), list = el('ol'); m.plan.steps.forEach(x => list.append(el('li', x)));
    d.append(el('summary', 'Plan, sources et limites'), list, el('p', m.plan.restrictions.join(' · ')));
    if (m.plan.intent_reasons?.length) d.append(el('p', 'Résultat demandé : ' + m.plan.deliverable + ' — ' + m.plan.intent_reasons.join(' ')));
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
    (r.sources || []).filter(x => !(x && x.kind === 'situation_societe')).slice(0, 30).forEach(x => d.append(el('p', typeof x === 'string' ? x : JSON.stringify(x))));
    (r.notes || []).forEach(x => d.append(el('p', x, 'muted')));
    if (r.proof) d.append(el('p', 'Relecture : ' + r.proof)); c.append(d);
    // 5.6.97 : la carte rafraîchie garde « Annuler la mission » (même bouton que la page Missions ; un projet déjà déposé reste dans le dossier)
    if (m.id && !m.complex && !['cancelled', 'verified', 'prepared', 'abstained'].includes(m.state)) {
      const x = el('button', 'Annuler la mission', 'ghost'); x.type = 'button'; x.dataset.mission5636 = 'annuler';
      x.dataset.confirm = 'Annuler définitivement cette mission ? Un projet déjà déposé reste dans le dossier.'; c.append(x);
    }
    if (m.id) c.append(details(m)); return c;
  }
  // 5.6.97 : choix du dossier dans la carte « Choix requis » : aucun dossier présélectionné, bouton actif après le choix, filtre au-delà de 8
  function choixDossier(m, choices) {
    const box = el('div', undefined, 'mission5697-choix'), id = 'm97-choix-' + (++nCarte);
    box.append(el('p', 'Votre demande « ' + extrait(m.instruction) + ' » repartira telle quelle dans le dossier choisi.', 'muted'));
    const label = el('label', 'Dossier de cette demande'); label.htmlFor = id;
    const s = el('select', undefined, 'mission567-choice'); s.id = id; s.required = true;
    const b = el('button', 'Lancer dans ce dossier'); b.type = 'button'; b.dataset.missionControl = 'resolve'; b.disabled = true;
    const remplir = f => {
      const garde = s.value; s.replaceChildren(new Option('Choisir le dossier…', ''));
      choices.forEach((libelle, v) => { if (!f || libelle.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').includes(f)) s.append(new Option(libelle, v)); });
      if ([...s.options].some(o => o.value === garde)) s.value = garde; b.disabled = !s.value;
    };
    remplir('');
    s.addEventListener('change', () => { b.disabled = !s.value; });
    box.append(label);
    if (choices.size > 8) {
      const f = el('input', undefined, 'mission5697-filtre'); f.type = 'search'; f.placeholder = 'Filtrer : client, adversaire, référence'; f.setAttribute('aria-label', 'Filtrer les dossiers');
      f.addEventListener('input', () => remplir(f.value.trim().toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, ''))); box.append(f);
    }
    box.append(s, b); return box;
  }
  // 5.6.97 : identifiants de la demande, de la mission et du travail, horodatages et dernier état vérifié (aucun texte confidentiel en plus)
  function details(m) {
    const d = el('details', undefined, 'mission5697-details'), dl = el('dl');
    const quand = v => { const x = new Date(v); return isNaN(x) ? '' : x.toLocaleString('fr-FR'); };
    [['Demande', m.request_key], ['Mission', m.id], ['Travail', m.job?.id ? 'n° ' + m.job.id + ' · ' + m.job.status : 'aucun pour l’instant'],
     ['Reçue le', quand(m.created)], ['Mise à jour', quand(m.updated)], ['Dernier état vérifié', (m.panneau?.libelle || m.label) + (m.verifie_a ? ' · ' + quand(m.verifie_a) : '')]]
      .forEach(([k, v]) => { if (v) dl.append(el('dt', k), el('dd', String(v))); });
    d.append(el('summary', 'Détails'), dl); return d;
  }
  // 5.6.97 (B08) : cartes posées par le navigateur avant tout reçu : envoi en cours, refus confirmé, reçu incertain
  function carteProvisoire(code, libelle, instruction, texte, classe) {
    const c = el('article', undefined, 'mission567-card'); c.dataset.etat = code;
    c.append(el('div', libelle, 'mission567-state'), el('h3', extrait(instruction, 300)));
    if (texte) c.append(el('p', texte, classe || 'mission5697-geste'));
    return c;
  }
  function afficherProvisoire(c) { provisoire = true; current.replaceChildren(c); }
  // 5.6.13 : fil partagé texte–voix. Le rafraîchissement du résultat courant ne remplace jamais l'historique.
  function addTurn(instruction, text, channel) {
    if (!history) return;
    const turn = el('article', undefined, 'ws-ai-turn'); turn.append(el('p', (channel === 'voice' ? '🎙 ' : '✎ ') + instruction, 'ws-ai-turn-me'), el('pre', text));
    history.prepend(turn); while (history.children.length > 30) history.lastChild.remove();
  }
  function show(m, focus) {
    provisoire = false; currentMission = m; storage.set(m.id); current.replaceChildren(card(m)); note(m.panneau?.libelle || m.label);
    const choix = focus && m.state === 'decision' && current.querySelector('.mission567-choice');   // 5.6.97 : le choix attendu reçoit le focus, dans la vue du volet
    if (choix) { choix.closest('.mission5697-choix').scrollIntoView({block: 'nearest'}); choix.focus({preventScroll: true}); }
    const final = ['answered', 'verified', 'prepared', 'abstained', 'error', 'interrupted'].includes(m.state);
    if (final && shownStates[m.id] !== m.state) { shownStates[m.id] = m.state; if (m.context?.channel !== 'voice') addTurn(m.instruction, (m.result?.text || m.result?.message || m.label), 'text'); }
    send.textContent = 'Envoyer l’instruction';   // 5.6.19 : libellé stable ; l'état de la mission est dans la carte et la note
  }
  async function refreshCurrent() {
    const id = currentMission?.id || storage.get(); if (!id || document.hidden || sending || provisoire) return;
    const rev = revision;   // 5.6.97 (B08) : une réponse de suivi devenue ancienne ne remplace ni la carte courante ni le brouillon
    try {const m = await api('mission?id=' + encodeURIComponent(id)); if (rev !== revision || sending || provisoire || (currentMission && currentMission.id !== id)) return;
      // 5.6.97 : un choix de dossier en cours n'est pas effacé par le suivi tant que la demande attend ce choix
      if (m.state === 'decision' && currentMission?.state === 'decision' && (current.contains(document.activeElement) || current.querySelector('.mission567-choice')?.value)) {currentMission = m; return;}
      show(m);}
    catch { /* Un autre compte ne récupère pas cette mission. */ }
  }
  async function refreshList() {
    const list = document.querySelector('#mission567-list'); if (!list || document.hidden || refreshBusy) return;
    refreshBusy = true;
    try {const data = await api('missions'); list.replaceChildren(...data.missions.map(card));} catch (e) {note(e.message);} finally {refreshBusy = false;}
  }
  function lireComposeur() {
    return {instruction: question.value.trim(), matter: matter.value, attachments: [...attachments], intent: intentSelect?.value || 'auto',
      autonomy:dock.querySelector('#ws-ai-autonomy').value, due: dock.querySelector('#ws-ai-due').value, context: {page: cfg.path || '/', selected_documents: selectedDocuments(), mission_id: currentMission?.matter === matter.value ? currentMission.id : '',
      mail_key: q.get('key') || '', ui: window.axiorhubIa5689?.contexte()}, channel: inputChannel,
      portee: dock.querySelector('#ws-ai-general')?.getAttribute('aria-pressed') === 'true' ? 'general' : ''};   // 5.6.96 (R01) : mode Général
  }
  const empreinte = p => JSON.stringify([p.instruction, p.matter, p.attachments, p.intent, p.autonomy, p.due, p.channel, p.portee, p.context?.mail_key || '']);
  const gele = p => Object.freeze({...p, attachments: Object.freeze([...p.attachments]), context: Object.freeze({...p.context})});
  // 5.6.97 (B08) : « nouvelle » = le composeur, avec une NOUVELLE clé (sauf s'il est identique à la tentative incertaine : même clé, aucun doublon) ;
  // « reprendre » = l'ancienne tentative, inchangée, même clé. Un refus confirmé libère la clé ; un reçu incertain propose les deux gestes.
  async function envoyer(mode) {
    if (sending || uploadBusy) return;
    let payload;
    if (mode === 'reprendre') { if (!tentativeIncertaine) return; payload = tentativeIncertaine.payload; }
    else {
      const champs = lireComposeur(); if (!champs.instruction) {note('Décrivez le résultat attendu.'); question.focus(); return;}
      payload = tentativeIncertaine && empreinte(champs) === tentativeIncertaine.empreinte ? tentativeIncertaine.payload : gele({...champs, request_key: token()});
    }
    const ancienne = tentativeIncertaine && tentativeIncertaine.payload !== payload ? tentativeIncertaine : null;
    const rev = ++revision; sending = true; send.disabled = true;
    afficherProvisoire(carteProvisoire('envoi', 'Envoi en cours', payload.instruction, 'Transmission au serveur… Le texte reste dans le champ tant que le serveur n’a pas confirmé la réception.'));
    note('Envoi en cours…');
    try {
      // 5.6.80 : si l'IA tarde, les passages trouvés sans modèle s'affichent après 4 s
      const asked = payload.instruction, scope5680 = payload.matter;
      setTimeout(() => {if (currentMission && ['creating','queued','running'].includes(currentMission.state)) window.axiorhubPassages?.(asked, scope5680, current);}, 4000);
      const m = await api('mission/create', payload);
      if (rev !== revision) return;
      tentativeIncertaine = null; memoIncertaine.set(null);
      if (question.value.trim() === payload.instruction) { question.value = ''; inputChannel = 'text'; }   // reçue : le texte passe dans la carte
      attachments = []; file.value = ''; renderFiles(); if (plan) plan.hidden = true; show(m, true); refreshList();
      if (ancienne) avertirAncienne(ancienne);
    } catch (e) {
      if (rev !== revision) return;
      if (e.incertain) {
        if (!tentativeIncertaine || tentativeIncertaine.payload !== payload) tentativeIncertaine = {payload, empreinte: empreinte(payload)};
        memoIncertaine.set(payload); afficherIncertain(e);
      } else {
        if (tentativeIncertaine?.payload === payload) { tentativeIncertaine = null; memoIncertaine.set(null); }
        afficherProvisoire(carteProvisoire('refusee', 'Refusée', payload.instruction, e.message + ' Rien n’a été enregistré : corrigez le texte puis envoyez à nouveau (nouvelle demande).', 'warn'));
        note('Refusée : ' + e.message); question.focus({preventScroll: true});
      }
    } finally {sending = false; send.disabled = uploadBusy; send.textContent = 'Envoyer l’instruction';}
  }
  function afficherIncertain(e) {
    const t = tentativeIncertaine, c = carteProvisoire('incertaine', 'Envoi à vérifier', t.payload.instruction,
      e.message + ' La demande a peut-être été reçue. Vérifiez-la avant d’en envoyer une autre : la vérification ne crée jamais de doublon.', 'warn');
    const reprendre = el('button', 'Vérifier / reprendre l’ancienne demande « ' + extrait(t.payload.instruction, 60) + ' »'); reprendre.type = 'button'; reprendre.dataset.aiReprendre = '1';
    const modifiee = el('button', 'Envoyer la demande modifiée', 'ghost'); modifiee.type = 'button'; modifiee.dataset.aiModifiee = '1';
    const maj = () => { if (!modifiee.isConnected) { question.removeEventListener('input', maj); return; } modifiee.hidden = empreinte(lireComposeur()) === t.empreinte; };
    c.append(reprendre, ' ', modifiee); afficherProvisoire(c);
    maj(); question.addEventListener('input', maj);
    note('Envoi à vérifier : ' + e.message);
  }
  async function avertirAncienne(ancienne) {   // 5.6.97 (B08) : la demande modifiée part avec une nouvelle clé ; l'ancienne, si elle a été reçue, est signalée
    try {
      const data = await api('missions');
      const recue = (data.missions || []).find(x => x.request_key === ancienne.payload.request_key);
      if (recue) note('Attention : l’ancienne demande « ' + extrait(ancienne.payload.instruction, 60) + ' » avait aussi été reçue (' + (recue.panneau?.libelle || recue.label) + '). Annulez-la dans Missions si elle n’est plus utile.');
    } catch { note('L’ancienne demande n’a pas pu être vérifiée : consultez Missions.'); }
  }
  send.addEventListener('click', () => envoyer('nouvelle'));
  document.addEventListener('click', async event => {
    const confirmer = event.target.closest('[data-recherche-confirmer]');   // 5.6.96 (R03) : confirmation explicite, un seul envoi
    if (confirmer) {
      if (confirmer.disabled) return; confirmer.disabled = true;
      try { const m = await api('mission/create', {request_key: token(), instruction: confirmer.dataset.rechercheInstruction, confirmer_recherche: confirmer.dataset.rechercheConfirmer,
        matter: '', attachments: [], intent: 'auto', autonomy: dock.querySelector('#ws-ai-autonomy').value, due: '', context: {page: cfg.path || '/'}, channel: 'text'});
        show(m); refreshList(); }
      catch (e) { confirmer.disabled = false; note(e.message); }
      return;
    }
    if (event.target.closest('[data-ai-reprendre]')) { envoyer('reprendre'); return; }   // 5.6.97 (B08)
    if (event.target.closest('[data-ai-modifiee]')) { envoyer('nouvelle'); return; }
    const rule = event.target.closest('[data-rule-decide]');
    if (rule) { rule.disabled = true; try { const v = await api('rule/decide', {id: rule.dataset.ruleDecide, action: rule.dataset.ruleAction}); note(v.message); rule.closest('p')?.remove(); } catch (e) { note(e.message); rule.disabled = false; } return; }
    const b = event.target.closest('[data-mission-control]'); if (!b) return;
    const c = b.closest('[data-mission-id]'), choix = c.querySelector('.mission567-choice');
    if (b.dataset.missionControl === 'resolve' && !choix?.value) { note('Choisissez d’abord le dossier de cette demande.'); choix?.focus(); return; }
    b.disabled = true; const rev = ++revision;   // 5.6.97 (B08) : un suivi lancé avant ce geste ne remplace pas son résultat
    try {const m = await api('mission/control', {id: c.dataset.missionId, action: b.dataset.missionControl,
      matter: choix?.value || ''}); if (rev === revision && currentMission?.id === m.id) show(m); await refreshList();}
    catch (e) {note(e.message);} finally {b.disabled = false;}
  });
  function stopAudio() {window.axiorhubVoix?.stop(); if (liveAudio) {liveAudio.pause(); liveAudio.src = ''; liveAudio = null;} if (audioURL) {URL.revokeObjectURL(audioURL); audioURL = '';}}
  async function read(text) {
    stopAudio(); if (!text) {note('Aucun texte à lire.'); return;} note('Préparation de la lecture vocale…');
    // 5.6.94 (V03, V05) : lecture commune (Brindille dans le navigateur, sinon voix du serveur), phrase par phrase, coupée par « Arrêter »
    if (window.axiorhubVoix) {try {await window.axiorhubVoix.speak(text, {matter: matter.value, note});} catch (e) {note(e.message);} return;}
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
  dock.querySelector('[data-audio-pause567]').addEventListener('click', () => {if (window.axiorhubVoix?.speaking()) {window.axiorhubVoix.togglePause(); return;} if (liveAudio) liveAudio.paused ? liveAudio.play().catch(e => note(e.message)) : liveAudio.pause();});
  dock.querySelector('[data-audio-stop567]').addEventListener('click', stopAudio);
  dock.querySelector('#ws-ai-clear')?.addEventListener('click', () => {   // 5.6.19 : effacer l'affichage, pas l'historique des missions
    stopAudio(); current.replaceChildren(); history.replaceChildren(); currentMission = null; storage.set(''); shownStates = {}; revision++; provisoire = false; tentativeIncertaine = null; memoIncertaine.set(null);
    note('Conversation effacée. Les missions restent consultables dans Outils › Missions.');
  });
  dock.querySelector('#ws-ai-dictate')?.addEventListener('click', async event => {
    const button = event.currentTarget; stopAudio();
    if (recorder && recorder.state !== 'inactive') {recorder.stop(); return;}
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {note('Microphone indisponible dans ce navigateur.'); return;}
    let stream;
    try {
      stream = await (window.axiorhubMic ? window.axiorhubMic.stream('dictee', {audio: true}) : navigator.mediaDevices.getUserMedia({audio: true}));   // 5.6.94 (V07) : voix isolée par Alkove
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
      rec.start(1000); button.textContent = '⏹ Arrêter la dictée'; window.axiorhubPilot.setMic(true); note('Microphone actif. La voix utilise les mêmes droits que le texte.' + (stream.axiorhubNote ? ' ' + stream.axiorhubNote : ''));
    } catch (e) {stream?.getTracks().forEach(t => t.stop()); note('Microphone non démarré : ' + e.message);}
  });
  // 5.6.13 : état de conversation partagé entre le texte et la voix (dossier, pièces jointes, courriel, mission courante, fil)
  const mic = dock.querySelector('#ws-ai-mic');
  // 5.6.80 : un clic fait pendant le chargement (v5680.js) a déjà ouvert le panneau : on termine son ouverture
  if (!dock.hidden && !embedded && window.__axPending) {launcher.setAttribute('aria-expanded', 'true'); question.focus({preventScroll: true}); refreshCurrent(); loadMatters();}
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
      // 5.6.94 : sur Paramètres › Voix, la vitesse se règle dans la section Voix ; son absence ne la remet pas à 0,7
      discreet: f.has('discreet'), ...(f.has('speech_rate') ? {speech_rate: Number(f.get('speech_rate'))} : {}), lexicon}); output.textContent = 'Préférences enregistrées.';}
    catch (e) {output.textContent = e.message;}
  });
  window.axiorhubProfile567 = api('profile');   // 5.6.9 : une seule lecture du profil par page (v568.js la réutilise)
  window.axiorhubProfile567.then(p => {profile = p; const titre = dock.querySelector('#ws-ai-titre'); if (titre) titre.textContent = 'Assistant AxiorHub' + (p.name && p.name !== 'Pilote' ? ' · ' + p.name : ''); dock.querySelector('#ws-ai-autonomy').value=p.initiative==='proposer' ? 'suggest' : 'prepare';}).catch(() => {});
  const connections = document.querySelector('#connections567-form');
  let catalog = JSON.parse(document.querySelector('#config567-catalog')?.textContent || 'null');
  connections?.addEventListener('submit', async event => {
    event.preventDefault(); const output = document.querySelector('#connections567-status'), values = {};
    for (const field of catalog.fields) {
      const input = [...connections.querySelectorAll('[data-config567]')].find(x => x.dataset.config567 === field.key);
      if (!input) continue;   // 5.6.30 : le catalogue relu après enregistrement contient aussi des réglages absents de cette page
      let value = field.type === 'bool' ? input.checked : input.value;
      if (['port', 'integer'].includes(field.type)) value = Number(value);
      if (['lines', 'paths', 'urls'].includes(field.type)) value = value.split('\n').map(x => x.trim()).filter(Boolean);
      if (field.type === 'secret' ? value.trim() : JSON.stringify(value) !== JSON.stringify(field.value)) values[field.key] = value;
    }
    if (!Object.keys(values).length) {output.textContent = 'Aucun changement à enregistrer.'; return;}
    const button = connections.querySelector('button[type=submit]') || [...connections.querySelectorAll('button')].pop(); button.disabled = true;
    output.textContent = 'Enregistrement…';
    try {
      const answer = await api('config/save', {revision: catalog.revision, values}); output.textContent = answer.message;
      catalog = await api('config/catalog'); connections.querySelectorAll('input[type=password]').forEach(x => {x.value = ''; x.placeholder = 'Secret configuré ; laisser vide pour le conserver';});
    } catch (e) {output.textContent = e.message;} finally {button.disabled = false;}
  });
  // 5.6.24 : choisir le dossier de travail local (navigation dans les dossiers, aucun fichier lu ; l'enregistrement reste explicite)
  document.querySelectorAll('[data-browse567]').forEach(b => b.addEventListener('click', async () => {
    const input = [...document.querySelectorAll('[data-config567]')].find(x => x.dataset.config567 === b.dataset.browse567);
    if (!input) return;
    const box = el('dialog', undefined, 'browse567'), where = el('p', 'Lecture…', 'browse567-path'), list = el('ul', undefined, 'browse567-list');
    const roots = el('div', undefined, 'browse567-roots'), actions = el('div', undefined, 'browse567-actions');
    const choose = el('button', 'Choisir ce dossier'), cancel = el('button', 'Annuler', 'ghost');
    choose.type = 'button'; cancel.type = 'button'; actions.append(choose, cancel);
    box.setAttribute('aria-label', 'Choisir le dossier de travail');
    box.append(el('h3', 'Choisir le dossier de travail'), roots, where, list, actions); document.body.append(box);
    let current = '';
    const close = () => { box.close(); box.remove(); b.focus(); };
    const load = async path => {
      try {
        const r = await api('config/browse' + (path ? '?path=' + encodeURIComponent(path) : ''));
        current = r.path; where.textContent = r.path; list.replaceChildren(); roots.replaceChildren();
        r.roots.forEach(x => { const k = el('button', x.label, 'ghost'); k.type = 'button'; k.addEventListener('click', () => load(x.path)); roots.append(k); });
        if (r.parent) { const up = el('button', '⬆ Dossier parent', 'ghost'); up.type = 'button'; up.addEventListener('click', () => load(r.parent)); list.append(el('li')); list.lastChild.append(up); }
        r.dirs.forEach(d => { const k = el('button', '📁 ' + d.name, 'browse567-dir'); k.type = 'button'; k.addEventListener('click', () => load(d.path)); const li = el('li'); li.append(k); list.append(li); });
        if (!r.dirs.length) list.append(el('li', 'Aucun sous-dossier.'));
        if (r.truncated) list.append(el('li', 'Liste limitée à 500 dossiers.'));
      } catch (e) { where.textContent = e.message; }
    };
    choose.addEventListener('click', () => { if (current) { input.value = current; input.dispatchEvent(new Event('input', {bubbles: true})); }
      close(); const out = document.querySelector('#connections567-status'); if (out) out.textContent = 'Dossier choisi : ' + current + '. Cliquez sur « Enregistrer les changements ».'; });
    cancel.addEventListener('click', close); box.addEventListener('cancel', e => { e.preventDefault(); close(); });
    if (box.showModal) box.showModal(); else box.setAttribute('open', '');
    await load(input.value.trim());
  }));
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
  // 5.6.97 (B08) : après un rechargement, une demande au reçu incertain est de nouveau proposée à la vérification (même clé), texte remis
  const incertaine = memoIncertaine.get();
  if (incertaine && typeof incertaine.instruction === 'string' && incertaine.request_key && Array.isArray(incertaine.attachments) && incertaine.context) {
    tentativeIncertaine = {payload: gele(incertaine), empreinte: empreinte(incertaine)};
    if (!question.value.trim()) question.value = incertaine.instruction;
    afficherIncertain(new Error('La réception de la dernière demande n’avait pas été confirmée avant le rechargement.'));
  }
  refreshCurrent();
})();
