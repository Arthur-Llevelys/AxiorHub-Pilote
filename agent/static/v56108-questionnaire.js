/* 5.6.108 (lot Q) : questionnaire d'un acte — ouvrir depuis la fiche du modèle, répondre par groupes (« Enregistrer et continuer »,
   « Je ne sais pas », « Répondre plus tard »), retenir une valeur trouvée dans une source, corriger une section, télécharger le brouillon
   partiel, confier les sections prêtes à l'IA. La saisie non enregistrée reste dans ce navigateur (fermeture, retour, IA en pause, refus 409)
   et revient à l'ouverture suivante ; le stockage peut être indisponible (navigation privée) : rien ne casse.
   Le serveur valide chaque réponse (champ, type, droits, révision) ; rien ici n'envoie, ne signe ni ne dépose. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var MESSAGES = { origine_ou_csrf_refuse: 'Session expirée : rechargez la page.' };

  function cle() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    var a = new Uint8Array(16); window.crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function poster(route, corps) {
    corps.cle = cle();
    return fetch(prefix + '/api440/m56108/questionnaire/' + route, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (v) {
        if (!r.ok) { var e = new Error(MESSAGES[v.error] || v.message || 'Demande refusée.'); e.statut = r.status; throw e; }
        return v;
      });
    });
  }
  function lire(k) { try { return JSON.parse(window.localStorage.getItem(k) || 'null'); } catch (e) { return null; } }
  function ecrire(k, v) { try { if (v === null) window.localStorage.removeItem(k); else window.localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* indisponible */ } }

  /* ---------------------------------------------------------------- ouverture depuis la fiche du modèle */
  $$('[data-q108-creer]').forEach(function (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var etat = form.querySelector('.q108-etat'), bouton = form.querySelector('button[type=submit]');
      var d = new FormData(form);
      if (!d.get('dossier')) { etat.textContent = 'Choisissez un dossier.'; return; }
      bouton.disabled = true; etat.textContent = 'Ouverture…';
      if (!d.get('modele')) { etat.textContent = 'Choisissez un modèle.'; return; }
      var corps = { modele: d.get('modele'), dossier: d.get('dossier'), societe: d.get('societe') || '' };
      if (form.hasAttribute('data-q108-reprendre')) corps.reprendre = true;   /* 5.6.111 : questionnaire déjà ouvert repris (un seul exemplaire) */
      poster('creer', corps).then(function (v) {
        window.location.href = prefix + '/corporate?vue=bibliotheque&questionnaire=' + encodeURIComponent(v.id);
      }).catch(function (e) { etat.textContent = e.message; bouton.disabled = false; });
    });
  });

  /* ---------------------------------------------------------------- formulaire de l'acte */
  var racine = document.querySelector('[data-q108]');
  if (!racine) return;
  var id = racine.getAttribute('data-id'), revision = Number(racine.getAttribute('data-revision'));
  var form = racine.querySelector('.q108-form'), etat = racine.querySelector('.q108-etat');
  var CLE = 'q108:' + id;
  var brouillon = lire(CLE) || { reponses: {} };
  var position = null;
  function dire(t) { etat.textContent = t; }
  function memoriser() { brouillon.revision = revision; ecrire(CLE, brouillon); }

  function valeurDe(ligne) {
    var type = ligne.getAttribute('data-type'), nom = ligne.getAttribute('data-q108-champ');
    if (type === 'booleen') {
      var c = ligne.querySelector('input[name="' + nom + '"]:checked');
      return c ? c.value === 'true' : null;
    }
    var champ = ligne.querySelector('[name="' + nom + '"]');
    return champ && String(champ.value).trim() !== '' ? champ.value : null;
  }
  function poser(ligne, valeur) {
    var type = ligne.getAttribute('data-type'), nom = ligne.getAttribute('data-q108-champ');
    if (type === 'booleen') {
      $$('input[name="' + nom + '"]', ligne).forEach(function (r) { r.checked = (r.value === 'true') === valeur; });
      return;
    }
    var champ = ligne.querySelector('[name="' + nom + '"]');
    if (champ) champ.value = valeur === null || valeur === undefined ? '' : String(valeur);
  }
  function noter(ligne) {
    var nom = ligne.getAttribute('data-q108-champ'), statut = ligne.getAttribute('data-choix') || '';
    var r = { valeur: valeurDe(ligne), statut: statut, source: ligne.getAttribute('data-source') || '' };
    brouillon.reponses[nom] = r;
    memoriser();
  }
  function marquer(ligne, statut) {
    ligne.setAttribute('data-choix', statut || '');
    $$('.q108-statut', ligne).forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-statut') === statut ? 'true' : 'false'); });
  }

  // saisie non enregistrée restaurée (même questionnaire) : elle n'est jamais perdue par une fermeture, une pause de l'IA ou un refus 409
  var restaurees = 0;
  Object.keys(brouillon.reponses || {}).forEach(function (nom) {
    var ligne = form.querySelector('[data-q108-champ="' + (window.CSS && CSS.escape ? CSS.escape(nom) : nom) + '"]');
    if (!ligne) return;
    var r = brouillon.reponses[nom];
    if (r.statut) marquer(ligne, r.statut); else if (r.valeur !== null && r.valeur !== undefined) poser(ligne, r.valeur);
    if (r.source) ligne.setAttribute('data-source', r.source);
    restaurees++;
  });
  if (restaurees) { var avis = racine.querySelector('.q108-brouillon'); if (avis) avis.hidden = false; }

  // « Dicter la réponse » sur chaque question à saisir (texte, partie, date, montant) : la dictée remplit le champ comme une frappe (brouillon local)
  if (window.axiorhubDictee108) {
    $$('[data-q108-champ]', form).forEach(function (ligne) {
      var type = ligne.getAttribute('data-type');
      if (['texte', 'partie', 'date', 'montant'].indexOf(type) < 0) return;
      var champ = ligne.querySelector('[name="' + ligne.getAttribute('data-q108-champ') + '"]');
      if (champ) window.axiorhubDictee108.monter(champ, { type: type, apres: type === 'montant' ? champ.parentNode : champ });
    });
  }

  form.addEventListener('input', function (ev) {
    var ligne = ev.target.closest('[data-q108-champ]'); if (!ligne) return;
    ligne.removeAttribute('data-source'); marquer(ligne, ''); noter(ligne);
  });
  form.addEventListener('change', function (ev) {
    var ligne = ev.target.closest('[data-q108-champ]'); if (!ligne) return;
    if (ev.target.type === 'radio') { ligne.removeAttribute('data-source'); marquer(ligne, ''); }
    noter(ligne);
  });
  form.addEventListener('click', function (ev) {
    var b = ev.target.closest('button'); if (!b) return;
    var ligne = b.closest('[data-q108-champ]');
    if (b.classList.contains('q108-statut') && ligne) {
      var s = b.getAttribute('data-statut');
      marquer(ligne, ligne.getAttribute('data-choix') === s ? '' : s); noter(ligne);
    } else if ((b.classList.contains('q108-retenir') || b.classList.contains('q108-suggerer')) && ligne) {
      var v; try { v = JSON.parse(b.getAttribute('data-valeur')); } catch (e) { return; }
      poser(ligne, v); marquer(ligne, '');
      if (b.classList.contains('q108-retenir')) ligne.setAttribute('data-source', b.getAttribute('data-source')); else ligne.removeAttribute('data-source');
      noter(ligne); dire('Valeur reprise : enregistrez pour la retenir.');
    }
  });
  $$('.q108-groupe', form).forEach(function (d) {
    d.addEventListener('toggle', function () { if (d.open) position = d.getAttribute('data-groupe'); });
  });

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var reponses = {};
    Object.keys(brouillon.reponses || {}).forEach(function (nom) {
      var r = brouillon.reponses[nom];
      if (r.statut) reponses[nom] = { statut: r.statut };
      else if (r.valeur !== null && r.valeur !== undefined) reponses[nom] = r.source ? { statut: 'confirme', valeur: r.valeur, source: r.source } : { statut: 'confirme', valeur: r.valeur };
    });
    var bouton = form.querySelector('button[type=submit]');
    if (!Object.keys(reponses).length && !position) { dire('Aucune réponse nouvelle à enregistrer.'); return; }
    bouton.disabled = true; dire('Enregistrement…');
    var corps = { id: id, revision: revision, reponses: reponses };
    if (position) corps.position = position;
    poster('repondre', corps).then(function () {
      ecrire(CLE, null); window.location.reload();
    }).catch(function (e) {
      bouton.disabled = false;
      dire(e.message + (e.statut === 409 ? '' : ' Votre saisie reste dans ce navigateur.'));
    });
  });

  var tele = racine.querySelector('.q108-telecharger');
  if (tele) tele.addEventListener('click', function () {
    tele.disabled = true; dire('Préparation du brouillon…');
    poster('telecharger', { id: id, revision: revision }).then(function (v) {
      return fetch(v.url, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (res) {
        var d = res.download; if (!d || !d.base64) throw new Error(res.message || 'Brouillon indisponible : relancez le téléchargement.');
        var blob = new Blob([Uint8Array.from(atob(d.base64), function (c) { return c.charCodeAt(0); })], { type: d.mime });
        var lien = URL.createObjectURL(blob), a = document.createElement('a');
        a.href = lien; a.download = d.filename; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(lien); }, 2000);
        dire(v.message);
      });
    }).catch(function (e) { dire(e.message); }).then(function () { tele.disabled = false; });
  });
  var ia = racine.querySelector('.q108-rediger');
  if (ia) ia.addEventListener('click', function () {
    ia.disabled = true; dire('Demande en cours…');
    poster('rediger', { id: id, revision: revision }).then(function (v) { dire(v.message); })
      .catch(function (e) { dire(e.message); ia.disabled = false; });
  });
  $$('.q108-enregistrer-correction', racine).forEach(function (b) {
    b.addEventListener('click', function () {
      var section = b.closest('[data-section]'), zone = section.querySelector('textarea');
      b.disabled = true; dire('Enregistrement de la correction…');
      poster('corriger', { id: id, revision: revision, section: section.getAttribute('data-section'), texte: zone.value }).then(function () {
        window.location.reload();
      }).catch(function (e) { dire(e.message); b.disabled = false; });
    });
  });
})();
