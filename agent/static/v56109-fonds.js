/* AxiorHub 5.6.109 (lot Q) : « Fonds juridique local » (G23) et « Fichiers de ce poste » (F13) dans les Paramètres.
   Aucun HTML reçu n'est injecté : les résultats sont construits par textContent. Chaque écriture porte une clé d'idempotence. */
(function () {
  'use strict';
  if (window.__axF56109) { return; }
  window.__axF56109 = true;
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
  var prefix = meta('axiorhub-prefix') || '';
  var csrf = meta('axiorhub-csrf') || '';
  var FONDS = prefix + '/api440/m56109/fonds/';
  var LOCAL = prefix + '/api440/m56109/fichiers-locaux/';

  function el(tag, texte, cls) { var n = document.createElement(tag); if (texte !== undefined && texte !== null) { n.textContent = texte; } if (cls) { n.className = cls; } return n; }
  function cle() { return 'q109-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10); }
  function lire(url) {
    return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) { throw new Error(d.message || 'Demande refusée.'); } return d; });
    });
  }
  function ecrire(url, corps) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(corps) }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) { throw new Error(d.message || 'Demande refusée.'); } return d; });
    });
  }
  function statut(section, texte) { var o = section.querySelector('[role="status"]'); if (o) { o.textContent = texte; } }
  function params(o) { return Object.keys(o).filter(function (k) { return o[k]; }).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); }

  function afficherFonds(zone, d) {
    zone.textContent = '';
    zone.appendChild(el('p', d.message || ''));
    (d.constats || []).forEach(function (c) { zone.appendChild(el('p', c.official_id + ' : ' + (c.etat === 'version_absente_a_cette_date' ? 'aucune version en vigueur à cette date dans le fonds' : 'couverture temporelle à vérifier'), 'a-verifier')); });
    if (!(d.resultats || []).length) { zone.appendChild(el('p', d.etat === 'index_a_reconstruire' ? 'Index à reconstruire.' : 'Aucun passage trouvé dans le fonds consulté.')); return; }
    d.resultats.forEach(function (r) {
      var a = el('article');
      a.appendChild(el('strong', (r.titre || r.official_id) + ' — version ' + r.version_id));
      a.appendChild(el('small', r.juridiction + ' · ' + (r.valid_from ? 'en vigueur depuis le ' + r.valid_from : (r.decision_date || '')) + ' · corpus arrêté au ' + (r.corpus_au || 'date inconnue')));
      a.appendChild(el('p', r.extrait));
      zone.appendChild(a);
    });
  }
  function afficherLocal(zone, d) {
    zone.textContent = '';
    if (d.reponse) { zone.appendChild(el('p', d.reponse)); }
    (d.citations || []).forEach(function (c) {
      var a = el('article', null, c.etat === 'verifiee' ? 'verifiee' : 'a-verifier');
      a.appendChild(el('strong', (c.etat === 'verifiee' ? 'Citation relue dans la source' : 'À vérifier') + ' — ' + c.document + ' · ' + c.localisateur));
      a.appendChild(el('p', c.passage));
      zone.appendChild(a);
    });
    if (d.etat === 'ia_en_pause') { zone.appendChild(el('p', 'IA en pause : passages sourcés seulement.')); }
    var passages = d.resultats || d.passages || [];
    if (!passages.length && !d.reponse) { zone.appendChild(el('p', 'Aucun passage trouvé dans le périmètre consulté (ce n’est pas une absence de documents).')); }
    passages.forEach(function (r) {
      var a = el('article');
      a.appendChild(el('strong', r.nom + ' · ' + r.localisateur.libelle));
      a.appendChild(el('small', r.libelle_emplacement + ' · version ' + String(r.version).slice(1, 13)));
      a.appendChild(el('p', r.extrait));
      zone.appendChild(a);
    });
  }

  document.addEventListener('submit', function (ev) {
    var f = ev.target;
    var section = f.closest('.f56109, .l56109');
    if (!section) { return; }
    ev.preventDefault();
    var donnees = {};
    Array.prototype.forEach.call(f.elements, function (x) { if (x.name) { donnees[x.name] = x.value; } });
    var action = f.getAttribute('data-f56109-form') || f.getAttribute('data-l56109-form');
    var travail;
    if (action === 'importer') { travail = ecrire(FONDS + 'importer', { chemin: donnees.chemin, cle: cle() }); }
    else if (action === 'rechercher') {
      travail = lire(FONDS + 'rechercher?' + params({ q: donnees.q, date: donnees.date })).then(function (d) { afficherFonds(section.querySelector('.f56109-resultats'), d); return d; });
    } else if (action === 'ajouter') { travail = ecrire(LOCAL + 'ajouter', { chemin: donnees.chemin, libelle: donnees.libelle, dossier: donnees.dossier, cle: cle() }); }
    else if (action === 'chercher') {
      travail = lire(LOCAL + 'chercher?' + params({ ids: section.getAttribute('data-ids'), q: donnees.q })).then(function (d) { afficherLocal(section.querySelector('.l56109-resultats'), d); return d; });
    }
    if (!travail) { return; }
    statut(section, 'En cours…');
    travail.then(function (d) { statut(section, d.libelle || d.libelle_etat || 'Terminé.'); }).catch(function (e) { statut(section, e.message); });
  });

  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-f56109], [data-l56109]');
    if (!b) { return; }
    var section = b.closest('.f56109, .l56109');
    var travail;
    if (b.hasAttribute('data-f56109')) {
      travail = ecrire(FONDS + b.getAttribute('data-f56109'), { lot: b.getAttribute('data-lot') || '', fonds: b.getAttribute('data-fonds') || '',
        version: b.getAttribute('data-version') ? Number(b.getAttribute('data-version')) : null, cle: cle() });
    } else if (b.getAttribute('data-l56109') === 'interroger') {
      var q = section.querySelector('input[name="q"]');
      travail = ecrire(LOCAL + 'interroger', { ids: (section.getAttribute('data-ids') || '').split(','), question: q ? q.value : '', cle: cle() })
        .then(function (d) { afficherLocal(section.querySelector('.l56109-resultats'), d); return d; });
    } else {
      travail = ecrire(LOCAL + b.getAttribute('data-l56109'), { id: b.getAttribute('data-id'),
        version: b.getAttribute('data-version') ? Number(b.getAttribute('data-version')) : null, cle: cle() });
    }
    b.disabled = true;
    statut(section, 'En cours…');
    travail.then(function (d) { statut(section, (d.libelle || (d.emplacement && d.emplacement.libelle_etat) || d.libelle_etat || 'Terminé.') + ' Rechargez la page pour voir l’état à jour.'); })
      .catch(function (e) { statut(section, e.message); }).then(function () { b.disabled = false; });
  });
}());
