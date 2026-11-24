/* AxiorHub 5.6.109 (lot L, F01) — lecteur des courriels : classer une pièce jointe ou un lot dans le dossier du cabinet.
   Appelé par la barre des pièces de v56106-lecteur.js (window.axh56109pieces.classer / suivre). Le formulaire s'ouvre dans la barre, sans
   changer de message : la liste, son filtre, sa sélection et son défilement ne bougent pas.
   Trois temps, comme la conservation 5.6.43 sur laquelle le classement est monté : choisir (dossier, emplacement, pièces), vérifier
   (aperçu avant copie : rien n'est écrit), classer (travail en file ; l'état affiché vient des reçus relus par le serveur, jamais de
   l'envoi de la demande). Chaque pièce est désignée par son index ET son empreinte : le serveur refuse une pièce qui ne serait plus ces
   octets. « Classer » ne déplace pas le message, n'envoie rien et ne change aucune règle de classement automatique.
   Tout est construit avec textContent ; aucun HTML venant du serveur n'est injecté ; aucun appel au modèle. */
(function () {
  'use strict';
  if (window.axh56109pieces) return;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var R = 'm56106/lecteur/';

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined) node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function api(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) { if (d && d.error) { var x = new Error(d.message || d.error); x.code = d.error; throw x; } return d; });
  }
  function qs(o) {
    var p = new URLSearchParams();
    Object.keys(o).forEach(function (k) { if (o[k] !== '' && o[k] !== undefined && o[k] !== null) p.set(k, o[k]); });
    return p.toString();
  }
  function cle() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return 'p109-' + Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function taille(n) { n = Number(n) || 0; return n > 1048576 ? (n / 1048576).toFixed(1).replace('.', ',') + ' Mo' : Math.max(1, Math.round(n / 1024)) + ' Ko'; }
  function busy(b, on) { if (b) { b.disabled = on; b.setAttribute('aria-busy', on ? 'true' : 'false'); } }
  function statut(zone, texte, erreur) { zone.textContent = texte; zone.className = 'lx56106-statut' + (erreur ? ' lx56106-erreur' : ''); }

  var dossiers = null;
  function chargerDossiers() {
    if (dossiers) return Promise.resolve(dossiers);
    return api(R + 'formulaires').then(function (d) { dossiers = d.dossiers || []; return dossiers; });
  }

  // ------------------------------------------------------------------ état d'un lot : pièces, phrase, destination, reprise
  var suivis = {};                         // lot → minuteur (un seul suivi par lot, même si la barre est redessinée)
  function rendreLot(zone, lot, ctx) {
    zone.textContent = '';
    var bloc = el('div', { 'class': 'p109-lot', 'data-p109-lot': lot.id });
    bloc.appendChild(el('p', { 'class': 'p109-phrase' }, [el('strong', { text: lot.phrase }), document.createTextNode(' — ' + lot.dossier + ' · ' + lot.destination_label)]));
    var liste = el('ul', { 'class': 'p109-liste p109-etats' });
    lot.pieces.forEach(function (p) {
      liste.appendChild(el('li', { 'class': 'p109-piece' }, [el('span', { 'class': 'p109-nom', title: p.nom, text: p.nom }),
        el('span', { 'class': 'p109-puce p109-' + p.etat, text: p.libelle }), p.motif ? el('span', { 'class': 'p109-meta', text: p.motif }) : null]));
    });
    bloc.appendChild(liste);
    var actions = el('span', { 'class': 'lx56106-actions' });
    if (lot.ouvrir) actions.appendChild(el('a', { 'class': 'ax-btn ghost', href: prefix + lot.ouvrir, text: 'Ouvrir la destination' }));
    if (lot.a_reprendre) {
      var reprise = el('button', { type: 'button', 'class': 'ax-btn', text: 'Reprendre le classement', title: 'Seules les pièces non classées sont reprises ; les pièces déjà vérifiées ne sont pas recopiées' });
      var cleReprise = cle();              // créée à l'affichage : un double clic ne lance pas deux reprises
      reprise.addEventListener('click', function () {
        busy(reprise, true);
        api(R + 'classer/reprendre', { id: lot.id, cle: cleReprise }).then(function (suite) { suivre({ zone: zone, lot: suite, ref: ctx.ref, fin: ctx.fin }); })
          .catch(function (e) { busy(reprise, false); bloc.appendChild(el('p', { 'class': 'lx56106-statut lx56106-erreur', role: 'alert', text: e.message })); });
      });
      actions.appendChild(reprise);
    }
    bloc.appendChild(actions);
    zone.appendChild(bloc);
  }
  function suivre(ctx) {
    var lot = ctx.lot, zone = ctx.zone, essais = 0;
    if (suivis[lot.id]) { clearTimeout(suivis[lot.id]); delete suivis[lot.id]; }
    rendreLot(zone, lot, ctx);
    if (lot.etat !== 'confirme' || lot.termine) { if (lot.termine && ctx.fin && ctx.rafraichir) ctx.fin(); return; }
    var tour = function () {
      delete suivis[lot.id];
      if (!zone.isConnected) return;       // autre courriel ouvert : le lot se retrouvera à la réouverture du message
      api(R + 'lot?' + qs({ id: lot.id })).then(function (frais) {
        if (!zone.isConnected) return;
        rendreLot(zone, frais, ctx);
        if (frais.termine) { if (ctx.fin) ctx.fin(); return; }
        if (++essais < 120) suivis[lot.id] = setTimeout(tour, essais < 10 ? 1500 : 4000);
        else zone.appendChild(el('p', { 'class': 'lx56106-note', text: 'Le classement prend plus de temps que prévu : son état se retrouvera en rouvrant ce courriel.' }));
      }).catch(function (e) { zone.appendChild(el('p', { 'class': 'lx56106-statut lx56106-erreur', role: 'alert', text: e.message })); });
    };
    suivis[lot.id] = setTimeout(tour, 1200);
  }

  // ------------------------------------------------------------------ formulaire : dossier, emplacement, pièces ; aperçu avant copie ; classer
  function classer(ctx) {
    var zone = ctx.zone, ref = ctx.ref;
    zone.textContent = '';
    var choisies = {};
    (ctx.choisies || []).forEach(function (a) { choisies[a.index] = true; });
    var dossier = el('select', { id: 'p109-dossier-' + ref.uid, required: 'required' }), emplacement = el('select', { id: 'p109-emplacement-' + ref.uid });
    var etat = el('p', { 'class': 'lx56106-statut', role: 'status', 'aria-live': 'polite' });
    var apercu = el('div', { 'class': 'p109-avant' });
    var verifier = el('button', { type: 'submit', 'class': 'ax-btn', text: 'Vérifier avant de copier' });
    var annuler = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Annuler' });
    var cases = [];
    var liste = el('fieldset', { 'class': 'p109-choix' }, [el('legend', { text: 'Pièces à classer' })]);
    (ctx.toutes || []).forEach(function (a) {
      var id = 'p109-p-' + ref.uid + '-' + a.index;
      var cb = el('input', { type: 'checkbox', id: id });
      cb.checked = !!choisies[a.index];
      cases.push({ cb: cb, piece: a });
      liste.appendChild(el('div', { 'class': 'p109-case' }, [cb, el('label', { 'for': id, text: (a.name || 'pièce') + ' · ' + taille(a.size) + (a.signature ? ' · image intégrée au message' : '') })]));
    });
    var form = el('form', { 'class': 'lx56106-formulaire p109-form', 'aria-label': 'Classer des pièces jointes dans le dossier' }, [
      el('h3', { text: 'Classer dans le dossier' }),
      el('div', { 'class': 'lx56106-ligne' }, [el('label', { 'for': dossier.id }, [el('span', { text: 'Dossier' }), dossier]),
        el('label', { 'for': emplacement.id }, [el('span', { text: 'Emplacement' }), emplacement])]),
      liste,
      el('p', { 'class': 'lx56106-note', text: 'Classer copie les pièces dans le dossier du cabinet. Le message reste dans la messagerie, rien n’est envoyé et aucune règle de classement automatique n’est modifiée.' }),
      el('div', { 'class': 'lx56106-actions' }, [verifier, annuler]), etat, apercu]);
    zone.appendChild(form);

    function chargerEmplacements() {
      emplacement.textContent = '';
      apercu.textContent = '';
      if (!dossier.value) { emplacement.disabled = true; return; }
      emplacement.disabled = true;
      api(R + 'destinations?' + qs({ affaire: dossier.value })).then(function (d) {
        (d.destinations || []).forEach(function (x) { var o = el('option', { value: x.id, text: x.label }); if (x.defaut) o.selected = true; emplacement.appendChild(o); });
        emplacement.disabled = false;
      }).catch(function (e) { statut(etat, e.message, true); });
    }
    chargerDossiers().then(function (tous) {
      dossier.appendChild(el('option', { value: '', text: 'Choisir le dossier…' }));
      tous.forEach(function (x) { var o = el('option', { value: x.id, text: x.label }); if (ctx.dossier && x.id === ctx.dossier.id) o.selected = true; dossier.appendChild(o); });
      chargerEmplacements();
    }).catch(function (e) { statut(etat, e.message, true); });
    dossier.addEventListener('change', chargerEmplacements);
    emplacement.addEventListener('change', function () { apercu.textContent = ''; });
    liste.addEventListener('change', function () { apercu.textContent = ''; });
    annuler.addEventListener('click', function () { zone.textContent = ''; if (ctx.fin) ctx.fin(); });
    form.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { ev.stopPropagation(); annuler.click(); } });

    var cles = {};                           // une clé par demande exacte : un double clic rejoint le même lot, une sélection changée en crée un autre
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var choix = cases.filter(function (c) { return c.cb.checked; }).map(function (c) { return { index: c.piece.index, empreinte: c.piece.empreinte }; });
      if (!dossier.value) { dossier.focus(); statut(etat, 'Choisissez le dossier où classer les pièces.', true); return; }
      if (!choix.length) { statut(etat, 'Cochez au moins une pièce à classer.', true); return; }
      var demande = Object.assign({}, ref, { affaire: dossier.value, destination: emplacement.value || 'nos_pieces', pieces: choix });
      var signature = JSON.stringify(demande);
      cles[signature] = cles[signature] || cle();
      demande.cle = cles[signature];
      busy(verifier, true); statut(etat, 'Vérification…');
      api(R + 'classer/preparer', demande).then(function (lot) {
        statut(etat, lot.message);
        apercu.textContent = '';
        apercu.appendChild(el('h4', { text: 'Aperçu avant copie — ' + lot.dossier + ' · ' + lot.destination_label }));
        var ul = el('ul', { 'class': 'p109-liste p109-etats' });
        lot.pieces.forEach(function (p) {
          ul.appendChild(el('li', { 'class': 'p109-piece' }, [el('span', { 'class': 'p109-nom', title: p.nom, text: p.nom }),
            el('span', { 'class': 'p109-meta', text: p.etat === 'verifiee' ? 'déjà classée dans ce dossier : ne sera pas recopiée' : 'sera copiée sous le nom « ' + p.nom_classement + ' »' })]));
        });
        apercu.appendChild(ul);
        var go = el('button', { type: 'button', 'class': 'ax-btn', text: 'Classer ' + lot.phrase.split(' ; ')[0].replace(' à copier', '') });
        go.addEventListener('click', function () {
          busy(go, true); statut(etat, 'Classement demandé…');
          api(R + 'classer/confirmer', { id: lot.id }).then(function (suite) {
            zone.textContent = '';
            suivre({ zone: ctx.suivi || zone, lot: suite, ref: ref, fin: ctx.fin, rafraichir: true });
          }).catch(function (e) { statut(etat, e.message, true); busy(go, false); });
        });
        apercu.appendChild(el('div', { 'class': 'lx56106-actions' }, [go]));
        go.focus();
      }).catch(function (e) { statut(etat, e.message, true); }).then(function () { busy(verifier, false); });
    });
    setTimeout(function () { (dossier.value ? verifier : dossier).focus(); }, 0);
  }

  window.axh56109pieces = { classer: classer, suivre: suivre };
})();
