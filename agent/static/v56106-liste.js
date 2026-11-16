/* AxiorHub 5.6.106 (lot L) — « Courriels » en vrai client de messagerie : liste dense, barre d'actions groupées, tri et filtres, recherche
   étendue, dossiers IMAP (créer, renommer, supprimer, sous-dossier du dossier client), glisser-déposer, raccourcis clavier proches
   d'Outlook, hauteur de la page tenue (défilement dans la liste et dans le lecteur seulement), onglets Liste / Lecteur en petite largeur.
   Aucun HTML venant du serveur n'est injecté : tout est construit ici. Rien n'est envoyé ; « Supprimer » déplace dans la Corbeille. */
(function () {
  'use strict';
  if (window.__axiorhub56106liste) return;
  window.__axiorhub56106liste = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) { box = el('div', { id: 'ax-toast', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(box); }
    var t = el('div', { 'class': 'ax-toast' + (bad ? ' bad' : ''), text: message });
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 6000);
  }
  function api(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) {
      if (d && d.error) { var e = new Error(d.message || d.error); e.code = d.error; e.data = d; throw e; }
      return d;
    });
  }
  function cle() {   // clé d'idempotence : une par clic
    var a = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return 'l106-' + Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function busy(b, on) { if (b) { b.disabled = on; b.setAttribute('aria-busy', on ? 'true' : 'false'); } }
  function saisie(t) {   // champ de saisie : les raccourcis ne s'y appliquent jamais
    if (!t) return false;
    var n = (t.tagName || '').toLowerCase();
    return n === 'input' || n === 'textarea' || n === 'select' || t.isContentEditable || !!(t.closest && t.closest('[contenteditable="true"],[role="textbox"],dialog[open] form'));
  }

  // ---------------------------------------------------------------- sous-dossier du dossier client (aussi depuis la fiche du dossier)
  function sousDossier(matter, bouton) {
    if (!matter) { toast('Choisissez le dossier client.', true); return Promise.reject(new Error('dossier_a_choisir')); }
    busy(bouton, true);
    return api('m56106/boite/sous-dossier', { matter: matter, cle: cle() }).then(function (r) { toast(r.message); return r; })
      .catch(function (e) { toast(e.message, true); throw e; }).then(function (r) { busy(bouton, false); return r; }, function (e) { busy(bouton, false); throw e; });
  }
  function boutonsFiche() {   // point de montage de la fiche du dossier : [data-bx56106-sous-dossier="<id>"]
    $$('[data-bx56106-sous-dossier]').forEach(function (b) {
      if (b.dataset.pret) return; b.dataset.pret = '1';
      var etat = b.parentNode.querySelector('[data-bx56106-sous-dossier-etat]');
      api('m56106/boite/sous-dossier?matter=' + encodeURIComponent(b.getAttribute('data-bx56106-sous-dossier'))).then(function (r) {
        if (etat) etat.textContent = r.dossier ? 'Sous-dossier de messagerie : « ' + r.dossier + ' ».' : 'Aucun sous-dossier de messagerie pour ce dossier.';
        if (r.dossier) b.textContent = '📂 Vérifier le sous-dossier de messagerie';
      }).catch(function () { if (etat) etat.textContent = ''; });
      b.addEventListener('click', function () {
        sousDossier(b.getAttribute('data-bx56106-sous-dossier'), b).then(function (r) {
          if (etat) etat.textContent = 'Sous-dossier de messagerie : « ' + r.dossier + ' ».';
        }).catch(function () {});
      });
    });
  }

  // ---------------------------------------------------------------- page Courriels
  var COURT = { drafted: 'Brouillon', ignored: 'Ignoré', observed: 'Analysé', review: 'À examiner', error: 'Erreur', retry: 'À reprendre',
                appending: 'Dépôt…', append_uncertain: 'À vérifier', manual: 'Demandé' };
  var ACTIONS = [   // action, icône, libellé court, infobulle
    ['trash', '🗑', 'Supprimer', 'Déplacer dans la Corbeille (récupérable) — Suppr'],
    ['archive', '🗄', 'Archiver', 'Archiver']];
  var MARQUER = [   // menu « Marquer » (barre du lecteur et barre d'actions groupées) : action, icône, libellé, raccourci
    ['lu', '✉', 'Comme lu', 'Ctrl+Q'], ['non_lu', '✉', 'Comme non lu', 'Ctrl+U'], ['tout_lu', '✉', 'Tout le dossier comme lu…', ''],
    ['suivi', '⚑', 'Suivi (drapeau)', 'Inser'], ['non_suivi', '⚐', 'Ne plus suivre', ''],
    ['epingler', '📌', 'Épingler', ''], ['desepingler', '📍', 'Désépingler', ''],
    ['junk', '⚠', 'Comme indésirable', ''], ['not_junk', '✓', 'Comme non indésirable', '']];
  var EPINGLE_AIDE = 'L’épingle est propre à AxiorHub (par personne) : elle n’est pas un drapeau de la messagerie et n’apparaît ni dans Outlook ni dans Thunderbird.';

  function page() {
    var box = $('#bx5625.bx56106'), L = window.axh5625Liste;
    if (!box || !L || !L.dense) return;
    var head = $('.bx5625-head', box), rows = $('#bx5625-rows'), panel = $('#bx5625-panel'), flist = $('#bx5625-flist');
    var etat = L.etat, matters = null;
    var chargerDossiersClients = function () {
      if (matters) return Promise.resolve(matters);
      return api('m567/matters').then(function (d) { matters = d.matters || []; return matters; }).catch(function () { matters = []; return []; });
    };

    // ---- sélection
    var coches = function () { return $$('.c5670-pick input:checked', rows); };
    var selection = function () {
      return coches().map(function (c) {
        var u = c.getAttribute('data-uid'), m = etat.lignes[u] || {};
        return { dossier: etat.folder || '', uid: u, objet: m.subject || '', expediteur: m.from || '', date: m.date || '' };
      });
    };
    var cibles = function () {   // cochés, sinon le courriel ouvert
      var s = selection();
      if (s.length) return s;
      var c = L.courant();
      return c ? [{ dossier: etat.folder || '', uid: String(c.uid), objet: c.subject || '', expediteur: c.from || '', date: c.date || '' }] : [];
    };

    // ---- outils de l'en-tête : tri, filtres et recherche étendue, « Plus »
    head.classList.add('bx56106-tete');
    var titre = $('#bx5625-title');
    var outils = el('div', { 'class': 'bx56106-outils' });
    var chercherB = $('.bx5625-search button[type=submit]', head);   // libellé court : la loupe, avec infobulle
    if (chercherB) { chercherB.textContent = '⌕'; chercherB.title = 'Rechercher — touche /'; chercherB.setAttribute('aria-label', 'Rechercher'); }
    // « Rechercher dans » : cases cumulables à côté du champ (un seul champ de recherche), choix mémorisé par personne
    var formQ = $('.bx5625-search', head), champQ = $('input[name=q]', formQ);
    var NOMS_DANS = { objet: 'Objet', corps: 'Corps', expediteur: 'Expéditeur', destinataires: 'Destinataires (À, Cc)' };
    var dans = el('details', { 'class': 'bx56106-dans' });
    var resumeDans = el('summary', { 'class': 'ax-btn ghost', title: 'Rechercher dans : objet, expéditeur', text: 'Dans ▾' });
    var corpsDans = el('fieldset', { 'class': 'bx56106-menu bx56106-dans-corps' }, [el('legend', { text: 'Rechercher dans' })]);
    Object.keys(NOMS_DANS).forEach(function (k) {
      var i = el('input', { type: 'checkbox', name: 'dans', value: k });
      i.checked = k === 'objet' || k === 'expediteur';
      corpsDans.appendChild(el('label', { 'class': 'bx56106-chk' }, [i, el('span', { text: ' ' + NOMS_DANS[k] })]));
    });
    corpsDans.appendChild(el('p', { 'class': 'ax-muted bx56106-note', text: 'La recherche dans le corps est faite par le serveur de messagerie : elle peut être plus lente sur une grosse boîte.' }));
    dans.appendChild(resumeDans); dans.appendChild(corpsDans);
    if (formQ) formQ.appendChild(dans);
    var champsDans = function () { return $$('input[name=dans]:checked', corpsDans).map(function (i) { return i.value; }); };
    var majDans = function () {
      var c = champsDans();
      var libelles = c.map(function (k) { return NOMS_DANS[k].replace(/ \(.*/, '').toLowerCase(); }).join(', ');
      resumeDans.title = 'Rechercher dans : ' + libelles;
      resumeDans.classList.toggle('bx56106-actif', c.join(',') !== 'objet,expediteur');
      if (champQ) { champQ.placeholder = 'Rechercher (' + libelles + ')'; champQ.setAttribute('aria-label', 'Rechercher dans ce dossier : ' + libelles); }
      etat.opts.dans = c.join(',') === 'objet,expediteur' ? '' : c.join(',');
    };
    corpsDans.addEventListener('change', function (ev) {
      if (!champsDans().length) { ev.target.checked = true; toast('Cochez au moins un champ de recherche.', true); return; }
      majDans();
      api('m56106/boite/preferences', { recherche: champsDans() }).catch(function (e) { toast(e.message, true); });
      if (etat.q) appliquer();
    });
    var tri = el('select', { 'class': 'bx56106-tri', 'aria-label': 'Trier les courriels', title: 'Trier les courriels' }, [
      el('option', { value: 'date_desc', text: '↓ Récents' }), el('option', { value: 'date_asc', text: '↑ Anciens' }),
      el('option', { value: 'expediteur', text: 'Expéditeur' }), el('option', { value: 'objet', text: 'Objet' })]);
    var filtres = el('details', { 'class': 'bx56106-filtres' });
    var resumeLib = el('span', { 'class': 'bx56106-lib', text: ' Trier, filtrer' }), resumeNb = el('span', { 'class': 'bx56106-nb' });   // libellé replié en petite largeur (icône et infobulle restent)
    var resume = el('summary', { 'class': 'ax-btn ghost', title: 'Trier et filtrer (non lus, suivis, pièces jointes, période)', 'aria-label': 'Trier et filtrer' },
      [el('span', { 'aria-hidden': 'true', text: '⚲' }), resumeLib, resumeNb]);
    var chk = function (nom, libelle) {
      var i = el('input', { type: 'checkbox', name: nom });
      return el('label', { 'class': 'bx56106-chk' }, [i, el('span', { text: ' ' + libelle })]);
    };
    var champ = function (nom, libelle, type) {
      return el('label', { 'class': 'bx56106-champ' }, [el('span', { text: libelle }), el('input', { type: type || 'text', name: nom, maxlength: '120' })]);
    };
    var formF = el('form', { 'class': 'bx56106-filtres-corps', role: 'search', 'aria-label': 'Filtres et recherche étendue' }, [
      el('fieldset', {}, [el('legend', { text: 'Trier par' }), tri]),
      el('fieldset', {}, [el('legend', { text: 'Afficher seulement' }), chk('non_lus', 'Non lus'), chk('suivis', 'Suivis'), chk('pj', 'Avec pièces jointes')]),
      el('fieldset', {}, [el('legend', { text: 'Période (sur le serveur)' }),
        el('div', { 'class': 'bx56106-periode' }, [champ('du', 'Du', 'date'), champ('au', 'Au', 'date')])]),
      el('div', { 'class': 'bx56106-pied' }, [el('button', { type: 'submit', 'class': 'ax-btn', text: 'Appliquer' }),
        el('button', { type: 'reset', 'class': 'ax-btn ghost', text: 'Effacer' })])]);
    filtres.appendChild(resume); filtres.appendChild(formF);
    var appliquer = function () {
      var o = { tri: tri.value === 'date_desc' ? '' : tri.value };
      ['non_lus', 'suivis', 'pj'].forEach(function (k) { if (formF.elements[k].checked) o[k] = '1'; });
      ['du', 'au'].forEach(function (k) { var v = formF.elements[k].value.trim(); if (v) o[k] = v; });
      var n = Object.keys(o).filter(function (k) { return k !== 'tri' && o[k]; }).length;
      if (etat.opts.dans) o.dans = etat.opts.dans;   // « Rechercher dans » gardé
      resumeNb.textContent = n ? ' (' + n + ')' : '';
      resume.setAttribute('aria-label', n ? 'Trier et filtrer, ' + n + ' filtre(s) actif(s)' : 'Trier et filtrer');
      resume.classList.toggle('bx56106-trie', !!o.tri);
      resume.classList.toggle('bx56106-actif', !!n);
      L.filtrer(o);
    };
    tri.addEventListener('change', appliquer);
    formF.addEventListener('submit', function (ev) { ev.preventDefault(); filtres.open = false; appliquer(); });
    formF.addEventListener('reset', function () { setTimeout(appliquer, 0); });
    var plus = el('details', { 'class': 'bx56106-plus' }, [el('summary', { 'class': 'ax-btn ghost', title: 'Plus de fonctions', 'aria-label': 'Plus de fonctions' },
      [el('span', { 'aria-hidden': 'true', text: '⋯' }), el('span', { 'class': 'bx56106-lib', text: ' Plus' })])]);
    var menu = el('div', { 'class': 'bx56106-menu', role: 'menu' });
    var regles = el('a', { 'class': 'bx56106-item', role: 'menuitem', href: prefix + '/parametres?rubrique=messagerie', text: '⚙ Signature et règles' });
    var classement = el('button', { type: 'button', 'class': 'bx56106-item', role: 'menuitem', text: '🗂 Classement automatique…' });
    classement.addEventListener('click', function () {
      plus.open = false;
      var d = $('.cx5643-general', head);
      if (!d) { toast('Classement automatique indisponible.', true); return; }
      head.classList.add('bx56106-regles'); d.open = true;
      var s = $('summary', d); if (s) s.focus();
    });
    var aideB = el('button', { type: 'button', 'class': 'bx56106-item', role: 'menuitem', text: '⌨ Raccourcis clavier (?)' });
    aideB.addEventListener('click', function () { plus.open = false; aide(); });
    var nouveauD = el('button', { type: 'button', 'class': 'bx56106-item', role: 'menuitem', text: '＋ Nouveau dossier…' });
    nouveauD.addEventListener('click', function () { plus.open = false; creerDossier(); });
    // « Actualiser » : recharge le dossier sans recharger la page (sélection, filtres et position gardés) ; heure de la mise à jour en bas
    var actualiserB = el('button', { type: 'button', 'class': 'ax-btn ghost bx56106-actualiser', 'data-bx56106': 'actualiser',
                                     title: 'Actualiser la liste (F5)', 'aria-label': 'Actualiser la liste (F5)', text: '⟳' });
    var auto = true;
    try { auto = window.localStorage.getItem('bx56106:auto') !== '0'; } catch (e) { void e; }
    var autoB = el('button', { type: 'button', 'class': 'bx56106-item', role: 'menuitemcheckbox', 'aria-checked': String(auto), 'data-bx56106': 'auto' });
    var majAuto = function () { autoB.setAttribute('aria-checked', String(auto)); autoB.textContent = (auto ? '☑' : '☐') + ' Actualiser toutes les 2 min (page visible)'; };
    majAuto();
    autoB.addEventListener('click', function () {
      auto = !auto; majAuto();
      try { window.localStorage.setItem('bx56106:auto', auto ? '1' : '0'); } catch (e) { void e; }
    });
    [regles, classement, nouveauD, autoB, aideB].forEach(function (x) { menu.appendChild(x); });
    plus.appendChild(menu);
    var memoire = null, majHeure = '';
    var actualiser = function () {
      memoire = { uids: selection().map(function (x) { return x.uid; }), haut: rows.scrollTop, ouvert: L.courant() ? String(L.courant().uid) : '' };
      L.recharger();
    };
    actualiserB.addEventListener('click', actualiser);
    setInterval(function () {   // rafraîchissement léger : page visible, rien d'ouvert ni en cours, désactivable dans « Plus »
      if (!auto || document.hidden || enCours || box.classList.contains('bx56106-glisse') || $('details[open]', box) || $('.bx56106-marquer-menu:not([hidden])', box)) return;
      actualiser();
    }, 120000);
    head.addEventListener('toggle', function (ev) {   // « Classement automatique » refermé : le bloc retourne caché
      var d = ev.target;
      if (d.classList && d.classList.contains('cx5643-general') && !d.open) head.classList.remove('bx56106-regles');
    }, true);
    outils.appendChild(actualiserB); outils.appendChild(filtres); outils.appendChild(plus);
    head.appendChild(outils);

    // ---- menu « Marquer » : courriel ouvert ou cochés ; entrées sans effet grisées ; clavier (flèches, Entrée, Échap), rôles menu/menuitem
    var ouverts = [];
    function menuMarquer(source, classe) {
      var racine = el('div', { 'class': 'bx56106-marquer' });
      var bouton = el('button', { type: 'button', 'class': classe + ' bx56106-marquer-btn', 'aria-haspopup': 'menu', 'aria-expanded': 'false',
                                  title: 'Marquer : lu, non lu, suivi, épinglé, indésirable', text: '🏷 Marquer ▾' });
      var liste = el('div', { 'class': 'bx56106-menu bx56106-marquer-menu', role: 'menu', 'aria-label': 'Marquer', hidden: 'hidden' });
      var entrees = MARQUER.map(function (a) {
        var b = el('button', { type: 'button', role: 'menuitem', 'class': 'bx56106-item', 'data-action': a[0], tabindex: '-1',
                               title: a[0].indexOf('epingl') >= 0 ? EPINGLE_AIDE : (a[3] ? 'Raccourci : ' + a[3] : '') },
          [el('span', { 'aria-hidden': 'true', text: a[1] + ' ' }), el('span', { text: a[2] }), a[3] ? el('kbd', { 'class': 'bx56106-kbd', text: a[3] }) : null]);
        b.addEventListener('click', function () {
          if (b.getAttribute('aria-disabled') === 'true') return;
          fermer(true);
          if (a[0] === 'tout_lu') toutLu(bouton); else agir(a[0], source(), {}, bouton);
        });
        liste.appendChild(b);
        return b;
      });
      var actifs = function () { return entrees.filter(function (b) { return b.getAttribute('aria-disabled') !== 'true'; }); };
      var maj = function () {   // entrées sans effet sur la sélection : grisées
        var items = source(), ls = items.map(function (x) { return etat.lignes[x.uid] || {}; }), connu = function (k) { return ls.some(function (m) { return k in m; }); };
        var effet = {
          lu: ls.some(function (m) { return m.seen === false; }) || (items.length && !connu('seen')),
          non_lu: ls.some(function (m) { return m.seen === true; }) || (items.length && !connu('seen')),
          tout_lu: true,
          suivi: ls.some(function (m) { return !m.flagged; }), non_suivi: ls.some(function (m) { return !!m.flagged; }),
          epingler: ls.some(function (m) { return !m.epingle; }), desepingler: ls.some(function (m) { return !!m.epingle; }),
          junk: items.length > 0 && etat.role !== 'junk', not_junk: items.length > 0 && etat.role === 'junk' };
        entrees.forEach(function (b) {
          var ok = !!effet[b.getAttribute('data-action')] && (items.length > 0 || b.getAttribute('data-action') === 'tout_lu');
          b.setAttribute('aria-disabled', String(!ok));
        });
      };
      var fermer = function (garderFocus) {
        if (liste.hidden) return;
        liste.hidden = true; bouton.setAttribute('aria-expanded', 'false');
        if (garderFocus) bouton.focus();
      };
      var ouvrir = function () {
        ouverts.forEach(function (f) { f(); });
        maj(); liste.hidden = false; bouton.setAttribute('aria-expanded', 'true');
        var a = actifs(); if (a.length) a[0].focus();
      };
      ouverts.push(function () { fermer(false); });
      bouton.addEventListener('click', function () { if (liste.hidden) ouvrir(); else fermer(true); });
      bouton.addEventListener('keydown', function (ev) { if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); ouvrir(); } });
      liste.addEventListener('keydown', function (ev) {
        var a = actifs(), i = a.indexOf(document.activeElement);
        if (ev.key === 'ArrowDown') { ev.preventDefault(); a[(i + 1) % a.length].focus(); }
        else if (ev.key === 'ArrowUp') { ev.preventDefault(); a[(i - 1 + a.length) % a.length].focus(); }
        else if (ev.key === 'Home') { ev.preventDefault(); a[0].focus(); }
        else if (ev.key === 'End') { ev.preventDefault(); a[a.length - 1].focus(); }
        else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); fermer(true); }
        else if (ev.key === 'Tab') fermer(false);
      });
      document.addEventListener('click', function (ev) { if (!racine.contains(ev.target)) fermer(false); });
      racine.appendChild(bouton); racine.appendChild(liste);
      return { racine: racine, maj: maj, bouton: bouton };
    }
    var toutLu = function (bouton) {
      var envoyer = function (confirme) {
        return api('m56106/boite/dossier/tout-lu', { dossier: etat.folder || '', uidvalidity: etat.uidvalidity || '', confirme: confirme || '', cle: cle() })
          .then(function (r) {
            if (r.confirmation_requise) { return (!confirme && window.confirm(r.message)) ? envoyer(String(r.nombre)) : null; }   // nombre confirmé
            toast(r.message, !!(r.echecs && r.echecs.length)); actualiser();
            return r;
          })
          .catch(function (e) { toast(e.message, true); if (e.code === 'boite_reindexee') L.recharger(); });
      };
      busy(bouton, true);
      envoyer('').then(function () { busy(bouton, false); });
    };

    // ---- barre d'actions groupées (apparaît dès qu'un courriel est coché, sur la ligne de « Tout cocher »)
    var barre = el('div', { id: 'bx56106-actions', 'class': 'bx56106-actions', role: 'toolbar', 'aria-label': 'Actions sur les courriels cochés', hidden: 'hidden' });
    var compte = el('span', { 'class': 'bx56106-compte', 'aria-live': 'polite' });
    barre.appendChild(compte);
    var boutons = {};
    ACTIONS.forEach(function (a) {
      var b = el('button', { type: 'button', 'class': 'bx56106-act', 'data-action': a[0], title: a[3], 'aria-label': a[3] },
        [el('span', { 'aria-hidden': 'true', text: a[1] }), el('span', { 'class': 'bx56106-lib', text: ' ' + a[2] })]);
      b.addEventListener('click', function () { agir(a[0], selection(), {}, b); });
      boutons[a[0]] = b; barre.appendChild(b);
    });
    var deplacer = el('select', { 'class': 'bx56106-deplacer', 'aria-label': 'Déplacer les courriels cochés vers un dossier', title: 'Déplacer vers un dossier' },
      [el('option', { value: '', text: '📁 Déplacer…' })]);
    deplacer.addEventListener('change', function () { var v = deplacer.value; deplacer.value = ''; if (v) agir('move', selection(), { cible: v }, deplacer); });
    barre.appendChild(deplacer);
    var marquerGroupe = menuMarquer(selection, 'bx56106-act');
    barre.appendChild(marquerGroupe.racine);
    var client = el('details', { 'class': 'bx56106-client' }, [el('summary', { 'class': 'bx56106-act', title: 'Dossier client : sous-dossier de messagerie ou classement',
      text: '📂 Dossier client' })]);
    var choixClient = el('select', { 'class': 'bx56106-matter', 'aria-label': 'Dossier client' }, [el('option', { value: '', text: 'Choisir le dossier client…' })]);
    var vers = el('button', { type: 'button', 'class': 'ax-btn', title: 'Déplacer vers le sous-dossier du dossier client (créé au besoin sous « Dossiers clients »)', text: 'Déplacer vers son sous-dossier' });
    var classer = el('button', { type: 'button', 'class': 'ax-btn ghost', title: 'Classer dans le dossier client (conservation dans CORRESPONDANCES)', text: 'Classer dans le dossier' });
    vers.addEventListener('click', function () { if (!choixClient.value) { choixClient.focus(); return; } client.open = false; agir('sous_dossier', cibles(), { matter: choixClient.value }, vers); });
    classer.addEventListener('click', function () { if (!choixClient.value) { choixClient.focus(); return; } client.open = false; agir('classer', cibles(), { matter: choixClient.value }, classer); });
    client.appendChild(el('div', { 'class': 'bx56106-menu bx56106-client-corps' }, [choixClient, vers, classer]));
    client.addEventListener('toggle', function () {
      if (!client.open || choixClient.options.length > 1) return;
      chargerDossiersClients().then(function (all) {
        all.forEach(function (x) { choixClient.appendChild(el('option', { value: x.id, text: x.label })); });
        var c = L.courant(); if (c && c.matter) choixClient.value = c.matter;
      });
    });
    barre.appendChild(client);
    barre.appendChild(el('span', { 'class': 'bx56106-actions-v', 'data-lot': 'V' }));   // 5.6.106 (lot V) : Résumer avec l'IA, → Tâche, → Agenda
    head.appendChild(barre);

    var majBarre = function () {
      var n = coches().length;
      barre.hidden = !n;
      head.classList.toggle('bx56106-selection', !!n);
      compte.textContent = n + (n > 1 ? ' cochés' : ' coché');
      boutons.trash.hidden = etat.role === 'trash';
      boutons.archive.hidden = etat.role === 'archive';
      if (deplacer.options.length <= 1 || deplacer.dataset.dossier !== etat.folder) {
        deplacer.textContent = ''; deplacer.dataset.dossier = etat.folder;
        deplacer.appendChild(el('option', { value: '', text: '📁 Déplacer…' }));
        (etat.folders || []).forEach(function (f) { if (f.name !== (etat.folder || (etat.dossiers56106 || {}).inbox)) deplacer.appendChild(el('option', { value: f.name, text: f.court || f.label })); });
      }
      ajusterHauteur();
    };
    rows.addEventListener('change', function (ev) { if (ev.target.closest('.c5670-pick')) majBarre(); });
    head.addEventListener('click', function (ev) { if (ev.target.closest('.c5670-all')) setTimeout(majBarre, 0); });
    var surListe = function () {
      etiquettes();
      epingles();
      var tout = $('.c5670-all', head); if (tout) tout.textContent = 'Tout cocher';
      var d = new Date();
      majHeure = 'à jour à ' + d.getHours() + ' h ' + ('0' + d.getMinutes()).slice(-2);
      var pages = $('#bx5625-pages');
      if (pages) {   // dans la ligne du compte (« Page 1 / 3 · 57 courriel(s) · à jour à 16 h 42 »), sans ligne de plus
        var compteur = $('span.ax-muted', pages);
        if (!compteur) { compteur = el('span', { 'class': 'ax-muted' }); pages.appendChild(compteur); }
        compteur.appendChild(el('span', { 'class': 'bx56106-heure', text: (compteur.textContent ? ' · ' : '') + majHeure }));
      }
      actualiserB.title = 'Actualiser la liste (F5) — ' + majHeure;
      var m = memoire; memoire = null;
      setTimeout(function () {   // après « Actualiser » : sélection, courriel ouvert et position gardés (cases posées par v5670.js)
        if (m) {
          m.uids.forEach(function (u) {
            var c = $('.c5670-pick input[data-uid="' + u + '"]', rows);
            if (c && !c.checked) { c.checked = true; c.dispatchEvent(new Event('change', { bubbles: true })); }
          });
          var o = m.ouvert && $('.ax-row[data-uid="' + m.ouvert + '"]', rows); if (o) o.classList.add('on');
          rows.scrollTop = m.haut;
        }
        majBarre();
      }, 0);
    };
    box.addEventListener('axh5625:liste', surListe);
    if (Object.keys(etat.lignes || {}).length) setTimeout(surListe, 0);   // liste affichée avant le démarrage de ce script
    var epingles = function () {   // épinglés en tête (icône) ; épingle au survol de chaque ligne
      $$('.ax-row[data-uid]', rows).forEach(function (r) {
        var m = etat.lignes[r.getAttribute('data-uid')] || {}, l1 = $('.bx56106-l1', r);
        r.classList.toggle('bx56106-epinglee', !!m.epingle);
        if (!l1 || $('.bx56106-epingler', l1)) return;
        l1.insertBefore(el('span', { 'class': 'bx56106-epingler', 'aria-hidden': 'true', title: (m.epingle ? 'Désépingler' : 'Épingler en tête de liste') + ' — ' + EPINGLE_AIDE,
                                     text: m.epingle ? '📌' : '📍' }), l1.querySelector('.bx56106-date'));
      });
      var ep = $$('.ax-row.bx56106-epinglee', rows); if (ep.length) ep[ep.length - 1].classList.add('bx56106-derniere-epingle');
    };
    rows.addEventListener('click', function (ev) {   // épingle au survol : n'ouvre pas le courriel
      var p = ev.target.closest && ev.target.closest('.bx56106-epingler');
      if (!p) return;
      ev.preventDefault(); ev.stopPropagation();
      var u = p.closest('.ax-row').getAttribute('data-uid'), m = etat.lignes[u] || {};
      agir(m.epingle ? 'desepingler' : 'epingler', [{ uid: u }], {}, null);
    }, true);
    var etiquettes = function () {   // étiquette d'état courte (le libellé complet reste en infobulle)
      $$('.bx56106-etat', rows).forEach(function (s) {
        var st = (s.className.match(/bx5625-st-(\w+)/) || [])[1];
        if (COURT[st]) s.textContent = COURT[st];
      });
    };

    // ---- envoi d'une action groupée : bilan exact, liste rechargée
    var enCours = false;
    var agir = function (action, items, extra, bouton) {
      if (enCours) return;
      if (!items.length) { toast('Cochez au moins un courriel.', true); return; }
      if (action === 'trash' && !window.confirm(items.length > 1 ? 'Déplacer ces ' + items.length + ' courriels dans la Corbeille ? Ils restent récupérables dans la messagerie.'
                                                                : 'Déplacer ce courriel dans la Corbeille ? Il reste récupérable dans la messagerie.')) return;
      enCours = true; busy(bouton, true);
      var corps = { action: action, dossier: etat.folder || '', uidvalidity: etat.uidvalidity || '', cle: cle(),
                    items: items.map(function (x) { return { uid: x.uid }; }) };
      Object.keys(extra || {}).forEach(function (k) { corps[k] = extra[k]; });
      return api('m56106/boite/actions', corps).then(function (r) {
        toast(r.message, !!(r.echecs && r.echecs.length));
        var ouvert = L.courant();
        var parti = ouvert && (r.uids_reussis || []).indexOf(String(ouvert.uid)) >= 0 && ['lu', 'non_lu', 'suivi', 'non_suivi', 'classer'].indexOf(action) < 0;
        if (parti) { var c = L.ctx(); if (c.clear) c.clear(); }
        actualiser();   // sélection, position et courriel ouvert gardés
        if (action === 'sous_dossier') L.dossiers();
        return r;
      }).catch(function (e) {
        toast(e.message, true);
        if (e.code === 'boite_reindexee') L.recharger();
      }).then(function (r) { enCours = false; busy(bouton, false); return r; });
    };

    // ---- dossiers : réduire la colonne, créer, renommer, supprimer, glisser-déposer
    var nav = $('.bx5625-folders', box), h2 = $('h2', nav);
    var replier = el('button', { type: 'button', 'class': 'bx56106-replier', 'aria-expanded': 'true', 'aria-controls': 'bx5625-flist', title: 'Réduire la colonne des dossiers', text: '⟨' });
    var nouveau = el('button', { type: 'button', 'class': 'bx56106-nouveau', 'data-bx56106': 'nouveau-dossier', title: 'Nouveau dossier', 'aria-label': 'Nouveau dossier', text: '＋' });
    var outilsD = el('span', { 'class': 'bx56106-dossiers-outils' }, [nouveau, replier]);
    if (h2) h2.appendChild(outilsD);
    try { if (window.localStorage.getItem('bx56106:replie') === '1') box.classList.add('bx56106-replie'); } catch (e) { void e; }
    var syncReplier = function () {
      var r = box.classList.contains('bx56106-replie');
      replier.setAttribute('aria-expanded', String(!r)); replier.textContent = r ? '⟩' : '⟨';
      replier.title = r ? 'Déplier la colonne des dossiers' : 'Réduire la colonne des dossiers';
    };
    syncReplier();
    replier.addEventListener('click', function () {
      box.classList.toggle('bx56106-replie'); syncReplier();
      try { window.localStorage.setItem('bx56106:replie', box.classList.contains('bx56106-replie') ? '1' : '0'); } catch (e) { void e; }
    });
    var creerDossier = function () {
      var nom = window.prompt('Nom du nouveau dossier de la messagerie :', '');
      if (nom === null || !nom.trim()) return;
      busy(nouveau, true);
      api('m56106/boite/dossier/creer', { nom: nom.trim(), cle: cle() }).then(function (r) {
        toast(r.message); return L.dossiers();
      }).catch(function (e) { toast(e.message, true); }).then(function () { busy(nouveau, false); });
    };
    nouveau.addEventListener('click', creerDossier);
    var gerer = function (nomImap, court, ancre) {
      var vieux = $('.bx56106-gerer-menu'); if (vieux) vieux.remove();
      var m = el('div', { 'class': 'bx56106-menu bx56106-gerer-menu', role: 'menu', 'aria-label': 'Dossier ' + court });
      var ren = el('button', { type: 'button', role: 'menuitem', 'class': 'bx56106-item', text: '✎ Renommer…' });
      var sup = el('button', { type: 'button', role: 'menuitem', 'class': 'bx56106-item', text: '🗑 Supprimer…' });
      var fermer = function () { m.remove(); ancre.focus(); };
      ren.addEventListener('click', function () {
        var nom = window.prompt('Nouveau nom du dossier « ' + court + ' » :', court);
        m.remove();
        if (nom === null || !nom.trim() || nom.trim() === court) return;
        api('m56106/boite/dossier/renommer', { ancien: nomImap, nom: nom.trim(), cle: cle() }).then(function (r) {
          toast(r.message);
          return L.dossiers().then(function () { if (etat.folder === nomImap) L.choisir(r.dossier); });
        }).catch(function (e) { toast(e.message, true); });
      });
      sup.addEventListener('click', function () {
        m.remove();
        if (!window.confirm('Supprimer le dossier « ' + court + ' » de la messagerie ?')) return;
        var envoyer = function (confirme) {
          return api('m56106/boite/dossier/supprimer', { dossier: nomImap, confirme: confirme || '', cle: cle() }).then(function (r) {
            if (r.confirmation_requise) { return (!confirme && window.confirm(r.message)) ? envoyer(String(r.nombre)) : null; }   // dossier non vide
            toast(r.message);
            return L.dossiers().then(function () { if (etat.folder === nomImap) L.choisir((etat.dossiers56106 || {}).inbox || 'INBOX'); });
          }).catch(function (e) { toast(e.message, true); });
        };
        envoyer('');
      });
      m.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { ev.preventDefault(); fermer(); } });
      m.appendChild(ren); m.appendChild(sup);
      ancre.parentNode.insertBefore(m, ancre.nextSibling);
      ren.focus();
    };
    var dansInit = false;
    var surDossiers = function (detail) {
      if (!dansInit && detail && Array.isArray(detail.recherche)) {   // choix « Rechercher dans » mémorisé pour la personne
        dansInit = true;
        $$('input[name=dans]', corpsDans).forEach(function (i) { i.checked = detail.recherche.indexOf(i.value) >= 0; });
        if (!champsDans().length) $$('input[name=dans]', corpsDans).forEach(function (i) { i.checked = i.value === 'objet' || i.value === 'expediteur'; });
        majDans();
      }
      $$('.bx5625-folder[data-dossier]', flist).forEach(function (b) {
        if (b.getAttribute('data-protege') || (b.nextElementSibling && b.nextElementSibling.classList.contains('bx56106-gerer'))) return;
        var nomImap = b.getAttribute('data-dossier'), court = b.textContent.replace(/^\S+\s*/, '').trim() || nomImap;
        var g = el('button', { type: 'button', 'class': 'bx56106-gerer', title: 'Renommer ou supprimer « ' + court + ' »', 'aria-label': 'Gérer le dossier ' + court, 'aria-haspopup': 'menu', text: '⋯' });
        g.addEventListener('click', function () { gerer(nomImap, court, g); });
        b.parentNode.insertBefore(g, b.nextSibling);
      });
      majBarre();
    };
    box.addEventListener('axh5625:dossiers', function (ev) { surDossiers(ev.detail); });
    // liste ou dossiers arrivés avant le démarrage de ce script (réponses reçues entre deux scripts différés) : traités maintenant
    if (etat.dossiers56106) setTimeout(function () { surDossiers(etat.dossiers56106); }, 0);
    // glisser un courriel (ou la sélection) sur un dossier de la colonne de gauche
    rows.addEventListener('dragstart', function (ev) {
      var ligne = ev.target.closest && ev.target.closest('.ax-row[data-uid]');
      if (!ligne) return;
      var u = ligne.getAttribute('data-uid'), sel = selection().map(function (x) { return x.uid; });
      var uids = sel.indexOf(u) >= 0 ? sel : [u];
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('application/x-axiorhub-courriels', JSON.stringify(uids));
      ev.dataTransfer.setData('text/plain', uids.length + ' courriel(s)');
      box.classList.add('bx56106-glisse');
    });
    rows.addEventListener('dragend', function () { box.classList.remove('bx56106-glisse'); $$('.bx56106-survol', flist).forEach(function (b) { b.classList.remove('bx56106-survol'); }); });
    var cibleDe = function (ev) {
      var b = ev.target.closest && ev.target.closest('.bx5625-folder[data-dossier]');
      if (!b || (b.getAttribute('data-dossier') === (etat.folder || (etat.dossiers56106 || {}).inbox))) return null;
      return Array.prototype.indexOf.call(ev.dataTransfer.types || [], 'application/x-axiorhub-courriels') >= 0 ? b : null;
    };
    flist.addEventListener('dragover', function (ev) { var b = cibleDe(ev); if (!b) return; ev.preventDefault(); ev.dataTransfer.dropEffect = 'move'; b.classList.add('bx56106-survol'); });
    flist.addEventListener('dragleave', function (ev) { var b = ev.target.closest && ev.target.closest('.bx5625-folder'); if (b) b.classList.remove('bx56106-survol'); });
    flist.addEventListener('drop', function (ev) {
      var b = cibleDe(ev); if (!b) return;
      ev.preventDefault(); b.classList.remove('bx56106-survol'); box.classList.remove('bx56106-glisse');
      var uids = [];
      try { uids = JSON.parse(ev.dataTransfer.getData('application/x-axiorhub-courriels')) || []; } catch (e) { uids = []; }
      var role = b.getAttribute('data-role'), action = role === 'trash' ? 'trash' : (role === 'junk' ? 'junk' : 'move');
      agir(action, uids.map(function (u) { return { uid: String(u) }; }), action === 'move' ? { cible: b.getAttribute('data-dossier') } : {}, null);
    });

    // ---- lecteur : menu « Marquer » à la place des boutons séparés (Non lu, Suivi, Indésirable), sous-dossier du dossier client
    var courantSeul = function () { var c = L.courant(); return c ? [{ dossier: etat.folder || '', uid: String(c.uid), objet: c.subject || '', expediteur: c.from || '', date: c.date || '' }] : []; };
    var completerLecteur = function () {
      var bar = $('.bx5628-bar', panel), c = L.courant();
      if (!bar || bar.dataset.l106 || !c) return;
      bar.dataset.l106 = '1';
      ['Marquer comme non lu', 'Marquer pour suivi', 'Message indésirable'].forEach(function (t) {   // regroupés dans « Marquer » (barre compacte)
        var b = $('[title="' + t + '"]', bar); if (b) { b.hidden = true; b.setAttribute('data-l106-regroupe', '1'); }
      });
      var mq = menuMarquer(courantSeul, 'bx5628-btn');
      bar.appendChild(mq.racine);
      var sd = el('button', { type: 'button', 'class': 'bx5628-btn', title: 'Classer dans un sous-dossier du dossier client (créé au besoin sous « Dossiers clients »)',
                              'aria-label': 'Classer dans un sous-dossier du dossier client', text: '📂 Sous-dossier client' });
      sd.addEventListener('click', function () { client.open = true; var s = $('summary', client); if (s) s.focus(); barre.hidden = false; head.classList.add('bx56106-selection'); });
      bar.appendChild(sd);
    };
    new MutationObserver(completerLecteur).observe(panel, { childList: true, subtree: true });

    // ---- raccourcis clavier (hors champ de saisie, page Courriels seulement)
    var lignes = function () { return $$('.ax-row[data-uid]', rows); };
    var aller = function (pas) {
      var ls = lignes(); if (!ls.length) return;
      var i = ls.indexOf($('.ax-row.on', rows)), j = i < 0 ? 0 : Math.max(0, Math.min(ls.length - 1, i + pas));
      ls[j].click(); ls[j].focus();
      ls[j].scrollIntoView({ block: 'nearest' });
    };
    var rediger = function (mode) {
      var c = L.courant();
      if (!c || !window.axh5628) { toast('Ouvrez d’abord un courriel.', true); return; }
      window.axh5628.compose(mode, c, L.ctx());
    };
    var aide = function () {
      var d = $('#bx56106-aide');
      if (!d) {
        d = el('div', { id: 'bx56106-aide', 'class': 'bx56106-aide', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'bx56106-aide-t', tabindex: '-1' });
        var t = el('table', {}, []), tb = el('tbody');
        [['Suppr', 'Déplacer dans la Corbeille'], ['Ctrl+R', 'Répondre'], ['Ctrl+Maj+R', 'Répondre à tous'], ['Ctrl+F', 'Transférer'],
         ['Ctrl+Q', 'Marquer comme lu'], ['Ctrl+U', 'Marquer comme non lu'], ['Inser', 'Marquer pour suivi'], ['J / K', 'Courriel suivant / précédent'],
         ['F5', 'Actualiser la liste (sans recharger la page)'], ['/', 'Rechercher'], ['?', 'Cette aide'], ['Échap', 'Fermer']].forEach(function (r) {
          tb.appendChild(el('tr', {}, [el('th', { scope: 'row' }, [el('kbd', { text: r[0] })]), el('td', { text: r[1] })]));
        });
        t.appendChild(tb);
        var fermer = el('button', { type: 'button', 'class': 'ax-btn', text: 'Fermer' });
        fermer.addEventListener('click', function () { d.hidden = true; });
        d.appendChild(el('div', { 'class': 'bx56106-aide-boite' }, [el('h2', { id: 'bx56106-aide-t', text: 'Raccourcis clavier de Courriels' }),
          el('p', { 'class': 'ax-muted', text: 'Actifs hors des champs de saisie. Ctrl+R, Ctrl+F et F5 remplacent ceux du navigateur sur cette page seulement ; Ctrl+F5 recharge toujours la page entière.' }),
          t, el('p', { 'class': 'ax-muted', text: 'Épingler (menu « Marquer ») : ' + EPINGLE_AIDE + ' 50 épingles au plus par personne.' }), fermer]));
        d.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { ev.preventDefault(); d.hidden = true; } });
        d.addEventListener('click', function (ev) { if (ev.target === d) d.hidden = true; });
        document.body.appendChild(d);
      }
      d.hidden = false;
      $('button', d).focus();
    };
    document.addEventListener('keydown', function (ev) {
      // F5 (sans modificateur) : actualise la liste, même depuis un champ (évite de perdre une saisie) ; Ctrl+F5 reste au navigateur
      if (ev.key === 'F5' && !ev.ctrlKey && !ev.shiftKey && !ev.altKey && !ev.metaKey) { ev.preventDefault(); actualiser(); return; }
      if (ev.defaultPrevented || saisie(ev.target) || ev.altKey || ev.metaKey) return;
      var aideOuverte = $('#bx56106-aide'); if (aideOuverte && !aideOuverte.hidden) return;
      var k = ev.key, ctrl = ev.ctrlKey;
      var fait = function () { ev.preventDefault(); ev.stopPropagation(); };
      if (ctrl && (k === 'r' || k === 'R')) { fait(); rediger(ev.shiftKey ? 'repondre_tous' : 'repondre'); return; }
      if (ctrl && !ev.shiftKey && (k === 'f' || k === 'F')) { fait(); rediger('transferer'); return; }
      if (ctrl && (k === 'q' || k === 'Q')) { fait(); agir('lu', cibles(), {}, null); return; }
      if (ctrl && (k === 'u' || k === 'U')) { fait(); agir('non_lu', cibles(), {}, null); return; }
      if (ctrl) return;
      if (k === 'Delete') { fait(); if (etat.role !== 'trash') agir('trash', cibles(), {}, null); return; }
      if (k === 'Insert') { fait(); agir('suivi', cibles(), {}, null); return; }
      if (k === 'j' || k === 'J') { fait(); aller(1); return; }
      if (k === 'k' || k === 'K') { fait(); aller(-1); return; }
      if (k === '/') { fait(); var q = $('.bx5625-search input[name=q]', box); if (q) { q.focus(); q.select(); } return; }
      if (k === '?') { fait(); aide(); return; }
      if (k === 'Escape') { $$('details.bx56106-filtres[open],details.bx56106-plus[open],details.bx56106-client[open],details.bx56106-dans[open]', box).forEach(function (d) { d.open = false; }); }
    }, true);

    // ---- petite largeur : onglets Liste / Lecteur
    var onglets = el('div', { 'class': 'bx56106-onglets', role: 'tablist', 'aria-label': 'Liste ou lecteur' });
    var oL = el('button', { type: 'button', role: 'tab', id: 'bx56106-o-liste', 'aria-controls': 'bx5625-rows', 'aria-selected': 'true', text: 'Liste' });
    var oR = el('button', { type: 'button', role: 'tab', id: 'bx56106-o-lecteur', 'aria-controls': 'bx5625-panel', 'aria-selected': 'false', text: 'Lecteur' });
    var vue = function (v) {
      box.setAttribute('data-vue', v);
      oL.setAttribute('aria-selected', String(v === 'liste')); oR.setAttribute('aria-selected', String(v === 'lecteur'));
    };
    oL.addEventListener('click', function () { vue('liste'); });
    oR.addEventListener('click', function () { vue('lecteur'); });
    onglets.appendChild(oL); onglets.appendChild(oR);
    box.insertBefore(onglets, box.firstChild);
    vue('liste');
    rows.addEventListener('click', function (ev) { if (ev.target.closest('.ax-row[data-uid]')) vue('lecteur'); });

    // ---- hauteur : la page entière ne défile pas ; la liste et le lecteur défilent chacun dans leur zone
    var ajusterHauteur = function () {
      if (window.matchMedia('(max-width: 700px)').matches) { box.style.removeProperty('--bx56106-haut'); return; }
      var haut = Math.round(box.getBoundingClientRect().top + (window.scrollY || 0));
      var dispo = Math.max(320, window.innerHeight - haut - 8);
      box.style.setProperty('--bx56106-haut', dispo + 'px');
      var deborde = document.scrollingElement.scrollHeight - window.innerHeight;
      if (deborde > 0 && dispo - deborde >= 320) box.style.setProperty('--bx56106-haut', (dispo - deborde) + 'px');
    };
    window.addEventListener('resize', ajusterHauteur);
    ajusterHauteur();
    setTimeout(ajusterHauteur, 300);
    setTimeout(ajusterHauteur, 1500);
    var raccourcir = function () {   // libellés courts dans la barre (le libellé complet reste en infobulle et pour les lecteurs d'écran)
      var n = $('.bx5628-new', head);
      if (n && !n.dataset.l106) { n.dataset.l106 = '1'; n.textContent = '✉ Nouveau'; n.title = 'Nouveau message'; n.setAttribute('aria-label', 'Nouveau message'); }
    };
    raccourcir();
    new MutationObserver(function () { raccourcir(); window.requestAnimationFrame(ajusterHauteur); }).observe(head, { childList: true });

    window.axh56106liste = { selection: selection, rafraichir: function () { actualiser(); }, agir: agir,
                             classerSousDossier: function (m) { client.open = true; barre.hidden = false; if (m && m.matter) chargerDossiersClients().then(function () { choixClient.value = m.matter; }); } };
    if (titre) titre.setAttribute('title', titre.textContent);
  }

  var demarre = false;
  function boot() { if (demarre) return; demarre = true; boutonsFiche(); page(); }
  // script différé chargé avant v5625.js : démarrage après tous les scripts différés (DOMContentLoaded), ou aussitôt si la page est prête
  if (document.readyState === 'complete') boot(); else { document.addEventListener('DOMContentLoaded', boot); window.addEventListener('load', boot); }
})();
