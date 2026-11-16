/* AxiorHub 5.6.106 (lot O) — « Créer une routine à partir du message ».
   Bouton dans la barre du lecteur (point de montage de v5628.js : window.axh56106routine.bouton(barre, courriel, contexte)) ; assistant
   prérempli par le serveur depuis le courriel ouvert (déclencheur, actions, dossier, mode) ; aperçu des courriels des 30 derniers jours qui
   auraient déclenché la routine ; enregistrement avec une clé d'idempotence. Sur Aujourd'hui : ouvre le pli désigné par l'adresse (#o106-…).
   Tout est construit avec textContent : aucun HTML venant du serveur ou d'un courriel n'est injecté. La routine ne fait jamais rien partir. */
(function () {
  'use strict';
  if (window.axh56106routine) return;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var R = 'm56106/routine/';
  var $ = function (s, r) { return (r || document).querySelector(s); };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) node.setAttribute(k, attrs[k] === true ? '' : attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) { box = el('div', { id: 'ax-toast', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(box); }
    var t = el('div', { 'class': 'ax-toast' + (bad ? ' bad' : ''), text: message });
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 5000);
  }
  function api(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) { if (d && d.error) { var x = new Error(d.message || d.error); x.code = d.error; throw x; } return d; });
  }
  function cle() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return 'o106-' + Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function qs(o) {
    var p = new URLSearchParams();
    Object.keys(o).forEach(function (k) { if (o[k] !== '' && o[k] !== undefined && o[k] !== null) p.set(k, o[k]); });
    return p.toString();
  }
  var uniq = 0;
  function champ(label, input, aide) {
    uniq += 1;
    input.id = input.id || 'o106-c' + uniq;
    var bloc = el('div', { 'class': 'o106-champ' }, [el('label', { 'for': input.id, text: label }), input]);
    if (aide) { var a = el('p', { 'class': 'o106-aide', id: input.id + '-aide', text: aide }); input.setAttribute('aria-describedby', a.id); bloc.appendChild(a); }
    return bloc;
  }
  function caseA(label, nom, coche) {
    var i = el('input', { type: 'checkbox', name: nom, checked: coche ? true : null });
    return { input: i, node: el('label', { 'class': 'o106-case' }, [i, document.createTextNode(' ' + label)]) };
  }
  function radio(nom, valeur, label, coche) {
    var i = el('input', { type: 'radio', name: nom, value: valeur, checked: coche ? true : null });
    return { input: i, node: el('label', { 'class': 'o106-case' }, [i, document.createTextNode(' ' + label)]) };
  }
  function statut(zone, texte, erreur) { zone.textContent = texte; zone.className = 'o106-statut' + (erreur ? ' o106-erreur' : ''); }
  function quand(iso) { return String(iso || '').slice(0, 16).replace('T', ' '); }

  // ------------------------------------------------------------------ assistant
  function construire(zone, p, fermer) {
    zone.textContent = '';
    var cleForm = cle();     // une seule clé pour cet assistant : un second clic ou une reprise réseau ne crée pas une seconde routine
    var titre = el('input', { maxlength: '120', required: true, value: p.titre || '' });
    var expMode = el('select', {}, [el('option', { value: 'adresse', text: 'Adresse exacte' }), el('option', { value: 'domaine', text: 'Tout le domaine' }),
                                    el('option', { value: 'aucun', text: 'Tout expéditeur' })]);
    var exp = el('input', { maxlength: '200', value: (p.criteres && p.criteres.expediteur) || '' });
    expMode.addEventListener('change', function () {
      exp.value = expMode.value === 'domaine' ? (p.criteres.domaine || '') : (expMode.value === 'adresse' ? (p.criteres.expediteur || '') : '');
      exp.disabled = expMode.value === 'aucun';
    });
    var objet = el('input', { maxlength: '200', value: (p.criteres && p.criteres.objet) || '' });
    var corps = el('input', { maxlength: '200', value: '' });
    var pj = caseA('Seulement avec une pièce jointe', 'piece_jointe', false);
    var boite = caseA('Seulement dans le dossier « ' + ((p.criteres && p.criteres.boite) || 'INBOX') + ' »', 'boite', true);
    var compte = caseA('Seulement pour le compte ' + ((p.criteres && p.criteres.compte) || ''), 'compte', false);
    var apercuBtn = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Vérifier sur les 30 derniers jours' });
    var apercuEtat = el('p', { 'class': 'o106-statut', role: 'status', 'aria-live': 'polite' });
    var apercuListe = el('ul', { 'class': 'o106-apercu' });
    var actions = {};
    var actionsBloc = el('fieldset', { 'class': 'o106-groupe' }, [el('legend', { text: 'Actions (une ou plusieurs)' })]);
    Object.keys(p.actions_possibles || {}).forEach(function (k) {
      var c = caseA(p.actions_possibles[k], 'action_' + k, (p.actions || []).indexOf(k) >= 0);
      actions[k] = c.input; actionsBloc.appendChild(c.node);
    });
    var ev = p.evenement || {};
    var repere = ev.date ? 'Repéré dans ce courriel : ' + (ev.nature ? ev.nature.replace(/_/g, ' ') + ' ' : '') + 'le ' + ev.date + (ev.heure ? ' à ' + ev.heure : '') +
      (ev.certitude === 'sure' ? ' (date et heure sûres).' : ' — à valider : ' + (ev.motifs || []).join(', ') + '.')
      : 'Aucune date d’audience sûre dans ce courriel : un événement serait proposé à compléter.';
    var dossier = el('select', {});
    dossier.appendChild(el('option', { value: '', text: 'Automatique (n° RG, références, parties)' }));
    (p.dossiers || []).forEach(function (d) { dossier.appendChild(el('option', { value: d.id, text: d.label })); });
    var reconnu = p.dossier && p.dossier.id ? 'Dossier reconnu pour ce courriel : ' + (p.dossier.label || p.dossier.id) + (p.dossier.sur ? '.' : ' (pas de façon sûre).')
      : 'Aucun dossier reconnu de façon sûre pour ce courriel (' + ((p.dossier && p.dossier.motif) || '—') + ') : l’élément irait dans « À décider ».';
    var proposerR = radio('o106-mode', 'proposer', 'Proposer à valider (recommandé)', true);
    var directR = radio('o106-mode', 'direct', 'Créer directement', false);
    var confirmer = caseA('Je confirme : créer sans validation (marqué « créé par la routine », annulable en un clic ; une date incertaine reste à valider)',
                          'confirmer_direct', false);
    var copie = caseA('Autoriser la copie vers Outlook ou Google pour cette routine (sinon seulement après validation)', 'copie_externe', false);
    var perso = radio('o106-portee', 'personnel', 'Personnelle', true);
    var cabinet = radio('o106-portee', 'cabinet', 'Du cabinet', false);
    confirmer.node.hidden = true; copie.node.hidden = true;
    [proposerR.input, directR.input].forEach(function (r) {
      r.addEventListener('change', function () { var d = directR.input.checked; confirmer.node.hidden = !d; copie.node.hidden = !d; });
    });
    var enregistrer = el('button', { type: 'submit', 'class': 'ax-btn', text: 'Enregistrer la routine' });
    var fermerBtn = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Fermer' });
    var etat = el('p', { 'class': 'o106-statut', role: 'status', 'aria-live': 'polite' });

    function criteres() {
      return { expediteur: expMode.value === 'aucun' ? '' : exp.value.trim(), objet: objet.value.trim(), corps: corps.value.trim(),
               piece_jointe: pj.input.checked, boite: boite.input.checked ? (p.criteres.boite || '') : '',
               compte: compte.input.checked ? (p.criteres.compte || '') : '' };
    }
    apercuBtn.addEventListener('click', function () {
      apercuListe.textContent = '';
      statut(apercuEtat, 'Recherche dans les courriels récents…');
      apercuBtn.disabled = true;
      api(R + 'apercu', { criteres: criteres() }).then(function (r) {
        statut(apercuEtat, r.message);
        (r.courriels || []).forEach(function (x) {
          var ligne = quand(x.date) + ' — ' + (x.expediteur || '') + ' — ' + (x.objet || '(sans objet)');
          if (x.evenement && x.evenement.date) ligne += ' · date repérée : ' + x.evenement.date + (x.evenement.heure ? ' ' + x.evenement.heure : '') +
            (x.evenement.certitude === 'sure' ? '' : ' (à valider)');
          apercuListe.appendChild(el('li', { text: ligne }));
        });
      }).catch(function (e) { statut(apercuEtat, e.message, true); }).then(function () { apercuBtn.disabled = false; });
    });

    var form = el('form', { 'class': 'o106-formulaire', 'aria-label': 'Créer une routine à partir du message' }, [
      el('h3', { text: 'Créer une routine à partir du message' }),
      el('p', { 'class': 'o106-aide', text: 'La routine agira à l’arrivée des prochains courriels qui remplissent toutes les conditions. Elle prépare et propose : elle n’envoie, ne signe, ne dépose ni ne facture jamais.' }),
      champ('Nom de la routine', titre),
      el('fieldset', { 'class': 'o106-groupe' }, [el('legend', { text: 'Déclencheur (toutes les conditions)' }),
        el('div', { 'class': 'o106-ligne' }, [champ('Expéditeur', expMode), champ('Adresse ou domaine', exp)]),
        champ('L’objet contient', objet, 'Texte simple (ni joker ni expression régulière) ; accents et majuscules ignorés.'),
        champ('Le corps contient', corps), pj.node, boite.node, compte.node,
        el('p', { 'class': 'o106-aide', text: 'Au moins un critère précis est exigé : l’expéditeur, un texte de l’objet ou un texte du corps.' }),
        apercuBtn, apercuEtat, apercuListe]),
      actionsBloc,
      el('p', { 'class': 'o106-aide', text: repere }),
      el('fieldset', { 'class': 'o106-groupe' }, [el('legend', { text: 'Dossier' }), champ('Rattachement', dossier), el('p', { 'class': 'o106-aide', text: reconnu })]),
      el('fieldset', { 'class': 'o106-groupe' }, [el('legend', { text: 'Mode' }), proposerR.node, directR.node, confirmer.node, copie.node]),
      el('fieldset', { 'class': 'o106-groupe' }, [el('legend', { text: 'Portée' }), perso.node, cabinet.node]),
      el('div', { 'class': 'o106-actions' }, [enregistrer, fermerBtn]), etat]);
    fermerBtn.addEventListener('click', function () { zone.remove(); if (fermer) fermer(); });
    form.addEventListener('submit', function (ev2) {
      ev2.preventDefault();
      var choisies = Object.keys(actions).filter(function (k) { return actions[k].checked; });
      if (titre.value.trim().length < 2) { titre.focus(); statut(etat, 'Donnez un nom à la routine.', true); return; }
      if (!choisies.length) { statut(etat, 'Choisissez au moins une action.', true); return; }
      if (directR.input.checked && !confirmer.input.checked) { confirmer.input.focus(); statut(etat, 'Cochez la confirmation pour « Créer directement ».', true); return; }
      enregistrer.disabled = true; enregistrer.setAttribute('aria-busy', 'true'); statut(etat, 'Enregistrement…');
      api(R + 'creer', { titre: titre.value.trim(), criteres: criteres(), actions: choisies, rattachement: dossier.value ? 'fixe' : 'auto', matter: dossier.value,
                         mode: directR.input.checked ? 'direct' : 'proposer', confirmer_direct: confirmer.input.checked, copie_externe: copie.input.checked,
                         scope: cabinet.input.checked ? 'cabinet' : 'personnel', origine: p.origine || {}, cle: cleForm })
        .then(function (r) {
          statut(etat, r.message || 'Routine enregistrée.');
          Array.prototype.forEach.call(form.querySelectorAll('input,select,button'), function (x) { if (x !== fermerBtn) x.disabled = true; });
          fermerBtn.textContent = 'Fermer';
          form.appendChild(el('a', { 'class': 'ax-btn ghost', href: prefix + '/aujourdhui?vue=routines#o106-' + (r.id || ''), text: 'Voir dans Aujourd’hui › Routines' }));
          toast(r.message || 'Routine enregistrée.');
        }).catch(function (e) { statut(etat, e.message, true); enregistrer.disabled = false; enregistrer.setAttribute('aria-busy', 'false'); });
    });
    zone.appendChild(form);
    setTimeout(function () { titre.focus(); }, 0);
  }

  function ouvrir(m, c, bar) {
    var zone = bar && bar.parentNode && bar.parentNode.querySelector('.o106-assistant');
    if (zone) { var f = zone.querySelector('input,select'); if (f) f.focus(); return; }
    zone = el('section', { 'class': 'o106-assistant', role: 'region', 'aria-label': 'Créer une routine à partir du message' });
    var attente = el('p', { 'class': 'o106-statut', role: 'status', text: 'Préparation de la routine à partir de ce courriel…' });
    zone.appendChild(attente);
    if (bar && bar.parentNode) bar.parentNode.insertBefore(zone, bar.nextSibling); else if (c && c.panel) c.panel.appendChild(zone);
    api(R + 'proposer?' + qs({ uid: String(m.uid || ''), dossier: m.folder || (c && c.folder) || '', uidvalidity: String(m.uidvalidity || '') }))
      .then(function (p) { construire(zone, p); })
      .catch(function (e) { statut(attente, e.message, true); });
  }

  function bouton(bar, m, c) {
    if (!bar || !m || !m.uid || (c && c.role === 'drafts')) return;
    var b = el('button', { type: 'button', 'class': 'bx5628-btn o106-btn', title: 'Créer une routine à partir du message',
                           'aria-label': 'Créer une routine à partir du message', text: '⚙ Routine…' });
    b.addEventListener('click', function () { ouvrir(m, c, bar); });
    bar.appendChild(b);
  }

  // ------------------------------------------------------------------ Aujourd'hui : ouvrir le pli désigné par l'adresse (#o106-…)
  function deplier() {
    var id = decodeURIComponent((location.hash || '').slice(1));
    if (!/^o106-[A-Za-z0-9-]{4,80}$/.test(id)) return;
    var cible = document.getElementById(id);
    if (!cible) return;
    for (var n = cible; n; n = n.parentElement) { if (n.tagName === 'DETAILS') n.open = true; }
    var pli = cible.querySelector('details.o106-histo');
    if (pli) pli.open = true;
    cible.scrollIntoView({ block: 'center' });
    var f = cible.querySelector('input,button,summary');
    if (f) f.focus({ preventScroll: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', deplier); else deplier();
  window.addEventListener('hashchange', deplier);

  window.axh56106routine = { bouton: bouton, ouvrir: ouvrir };
})();
