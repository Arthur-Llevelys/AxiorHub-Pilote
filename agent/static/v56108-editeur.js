/* AxiorHub 5.6.108 (lot W) — modifier un .docx sans serveur de documents.
   1. Cadre de l'éditeur (/editeur/wordcraft/cadre?host=parent) : charge WordCraft (fichiers servis par AxiorHub) ; window.open neutralisé
      (aucun lien externe ouvert depuis l'éditeur). WordCraft dialogue lui-même avec la page parente (même origine) par postMessage.
   2. Page « Modifier dans l'éditeur intégré » : ouverture (séance), envoi du document au cadre, chaque enregistrement renvoyé à AxiorHub,
      contrôle affiché AVANT « Garder cette version » / « Abandonner cette version ».
   3. Page « Ouvrir dans Word / LibreOffice » (poste) : copie de travail ouverte, enregistrement détecté par empreinte, reprise contrôlée.
   Tout texte reçu passe par textContent ; aucun HTML du serveur ni du document n'est injecté.

   5.6.109 (C08) — saisie préservée. Révision de SAISIE (etat.modifications : chaque « dirty » vrai l'avance) et révision de SAUVEGARDE
   (etat.revision : candidat acquitté par le serveur) sont distinctes. Chaque instantané reçu de l'éditeur (« save ») est numéroté par l'hôte
   (etat.instantanes) et fige la révision de saisie qu'il contient ; le serveur rend ce numéro avec la révision du candidat ; un reçu
   n'acquitte que SON instantané et ne déclare l'éditeur propre que si aucune saisie n'est postérieure. Limite du contrat : WordCraft 0.18.1
   n'envoie aucun identifiant avec « save » et « dirty » (fichiers de l'archive inchangés) ; l'association repose donc sur l'ordre des
   messages vus par l'hôte. Si une version future du cadre joint un nombre « revision » à ces DEUX messages, il est utilisé tel quel.
   5.6.109 (C07) — écriture à résultat incertain. Réponse perdue ou reçu « à vérifier » : l'état « À vérifier » reste affiché, « Vérifier »
   redemande la MÊME décision (le serveur relit la même cible : jamais un second fichier), « Réessayer l'écriture » n'existe que si le serveur
   a constaté le fichier absent. */
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
  var etat = { seance: '', revision: 0, candidat: 0, sale: false, modifications: 0, envois: 0, instantanes: 0, contrat: false, incertain: null,
               file: Promise.resolve(), sonde: 0, vu: '' };

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
  // 5.6.109 (C07) : boutons de l'état « À vérifier », créés ici (texte seul) dans la zone du contrôle des deux pages
  function boutonIncertain(nom, libelle) {
    var b = controle.querySelector('[data-w108="' + nom + '"]');
    if (!b) {
      b = el('button', nom === 'verifier' ? 'ax-btn' : 'ax-btn ghost', libelle);
      b.type = 'button'; b.setAttribute('data-w108', nom);
      var zone = controle.querySelector('.w108-actions') || controle;
      if (zone.insertBefore) { zone.insertBefore(b, zone.firstChild); } else { zone.appendChild(b); }
    }
    return b;
  }
  function montrerIncertain(j, seance, revision) {
    etat.incertain = { seance: (j && j.seance) || seance, revision: (j && j.revision) || revision, reessayable: !!(j && j.reessayable) };
    var garder = controle.querySelector('[data-w108="garder"]'); if (garder) { garder.hidden = true; }
    boutonIncertain('verifier', 'Vérifier').hidden = false;
    boutonIncertain('reessayer', 'Réessayer l’écriture').hidden = !etat.incertain.reessayable;
    if (malgre) { malgre.hidden = true; }
    controle.hidden = false;
  }
  function finIncertain() {
    if (!etat.incertain) { return; }
    etat.incertain = null;
    boutonIncertain('verifier', 'Vérifier').hidden = true;
    boutonIncertain('reessayer', 'Réessayer l’écriture').hidden = true;
    var garder = controle.querySelector('[data-w108="garder"]'); if (garder) { garder.hidden = false; }
  }
  function decider(decision, bouton, reessayer) {
    var attribut = function (nom) { return (bouton && bouton.getAttribute && bouton.getAttribute(nom)) || ''; };
    var ligne = attribut('data-w108-seance') ? bouton : null;      // écriture à vérifier listée par la page (après un rechargement)
    var seance = attribut('data-w108-seance') || (etat.incertain && etat.incertain.seance) || etat.seance;
    var revision = Number(attribut('data-w108-revision') || (etat.incertain && etat.incertain.revision) || etat.candidat);
    if (!seance || !revision) { dire('Aucune version à décider : enregistrez d’abord le document.', true); return; }
    var corps = { seance: seance, revision: revision, decision: decision, cle: cle(), malgre_changement: !!(malgreCase && malgreCase.checked) };
    if (reessayer) { corps.reessayer = true; }
    var verification = decision === 'garder' && (etat.incertain || ligne);
    dire(decision !== 'garder' ? 'Abandon de cette version…' : (verification ? 'Vérification du fichier attendu dans le dossier…' : 'Écriture de la nouvelle version…'));
    poster('decider', corps).then(function (r) {
      if (r.statut === 409 && r.j && r.j.error === 'original_modifie') {
        if (malgre) { malgre.hidden = false; }
        dire(message(r), true); return;
      }
      if (r.ok && r.j && r.j.etat === 'a_verifier') {              // ni reçu ni échec : la même décision se vérifie, rien d'autre n'est écrit
        if (!ligne) { montrerIncertain(r.j, seance, revision); }
        dire(message(r, 'À vérifier : le résultat de l’écriture n’est pas connu.'), true); return;
      }
      if (!r.ok) { dire(message(r), true); return; }
      if (ligne) { var li = ligne.closest && ligne.closest('li'); if (li) { li.hidden = true; } }
      if (!ligne || seance === etat.seance) { etat.candidat = 0; finIncertain(); controle.hidden = true; }
      dire(message(r, 'Fait.'));
      if (r.j && r.j.ferme) { arreterSonde(); etat.seance = ''; var rep = document.querySelector('[data-w108-reprise]'); if (rep) { rep.hidden = true; } }
    }, function () {
      // réponse perdue : l'écriture a pu aboutir ; « Vérifier » redemande la MÊME décision (même cible côté serveur), jamais un second fichier
      if (decision === 'garder' && !ligne) { montrerIncertain(null, seance, revision); }
      dire('À vérifier : le résultat de l’écriture n’est pas connu. Rien d’autre ne sera écrit ; « Vérifier » relit le fichier attendu.', true);
    });
  }
  function fermer() {
    if (!etat.seance) { return; }
    if (etat.envois) { dire('Enregistrement en cours : attendez son résultat avant de fermer.', true); return; }
    if (etat.incertain) { dire('Une écriture reste à vérifier : utilisez « Vérifier » ou « Abandonner cette version » avant de fermer.', true); return; }
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
    // révision de saisie contenue dans CET instantané (celle du cadre s'il la fournit sur « save » et « dirty », sinon le compteur de l'hôte)
    var octets = donnees.bytes, saisie = (etat.contrat && typeof donnees.revision === 'number') ? donnees.revision : etat.modifications;
    var numero = (etat.instantanes += 1);
    etat.envois += 1; etat.sale = true;
    etat.file = etat.file.then(function () {
      dire('Contrôle de la version enregistrée…');
      var q = 'seance=' + encodeURIComponent(etat.seance) + '&revision=' + encodeURIComponent(etat.revision) + '&cle=' + encodeURIComponent(cle()) +
              '&instantane=' + numero;
      return fetch(cfg.api + 'enregistrer?' + q, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: octets }).then(reponse).then(function (r) {
        if (!r.ok) { dire(message(r), true); return; }                  // refus : rien n'est acquitté, la saisie reste « modifiée »
        if (r.j.instantane !== undefined && Number(r.j.instantane) !== numero) {
          dire('Reçu d’un autre enregistrement : cette version n’est pas confirmée. Enregistrez de nouveau.', true); return;
        }
        etat.revision = r.j.revision; etat.sale = etat.modifications !== saisie;   // propre seulement si aucune saisie n'est postérieure à CET instantané
        dire(etat.sale ? 'Version reçue et contrôlée. Des modifications plus récentes ne sont pas encore enregistrées dans cette version.'
                       : 'Version reçue et contrôlée : décidez ci-dessous si vous la gardez.');
        montrerControle(r.j);
      });
    }).catch(function () { etat.sale = true; dire('Résultat de l’enregistrement inconnu : vérifiez la séance avant un nouvel essai.', true); }).finally(function () { etat.envois -= 1; });
  }
  window.addEventListener('message', function (ev) {
    if (!iframe || ev.source !== iframe.contentWindow || ev.origin !== location.origin) { return; }
    var d = ev.data || {};
    if (d.type === 'wordcraft:ready') { if (etat.seance) { envoyerAuCadre(); } }
    else if (d.type === 'wordcraft:save') { recevoir(d); }
    else if (d.type === 'wordcraft:dirty') {
      if (d.dirty) {
        if (typeof d.revision === 'number' && d.revision > etat.modifications) { etat.contrat = true; etat.modifications = d.revision; } else { etat.modifications += 1; }
        etat.sale = true;
      } else if (!etat.envois) { etat.sale = false; }                 // « propre » annoncé pendant un envoi : seul le reçu de cet envoi en décide
    }
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
    else if (a === 'garder' || a === 'abandonner') { decider(a, b); }
    else if (a === 'verifier') { decider('garder', b); }               // 5.6.109 (C07) : même décision, même cible : relecture seule
    else if (a === 'reessayer') { decider('garder', b, true); }
    else if (a === 'fermer') { fermer(); }
  });
  window.addEventListener('beforeunload', function (ev) {
    if (etat.seance && (etat.sale || etat.candidat || etat.envois || etat.incertain)) { ev.preventDefault(); ev.returnValue = ''; }
  });
  if (mode === 'traitement') { dire(''); }
})();
