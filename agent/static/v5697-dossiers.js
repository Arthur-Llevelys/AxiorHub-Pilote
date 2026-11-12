/* AxiorHub 5.6.97 (lot dossiers) — écran Dossiers, Nouveau dossier, import, « Classer dans un dossier » dans Courriels.
   Tout passe par l'API m57/dossiers (JSON, jeton CSRF, clé d'idempotence rendue par le serveur à l'ouverture du formulaire : un double clic
   ou un rejeu rejoint la même opération). Aucun HTML venant du serveur n'est injecté : les textes passent par textContent. Aucun appel au modèle. */
(function () {
  'use strict';
  if (window.axh5697Dossiers) return;   // chargé une seule fois, même si la page l'inclut deux fois
  window.axh5697Dossiers = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  document.documentElement.classList.add('js');

  function el(tag, cls, texte) { var n = document.createElement(tag); if (cls) n.className = cls; if (texte !== undefined) n.textContent = texte; return n; }
  function uuid() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID().replace(/-/g, '');
    var a = new Uint8Array(16); window.crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function api(route, corps, methode) {
    var opts = corps === undefined ? { credentials: 'same-origin' } : {
      method: methode || 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) };
    return fetch(prefix + '/api440/' + route, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur : rien n’est confirmé.' }; })
        .then(function (d) {
          if (!r.ok || (d && d.error)) {
            var e = new Error((d && d.message) || 'Le serveur a refusé la demande : rien n’a été enregistré.');
            e.code = d && d.error; e.champs = (d && d.champs) || {}; e.status = r.status; throw e;
          }
          return d;
        });
    }, function () {
      var e = new Error('Connexion interrompue : la demande a peut-être été reçue. Renvoyez-la : la même clé évite tout doublon.');
      e.code = 'reseau'; e.champs = {}; throw e;
    });
  }

  /* --------------------------------------------------------------------------------------- lecture d'un formulaire */
  function poser(obj, nom, valeur) {
    var parties = nom.split('.'), o = obj;
    for (var i = 0; i < parties.length - 1; i++) { o[parties[i]] = o[parties[i]] || {}; o = o[parties[i]]; }
    o[parties[parties.length - 1]] = valeur;
  }
  function lire(form) {
    var data = {}, listes = {};
    Array.prototype.forEach.call(form.querySelectorAll('input,select,textarea'), function (f) {
      if (!f.name || f.disabled || (f.closest && f.closest('[data-d97-partie]'))) return;
      if (f.type === 'radio') { if (f.checked) poser(data, f.name, f.value); return; }
      if (f.type === 'checkbox') {
        if (f.name === 'membres') { listes.membres = listes.membres || []; if (f.checked) listes.membres.push(f.value); return; }
        poser(data, f.name, f.checked); return;
      }
      poser(data, f.name, f.value);
    });
    Object.keys(listes).forEach(function (k) { data[k] = listes[k]; });
    var parties = Array.prototype.map.call(form.querySelectorAll('[data-d97-partie]'), function (row) {
      var v = function (n) { var f = row.querySelector('[name="' + n + '"]'); return f ? f.value.trim() : ''; };
      return { nom: v('nom'), courriel: v('courriel'), role: v('role') };
    }).filter(function (p) { return p.nom; });
    if (form.querySelector('[data-d97-partie]')) data.parties = parties;
    if ('client_a_identifier' in data) { data.client_etat = data.client_a_identifier ? 'a_identifier' : (data.client ? 'identifie' : 'a_identifier'); delete data.client_a_identifier; }
    if (data.conflit && !data.conflit.id) delete data.conflit;
    if (data.message && !data.message.uid) delete data.message;
    if (data.version !== undefined && data.version !== '') data.version = parseInt(data.version, 10);
    return data;
  }
  function effacerErreurs(form) {
    Array.prototype.forEach.call(form.querySelectorAll('[data-erreur]'), function (z) { z.textContent = ''; });
    Array.prototype.forEach.call(form.querySelectorAll('[aria-invalid]'), function (f) { f.removeAttribute('aria-invalid'); });
  }
  function montrerErreurs(form, champs) {
    var premier = null;
    Object.keys(champs || {}).forEach(function (nom) {
      var z = form.querySelector('[data-erreur="' + nom + '"]');
      var texte = typeof champs[nom] === 'string' ? champs[nom] : '';
      if (nom === 'proposition' && texte) {
        var ref = form.querySelector('[name="reference"]');
        z = form.querySelector('[data-erreur="reference"]');
        if (ref) { ref.value = texte; }
        texte = 'Référence libre proposée : ' + texte + '. Vérifiez puis renvoyez.';
        if (z) z.textContent = (z.textContent ? z.textContent + ' ' : '') + texte;
        return;
      }
      if (z) z.textContent = texte;
      var f = form.querySelector('[name="' + nom + '"]') || form.querySelector('[name^="' + nom + '."]');
      if (f) { f.setAttribute('aria-invalid', 'true'); if (!premier) premier = f; }
    });
    if (premier) premier.focus();
  }

  /* --------------------------------------------------------------------------------------- formulaires du lot */
  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form.d97-form[data-d97-api]');
    if (!form) return;
    ev.preventDefault();
    if (form.getAttribute('data-d97-envoi') === '1') return;   // double clic ou Entrée répétée : un seul envoi
    var statut = form.querySelector('.d97-statut'), bouton = ev.submitter || form.querySelector('button[type=submit]');
    effacerErreurs(form);
    var data = lire(form);
    if (data.conflit && !data.conflit.decision) {
      montrerErreurs(form, { conflit: 'Choisissez « Poursuivre » ou « Mettre en attente » après la vérification des conflits.' });
      return;
    }
    form.setAttribute('data-d97-envoi', '1');
    if (bouton) { bouton.disabled = true; bouton.setAttribute('aria-busy', 'true'); }
    if (statut) statut.textContent = 'Envoi en cours…';
    api(form.getAttribute('data-d97-api'), data, form.getAttribute('data-d97-methode') || 'POST').then(function (r) {
      if (statut) statut.textContent = r.message || 'Enregistré.';
      var apres = form.getAttribute('data-d97-apres');
      if (apres === 'ouvrir' && r.url) { window.location.href = r.url; return; }
      if (apres === 'recharger') { setTimeout(function () { window.location.reload(); }, 400); }
    }).catch(function (e) {
      if (statut) statut.textContent = e.message;
      montrerErreurs(form, e.champs);
    }).then(function () {
      form.removeAttribute('data-d97-envoi');
      if (bouton) { bouton.disabled = false; bouton.removeAttribute('aria-busy'); }
    });
  });

  /* --------------------------------------------------------------------------------------- Nouveau dossier */
  var nouveau = document.getElementById('d97-nouveau');
  if (nouveau) {
    var parties = nouveau.querySelector('[data-d97-parties]');
    nouveau.addEventListener('click', function (ev) {
      if (ev.target.closest('[data-d97-ajouter-partie]')) {
        var modele = parties.querySelector('[data-d97-partie]'), copie = modele.cloneNode(true);
        Array.prototype.forEach.call(copie.querySelectorAll('input'), function (f) { f.value = ''; });
        parties.appendChild(copie);
        copie.querySelector('input').focus();
      }
      if (ev.target.closest('[data-d97-verifier]')) verifierConflits();
    });
    var chemin = nouveau.querySelector('[data-d97-chemin]'), minuterie = null;
    function proposer() {
      clearTimeout(minuterie);
      minuterie = setTimeout(function () {
        var titre = nouveau.querySelector('[name="titre"]').value || 'Nouveau dossier';
        api('m57/dossiers/proposition?' + new URLSearchParams({ titre: titre }).toString()).then(function (r) {
          var ref = nouveau.querySelector('[name="reference"]').value.trim() || r.reference;
          if (chemin && r.emplacement) chemin.textContent = r.emplacement.replace(/ - [^/]*$/, ' - ' + ref.toUpperCase());
        }).catch(function () { if (chemin) chemin.textContent = 'Emplacement proposé à l’enregistrement.'; });
      }, 300);
    }
    nouveau.addEventListener('input', function (ev) { if (ev.target.name === 'titre' || ev.target.name === 'reference') proposer(); });
    proposer();
    var verifierConflits = function () {
      var zone = nouveau.querySelector('[data-d97-resultat]'), decision = nouveau.querySelector('[data-d97-decision]');
      var data = lire(nouveau); delete data.cle; delete data.conflit;
      zone.textContent = 'Vérification en cours…';
      api('m57/dossiers/conflits', data).then(function (r) {
        zone.textContent = '';
        zone.appendChild(el('p', 'd97-risque', r.resume));
        if (r.correspondances.length) {
          var ul = el('ul');
          r.correspondances.forEach(function (m) {
            ul.appendChild(el('li', '', m.party + ' — ' + (m.matter_label || 'dossier') + ' (' + ({ conflit_potentiel: 'conflit potentiel', a_verifier: 'à vérifier', meme_cote: 'même côté' }[m.risk] || m.risk) + ')'));
          });
          zone.appendChild(ul);
        }
        zone.appendChild(el('p', 'd97-aide', r.avis));
        (r.limites || []).slice(0, 2).forEach(function (l) { zone.appendChild(el('p', 'd97-aide', l)); });
        nouveau.querySelector('[name="conflit.id"]').value = r.id;
        decision.hidden = false;
      }).catch(function (e) { zone.textContent = e.message; montrerErreurs(nouveau, e.champs); });
    };
  }

  /* --------------------------------------------------------------------------------------- écran Dossiers */
  var liste = document.querySelector('[data-d97-liste]');
  if (liste) {
    var cle = 'd97:' + location.pathname + location.search;
    Array.prototype.forEach.call(liste.querySelectorAll('select[data-d97-auto]'), function (s) {
      s.addEventListener('change', function () { s.form.submit(); });
    });
    liste.addEventListener('click', function (ev) {
      var a = ev.target.closest('a.d97-ouvrir');
      if (a) {
        try { sessionStorage.setItem(cle, JSON.stringify({ y: window.scrollY, id: a.closest('tr').getAttribute('data-d97-ligne') })); } catch (e) { /* stockage indisponible */ }
      }
      var b = ev.target.closest('[data-d97-details]');
      if (b) {
        var tr = b.closest('tr'), ouvert = tr.classList.toggle('d97-details-ouverts');
        b.setAttribute('aria-expanded', ouvert ? 'true' : 'false');
      }
    });
    try {   // retour de la fiche : filtre (adresse), sélection et défilement restaurés
      var mem = JSON.parse(sessionStorage.getItem(cle) || 'null');
      if (mem) {
        var tr = liste.querySelector('tr[data-d97-ligne="' + (window.CSS && CSS.escape ? CSS.escape(mem.id) : mem.id) + '"]');
        if (tr) { tr.classList.add('d97-dernier'); }
        window.scrollTo(0, mem.y || 0);
        if (tr) { var lien = tr.querySelector('a.d97-ouvrir'); if (lien) lien.focus({ preventScroll: true }); }
      }
    } catch (e) { /* stockage indisponible : rien à restaurer */ }
  }

  /* --------------------------------------------------------------------------------------- Courriels : Classer dans un dossier */
  function classer(m) {
    var bloc = el('section', 'd97-classer');
    bloc.setAttribute('aria-label', 'Classer dans un dossier');
    bloc.appendChild(el('h3', '', 'Classer dans un dossier'));
    var etat = el('p', 'd97-recu'); etat.setAttribute('role', 'status'); bloc.appendChild(etat);
    var dejaVersion = null;
    if (m.classement && m.classement.dossier) {
      etat.textContent = 'Classé dans ' + m.classement.titre + ' (' + m.classement.reference + ').';
      dejaVersion = m.classement.version;
    }
    bloc.appendChild(el('p', 'd97-aide', 'Compte : ' + (m.compte || 'boîte du cabinet') + ' · Message : ' + (m.subject || '(sans objet)') +
      '. Classer ne déplace rien dans la boîte de courriel.'));
    var lab = el('label', 'd97-champ', 'Rechercher un dossier (client, référence, partie)');
    var q = el('input'); q.type = 'search'; q.setAttribute('data-no-dictation', ''); lab.appendChild(q); bloc.appendChild(lab);
    var choix = el('div', 'd97-choix'); choix.setAttribute('role', 'radiogroup'); choix.setAttribute('aria-label', 'Dossiers autorisés'); bloc.appendChild(choix);
    var nomRadio = 'd97c-' + uuid().slice(0, 8);
    function charger() {
      api('m57/dossiers?' + new URLSearchParams({ q: q.value, limite: 30 }).toString()).then(function (r) {
        choix.textContent = '';
        if (!r.dossiers.length) { choix.appendChild(el('p', 'd97-aide', 'Aucun dossier autorisé ne correspond.')); return; }
        r.dossiers.forEach(function (d) {
          if (d.provenance === 'non_importe') return;
          var l = el('label'), i = el('input'); i.type = 'radio'; i.name = nomRadio; i.value = d.id;
          i.setAttribute('data-titre', d.titre);
          l.appendChild(i); l.appendChild(el('span', '', d.titre + ' — ' + d.reference + (d.client ? ' · ' + d.client : '')));
          choix.appendChild(l);
        });
      }).catch(function (e) { choix.textContent = e.message; });
    }
    var t = null; q.addEventListener('input', function () { clearTimeout(t); t = setTimeout(charger, 250); }); charger();
    var opts = el('fieldset', 'd97-radios'); opts.appendChild(el('legend', '', 'Rangement (facultatif)'));
    var cArch = el('input'); cArch.type = 'checkbox';
    var lArch = el('label', 'd97-case'); lArch.appendChild(cArch); lArch.appendChild(el('span', '', 'Archiver le courriel : original .eml et aperçu dans CORRESPONDANCES'));
    opts.appendChild(lArch);
    var cPj = el('input'); cPj.type = 'checkbox';
    var noms = (m.attachments || []).map(function (a) { return a.name; });
    var lPj = el('label', 'd97-case'); lPj.appendChild(cPj);
    lPj.appendChild(el('span', '', noms.length ? 'Enregistrer les pièces jointes dans NOS PIECES / PJ courriels : ' + noms.join(', ') : 'Aucune pièce jointe'));
    cPj.disabled = !noms.length;
    opts.appendChild(lPj); bloc.appendChild(opts);
    var motifLab = el('label', 'd97-champ', 'Motif du changement de classement'); var motif = el('input'); motifLab.appendChild(motif);
    motifLab.hidden = dejaVersion === null; bloc.appendChild(motifLab);
    var actions = el('p', 'd97-actions');
    var bouton = el('button', 'ax-btn', dejaVersion === null ? 'Classer' : 'Modifier le classement'); bouton.type = 'button';
    var creer = el('a', 'ax-btn ghost', 'Créer un dossier depuis ce message');
    creer.href = prefix + '/dossiers/nouveau?' + new URLSearchParams({ depuis: 'courriel', boite: m.folder || 'INBOX', uid: m.uid, uidvalidity: m.uidvalidity || '' }).toString();
    actions.appendChild(bouton); actions.appendChild(creer);
    if (m.demande_url && m.demande_url.charAt(0) === '/') {   // lot documents-mission : même formulaire de demande, origine « courriel »
      var demande = el('a', 'ax-btn ghost', 'Créer une demande depuis ce message'); demande.href = prefix + m.demande_url; actions.appendChild(demande);
    }
    bloc.appendChild(actions);
    var cle = uuid();
    bouton.addEventListener('click', function () {
      var choisi = choix.querySelector('input:checked');
      if (!choisi) { etat.textContent = 'Choisissez d’abord un dossier.'; return; }
      bouton.disabled = true; etat.textContent = 'Classement en cours…';
      var corps = { cle: cle, message: { boite: m.folder || 'INBOX', uid: String(m.uid), uidvalidity: String(m.uidvalidity || ''), message_id: m.message_id || '',
        sujet: m.subject || '', expediteur: m.from || '', date: m.date || '' }, options: { archiver: cArch.checked, pieces_jointes: cPj.checked } };
      if (dejaVersion !== null) { corps.version = dejaVersion; corps.motif = motif.value; }
      api('m57/dossiers/' + encodeURIComponent(choisi.value) + '/courriels', corps).then(function (r) {
        etat.textContent = r.message + '. ' + r.imap;
        var lien = el('a', '', 'Ouvrir les correspondances du dossier'); lien.href = prefix + r.url; bloc.appendChild(lien);
        cle = uuid(); dejaVersion = r.version; bouton.textContent = 'Modifier le classement'; motifLab.hidden = false;
        var actionsRangement = [];
        if (cArch.checked) actionsRangement.push('archive_email');
        if (cPj.checked) actionsRangement.push('file_attachments');
        if (!actionsRangement.length) return null;
        return api('m5643/correspondances/preparer', { source: { folder: m.folder || 'INBOX', uid: m.uid, uidvalidity: m.uidvalidity || '' },
          matter_id: choisi.value, actions: actionsRangement }).then(function (p) {
          return api('m5643/correspondances/confirmer', { proposal_id: p.proposal_id, expected_version: p.version, request_key: uuid() });
        }).then(function (s) {
          bloc.appendChild(el('p', 'd97-aide', 'Rangement demandé : ' + (s.conservation_label || s.message || 'en attente') + '. L’état affiché vient de la relecture des fichiers.'));
        }).catch(function (e) { bloc.appendChild(el('p', 'd97-erreur', 'Classement fait ; rangement des fichiers non lancé : ' + e.message)); });
      }).catch(function (e) {
        etat.textContent = e.code === 'version_perimee' ? 'Ce courriel est déjà classé dans un autre dossier : rechargez puis utilisez « Modifier le classement ».' : e.message;
      }).then(function () { bouton.disabled = false; });
    });
    return bloc;
  }
  window.axh5697Classer = classer;
})();
