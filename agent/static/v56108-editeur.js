/* AxiorHub 5.6.108 (lot W) — modifier un .docx sans serveur de documents.
   1. Cadre de l'éditeur (/editeur/wordcraft/cadre?host=parent) : charge WordCraft (fichiers servis par AxiorHub) ; window.open neutralisé
      (aucun lien externe ouvert depuis l'éditeur). WordCraft dialogue lui-même avec la page parente (même origine) par postMessage.
   2. Page « Modifier dans l'éditeur intégré » : ouverture (séance), envoi du document au cadre, chaque enregistrement renvoyé à AxiorHub,
      contrôle affiché AVANT « Garder cette version » / « Abandonner cette version ».
   3. Page « Ouvrir dans Word / LibreOffice » (poste) : copie de travail ouverte, enregistrement détecté par empreinte, reprise contrôlée.
   Tout texte reçu passe par textContent ; aucun HTML du serveur ni du document n'est injecté. */
(function () {
  'use strict';
  var racine = document.documentElement;

  // ------------------------------------------------------------------------------------------------ 1. cadre
  if (racine.getAttribute('data-w108') === 'cadre') {
    window.open = function () { return null; };   // liens d'aide, site de l'éditeur, liens du document : jamais ouverts d'ici
    var base = racine.getAttribute('data-w108-statique') || '';
    var v = encodeURIComponent(racine.getAttribute('data-w108-version') || '');
    var chargement = document.querySelector('[data-w108-chargement]');
    import(base + 'wordcraft-web-bc9cdbc0b42827ee.js?v=' + v).then(function (m) {
      return m.default({ module_or_path: base + 'wordcraft-web-bc9cdbc0b42827ee_bg.wasm?v=' + v });
    }).then(function () { if (chargement) { chargement.hidden = true; } }, function () {
      if (chargement) { chargement.textContent = 'L’éditeur n’a pas pu démarrer dans ce navigateur (WebGL 2 ou WebGPU requis).'; }
      try { window.parent.postMessage({ type: 'wordcraft:failed', error: 'demarrage' }, location.origin); } catch (err) { /* page seule */ }
    });
    return;
  }

  var conf = document.querySelector('[data-w108-config]');
  if (!conf) { return; }
  var cfg;
  try { cfg = JSON.parse(conf.getAttribute('data-w108-config')); } catch (err) { return; }
  var mode = conf.getAttribute('data-w108-mode') || 'wordcraft';
  var zoneEtat = document.querySelector('[data-w108-etat]');
  var controle = document.querySelector('[data-w108-controle]');
  var rapport = document.querySelector('[data-w108-rapport]');
  var malgre = document.querySelector('[data-w108-malgre]');
  var malgreCase = document.querySelector('[data-w108-malgre-case]');
  var etat = { seance: '', revision: 0, candidat: 0, sale: false, file: Promise.resolve(), sonde: 0, vu: '' };

  function meta(nom) { var m = document.querySelector('meta[name="' + nom + '"]'); return m ? m.getAttribute('content') : ''; }
  function dire(texte, alerte) {
    if (!zoneEtat) { return; }
    zoneEtat.textContent = texte || '';
    zoneEtat.classList.toggle('w108-erreur', !!alerte);
  }
  function cle() {
    var a = new Uint8Array(12); window.crypto.getRandomValues(a);
    return 'w108-' + Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function reponse(r) {
    return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, statut: r.status, j: j }; });
  }
  function poster(route, donnees) {
    return fetch(cfg.api + route, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: JSON.stringify(donnees) }).then(reponse);
  }
  function lire(route, params) {
    var q = Object.keys(params).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
    return fetch(cfg.api + route + '?' + q, { credentials: 'same-origin', cache: 'no-store' });
  }
  function el(tag, classe, texte) {
    var n = document.createElement(tag);
    if (classe) { n.className = classe; }
    if (texte !== undefined) { n.textContent = texte; }
    return n;
  }
  function message(r, defaut) { return (r && r.j && r.j.message) || defaut || 'Demande refusée.'; }

  // ------------------------------------------------------------------------------------------------ contrôle et décision (communs)
  function montrerControle(res) {
    var c = res.controle || {};
    etat.candidat = res.revision;
    rapport.textContent = '';
    rapport.appendChild(el('p', c.pertes && c.pertes.length ? 'w108-resume w108-attention' : 'w108-resume', c.resume || ''));
    if (c.pertes && c.pertes.length) {
      rapport.appendChild(el('h3', '', 'Pertes ou changements de structure'));
      var ul = el('ul', 'w108-pertes');
      c.pertes.forEach(function (p) {
        ul.appendChild(el('li', '', p.libelle + ' : ' + p.avant + ' → ' + p.apres + (p.detail ? ' (' + p.detail + ')' : '')));
      });
      rapport.appendChild(ul);
    }
    var t = c.texte || {};
    rapport.appendChild(el('p', '', 'Texte : ' + (t.modifies || 0) + ' paragraphe(s) modifié(s), ' + (t.ajoutes || 0) + ' ajouté(s), ' +
                                    (t.supprimes || 0) + ' supprimé(s)' + (t.espaces ? ', ' + t.espaces + ' avec des espaces seulement' : '') + '.'));
    if (t.details && t.details.length) {
      var d = el('details', 'w108-details'); d.appendChild(el('summary', '', 'Voir les paragraphes concernés'));
      var ol = el('ul');
      t.details.forEach(function (x) {
        var li = el('li');
        li.appendChild(el('strong', '', '¶ ' + x.position + ' — ' + x.nature));
        if (x.avant) { li.appendChild(el('span', 'w108-avant', ' Avant : ' + x.avant)); }
        if (x.apres) { li.appendChild(el('span', 'w108-apres', ' Après : ' + x.apres)); }
        ol.appendChild(li);
      });
      d.appendChild(ol); rapport.appendChild(d);
    }
    if (malgre) { malgre.hidden = true; if (malgreCase) { malgreCase.checked = false; } }
    controle.hidden = false;
    var h = controle.querySelector('h2'); if (h) { h.focus(); }
  }
  function decider(decision) {
    if (!etat.seance || !etat.candidat) { dire('Aucune version à décider : enregistrez d’abord le document.', true); return; }
    var corps = { seance: etat.seance, revision: etat.candidat, decision: decision, cle: cle(), malgre_changement: !!(malgreCase && malgreCase.checked) };
    dire(decision === 'garder' ? 'Écriture de la nouvelle version…' : 'Abandon de cette version…');
    poster('decider', corps).then(function (r) {
      if (r.statut === 409 && r.j && r.j.error === 'original_modifie') {
        if (malgre) { malgre.hidden = false; }
        dire(message(r), true); return;
      }
      if (!r.ok) { dire(message(r), true); return; }
      etat.candidat = 0;
      controle.hidden = true;
      dire(message(r, 'Fait.'));
      if (r.j && r.j.ferme) { arreterSonde(); etat.seance = ''; var rep = document.querySelector('[data-w108-reprise]'); if (rep) { rep.hidden = true; } }
    }, function () { dire('Serveur injoignable : rien n’a été écrit.', true); });
  }
  function fermer() {
    if (!etat.seance) { return; }
    if (etat.sale && !window.confirm('Des modifications ne sont pas enregistrées dans l’éditeur. Fermer quand même ?')) { return; }
    poster('fermer', { seance: etat.seance, cle: cle() }).then(function (r) {
      dire(r.ok ? 'Éditeur fermé : copies de travail retirées. L’original est intact.' : message(r), !r.ok);
      if (r.ok) {
        etat.seance = ''; etat.sale = false; arreterSonde();
        var zone = document.querySelector('[data-w108-zone]'); if (zone) { zone.hidden = true; }
        if (iframe) { var vide = iframe.cloneNode(false); vide.removeAttribute('src'); iframe.replaceWith(vide); iframe = vide; }
        controle.hidden = true;
        var rep = document.querySelector('[data-w108-reprise]'); if (rep) { rep.hidden = true; }
      }
    });
  }

  // ------------------------------------------------------------------------------------------------ 2. WordCraft
  var iframe = document.querySelector('[data-w108-iframe]');
  function envoyerAuCadre() {
    lire('fichier', { seance: etat.seance }).then(function (r) {
      if (!r.ok) { throw new Error('lecture'); }
      return r.arrayBuffer();
    }).then(function (octets) {
      iframe.contentWindow.postMessage({ type: 'wordcraft:open', name: cfg.nom, bytes: octets }, location.origin, [octets]);
      dire('Document ouvert dans WordCraft. Enregistrez (Ctrl+S) pour faire contrôler une nouvelle version.');
    }).catch(function () { dire('Le document n’a pas pu être transmis à l’éditeur : rechargez la page.', true); });
  }
  function recevoir(donnees) {
    var nom = String(donnees.name || '');
    if (!/\.docx$/i.test(nom)) { dire('Export « ' + nom + ' » ignoré : seul l’enregistrement au format Word (.docx) est repris ici.'); return; }
    var octets = donnees.bytes;
    etat.file = etat.file.then(function () {
      dire('Contrôle de la version enregistrée…');
      var q = 'seance=' + encodeURIComponent(etat.seance) + '&revision=' + encodeURIComponent(etat.revision) + '&cle=' + encodeURIComponent(cle());
      return fetch(cfg.api + 'enregistrer?' + q, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: octets }).then(reponse).then(function (r) {
        if (!r.ok) { dire(message(r), true); return; }
        etat.revision = r.j.revision; etat.sale = false;
        dire('Version reçue et contrôlée : décidez ci-dessous si vous la gardez.');
        montrerControle(r.j);
      });
    }).catch(function () { dire('Serveur injoignable : la version n’a pas été reçue. Enregistrez de nouveau.', true); });
  }
  window.addEventListener('message', function (ev) {
    if (!iframe || ev.source !== iframe.contentWindow || ev.origin !== location.origin) { return; }
    var d = ev.data || {};
    if (d.type === 'wordcraft:ready') { if (etat.seance) { envoyerAuCadre(); } }
    else if (d.type === 'wordcraft:save') { recevoir(d); }
    else if (d.type === 'wordcraft:dirty') { etat.sale = !!d.dirty; }
    else if (d.type === 'wordcraft:failed') { dire('L’éditeur n’a pas pu démarrer dans ce navigateur (WebGL 2 ou WebGPU requis). Utilisez Word ou le serveur de documents.', true); }
  });
  function ouvrirWordcraft(bouton) {
    var accepte = document.querySelector('[data-w108-accepte]');
    bouton.disabled = true;
    dire('Ouverture…');
    poster('ouvrir', { s: cfg.s, p: cfg.p, accepte: !!(accepte && accepte.checked), cle: cle() }).then(function (r) {
      if (!r.ok) { dire(message(r), true); bouton.disabled = false; return; }
      etat.seance = r.j.seance; etat.revision = r.j.revision || 0;
      var zone = document.querySelector('[data-w108-zone]'); if (zone) { zone.hidden = false; }
      var fb = document.querySelector('[data-w108="fermer"]'); if (fb) { fb.hidden = false; }
      dire('Chargement de l’éditeur (première fois : quelques secondes)…');
      // cadre neuf, adresse posée avant l'insertion : pas de navigation depuis le document vide initial d'un cadre déjà inséré
      var neuf = iframe.cloneNode(false);
      neuf.src = cfg.cadre;
      iframe.replaceWith(neuf);
      iframe = neuf;
    }, function () { dire('Serveur injoignable.', true); bouton.disabled = false; });
  }

  // ------------------------------------------------------------------------------------------------ 3. Word / LibreOffice sur le poste
  function arreterSonde() { if (etat.sonde) { clearInterval(etat.sonde); etat.sonde = 0; } }
  function sonder() {
    if (!etat.seance) { return; }
    lire('word/etat', { seance: etat.seance }).then(reponse).then(function (r) {
      if (!r.ok) { dire(message(r), true); arreterSonde(); return; }
      var rep = document.querySelector('[data-w108-reprise]');
      if (!r.j.present) { dire(r.j.message || 'Copie de travail absente.', true); arreterSonde(); return; }
      if (r.j.modifie && r.j.sha256 !== etat.vu) {
        etat.vu = r.j.sha256;
        dire('Enregistrement détecté dans ' + cfg.traitement + ' (' + new Date().toLocaleTimeString() + ') : vous pouvez reprendre cette version.');
        if (rep) { rep.hidden = false; }
      } else if (!r.j.modifie && !r.j.deja_repris && rep) {
        rep.hidden = false;
      }
    }).catch(function () { /* poste momentanément occupé : nouvel essai au prochain passage */ });
  }
  function ouvrirTraitement(bouton) {
    bouton.disabled = true;
    dire('Ouverture de la copie de travail…');
    poster('word/ouvrir', { s: cfg.s, p: cfg.p, cle: cle() }).then(function (r) {
      bouton.disabled = false;
      if (!r.ok) { dire(message(r), true); return; }
      etat.seance = r.j.seance; etat.revision = r.j.revision || 0;
      dire(message(r), !r.j.lance);
      var rep = document.querySelector('[data-w108-reprise]'); if (rep) { rep.hidden = false; }
      arreterSonde(); etat.sonde = setInterval(sonder, 3000);
    }, function () { bouton.disabled = false; dire('Serveur injoignable.', true); });
  }
  function reprendre() {
    if (!etat.seance) { return; }
    lire('word/etat', { seance: etat.seance }).then(reponse).then(function (r) {
      if (!r.ok || !r.j.present) { dire(message(r, 'Copie de travail absente.'), true); return; }
      if (!r.j.modifie) { dire('Aucun nouvel enregistrement : enregistrez d’abord dans ' + cfg.traitement + ' (Ctrl+S).', true); return; }
      return poster('word/reprendre', { seance: etat.seance, sha256: r.j.sha256, cle: cle() }).then(function (r2) {
        if (!r2.ok) { dire(message(r2), true); return; }
        etat.revision = r2.j.revision;
        dire('Version reprise et contrôlée : décidez ci-dessous si vous la gardez.');
        montrerControle(r2.j);
      });
    }).catch(function () { dire('Serveur injoignable.', true); });
  }

  // ------------------------------------------------------------------------------------------------ actions
  var accepte = document.querySelector('[data-w108-accepte]');
  var ouvrirBtn = document.querySelector('[data-w108="ouvrir"]');
  if (accepte && ouvrirBtn) { accepte.addEventListener('change', function () { ouvrirBtn.disabled = !accepte.checked; }); }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-w108]');
    if (!b || b.disabled) { return; }
    var a = b.getAttribute('data-w108');
    if (a === 'ouvrir') { ouvrirWordcraft(b); }
    else if (a === 'word-ouvrir') { ouvrirTraitement(b); }
    else if (a === 'word-reprendre') { reprendre(); }
    else if (a === 'garder' || a === 'abandonner') { decider(a); }
    else if (a === 'fermer') { fermer(); }
  });
  window.addEventListener('beforeunload', function (ev) {
    if (etat.seance && (etat.sale || etat.candidat)) { ev.preventDefault(); ev.returnValue = ''; }
  });
  if (mode === 'traitement') { dire(''); }
})();
