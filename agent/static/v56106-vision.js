/* 5.6.106 (lot I) : « Joindre une image ou une page » dans l'assistant (volet ws-ai-dock).
   Fichiers du dossier choisi (PDF, PNG, JPEG), page d'un PDF avec sa vignette, aperçu de ce qui partira (image locale, image masquée avec
   le nombre de zones noircies, ou texte seul avec le motif) AVANT l'envoi. Le choix est lu par v567.js (window.axh56106vision.pourEnvoi())
   au moment de l'envoi ; le serveur revérifie dossier, chemins et droits. Aucun HTML venant du serveur n'est injecté (textContent seul). */
(() => {
  'use strict';
  const dock = document.querySelector('#ws-ai-dock');
  const cfg = JSON.parse(document.querySelector('#ws-ai-config')?.textContent || '{}');
  if (!dock || !cfg.mission_api) return;
  const prefix = cfg.prefix || '', csrf = cfg.csrf || '', MAX = 4;
  const matter = dock.querySelector('#ws-ai-matter'), send = dock.querySelector('#ws-ai-send'), question = dock.querySelector('#ws-ai-question');
  const row = dock.querySelector('.ws-ai-sendrow');
  if (!matter || !send || !row) return;
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  let choisies = [];          // [{chemin, nom, page, etat}]
  let dossierChoix = matter.value;

  // ------------------------------------------------------------------ éléments du volet
  const bouton = el('button', '🖼 Joindre une image', 'v106i-joindre');   // libellé court : la rangée d'envoi reste sur une ligne
  bouton.type = 'button';
  bouton.setAttribute('aria-label', 'Joindre une image ou une page de PDF');
  bouton.title = 'Joindre une image ou une page de PDF des fichiers du dossier (masquée avant tout envoi à un fournisseur externe)';
  row.insertBefore(bouton, send);
  const liste = el('ul', undefined, 'v106i-choix');
  liste.setAttribute('aria-label', 'Images jointes à la prochaine demande');
  liste.hidden = true;
  (dock.querySelector('#ws-ai-files') || question).insertAdjacentElement('afterend', liste);
  const avis = el('p', '', 'v106i-avis');
  avis.setAttribute('role', 'status');
  liste.insertAdjacentElement('afterend', avis);

  // ------------------------------------------------------------------ boîte de dialogue
  const dlg = el('dialog', undefined, 'v106i-dialog');
  dlg.setAttribute('aria-labelledby', 'v106i-titre');
  const titre = el('h2', 'Joindre une image ou une page', '');
  titre.id = 'v106i-titre';
  const aide = el('p', 'Fichiers du dossier (PDF, PNG, JPEG). Modèle local : l’image part telle quelle. Fournisseur externe : elle est masquée avant l’envoi ; '
    + 'si le masquage échoue, rien ne part. Le masquage n’est pas parfait (écriture manuscrite, logos, tampons) : vérifiez l’aperçu.', 'v106i-aide');
  const lFichier = el('label', 'Fichier ');
  const choixFichier = el('select');
  lFichier.append(choixFichier);
  const lPage = el('label', 'Page ');
  const page = el('input');
  page.type = 'number'; page.min = '1'; page.value = '1'; page.size = 5;
  const total = el('span', '', 'v106i-total');
  lPage.append(page, total);
  const vignette = el('img', undefined, 'v106i-vignette');
  vignette.alt = 'Vignette de la page choisie';
  vignette.hidden = true;
  const etat = el('p', '', 'v106i-etat');
  etat.setAttribute('role', 'status');
  const apercu = el('div', undefined, 'v106i-apercu');
  const voir = el('button', 'Voir ce qui sera envoyé', 'ax-btn ghost');
  voir.type = 'button';
  const ajouter = el('button', 'Joindre à la demande', 'ax-btn');
  ajouter.type = 'button';
  const fermer = el('button', 'Fermer', 'ax-btn ghost');
  fermer.type = 'button';
  const actions = el('div', undefined, 'v106i-actions');
  actions.append(voir, ajouter, fermer);
  dlg.append(titre, aide, lFichier, lPage, vignette, etat, apercu, actions);
  dock.append(dlg);   // dans le volet : son piège de focus (v5696-affichage.js) reste cohérent ; showModal la place au premier plan

  async function lire(route, params) {
    const r = await fetch(prefix + '/api440/m56106/vision/' + route + '?' + new URLSearchParams(params), {credentials: 'same-origin'});
    const v = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(v.message || 'Demande refusée.');
    return v;
  }
  async function poster(route, data) {
    const r = await fetch(prefix + '/api440/m56106/vision/' + route, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)});
    const v = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(v.message || 'Demande refusée.');
    return v;
  }
  const estPdf = c => /\.pdf$/i.test(c || '');
  function majVignette() {
    const c = choixFichier.value;
    if (!c) { vignette.hidden = true; return; }
    vignette.hidden = false;
    vignette.src = prefix + '/api440/m56106/vision/vignette?' + new URLSearchParams({d: matter.value, p: c, page: page.value || '1'});
  }
  vignette.addEventListener('error', () => { vignette.hidden = true; etat.textContent = 'Vignette indisponible (outil de rendu absent ou page illisible).'; });
  async function ouvrir() {
    if (!matter.value) { avis.textContent = 'Choisissez d’abord un dossier : les images viennent des fichiers du dossier.'; return; }
    if (choisies.length >= MAX) { avis.textContent = 'Quatre images au plus par demande.'; return; }
    choixFichier.replaceChildren(); apercu.replaceChildren(); etat.textContent = 'Chargement des fichiers…';
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
    try {
      const v = await lire('fichiers', {d: matter.value});
      if (!v.fichiers.length) { etat.textContent = 'Aucun PDF ni image connu dans ce dossier.'; return; }
      v.fichiers.forEach(f => { const o = el('option', f.nom + (f.illisible ? ' (illisible)' : '')); o.value = f.chemin; choixFichier.append(o); });
      etat.textContent = v.outils && !v.outils.rendu ? 'Outil de rendu des pages absent : seules les petites images PNG ou JPEG peuvent être jointes, pour un modèle local.' : '';
      await choisirFichier();
    } catch (e) { etat.textContent = e.message; }
    choixFichier.focus();
  }
  async function choisirFichier() {
    apercu.replaceChildren(); page.value = '1'; total.textContent = '';
    lPage.hidden = !estPdf(choixFichier.value);
    if (estPdf(choixFichier.value)) {
      try { const v = await lire('pages', {d: matter.value, p: choixFichier.value}); page.max = String(v.pages); total.textContent = ' sur ' + v.pages; }
      catch (e) { etat.textContent = e.message; }
    }
    majVignette();
  }
  choixFichier.addEventListener('change', choisirFichier);
  page.addEventListener('change', () => { apercu.replaceChildren(); majVignette(); });
  async function montrer(chemin, numero, cible, statut) {
    statut.textContent = 'Préparation de l’aperçu (rendu et, pour un fournisseur externe, OCR et masquage)…';
    cible.replaceChildren();
    const v = await poster('apercu', {d: matter.value, p: chemin, page: Number(numero) || 1});
    statut.textContent = v.message || '';
    if (v.image && /^data:image\/(png|jpeg);base64,/.test(v.image)) {
      const img = el('img', undefined, 'v106i-image');
      img.src = v.image;
      img.alt = v.envoi === 'image_masquee' ? 'Image masquée telle qu’elle sera envoyée (' + v.zones + ' zones noircies)' : 'Image telle qu’elle sera envoyée';
      cible.append(img);
    }
    return v;
  }
  voir.addEventListener('click', async () => {
    if (!choixFichier.value) return;
    voir.disabled = true;
    try { await montrer(choixFichier.value, page.value, apercu, etat); } catch (e) { etat.textContent = e.message; }
    finally { voir.disabled = false; }
  });
  ajouter.addEventListener('click', () => {
    const chemin = choixFichier.value; if (!chemin) return;
    const numero = estPdf(chemin) ? Math.max(1, Number(page.value) || 1) : 1;
    if (choisies.some(x => x.chemin === chemin && x.page === numero)) { etat.textContent = 'Cette page est déjà jointe.'; return; }
    if (choisies.length >= MAX) { etat.textContent = 'Quatre images au plus par demande.'; return; }
    const nom = choixFichier.selectedOptions[0]?.textContent || chemin.split('/').pop();
    choisies.push({chemin, page: numero, nom: nom.replace(/ \(illisible\)$/, '')});
    dossierChoix = matter.value;
    rendre();
    if (dlg.close) dlg.close(); else dlg.removeAttribute('open');
    bouton.focus();
  });
  fermer.addEventListener('click', () => { if (dlg.close) dlg.close(); else dlg.removeAttribute('open'); bouton.focus(); });
  bouton.addEventListener('click', ouvrir);

  // ------------------------------------------------------------------ liste des images jointes
  function rendre() {
    liste.replaceChildren();
    liste.hidden = !choisies.length;
    choisies.forEach((x, i) => {
      const li = el('li', undefined, 'v106i-puce');
      li.append(el('span', '🖼 ' + x.nom + (estPdf(x.chemin) ? ' — page ' + x.page : '')));
      const b1 = el('button', 'Aperçu', 'v106i-mini'); b1.type = 'button';
      const b2 = el('button', 'Retirer', 'v106i-mini'); b2.type = 'button';
      b2.setAttribute('aria-label', 'Retirer ' + x.nom);
      const zone = el('div', undefined, 'v106i-apercu'), st = el('p', '', 'v106i-etat');
      st.setAttribute('role', 'status');
      b1.addEventListener('click', async () => {
        if (zone.childElementCount) { zone.replaceChildren(); st.textContent = ''; return; }
        b1.disabled = true;
        try { await montrer(x.chemin, x.page, zone, st); } catch (e) { st.textContent = e.message; } finally { b1.disabled = false; }
      });
      b2.addEventListener('click', () => { choisies.splice(i, 1); rendre(); bouton.focus(); });
      li.append(b1, b2, st, zone);
      liste.append(li);
    });
    avis.textContent = choisies.length ? choisies.length + ' image(s) jointe(s) à la prochaine demande.' : '';
  }
  // Les images appartiennent à un dossier : un changement de dossier les retire.
  matter.addEventListener('change', () => { if (choisies.length && matter.value !== dossierChoix) { choisies = []; rendre(); avis.textContent = 'Images retirées : autre dossier choisi.'; } });
  // Après l'envoi (v567.js a déjà lu le composeur, son écouteur passe avant celui-ci) : la liste est vidée, la réponse dira ce qui est parti.
  function apresEnvoi() {
    if (!choisies.length || !question?.value.trim()) return;   // instruction vide : v567.js n'envoie rien, les images restent jointes
    const n = choisies.length;
    setTimeout(() => { choisies = []; rendre(); avis.textContent = n + ' image(s) jointe(s) à la demande envoyée : la réponse indique si elles ont été masquées, envoyées telles quelles ou remplacées par le texte.'; }, 0);
  }
  send.addEventListener('click', apresEnvoi);
  question?.addEventListener('keydown', ev => { if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) apresEnvoi(); });

  window.axh56106vision = {
    pourEnvoi: () => (matter.value && matter.value === dossierChoix ? choisies.map(x => ({chemin: x.chemin, page: x.page})) : []),
    vider: () => { choisies = []; rendre(); },
  };
})();
