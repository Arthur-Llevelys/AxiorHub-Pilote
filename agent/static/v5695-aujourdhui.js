/* AxiorHub 5.6.95 (lot aujourdhui, A02) : encadré « Actuellement » d'Aujourd'hui.
   - Mise à jour automatique toutes les 60 s par une route légère : l'empreinte connue part dans If-None-Match ; « 304 » = rien n'a changé,
     la page n'est pas touchée. Suspendue quand l'onglet est caché ; au retour, une mise à jour immédiate si la dernière a plus d'une minute.
   - Actualiser : tout de suite. Arrêter / Reprendre : la mise à jour automatique, choix mémorisé pour la personne (route actuellement/auto).
   - Écouter : lit l'encadré avec la voix choisie (window.axiorhubVoix : Brindille dans le navigateur par défaut, abréviations développées par
     le serveur) ; Arrêter la lecture. Rien n'est lu sans un clic.
   - Plis d'Aujourd'hui (A03) : veille, veille des sociétés, À retenir, Reprendre (<details data-pli>) ; ouvert ou replié, mémorisé pour la
     personne (route actuellement/plis). Ouverts par défaut.
   Aucun de ces boutons ne déclenche de travail IA. Aucun script en ligne, aucun style injecté (CSP). Textes : catalogue js.5695.* (4 langues). */
(() => {
  'use strict';
  if (window.__ax95) return;                 // le script peut être lié deux fois (encadré et plis) : une seule exécution
  window.__ax95 = true;
  const meta = name => document.querySelector('meta[name="' + name + '"]')?.content || '';
  const prefix = meta('axiorhub-prefix') || (document.getElementById('c530')?.dataset.prefix || ''), csrf = meta('axiorhub-csrf');
  let messages = null;
  const msg = (key, fallback, values) => {
    if (!messages) { try { messages = JSON.parse(document.querySelector('#l5631-msg')?.textContent || '{}'); } catch (e) { messages = {}; } }
    let text = messages['js.5695.' + key] || fallback;
    Object.entries(values || {}).forEach(([k, v]) => { text = text.split('{' + k + '}').join(String(v)); });
    return text;
  };
  async function poster(route, data) {
    const r = await fetch(prefix + '/api440/m5689/actuellement/' + route, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)});
    const v = await r.json().catch(() => ({}));
    if (!r.ok || v.error) throw new Error(v.message || v.error || ('HTTP ' + r.status));
    return v;
  }

  // ------------------------------------------------------------ plis : l'état choisi est enregistré pour la personne
  document.addEventListener('toggle', ev => {
    const pli = ev.target;
    if (!(pli instanceof HTMLDetailsElement) || !pli.dataset.pli) return;
    const ouvert = pli.open;
    if ((pli.dataset.ouvert === '1') === ouvert) return;   // état d'origine (événement émis au chargement) : rien à enregistrer
    pli.dataset.ouvert = ouvert ? '1' : '0';
    poster('plis', {bloc: pli.dataset.pli, ouvert}).catch(e => {
      const zone = pli.querySelector('.t95-pli-statut');
      if (zone) zone.textContent = msg('pli_echec', 'L’état replié ou ouvert n’a pas pu être enregistré ({raison}).', {raison: e.message});
    });
  }, true);

  const box = document.getElementById('ax95-actuellement');
  if (!box) return;
  const zone = document.getElementById('ax95-zone'), statut = document.getElementById('ax95-statut'), heure = document.getElementById('ax95-heure');
  const mode = document.getElementById('ax95-mode');
  const btn = name => box.querySelector('[data-ax95="' + name + '"]');
  const delai = Math.max(15, Number(box.dataset.delai) || 60) * 1000;
  let etag = box.dataset.etag || '', auto = box.dataset.auto !== '0', timer = 0, derniere = Date.now(), enCours = false;
  let texte = '';
  try { texte = JSON.parse(document.getElementById('ax95-texte')?.textContent || '""'); } catch (e) { texte = ''; }
  const dire = (message, annonce) => { if (statut) { statut.textContent = message; statut.dataset.annonce = annonce ? '1' : ''; } };

  async function actualiser(manuel) {
    if (enCours) return;
    enCours = true;
    if (manuel) dire(msg('maj_encours', 'Mise à jour…'));
    try {
      const r = await fetch(prefix + '/api440/m5689/actuellement/etat', {credentials: 'same-origin', cache: 'no-store',
        headers: etag ? {'If-None-Match': '"' + etag + '"'} : {}});
      derniere = Date.now();
      if (r.status === 304) {
        box.dataset.dernier = '304';
        if (manuel) dire(msg('rien_neuf', 'Rien de nouveau depuis la dernière mise à jour.'), true);
        return;
      }
      const v = await r.json().catch(() => ({}));
      if (!r.ok || v.error) throw new Error(v.message || v.error || ('HTTP ' + r.status));
      etag = v.etag || '';
      box.dataset.etag = etag;
      box.dataset.dernier = '200';
      if (typeof v.html === 'string') zone.innerHTML = v.html;      // HTML échappé par le serveur (actuellement5695.lignes_html)
      if (heure && v.heure) heure.textContent = v.heure;
      texte = v.texte || texte;
      dire(manuel ? msg('maj_ok', 'Encadré mis à jour.') : '', manuel);
    } catch (e) {
      dire(msg('maj_echec', 'Mise à jour impossible pour l’instant ({raison}) : les nombres affichés datent de {heure}.',
        {raison: e.message, heure: heure?.textContent || msg('derniere', 'la dernière lecture')}), true);
    } finally {
      enCours = false;
      planifier();
    }
  }

  function planifier() {
    clearTimeout(timer);
    timer = 0;
    if (!auto || document.hidden) return;
    timer = setTimeout(() => actualiser(false), Math.max(1000, delai - (Date.now() - derniere)));
  }

  function afficherMode() {
    const b = btn('auto');
    if (b) {
      b.setAttribute('aria-pressed', auto ? 'false' : 'true');
      const label = b.querySelector('span');
      if (label) label.textContent = auto ? msg('btn_arreter', 'Arrêter la mise à jour') : msg('btn_reprendre', 'Reprendre la mise à jour');
    }
    if (mode) mode.textContent = auto ? msg('mode_auto', ' · mise à jour automatique toutes les minutes') : msg('mode_arret', ' · mise à jour automatique arrêtée');
    box.dataset.auto = auto ? '1' : '0';
  }

  async function basculer() {
    const voulu = !auto;
    try {
      const v = await poster('auto', {auto: voulu});
      auto = v.auto === true;
      afficherMode();
      dire(auto ? msg('auto_ok', 'Mise à jour automatique reprise.') : msg('arret_ok', 'Mise à jour automatique arrêtée. « Actualiser » reste disponible.'), true);
      if (auto) actualiser(false); else planifier();
    } catch (e) {
      dire(msg('choix_echec', 'Le choix n’a pas pu être enregistré ({raison}).', {raison: e.message}), true);
    }
  }

  function lecture(active) {
    const ecouter = btn('ecouter'), taire = btn('taire');
    if (ecouter) ecouter.hidden = active;
    if (taire) taire.hidden = !active;
    box.dataset.lecture = active ? '1' : '';
    if (active && taire) taire.focus(); else if (!active && ecouter && document.activeElement === taire) ecouter.focus();
  }

  async function ecouter() {
    const voix = window.axiorhubVoix;
    if (!voix) { dire(msg('voix_absente', 'La lecture à voix haute n’est pas disponible sur cette page.'), true); return; }
    lecture(true);
    try {
      const fin = await voix.speak(texte, {note: m => dire(m, true)});
      if (fin && fin.provider) box.dataset.voix = fin.provider;
    } catch (e) {
      box.dataset.erreurVoix = e.message;
    } finally {
      lecture(false);
    }
  }

  box.addEventListener('click', ev => {
    const b = ev.target.closest('[data-ax95]');
    if (!b) return;
    const action = b.dataset.ax95;
    if (action === 'actualiser') actualiser(true);
    else if (action === 'auto') basculer();
    else if (action === 'ecouter') ecouter();
    else if (action === 'taire') { window.axiorhubVoix?.stop(); lecture(false); dire(msg('lecture_arretee', 'Lecture arrêtée.'), true); }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(timer); timer = 0; return; }
    if (auto && Date.now() - derniere >= delai) actualiser(false); else planifier();
  });
  window.addEventListener('pagehide', () => { clearTimeout(timer); window.axiorhubVoix?.stop(); });
  afficherMode();
  planifier();
})();
