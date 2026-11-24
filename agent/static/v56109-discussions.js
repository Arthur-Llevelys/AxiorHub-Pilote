/* AxiorHub 5.6.109 (lot H, F09) : actions sur une discussion (vue agrandie /assistant) — renommer, épingler, grouper, archiver, retirer de
   ma liste, rattacher, changer de dossier, partager (Lecteur / Participant), retirer l'accès, supprimer (corbeille), restaurer ; groupes
   personnels ; politique du cabinet (administrateur) ; attribution d'anciennes discussions (administrateur).
   Le serveur relit propriété, ACL, politique, droits et révision à chaque demande : ce script n'autorise rien. Chaque demande porte une clé
   d'idempotence et la révision lue ; un conflit (409) invite à actualiser sans rien écraser. Textes insérés par textContent. */
(function () {
  'use strict';
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
  var prefix = meta('axiorhub-prefix') || '';
  var csrf = meta('axiorhub-csrf') || '';
  var D = prefix + '/api440/m56109/discussions/', H = prefix + '/api440/m56109/historique/';

  function el(tag, texte, cls) { var n = document.createElement(tag); if (texte !== undefined && texte !== null) n.textContent = texte; if (cls) n.className = cls; return n; }
  function cle() { var a = new Uint8Array(12); (window.crypto || {}).getRandomValues ? crypto.getRandomValues(a) : a.forEach(function (_, i) { a[i] = Math.random() * 256; });
    return 'h109-' + Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join(''); }
  function bouton(texte, cls) { var b = el('button', texte, cls || 'ax-btn ghost'); b.type = 'button'; return b; }
  function dire(texte) {
    var p = document.querySelector('[data-h109-etat]') || document.getElementById('ax-toast');
    if (!p) { p = el('p', '', 'h109-statut'); p.setAttribute('role', 'status'); var r = document.querySelector('[data-h109]'); if (r) r.prepend(p); }
    p.textContent = texte;
  }
  function poster(base, action, corps) {
    corps.cle = cle();
    return fetch(base + action, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) { var e = new Error(d.message || 'Demande refusée.'); e.code = d.error; throw e; } return d; }); });
  }
  function apres(promesse, message) {
    return promesse.then(function () { dire(message || 'Enregistré.'); setTimeout(function () { location.reload(); }, 400); })
      .catch(function (e) { dire(e.message + (e.code === 'conflit_de_revision' ? ' (Actualisez la page.)' : '')); });
  }
  function obtenir(url) { return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.message || 'Indisponible.'); return d; }); }); }

  /* ------------------------------------------------------------------ actions du fil choisi */
  function actionsFil() {
    var detail = document.querySelector('[data-h109-detail]'), zone = detail && detail.querySelector('[data-h109-actions]');
    if (!zone || zone.dataset.pret) return;
    zone.dataset.pret = '1';
    var id = detail.dataset.h109Detail, rev = parseInt(detail.dataset.h109Revision, 10), role = detail.dataset.h109Role, proprio = role === 'proprietaire';
    var ligne = document.querySelector('[data-h109-fil="' + id + '"]');
    var epingle = ligne ? ligne.dataset.h109Epingle === '1' : false, groupeActuel = ligne ? ligne.dataset.h109Groupe : '';
    var barre = el('div', null, 'h109-barre'); barre.setAttribute('role', 'group'); barre.setAttribute('aria-label', 'Actions sur la discussion');
    function pref(valeurs, msg) { return apres(poster(D, 'preferences', { id: id, valeurs: valeurs }), msg); }

    if (proprio) {
      var renommer = bouton('Renommer');
      renommer.addEventListener('click', function () {
        var titre = window.prompt('Nouveau titre de la discussion (le texte des messages ne change pas) :', (document.querySelector('[data-h109-titre]') || {}).textContent || '');
        if (titre !== null) apres(poster(D, 'renommer', { id: id, titre: titre, revision: rev }), 'Discussion renommée.');
      });
      barre.append(renommer);
    }
    var bEp = bouton(epingle ? 'Désépingler' : 'Épingler'); bEp.addEventListener('click', function () { pref({ pinned: !epingle }, epingle ? 'Discussion désépinglée.' : 'Discussion épinglée.'); });
    var bAr = bouton('Archiver pour moi'); bAr.addEventListener('click', function () { pref({ archived: !/archives=1/.test(location.search) }, 'Préférence enregistrée.'); });
    if (/archives=1/.test(location.search)) bAr.textContent = 'Désarchiver';
    var bMa = bouton('Retirer de ma liste'); bMa.addEventListener('click', function () { pref({ hidden: true }, 'Retirée de votre liste (rien n’est supprimé).'); });
    barre.append(bEp, bAr, bMa);

    // groupe personnel (un niveau)
    obtenir(H + 'groupes').then(function (d) {
      var choix = el('select'); choix.setAttribute('aria-label', 'Groupe de la discussion');
      choix.append(new Option('Sans groupe', ''));
      (d.groupes || []).forEach(function (g) { choix.append(new Option(g.name, g.id, false, g.id === groupeActuel)); });
      choix.addEventListener('change', function () { pref({ group_id: choix.value || null }, 'Groupe enregistré.'); });
      var lab = el('label', 'Groupe ', 'h109-groupe'); lab.append(choix); barre.append(lab);
    }).catch(function () { /* groupes facultatifs */ });

    if (proprio) {
      var bRat = bouton('Rattacher');
      bRat.addEventListener('click', function () {
        var type = window.prompt('Type d’élément à rattacher : dossier, courriel, pièce ou discussion', 'dossier');
        var types = { dossier: 'matter', courriel: 'mail', 'pièce': 'piece', piece: 'piece', discussion: 'ai_thread' };
        if (!type || !types[type.trim().toLowerCase()]) return;
        var ident = window.prompt('Identifiant de l’élément (référence du dossier, clé du courriel, chemin de la pièce ou identifiant de la discussion) :', '');
        if (ident) apres(poster(D, 'rattacher', { id: id, type: types[type.trim().toLowerCase()], element: ident.trim(), version: '', revision: rev }), 'Élément rattaché (rien n’a été importé ni partagé).');
      });
      var bCtx = bouton('Changer de dossier');
      bCtx.addEventListener('click', function () {
        var mid = window.prompt('Référence du nouveau dossier (vide : sans dossier). Les messages déjà écrits gardent leur dossier d’origine.', '');
        if (mid !== null) apres(poster(D, 'contexte', { id: id, dossier: mid.trim(), revision: rev }), 'Dossier de la discussion changé.');
      });
      var bSup = bouton('Supprimer', 'ax-btn danger');
      bSup.addEventListener('click', function () {
        if (!window.confirm('Supprimer cette discussion pour tous les participants ? Elle va dans la corbeille (restaurable). Aucune pièce, aucun courriel ni aucun document n’est supprimé.')) return;
        poster(D, 'supprimer', { id: id, revision: rev, confirmation: 'supprimer_pour_tous' })
          .then(function () { location.href = prefix + '/assistant?vue=historique'; })
          .catch(function (e) { dire(e.message); });
      });
      barre.append(bRat, bCtx, bSup);
    }
    zone.append(barre);

    // participants : liste pour tous ; partage et retrait pour le propriétaire
    var bloc = el('details', null, 'h109-membres'); bloc.append(el('summary', 'Participants'));
    zone.append(bloc);
    bloc.addEventListener('toggle', function charge() {
      if (!bloc.open || bloc.dataset.pret) return;
      bloc.dataset.pret = '1';
      obtenir(H + 'membres?id=' + encodeURIComponent(id)).then(function (d) {
        bloc.append(el('p', 'Propriétaire : ' + d.proprietaire));
        var ul = el('ul');
        (d.membres || []).forEach(function (m) {
          var li = el('li', m.nom + ' — ' + m.role_libelle + (m.actif ? '' : ' (compte inactif)'));
          if (proprio) {
            var r = bouton('Retirer l’accès');
            r.addEventListener('click', function () { apres(poster(D, 'revoquer', { id: id, compte: m.user_id, revision: rev }), 'Accès retiré.'); });
            li.append(' ', r);
          }
          ul.append(li);
        });
        if (!(d.membres || []).length) ul.append(el('li', 'Discussion privée : aucun participant.'));
        bloc.append(ul);
        if (proprio && d.candidats) {
          if (!d.politique.internal_share_enabled) { bloc.append(el('p', 'Le partage interne est désactivé par l’administrateur du cabinet.', 'h109-note')); return; }
          var qui = el('select'); qui.setAttribute('aria-label', 'Compte du cabinet');
          d.candidats.forEach(function (c) { qui.append(new Option(c.nom + (c.role ? ' (' + c.role + ')' : ''), c.user_id)); });
          var role = el('select'); role.setAttribute('aria-label', 'Rôle');
          role.append(new Option('Lecteur (consulte)', 'viewer'), new Option('Participant (peut continuer avec ses propres droits)', 'contributor'));
          var p = bouton('Partager', 'ax-btn');
          p.addEventListener('click', function () { if (qui.value) apres(poster(D, 'partager', { id: id, compte: qui.value, role: role.value, revision: rev }), 'Discussion partagée.'); });
          var f = el('div', null, 'h109-partage'); f.append(el('p', 'Partager avec un compte du cabinet. Partager une discussion n’ouvre aucune pièce ni aucun dossier.', 'h109-note'), qui, role, p);
          bloc.append(f);
        }
      }).catch(function (e) { bloc.append(el('p', e.message, 'h109-alerte')); });
    });
  }

  /* ------------------------------------------------------------------ groupes personnels (colonne de la liste) */
  function groupes() {
    var liste = document.querySelector('.h109-liste');
    if (!liste || liste.dataset.groupes) return;
    liste.dataset.groupes = '1';
    var bloc = el('details', null, 'h109-groupes'); bloc.append(el('summary', 'Mes groupes'));
    liste.append(bloc);
    bloc.addEventListener('toggle', function () {
      if (!bloc.open || bloc.dataset.pret) return;
      bloc.dataset.pret = '1';
      obtenir(H + 'groupes').then(function (d) {
        var ul = el('ul');
        (d.groupes || []).forEach(function (g) {
          var li = el('li', g.name + ' ');
          var r = bouton('Renommer'); r.addEventListener('click', function () { var n = window.prompt('Nouveau nom du groupe :', g.name); if (n) apres(poster(D, 'groupe-modifier', { groupe: g.id, nom: n, revision: g.revision })); });
          var s = bouton('Supprimer le groupe'); s.addEventListener('click', function () { if (window.confirm('Supprimer le groupe « ' + g.name + ' » ? Ses discussions passent dans « Sans groupe » ; aucune n’est supprimée.')) apres(poster(D, 'groupe-supprimer', { groupe: g.id, revision: g.revision })); });
          li.append(r, s); ul.append(li);
        });
        var nom = el('input'); nom.maxLength = 60; nom.placeholder = 'Nom du groupe'; nom.setAttribute('aria-label', 'Nom du nouveau groupe');
        var c = bouton('Créer le groupe'); c.addEventListener('click', function () { if (nom.value.trim()) apres(poster(D, 'groupe-creer', { nom: nom.value.trim() }), 'Groupe créé.'); });
        bloc.append(ul, nom, c);
      }).catch(function (e) { bloc.append(el('p', e.message, 'h109-alerte')); });
    });
  }

  /* ------------------------------------------------------------------ corbeille, attribution, politique */
  function corbeille() {
    document.querySelectorAll('[data-h109-restaurer]').forEach(function (b) {
      b.addEventListener('click', function () {
        var li = b.closest('[data-h109-fil]');
        apres(poster(D, 'restaurer', { id: li.dataset.h109Fil, revision: parseInt(li.dataset.h109Revision, 10) }), 'Discussion restaurée.');
      });
    });
  }
  function attribution() {
    var b = document.querySelector('[data-h109-attribuer]');
    if (!b) return;
    b.addEventListener('click', function () {
      var ids = Array.prototype.map.call(document.querySelectorAll('[data-h109-ancien]:checked'), function (x) { return x.value; });
      var compte = document.querySelector('[data-h109-compte-cible]').value, preuve = document.querySelector('[data-h109-preuve]').value;
      if (!ids.length) { dire('Cochez au moins une discussion.'); return; }
      apres(poster(H, 'attribuer', { ids: ids, compte: compte, preuve: preuve }), 'Attribution enregistrée et tracée.');
    });
  }
  function politique() {
    var f = document.querySelector('[data-h109-politique]');
    var b = f && f.querySelector('[data-h109-politique-enregistrer]');
    if (!b) return;
    b.addEventListener('click', function () {
      var valeurs = { user_delete_enabled: f.querySelector('[name=user_delete_enabled]').checked, internal_share_enabled: f.querySelector('[name=internal_share_enabled]').checked,
        corbeille_jours: parseInt(f.querySelector('[name=corbeille_jours]').value, 10) };
      apres(poster(D, 'politique', { valeurs: valeurs, revision: parseInt(f.dataset.h109Revision, 10) }), 'Réglage enregistré.');
    });
  }

  function demarrer() { actionsFil(); groupes(); corbeille(); attribution(); politique(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', demarrer); else demarrer();
})();
