/* AxiorHub 5.6.109 (lot V, F08) — Produire : création d'un livrable structuré depuis une trame, édition des lignes dans le lecteur commun,
   plan des sous-agents, modèles Word du cabinet (import, validation, production contrôlée).
   Chaque enregistrement envoie la révision lue et une clé d'opération : 409 = le livrable a changé, la saisie reste dans la page ; réponse
   perdue = « Vérifier » relit l'état durable de la même clé (aucune nouvelle écriture). Textes posés par textContent ou value, jamais en HTML.
   Sous Node (tests), le module exporte ses fonctions pures. */
(function () {
  'use strict';

  /* Contenu à enregistrer, reconstruit depuis l'état de l'éditeur : mêmes blocs, mêmes identifiants de lignes, rien d'autre. */
  function construirePayload(base, blocs) {
    return { kind: base.kind, title: base.title, blocks: blocs.map(function (b) {
      if (b.type === 'table') {
        return { id: b.id, type: 'table', source_ids: b.source_ids.slice(), columns: b.columns.slice(),
                 rows: b.rows.map(function (r) { return { id: r.id, cells: r.cells.slice(), statut: r.statut, source_ids: r.source_ids.slice() }; }) };
      }
      return { id: b.id, type: b.type, source_ids: b.source_ids.slice(), text: b.text };
    }) };
  }

  /* Identifiant stable d'une ligne nouvelle : L<n> libre dans le tableau (jamais la réutilisation d'un identifiant existant). */
  function nouvelId(rows) {
    var pris = {}, n = 1;
    rows.forEach(function (r) { pris[r.id] = true; });
    while (pris['L' + n]) { n += 1; }
    return 'L' + n;
  }

  var api = { construirePayload: construirePayload, nouvelId: nouvelId };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  if (window.AxhProduire109) { return; }                    // script inclus deux fois (vues et lecteur) : une seule initialisation
  window.AxhProduire109 = api;

  function meta(nom) { var m = document.querySelector('meta[name="' + nom + '"]'); return m ? m.getAttribute('content') : ''; }
  function cle() {
    var a = new Uint8Array(12); window.crypto.getRandomValues(a);
    return 'v109-' + Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function reponse(r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, statut: r.status, j: j }; }); }
  function poster(url, donnees, octets) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': octets ? 'application/octet-stream' : 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') },
      body: octets || JSON.stringify(donnees) }).then(reponse);
  }
  function el(tag, classe, texte) { var n = document.createElement(tag); if (classe) { n.className = classe; } if (texte !== undefined) { n.textContent = texte; } return n; }
  function message(r, defaut) { return (r && r.j && r.j.message) || defaut || 'Demande refusée.'; }
  function etatDe(form) { return form.querySelector('.v109-etat'); }
  function dire(zone, texte, alerte) { if (zone) { zone.textContent = texte || ''; zone.classList.toggle('v109-erreur', !!alerte); } }

  // ------------------------------------------------------------------------------------------------ formulaires (Créer, Modèles, plan)
  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-v109]');
    if (!form) { return; }
    ev.preventDefault();
    var nature = form.getAttribute('data-v109'), url = form.getAttribute('data-v109-api'), zone = etatDe(form);
    var bouton = form.querySelector('button[type="submit"]'); if (bouton) { bouton.disabled = true; }
    var fin = function () { if (bouton) { bouton.disabled = false; } };
    var f = form.elements;
    if (nature === 'creer') {
      dire(zone, 'Création…');
      poster(url, { trame: f.trame.value, dossier: f.dossier.value, titre: f.titre.value, cle: cle() }).then(function (r) {
        dire(zone, message(r), !r.ok); fin();
        if (r.ok && r.j.href) { location.href = r.j.href; }
      }, function () { dire(zone, 'Serveur injoignable : rien n’a été confirmé.', true); fin(); });
    } else if (nature === 'importer') {
      var fichier = f.fichier.files && f.fichier.files[0];
      if (!fichier) { dire(zone, 'Choisissez un fichier .docx.', true); fin(); return; }
      fichier.arrayBuffer().then(function (octets) {
        var q = ['nom', 'type', 'droit', 'juridiction', 'variante'].map(function (k) { return k + '=' + encodeURIComponent(f[k].value || ''); }).join('&');
        return poster(url + '?' + q + '&cle=' + cle(), null, octets);
      }).then(function (r) { dire(zone, r.ok ? 'Modèle importé : relisez ses zones et ses parties fixes, puis faites-le valider.' : message(r), !r.ok); fin(); if (r.ok) { location.reload(); } },
        function () { dire(zone, 'Import interrompu : rien n’a été confirmé.', true); fin(); });
    } else if (nature === 'exemple' || nature === 'valider') {
      var corps = { cle: cle() };
      if (nature === 'valider') { corps.sha256 = f.sha256.value; }
      poster(url, corps).then(function (r) { dire(zone, message(r, r.ok ? 'Fait.' : ''), !r.ok); fin(); if (r.ok) { location.reload(); } },
        function () { dire(zone, 'Serveur injoignable.', true); fin(); });
    } else if (nature === 'produire') {
      var valeurs = {};
      Array.prototype.forEach.call(form.querySelectorAll('textarea[name^="champ:"]'), function (t) { valeurs[t.name.slice(6)] = t.value; });
      var cleProd = form.getAttribute('data-v109-cle') || cle();
      form.setAttribute('data-v109-cle', cleProd);               // un nouvel essai après une réponse perdue relit la même production
      dire(zone, 'Production et contrôle du document…');
      poster(url, { dossier: f.dossier.value, titre: f.titre.value, valeurs: valeurs, cle: cleProd }).then(function (r) {
        fin();
        if (!r.ok) { dire(zone, message(r), true); form.removeAttribute('data-v109-cle'); return; }
        dire(zone, r.j.etat_label || 'État inconnu.', !r.j.pret);
        if (r.j.pret && r.j.href) {
          var a = el('a', 'ax-btn ghost', 'Ouvrir le document'); a.href = r.j.href; zone.appendChild(document.createTextNode(' ')); zone.appendChild(a);
          form.removeAttribute('data-v109-cle');
        }
      }, function () { fin(); dire(zone, 'Réponse perdue : renvoyez la même demande, elle relira le même fichier (rien d’autre ne sera écrit).', true); });
    } else if (nature === 'plan') {
      var plan;
      try { plan = JSON.parse(f.plan.value || '[]'); } catch (err) { dire(zone, 'Plan illisible (JSON attendu).', true); fin(); return; }
      var conf = configLecteur();
      poster(conf.plan, { plan: plan, budget_tokens: Number(f.budget_tokens.value), cle: cle() }).then(function (r) { dire(zone, message(r), !r.ok); fin(); },
        function () { dire(zone, 'Serveur injoignable.', true); fin(); });
    } else { fin(); }
  });

  // ------------------------------------------------------------------------------------------------ éditeur du lecteur commun
  function configLecteur() {
    var c = document.querySelector('[data-v109-config]');
    try { return c ? JSON.parse(c.getAttribute('data-v109-config')) : null; } catch (err) { return null; }
  }
  var conf = configLecteur();
  var hote = document.querySelector('[data-v109-editeur]');
  if (!conf || !hote) { return; }
  var zoneEtat = document.querySelector('[data-v109-etat]');
  var etat = { base: null, blocs: [], revision: 0, sale: false, cle: '' };

  function marquer() { etat.sale = true; dire(zoneEtat, 'Modifications non enregistrées.'); }
  function cellule(valeur, maj) {
    var i = el('input'); i.type = 'text'; i.value = valeur; i.disabled = !conf.modifiable;
    i.addEventListener('input', function () { maj(i.value); marquer(); });
    return i;
  }
  function rendre() {
    hote.textContent = '';
    etat.blocs.forEach(function (b) {
      if (b.type !== 'table') {
        var t = el('textarea', 'v109-texte'); t.value = b.text; t.rows = b.type === 'heading' ? 1 : 3; t.disabled = !conf.modifiable;
        t.setAttribute('aria-label', b.type === 'heading' ? 'Titre de section' : 'Paragraphe');
        t.addEventListener('input', function () { b.text = t.value; marquer(); });
        hote.appendChild(t); return;
      }
      var table = el('table', 'v109-table'), tete = el('tr');
      b.columns.concat(['Statut', '']).forEach(function (c) { var th = el('th', '', c); th.scope = 'col'; tete.appendChild(th); });
      var thead = el('thead'); thead.appendChild(tete); table.appendChild(thead);
      var corps = el('tbody');
      b.rows.forEach(function (r, index) {
        var tr = el('tr'); tr.setAttribute('data-ligne', r.id);
        r.cells.forEach(function (v, j) { var td = el('td'); td.appendChild(cellule(v, function (x) { r.cells[j] = x; })); tr.appendChild(td); });
        var tdS = el('td'), s = el('select'); s.disabled = !conf.modifiable; s.setAttribute('aria-label', 'Statut de la ligne ' + r.id);
        Object.keys(conf.statuts).forEach(function (k) { var o = el('option', '', conf.statuts[k]); o.value = k; o.selected = k === r.statut; s.appendChild(o); });
        s.addEventListener('change', function () { r.statut = s.value; marquer(); });
        tdS.appendChild(s); tr.appendChild(tdS);
        var tdX = el('td');
        if (conf.modifiable) {
          var x = el('button', 'ax-btn ghost', 'Retirer'); x.type = 'button'; x.setAttribute('aria-label', 'Retirer la ligne ' + r.id);
          x.addEventListener('click', function () { b.rows.splice(index, 1); marquer(); rendre(); });
          tdX.appendChild(x);
        }
        tr.appendChild(tdX); corps.appendChild(tr);
      });
      table.appendChild(corps); hote.appendChild(table);
      if (conf.modifiable) {
        var ajout = el('button', 'ax-btn ghost', 'Ajouter une ligne'); ajout.type = 'button';
        ajout.addEventListener('click', function () {
          b.rows.push({ id: nouvelId(b.rows), cells: b.columns.map(function () { return ''; }), statut: 'a_confirmer', source_ids: [] });
          marquer(); rendre();
        });
        hote.appendChild(ajout);
      }
    });
    if (conf.modifiable) {
      var enr = el('button', 'ax-btn', 'Enregistrer une nouvelle version'); enr.type = 'button'; enr.setAttribute('data-v109-enregistrer', '1');
      enr.addEventListener('click', enregistrer); hote.appendChild(enr);
    }
  }
  function charger() {
    fetch(conf.contenu, { credentials: 'same-origin', cache: 'no-store' }).then(reponse).then(function (r) {
      if (!r.ok) { dire(zoneEtat, message(r, 'Contenu indisponible.'), true); return; }
      etat.base = r.j.payload; etat.revision = r.j.revision;
      etat.blocs = JSON.parse(JSON.stringify(r.j.payload.blocks));
      if (r.j.revision !== r.j.derniere_revision) { conf.modifiable = false; }
      rendre();
      dire(zoneEtat, 'Version ' + r.j.revision + ' enregistrée sur le serveur.');
    }, function () { dire(zoneEtat, 'Contenu indisponible pour l’instant.', true); });
  }
  function verifier() {
    if (!etat.cle) { return; }
    fetch(conf.operations + encodeURIComponent(etat.cle), { credentials: 'same-origin', cache: 'no-store' }).then(reponse).then(function (r) {
      if (r.ok) { etat.sale = false; dire(zoneEtat, 'Version ' + r.j.revision + ' bien enregistrée.'); location.reload(); }
      else { dire(zoneEtat, 'Cet enregistrement n’a pas eu lieu : votre saisie est toujours dans la page, enregistrez de nouveau.', true); etat.cle = ''; }
    });
  }
  function enregistrer() {
    var payload = construirePayload(etat.base, etat.blocs);
    etat.cle = etat.cle || cle();
    dire(zoneEtat, 'Enregistrement…');
    poster(conf.enregistrer, { payload: payload, revision: etat.revision, cle: etat.cle }).then(function (r) {
      if (r.statut === 409) { dire(zoneEtat, message(r), true); etat.cle = ''; return; }
      if (!r.ok) { dire(zoneEtat, message(r), true); etat.cle = ''; return; }
      if (r.j.durable !== true) { dire(zoneEtat, 'Enregistrement non confirmé : votre saisie reste dans la page.', true); return; }
      etat.sale = false; etat.cle = '';
      dire(zoneEtat, message(r, 'Version enregistrée.'));
      location.reload();
    }, function () {
      dire(zoneEtat, 'Réponse perdue : l’enregistrement a pu aboutir. Vérifiez avant de recommencer.', true);
      var b = el('button', 'ax-btn ghost', 'Vérifier'); b.type = 'button'; b.addEventListener('click', verifier); zoneEtat.appendChild(b);
    });
  }
  window.addEventListener('beforeunload', function (ev) { if (etat.sale) { ev.preventDefault(); ev.returnValue = ''; } });
  charger();
})();
