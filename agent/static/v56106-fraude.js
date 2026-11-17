/* AxiorHub 5.6.106 (lot F) — alerte « courriel potentiellement frauduleux » (hameçonnage, usurpation, fraude au virement).
   Liste : pastille (icône, couleur et texte) sur chaque courriel signalé. Lecteur : bandeau en tête avec les motifs en clair, des conseils
   concrets et trois actions (« Signaler comme indésirable », « Je connais cet expéditeur », « Ce n'est pas une fraude ») ; pour « Suspect » et
   « Danger », liens désactivés (le serveur retire leur cible dans l'affichage mis en forme ; l'adresse réelle n'est montrée ici qu'après un
   avertissement) et pièces à risque signalées, jamais prévisualisées. Sans alerte, rien n'est affiché (jamais « sûr »).
   Aucun point de montage dans les fichiers des autres lots : la liste est suivie par l'événement « axh5625:liste », le lecteur par un
   observateur du panneau. Tout est construit avec textContent ; aucun HTML venant du courriel n'entre dans la page.
   5.6.107 (lot F2) : alerte plus discrète. « Attention » : une ligne fine repliée (« ⚠ Attention — N indices · Voir le détail ▾ ») dans la
   ligne de puces du lecteur ; « Suspect » : une ligne avec le premier motif, le reste replié ; « Danger » : déplié, jamais repliable. Motifs,
   conseils, liens et actions sont dans le pli (<details>, clavier). Pli ouvert ou fermé mémorisé dans le navigateur par personne (repère court
   du compte, jamais l'identifiant) et par niveau ; stockage absent ou refusé : replié, sans erreur. */
(function () {
  'use strict';
  if (window.__axiorhub56106f) return;
  window.__axiorhub56106f = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var R = 'm56106/fraude/';
  var ICONES = { attention: '⚠', suspect: '⚠', danger: '⛔' };
  var REPLIABLE = { attention: true, suspect: true };   // 5.6.107 (lot F2) : « Danger » n'y est pas, il reste toujours déplié

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
  function toast(message, bad) {
    var box = document.getElementById('ax-toast');
    if (!box) { box = el('div', { id: 'ax-toast', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(box); }
    var t = el('div', { 'class': 'ax-toast' + (bad ? ' bad' : ''), text: message });
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 5000);
  }
  function api(name, body) {
    return fetch(prefix + '/api440/' + name, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; }); })
      .then(function (d) { if (d && d.error) throw new Error(d.message || d.error); return d; });
  }
  function cle() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return 'fr-' + Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function busy(b, on) { if (b) { b.disabled = on; b.setAttribute('aria-busy', on ? 'true' : 'false'); } }
  // 5.6.107 (lot F2) : pli mémorisé par personne et par niveau ; stockage absent, refusé ou plein : replié et rien n'est retenu
  function compte() { return meta('axiorhub-compte') || 'cabinet'; }
  function lirePli(niveau) {
    try {
      return window.localStorage.getItem('axh-fr107-pli:' + compte() + ':' + niveau) === 'ouvert';
    } catch (e) { return false; }
  }
  function ecrirePli(niveau, ouvert) {
    try {
      window.localStorage.setItem('axh-fr107-pli:' + compte() + ':' + niveau, ouvert ? 'ouvert' : 'ferme');
    } catch (e) { /* stockage indisponible : le pli n'est pas retenu */ }
  }

  // ------------------------------------------------------------------ pastille de la liste
  function pastille(f) {
    var titre = 'Courriel potentiellement frauduleux — ' + f.libelle + (f.motifs && f.motifs.length ? ' : ' + f.motifs.join(' ') : '') +
      (f.partiel ? ' (analyse des en-têtes ; ouvrez le courriel pour l’analyse complète)' : '');
    return el('span', { 'class': 'fr106-pastille fr106-' + f.niveau, title: titre }, [
      el('span', { 'aria-hidden': 'true', text: ICONES[f.niveau] || '⚠' }),
      el('span', { 'class': 'fr106-pastille-texte', text: f.libelle })]);
  }
  function decorer(items) {
    var rows = document.getElementById('bx5625-rows');
    if (!rows || !items) return;
    items.forEach(function (m) {
      var f = m && m.fraude;
      if (!f || !f.niveau) return;
      var b = rows.querySelector('[data-uid="' + String(m.uid).replace(/[^0-9]/g, '') + '"]');
      if (!b || b.querySelector('.fr106-pastille')) return;
      var cible = b.querySelector('.bx56106-l1') || b.querySelector('.ax-row-top') || b;
      cible.insertBefore(pastille(f), cible.firstChild);
      b.classList.add('fr106-ligne', 'fr106-ligne-' + f.niveau);
      var lbl = b.getAttribute('aria-label');
      if (lbl && lbl.indexOf('Alerte ') !== 0) b.setAttribute('aria-label', 'Alerte « ' + f.libelle + ' » : courriel potentiellement frauduleux. ' + lbl);
    });
  }

  // ------------------------------------------------------------------ bandeau du lecteur
  var bandeau = null, pour = null;
  function ctx() { return window.axh5625Ctx ? window.axh5625Ctx() : null; }
  function ref(m) { return { uid: String(m.uid), dossier: m.folder || '', uidvalidity: String(m.uidvalidity || ''), cle: cle() }; }
  function actionBouton(texte, aide, quoi, m, statut, confirmation) {
    var b = el('button', { type: 'button', 'class': 'ax-btn ghost', text: texte, title: aide });
    b.addEventListener('click', function () {
      if (confirmation && !window.confirm(confirmation)) return;
      busy(b, true); statut.textContent = 'Enregistrement…';
      api(R + quoi, ref(m)).then(function (r) {
        statut.textContent = r.message; toast(r.message);
        var c = ctx();
        if (c) { if (quoi === 'indesirable') { if (c.clear) c.clear(); } else if (c.open) c.open(String(m.uid)); if (c.reload) c.reload(); }
      }).catch(function (e) { statut.textContent = e.message; toast(e.message, true); busy(b, false); });
    });
    return b;
  }
  function construire(m) {
    var f = m.fraude;
    if (f.indisponible) return el('p', { 'class': 'fr106-indisponible', role: 'status', text: f.message || 'Contrôle anti-fraude indisponible pour ce courriel.' });
    if (!REPLIABLE[f.niveau]) return construireDeplie(m, f);
    return construireReplie(m, f);
  }
  // « Danger » (et tout niveau inconnu) : bandeau déplié comme en 5.6.106, l'alerte doit sauter aux yeux
  function construireDeplie(m, f) {
    var titre = el('h3', { 'class': 'fr106-titre', id: 'fr106-titre-' + String(m.uid).replace(/[^0-9]/g, '') }, [
      el('span', { 'aria-hidden': 'true', text: (ICONES[f.niveau] || '⚠') + ' ' }),
      document.createTextNode('Courriel potentiellement frauduleux — ' + f.libelle)]);
    var sec = el('section', { 'class': 'fr106-bandeau fr106-' + f.niveau, role: 'alert', 'aria-labelledby': titre.id }, [titre]);
    remplir(sec, m, f);
    return sec;
  }
  // 5.6.107 (lot F2) : « Attention » et « Suspect » : une ligne (résumé du <details>), le détail dans le pli
  function construireReplie(m, f) {
    var n = (f.signaux && f.signaux.length) || (f.motifs || []).length;
    var suspect = f.niveau === 'suspect';
    var id = 'fr107-resume-' + String(m.uid).replace(/[^0-9]/g, '');
    var voir = el('span', { 'class': 'fr107-voir', text: 'Voir le détail' });
    var fleche = el('span', { 'class': 'fr107-fleche', 'aria-hidden': 'true', text: '▾' });
    var resume = el('summary', { id: id, title: 'Courriel potentiellement frauduleux — ' + f.libelle + ((f.motifs || []).length ? ' : ' + f.motifs.join(' ') : '') }, [
      el('span', { 'aria-hidden': 'true', text: (ICONES[f.niveau] || '⚠') + ' ' }),
      el('span', { 'class': 'fr107-niveau', text: f.libelle + ' — ' + n + ' indice' + (n > 1 ? 's' : '') }),
      suspect && f.motifs && f.motifs.length ? el('span', { 'class': 'fr107-motif', text: ' : ' + f.motifs[0] }) : null,
      document.createTextNode(' · '), voir, document.createTextNode(' '), fleche]);
    var detail = el('div', { 'class': 'fr107-detail' }, [el('p', { 'class': 'fr106-sous-titre', text: 'Courriel potentiellement frauduleux — ' + f.libelle })]);
    remplir(detail, m, f);
    var pli = el('details', { 'class': 'fr107-pli' }, [resume, detail]);
    pli.open = lirePli(f.niveau);
    var majResume = function () { voir.textContent = pli.open ? 'Masquer le détail' : 'Voir le détail'; fleche.textContent = pli.open ? '▴' : '▾'; };
    majResume();
    pli.addEventListener('toggle', function () { majResume(); ecrirePli(f.niveau, pli.open); });
    return el('div', { 'class': 'fr106-bandeau fr106-' + f.niveau + ' fr107-ligne' + (suspect ? ' fr107-pleine' : ''), role: suspect ? 'alert' : 'status',
                        'aria-label': 'Alerte de sécurité : courriel potentiellement frauduleux — ' + f.libelle }, [pli]);
  }
  // contenu commun : motifs, conseils, pièces, liens désactivés, second avis, actions
  function remplir(sec, m, f) {
    var grave = f.niveau === 'suspect' || f.niveau === 'danger';
    var motifs = el('ul', { 'class': 'fr106-motifs' });
    (f.motifs || []).forEach(function (x) { motifs.appendChild(el('li', { text: x })); });
    sec.appendChild(motifs);
    if (f.conseils && f.conseils.length) {
      var conseils = el('ul', { 'class': 'fr106-conseils', 'aria-label': 'Conseils' });
      f.conseils.forEach(function (x) { conseils.appendChild(el('li', { text: x })); });
      sec.appendChild(el('p', { 'class': 'fr106-sous-titre', text: 'Que faire' }));
      sec.appendChild(conseils);
    }
    if (f.pieces_risque && f.pieces_risque.length) {
      sec.appendChild(el('p', { 'class': 'fr106-pieces', text: 'Pièce(s) à risque, sans aperçu : ' + f.pieces_risque.join(', ') + '. Ne les ouvrez pas.' }));
    }
    if (grave && f.liens && f.liens.length) {
      var liste = el('ul', { 'class': 'fr106-liens' });
      f.liens.forEach(function (l) {
        var voir = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Voir l’adresse', 'aria-expanded': 'false' });
        var adresse = el('code', { 'class': 'fr106-adresse', hidden: 'hidden', text: l.adresse + (l.motif ? ' — ' + l.motif : '') });
        voir.addEventListener('click', function () {
          if (!adresse.hidden) { adresse.hidden = true; voir.setAttribute('aria-expanded', 'false'); return; }
          if (!window.confirm('Ce lien est désactivé : le courriel est peut-être frauduleux. N’ouvrez pas cette adresse et ne saisissez aucun identifiant. Afficher l’adresse réelle du lien ?')) return;
          adresse.hidden = false; voir.setAttribute('aria-expanded', 'true');
        });
        liste.appendChild(el('li', {}, [el('span', { text: (l.texte || '(lien sans texte)') + ' ' }), voir, adresse]));
      });
      sec.appendChild(el('details', { 'class': 'fr106-liens-bloc' }, [el('summary', { text: 'Liens du message (désactivés) : ' + f.liens.length }), liste]));
    }
    if (f.avis) sec.appendChild(el('p', { 'class': 'fr106-avis', text: 'Second avis de l’IA locale : ' + f.avis.avis + (f.avis.raison ? ' — ' + f.avis.raison : '') }));
    var statut = el('p', { 'class': 'fr106-statut', role: 'status', 'aria-live': 'polite' });
    var actions = el('div', { 'class': 'fr106-actions' }, [
      actionBouton('Signaler comme indésirable', 'Déplacer ce courriel dans Indésirables', 'indesirable', m, statut,
                   'Déplacer ce courriel dans le dossier Indésirables de la messagerie ?')]);
    if (f.exception_possible) {
      actions.appendChild(actionBouton('Je connais cet expéditeur', 'Ne plus signaler l’identité de cette adresse (et d’elle seule)', 'exception', m, statut,
        'Noter ' + (f.expediteur || 'cet expéditeur') + ' comme expéditeur connu ? Seule cette adresse est concernée ; un changement de RIB, un lien ou une pièce dangereux resteront signalés.'));
    }
    actions.appendChild(actionBouton('Ce n’est pas une fraude', 'Ce courriel ne sera plus signalé ; votre retour ajuste les alertes', 'retour', m, statut,
      'Confirmer que ce courriel n’est pas une fraude ? Il ne sera plus signalé. Un IBAN qu’il contient sera retenu comme celui de cet expéditeur.'));
    sec.appendChild(actions);
    sec.appendChild(statut);
  }
  function marquerPieces(panel, f) {
    if (!f || !f.pieces_risque || !f.pieces_risque.length) return;
    var noms = f.pieces_risque;
    Array.prototype.forEach.call(panel.querySelectorAll('.lx56106-piece, .bx5625-pjrow'), function (row) {
      if (row.querySelector('.fr106-piece-risque')) return;
      var nom = ((row.firstElementChild || row).textContent || '');
      var trouve = noms.filter(function (n) { return nom.indexOf(n) === 0; })[0];
      if (!trouve) return;
      row.insertBefore(el('span', { 'class': 'fr106-piece-risque', text: '⚠ Pièce à risque : ne l’ouvrez pas' }), row.firstChild);
      Array.prototype.forEach.call(row.querySelectorAll('button'), function (b) { if (/Aperçu/.test(b.textContent)) { b.disabled = true; b.hidden = true; } });
      Array.prototype.forEach.call(row.querySelectorAll('a[download]'), function (a) {
        a.addEventListener('click', function (ev) {
          if (!window.confirm('Cette pièce jointe est à risque (courriel potentiellement frauduleux). La télécharger quand même ? Ne l’ouvrez pas sans vérification.')) ev.preventDefault();
        });
      });
    });
  }
  function placer() {
    var L = window.axh5625Liste, panel = document.getElementById('bx5625-panel');
    if (!L || !panel) return;
    var m = L.courant ? L.courant() : null;
    var f = m && m.fraude;
    if (!f || (!f.niveau && !f.indisponible)) {
      if (bandeau && bandeau.parentNode) bandeau.parentNode.removeChild(bandeau);
      bandeau = null; pour = null; return;
    }
    if (pour !== m) {
      if (bandeau && bandeau.parentNode) bandeau.parentNode.removeChild(bandeau);
      bandeau = construire(m); pour = m;
    }
    var zone = panel.querySelector('.lx56106-bandeaux');
    if (zone) {
      if (bandeau.parentNode !== zone || zone.firstChild !== bandeau) zone.insertBefore(bandeau, zone.firstChild);
    } else {
      var tete = panel.querySelector('.bx5625-mhead');
      var attendreV = window.axh56106lecteur && !(tete && tete.getAttribute('data-fr106-repli'));
      if (tete && !tete.hidden && !attendreV) {
        if (tete.nextSibling !== bandeau) tete.parentNode.insertBefore(bandeau, tete.nextSibling);
      } else if (tete && attendreV && !tete.getAttribute('data-fr106-attente')) {
        tete.setAttribute('data-fr106-attente', '1');   // lecteur du lot V attendu ; s'il ne se pose pas, repli sous l'en-tête d'origine
        setTimeout(function () { if (!panel.querySelector('.lx56106-bandeaux') && tete.isConnected) { tete.setAttribute('data-fr106-repli', '1'); planifier(); } }, 800);
      }
    }
    marquerPieces(panel, f);
  }
  var prevu = false;
  function planifier() { if (prevu) return; prevu = true; setTimeout(function () { prevu = false; placer(); }, 0); }

  function boot() {
    var boite = document.getElementById('bx5625');
    if (!boite) return;
    boite.addEventListener('axh5625:liste', function (ev) { decorer(ev.detail && ev.detail.items); });
    if (window.axh5625Liste && window.axh5625Liste.etat && window.axh5625Liste.etat.lignes) {   // liste déjà affichée avant ce script
      var l = window.axh5625Liste.etat.lignes;
      decorer(Object.keys(l).map(function (k) { return l[k]; }));
    }
    var panel = document.getElementById('bx5625-panel');
    if (panel && window.MutationObserver) new MutationObserver(planifier).observe(panel, { childList: true, subtree: true });
    var rows = document.getElementById('bx5625-rows');
    if (rows && window.MutationObserver) new MutationObserver(function () {   // lignes repeintes sans nouvel événement (tri local, rafraîchissement)
      var L = window.axh5625Liste;
      if (L && L.etat && L.etat.lignes) { var x = L.etat.lignes; decorer(Object.keys(x).map(function (k) { return x[k]; })); }
    }).observe(rows, { childList: true });
    planifier();
  }
  window.axh56106fraude = { pastille: pastille, decorer: decorer, construire: construire, placer: placer };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
