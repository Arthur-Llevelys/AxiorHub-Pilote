/* AxiorHub 5.6.111 (lot F) — onglet « Conversation IA » du dossier.
   Discussions du dossier (historique commun, filtre avant pagination), instruction envoyée par integration.submit_question (route
   m56111/conversation/…/instruction, clé d'idempotence : un double clic ou un nouvel essai après coupure rend le même reçu), dictée commune
   (window.axiorhubDictee.attacher : c'est un objet), brouillon côté serveur (m56109/historique/brouillon), pièces jointes (route existante
   /assistant/attachment), volet d'aperçu commun du dossier (v5679.js) à droite, navigateur supervisé suivi par ses routes existantes
   (m56109/navigateur/etat et /main, m5673/…), missions par m568/mission5614/control et m56109/livrables/<id>/plan.
   Tout texte venu du serveur passe par textContent ; aucun HTML n'est injecté. Rien n'est envoyé sans un clic sur « Envoyer ». */
(function () {
  'use strict';
  var racine = document.getElementById('c111');
  if (!racine || racine.__c111) return;
  racine.__c111 = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var mid = racine.getAttribute('data-c111-matter') || '';
  var role = racine.getAttribute('data-c111-role') || '';
  var poste = racine.getAttribute('data-c111-poste') === '1';
  var $ = function (s, r) { return (r || racine).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || racine).querySelectorAll(s)); };
  var CLE_ETAT = 'axiorhub-c111:' + mid;
  var ACTIFS = ['envoyee', 'en_attente', 'en_cours', 'en_pause', 'a_preciser'];

  var saisie = $('#c111-saisie'), form = $('[data-c111-form]'), envoyerBtn = $('[data-c111-envoyer]');
  var recu = $('[data-c111-recu]'), suiviTxt = $('[data-c111-suivi]'), arreterBtn = $('[data-c111-arreter]');
  var zoneMessages = $('[data-c111-messages]'), listeFils = $('[data-c111-fils]'), plusFils = $('[data-c111-plus]');
  var titreFil = $('[data-c111-fil-titre]'), centre = $('.c111-centre');
  var pane = document.getElementById('d79-preview'), vide = $('[data-c111-vide]');
  var etat = lireEtat();
  var fil = racine.getAttribute('data-c111-fil') || etat.fil || '';
  var roleAcces = 'proprietaire', cleEnCours = null, envoiEnCours = false, demande = null, minuteur = null, suiteFils = null;
  var pieces = [], docCourant = etat.doc || '', brouillonMinuteur = null, brouillonContexte = '', navMinuteur = null, seance = null;

  // ------------------------------------------------------------------ outils
  function lireEtat() { try { return JSON.parse(sessionStorage.getItem(CLE_ETAT) || '{}') || {}; } catch (e) { return {}; } }
  function garderEtat() {
    try {
      sessionStorage.setItem(CLE_ETAT, JSON.stringify({ fil: fil, doc: docCourant, page: pagePdf(), mode: racine.getAttribute('data-c111-mode') || 'document',
        zone: racine.getAttribute('data-c111-zone') || 'discussion', agrandi: racine.getAttribute('data-c111-agrandi') === '1',
        vue: racine.getAttribute('data-c111-vue-active') || 'discussion', portee: portee(), fichiers: fichiersCoches() }));
    } catch (e) { /* stockage de l'onglet indisponible : rien n'est perdu côté serveur */ }
  }
  function el(tag, attrs, enfants) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k];
      else if (k === 'class') n.className = attrs[k];
      else n.setAttribute(k, attrs[k]);
    });
    (enfants || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function cle() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function toast(message, mauvais) {
    if (window.axh5670 && window.axh5670.toast) { window.axh5670.toast(message, mauvais); return; }
    var box = document.getElementById('ax-toast');
    if (!box) return;
    var t = el('div', { 'class': 'ax-toast' + (mauvais ? ' bad' : ''), text: message });
    box.appendChild(t); setTimeout(function () { t.remove(); }, mauvais ? 9000 : 5000);
  }
  function api(nom, corps) {
    var opts = corps === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps)
    };
    return fetch(prefix + '/api440/' + nom, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; }).then(function (d) {
        if (!r.ok || (d && d.error)) { var err = new Error((d && d.message) || 'Demande refusée.'); err.code = d && d.error; err.http = r.status; throw err; }
        return d;
      });
    }, function () { var err = new Error('Connexion au serveur interrompue.'); err.reseau = true; throw err; });
  }
  function conv(action) { return 'm56111/conversation/' + encodeURIComponent(mid) + '/' + action; }
  function hrefDoc(chemin) { return prefix + '/documents/edit?path=' + encodeURIComponent(chemin) + '&matter=' + encodeURIComponent(mid); }
  function dateLocale(iso) {
    try { var d = new Date(iso); if (isNaN(d)) return ''; return d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }); }
    catch (e) { return ''; }
  }
  function tr(texte) { return window.ax5634Translate ? window.ax5634Translate(texte) : texte; }   // dictionnaire des écrans (v5634t.js)
  function mobile() { return window.matchMedia && window.matchMedia('(max-width: 960px)').matches; }

  // ------------------------------------------------------------------ hauteur disponible (ResizeObserver / visualViewport)
  /* La fiche du dossier (en-tête, bandeaux) peut occuper plus de la moitié de l'écran : à l'ouverture, la page est amenée sur la barre des
     onglets (l'en-tête reste au-dessus, accessible en remontant) et l'onglet prend la hauteur qui reste sous cette barre. La hauteur ne
     dépend donc pas du défilement : fenêtre visible − barre fixe du gabarit − barre des onglets − marge. */
  var demandeHauteur = 0, onglets = document.querySelector('nav.d79-tabs');
  function barreFixe() {   // hauteur d'une barre fixe ou collante en haut de la fenêtre (gabarit), 0 sinon
    var x = Math.min(window.innerWidth - 1, Math.max(0, racine.getBoundingClientRect().left + 8));
    var n = document.elementFromPoint(x, 1), bas = 0;
    for (; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      var pos = getComputedStyle(n).position;
      if (pos === 'fixed' || pos === 'sticky') { bas = Math.max(0, n.getBoundingClientRect().bottom); break; }
    }
    return bas;
  }
  function ajusterHauteur() {
    cancelAnimationFrame(demandeHauteur);
    demandeHauteur = requestAnimationFrame(function () {
      var vv = window.visualViewport, h = vv ? vv.height : window.innerHeight;
      var ecart = onglets ? Math.max(0, racine.getBoundingClientRect().top - onglets.getBoundingClientRect().top) : 0;
      var dispo = Math.floor(h - barreFixe() - ecart - 12);
      racine.style.setProperty('--conversation-hauteur-disponible', Math.max(mobile() ? 360 : 420, dispo) + 'px');
    });
  }
  function amenerSurOnglets() {
    if (!onglets || window.location.hash) return;
    var cible = window.scrollY + onglets.getBoundingClientRect().top - barreFixe();
    if (cible > 0) window.scrollTo(0, cible);
  }
  window.addEventListener('resize', ajusterHauteur);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', ajusterHauteur);
  if (window.ResizeObserver) {
    var observe = new ResizeObserver(ajusterHauteur);
    $$('.d79-head, .d79-tabs, .d27-paused', document).forEach(function (n) { observe.observe(n); });
  }

  // ------------------------------------------------------------------ zones (Discussion, Historique, Missions), modes, agrandi, bascule mobile
  function choisirZone(zone) {
    racine.setAttribute('data-c111-zone', zone);
    $$('[data-c111-zone]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-c111-zone') === zone)); });
    $$('[data-c111-zone-contenu]').forEach(function (z) { z.hidden = z.getAttribute('data-c111-zone-contenu') !== zone; });
    if (zone === 'historique' && !listeFils.childElementCount) chargerFils(false);
    if (zone === 'missions') rafraichirSuivi();
    garderEtat();
  }
  $$('[data-c111-zone]').forEach(function (b) { b.addEventListener('click', function () { choisirZone(b.getAttribute('data-c111-zone')); }); });

  function choisirMode(mode) {
    racine.setAttribute('data-c111-mode', mode);
    $$('[data-c111-mode]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-c111-mode') === mode)); });
    $$('[data-c111-volet]').forEach(function (v) { v.hidden = v.getAttribute('data-c111-volet') !== mode; });
    if (mode === 'navigateur') suivreNavigateur(); else arreterNavigateur();
    garderEtat();
  }
  $$('[data-c111-mode]').forEach(function (b) { b.addEventListener('click', function () { choisirMode(b.getAttribute('data-c111-mode')); }); });

  var agrandir = $('[data-c111-agrandir]');
  function poserAgrandi(oui) {
    racine.setAttribute('data-c111-agrandi', oui ? '1' : '0');
    agrandir.setAttribute('aria-pressed', String(oui));
    agrandir.textContent = oui ? 'Réduire' : 'Agrandir';
    garderEtat();
  }
  agrandir.addEventListener('click', function () { poserAgrandi(racine.getAttribute('data-c111-agrandi') !== '1'); });

  function choisirVue(vue) {
    racine.setAttribute('data-c111-vue-active', vue);
    $$('[data-c111-vue]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-c111-vue') === vue)); });
    $$('[data-c111-panneau-mobile]').forEach(function (p) { p.hidden = mobile() && p.getAttribute('data-c111-panneau-mobile') !== (vue === 'apercu' ? 'apercu' : 'discussion'); });
    ajusterHauteur();
    garderEtat();
  }
  $$('[data-c111-vue]').forEach(function (b) { b.addEventListener('click', function () { choisirVue(b.getAttribute('data-c111-vue')); }); });
  if (window.matchMedia) {
    var mq = window.matchMedia('(max-width: 960px)');
    var surChangement = function () { choisirVue(racine.getAttribute('data-c111-vue-active') || 'discussion'); };
    if (mq.addEventListener) mq.addEventListener('change', surChangement); else if (mq.addListener) mq.addListener(surChangement);
  }

  // ------------------------------------------------------------------ volet Document : aperçu commun (v5679.js), document et page gardés
  function pagePdf() {
    var n = pane && pane.querySelector('.m82-pdf-bar input[type=number]');
    var v = n ? parseInt(n.value, 10) : 0;
    return v > 0 ? v : 1;
  }
  racine.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('a[data-d79-doc]');
    if (a && !(pane && pane.contains(a))) { docCourant = a.getAttribute('data-d79-doc') || ''; garderEtat(); }
  }, true);
  function surApercu() {
    var ouvert = pane && !pane.hidden;
    if (vide) vide.hidden = !!ouvert;
    if (!ouvert) docCourant = '';
    if (mobile()) choisirVue(ouvert ? 'apercu' : 'discussion');
    garderEtat();
  }
  if (pane && window.MutationObserver) new MutationObserver(surApercu).observe(pane, { attributes: true, attributeFilter: ['hidden'] });
  function rouvrirDocument(chemin, page) {
    if (!chemin || !pane) return;
    var connu = $$('a[data-d79-doc]').some(function (a) { return a.getAttribute('data-d79-doc') === chemin; });
    if (!connu) return;                         // seulement un fichier proposé par le serveur pour ce dossier
    var lien = el('a', { href: hrefDoc(chemin) + (page > 1 ? '#page=' + page : ''), 'data-d79-doc': chemin, hidden: '' });
    racine.appendChild(lien);
    lien.click();
    lien.remove();
  }

  // ------------------------------------------------------------------ contexte utilisé
  var choix = $('#c111-choix'), modifier = $('[data-c111-modifier]'), resume = $('[data-c111-resume]');
  var nbFichiers = $$('[data-c111-fichier]').length, courriels = ($('[data-c111-contexte]') || racine).getAttribute('data-c111-courriels');
  function portee() { var r = $('input[name="c111-portee"]:checked'); return r ? r.value : 'tout'; }
  function fichiersCoches() { return $$('[data-c111-fichier]:checked').map(function (c) { return c.value; }); }
  function majContexte() {
    var choisis = fichiersCoches();
    $$('[data-c111-fichier]').forEach(function (c) { c.disabled = !c.checked && choisis.length >= 20; });
    if (!resume) return;
    if (portee() === 'selection') {
      resume.textContent = choisis.length ? choisis.length + ' fichier(s) choisi(s), données du dossier' : 'aucun fichier choisi — cochez des fichiers ou revenez à tout le dossier';
    } else {
      resume.textContent = 'tout le dossier — ' + nbFichiers + ' fichier(s), ' + (courriels !== '' && courriels !== null ? courriels + ' courriel(s)' : 'nombre de courriels inconnu') + ', données du dossier';
    }
    garderEtat();
  }
  if (modifier && choix) {
    modifier.addEventListener('click', function () {
      choix.hidden = !choix.hidden;
      modifier.setAttribute('aria-expanded', String(!choix.hidden));
      modifier.textContent = choix.hidden ? 'Modifier' : 'Fermer';
    });
  }
  $$('input[name="c111-portee"], [data-c111-fichier]').forEach(function (i) { i.addEventListener('change', majContexte); });
  if (etat.portee === 'selection') {
    var radio = $('input[name="c111-portee"][value="selection"]');
    if (radio) radio.checked = true;
    (etat.fichiers || []).forEach(function (chemin) { $$('[data-c111-fichier]').forEach(function (c) { if (c.value === chemin) c.checked = true; }); });
  }

  // ------------------------------------------------------------------ dictée (composant commun)
  var micro = $('[data-c111-micro]'), statutMicro = $('[data-c111-micro-statut]');
  function brancherDictee() {
    var D = window.axiorhubDictee;
    if (!micro) return;
    if (!D || typeof D.attacher !== 'function') {
      micro.disabled = true;
      micro.title = 'Dictée indisponible sur cette page';
      if (statutMicro) statutMicro.textContent = 'Dictée indisponible sur cette page.';
      return;
    }
    D.attacher(micro, {
      statut: statutMicro,
      inserer: function (texte) {
        var debut = typeof saisie.selectionStart === 'number' ? saisie.selectionStart : saisie.value.length;
        var fin = typeof saisie.selectionEnd === 'number' ? saisie.selectionEnd : debut;
        saisie.setRangeText(texte, debut, fin, 'end');
        saisie.dispatchEvent(new Event('input', { bubbles: true }));
        saisie.focus();
      }
    });
    racine.setAttribute('data-c111-dictee', 'attachee');
  }

  // ------------------------------------------------------------------ brouillon (mécanisme commun de l'historique)
  function contexteBrouillon() { return 'c111:' + mid + ':' + (fil || 'nouvelle'); }
  function enregistrerBrouillon(immediat) {
    clearTimeout(brouillonMinuteur);
    var ctx = brouillonContexte || contexteBrouillon(), texte = saisie.value;
    var faire = function () {   // fetch « keepalive » à la fermeture de la page (le jeton CSRF est un en-tête : pas de sendBeacon)
      fetch(prefix + '/api440/m56109/historique/brouillon', {
        method: 'POST', credentials: 'same-origin', keepalive: !!immediat,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ contexte: ctx, texte: texte.slice(0, 12000) })
      }).catch(function () { /* brouillon non enregistré : le texte reste dans la page */ });
    };
    if (immediat) faire(); else brouillonMinuteur = setTimeout(faire, 900);
  }
  function chargerBrouillon() {
    brouillonContexte = contexteBrouillon();
    var ctx = brouillonContexte;
    return api('m56109/historique/brouillon?contexte=' + encodeURIComponent(ctx)).then(function (d) {
      if (ctx === brouillonContexte && !saisie.value && d && d.texte) { saisie.value = d.texte; ajusterSaisie(); }
    }).catch(function () { /* aucun brouillon lisible */ });
  }
  function ajusterSaisie() { saisie.style.height = 'auto'; saisie.style.height = Math.min(saisie.scrollHeight + 2, 200) + 'px'; }
  saisie.addEventListener('input', function () { ajusterSaisie(); enregistrerBrouillon(false); });
  saisie.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); envoyer(); } });
  window.addEventListener('pagehide', function () { if (brouillonMinuteur) enregistrerBrouillon(true); garderEtat(); });

  // ------------------------------------------------------------------ pièces jointes (route existante, périmètre du dossier)
  var listePieces = $('[data-c111-pieces]'), garder = $('[data-c111-garder]'), joindre = $('[data-c111-joindre]');
  function rendrePieces() {
    listePieces.replaceChildren();
    pieces.forEach(function (p, i) {
      var etatTxt = p.etat === 'envoi' ? 'envoi…' : (p.etat === 'ok' ? 'prête' : 'échec : ' + p.message);
      var retirer = el('button', { type: 'button', 'class': 'c111-lien', text: 'Retirer', 'aria-label': 'Retirer ' + p.nom });
      retirer.addEventListener('click', function () { pieces.splice(i, 1); rendrePieces(); });
      listePieces.appendChild(el('li', { 'class': 'c111-piece c111-piece-' + p.etat }, [el('span', { translate: 'no', text: p.nom }), document.createTextNode(' — '),
        el('span', { 'class': 'ax-muted', text: etatTxt }), document.createTextNode(' '), retirer]));
    });
    if (garder) garder.parentNode.hidden = !pieces.length;
  }
  if (joindre) {
    joindre.addEventListener('change', function () {
      Array.prototype.slice.call(joindre.files || []).forEach(function (f) {
        var p = { nom: f.name, etat: 'envoi', id: '', message: '' };
        pieces.push(p); rendrePieces();
        fetch(prefix + '/assistant/attachment', {
          method: 'POST', credentials: 'same-origin', body: f,
          headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrf, 'X-Attachment-Name': encodeURIComponent(f.name), 'X-Attachment-Matter': mid }
        }).then(function (r) { return r.json().catch(function () { return { error: 'reponse_invalide' }; }).then(function (d) { return { ok: r.ok, d: d || {} }; }); })
          .then(function (x) {
            if (!x.ok || !x.d.attachment_id) throw new Error(x.d.message || x.d.error || 'pièce refusée');
            p.etat = 'ok'; p.id = x.d.attachment_id;
          }).catch(function (err) { p.etat = 'echec'; p.message = err.message === 'Failed to fetch' ? 'connexion interrompue' : err.message; })
          .then(rendrePieces);
      });
      joindre.value = '';
    });
  }

  // ------------------------------------------------------------------ discussions du dossier
  function ligneFil(x) {
    var bouton = el('button', { type: 'button', 'class': 'c111-fil-bouton' + (x.id === fil ? ' c111-choisi' : ''), 'aria-current': x.id === fil ? 'true' : 'false' }, [
      el('span', { 'class': 'c111-fil-titre', translate: 'no', text: x.title || 'Discussion' }),
      el('span', { 'class': 'ax-muted c111-fil-meta', text: dateLocale(x.updated) + ' · ' + x.etat_libelle + (x.role_acces === 'viewer' ? ' · Lecteur' : (x.role_acces === 'contributor' ? ' · Participant' : '')) }),
      x.extrait ? el('span', { 'class': 'c111-fil-extrait', translate: 'no', text: x.extrait }) : null]);
    bouton.addEventListener('click', function () { choisirFil(x.id, x.role_acces, x.title); choisirZone('discussion'); });
    return el('li', { 'data-c111-fil-id': x.id }, [bouton]);
  }
  function chargerFils(suite) {
    var url = conv('fils') + (suite && suiteFils ? '?apres=' + encodeURIComponent(suiteFils) : '');
    if (!suite) listeFils.replaceChildren(el('li', { 'class': 'ax-muted', text: 'Chargement des discussions…' }));
    return api(url).then(function (d) {
      if (!suite) listeFils.replaceChildren();
      (d.epinglees || []).concat(d.items || []).forEach(function (x) { listeFils.appendChild(ligneFil(x)); });
      if (!listeFils.childElementCount) listeFils.appendChild(el('li', { 'class': 'ax-muted', text: 'Aucune discussion sur ce dossier pour le moment.' }));
      suiteFils = d.next || null;
      plusFils.hidden = !suiteFils;
    }).catch(function (err) {
      if (err.code === 'curseur_invalide') { suiteFils = null; return chargerFils(false); }
      listeFils.replaceChildren(el('li', { 'class': 'c111-alerte', role: 'alert', text: err.message }));
    });
  }
  plusFils.addEventListener('click', function () { chargerFils(true); });

  function rendreMessage(m) {
    var auteur = m.role === 'user' ? 'Vous' : 'Assistant — à relire';
    var texte = el('div', { 'class': 'c111-texte', translate: 'no' });
    texte.textContent = m.contenu;
    var art = el('article', { 'class': 'c111-msg c111-msg-' + (m.role === 'user' ? 'vous' : 'ia') + (m.masque ? ' c111-msg-masque' : ''), 'data-c111-message': String(m.id) }, [
      el('header', { 'class': 'c111-msg-tete' }, [el('strong', { text: auteur }), el('span', { 'class': 'ax-muted', text: ' ' + dateLocale(m.created) + (m.role === 'user' ? ' · ' + m.etat_libelle : '') })]),
      texte]);
    if (m.contenu.length > 4000) {
      art.classList.add('c111-long');
      var tout = el('button', { type: 'button', 'class': 'c111-lien', 'aria-expanded': 'false', text: 'Afficher tout le texte (' + m.contenu.length + ' caractères)' });
      tout.addEventListener('click', function () {
        var ouvert = art.classList.toggle('c111-long-ouvert');
        tout.setAttribute('aria-expanded', String(ouvert));
        tout.textContent = ouvert ? 'Réduire le texte' : 'Afficher tout le texte (' + m.contenu.length + ' caractères)';
      });
      art.appendChild(tout);
    }
    if (m.sources && m.sources.length) {
      var ul = el('ul', { 'class': 'c111-sources', 'aria-label': 'Sources' });
      m.sources.forEach(function (s) {
        ul.appendChild(el('li', {}, [s.chemin ? el('a', { href: hrefDoc(s.chemin), 'data-d79-doc': s.chemin, translate: 'no', text: s.titre.split('/').pop() })
                                              : el('span', { translate: 'no', text: s.titre })]));
      });
      art.appendChild(el('div', { 'class': 'c111-sources-bloc' }, [el('span', { 'class': 'ax-muted', text: 'Sources : ' }), ul]));
    }
    return art;
  }
  function chargerFil(id) {
    var pages = 0, tous = [];
    function page(apres) {
      return api(conv('fil') + '?id=' + encodeURIComponent(id) + '&apres=' + apres).then(function (d) {
        if (id !== fil) return null;
        tous = tous.concat(d.items || []);
        roleAcces = (d.thread && d.thread.role_acces) || roleAcces;
        if (d.thread && titreFil) titreFil.textContent = (d.thread.title || 'Discussion') + (roleAcces === 'viewer' ? ' · lecture seule' : '');
        pages += 1;
        if (d.next && pages < 40) return page(d.next);
        return d;
      });
    }
    zoneMessages.setAttribute('aria-busy', 'true');
    return page(0).then(function (d) {
      if (d === null) return;
      zoneMessages.replaceChildren();
      tous.forEach(function (m) { zoneMessages.appendChild(rendreMessage(m)); });
      if (!tous.length) zoneMessages.appendChild(el('p', { 'class': 'ax-muted', text: 'Discussion vide.' }));
      zoneMessages.appendChild(el('p', { 'class': 'c111-export' }, [el('a', { href: prefix + '/api440/m56109/historique/exporter?id=' + encodeURIComponent(id) + '&format=md',
        text: 'Exporter la discussion (texte complet)' })]));
      verrouillerComposeur();
      centre.scrollTop = centre.scrollHeight;
    }).catch(function (err) {
      zoneMessages.replaceChildren(el('p', { 'class': 'c111-alerte', role: 'alert', text: err.message }));
      if (err.code === 'introuvable') { fil = ''; garderEtat(); }
    }).then(function () { zoneMessages.removeAttribute('aria-busy'); });
  }
  function verrouillerComposeur() {
    var lecture = !!fil && roleAcces === 'viewer';
    saisie.disabled = lecture;
    envoyerBtn.disabled = lecture || envoiEnCours;
    if (lecture) recu.textContent = 'Discussion partagée en lecture seule : ouvrez une nouvelle discussion pour poser une question.';
  }
  function choisirFil(id, acces, titre) {
    if (brouillonMinuteur) enregistrerBrouillon(true);
    fil = id || '';
    roleAcces = acces || 'proprietaire';
    cleEnCours = null;
    demande = null;
    saisie.value = '';
    ajusterSaisie();
    recu.textContent = '';
    $$('.c111-fil-bouton', listeFils).forEach(function (b) {
      var choisi = b.parentNode.getAttribute('data-c111-fil-id') === fil;
      b.classList.toggle('c111-choisi', choisi); b.setAttribute('aria-current', String(choisi));
    });
    if (!fil) {
      if (titreFil) titreFil.textContent = 'Nouvelle discussion';
      zoneMessages.replaceChildren(el('p', { 'class': 'ax-muted c111-accueil', text: 'Nouvelle discussion : écrivez votre instruction. Le premier message crée la discussion, rattachée à ce dossier.' }));
      verrouillerComposeur();
    } else {
      if (titreFil) titreFil.textContent = titre || 'Discussion';
      chargerFil(fil);
    }
    chargerBrouillon();
    rafraichirSuivi();
    garderEtat();
  }
  $('[data-c111-nouvelle]').addEventListener('click', function () { choisirFil('', 'proprietaire'); choisirZone('discussion'); saisie.focus(); });

  // ------------------------------------------------------------------ envoi d'une instruction
  function envoyer() {
    if (envoiEnCours) return;
    var question = saisie.value.trim();
    if (!question) { recu.textContent = 'Écrivez une instruction avant d’envoyer.'; saisie.focus(); return; }
    if (pieces.some(function (p) { return p.etat === 'envoi'; })) { recu.textContent = 'Une pièce jointe est encore en cours d’envoi : patientez.'; return; }
    if (pieces.some(function (p) { return p.etat === 'echec'; })) { recu.textContent = 'Retirez la pièce jointe en échec avant d’envoyer.'; return; }
    if (portee() === 'selection' && !fichiersCoches().length) { recu.textContent = 'Cochez au moins un fichier, ou choisissez « Tout le dossier ».'; return; }
    cleEnCours = cleEnCours || cle();
    var corps = { question: question, fil: fil, fichiers: portee() === 'selection' ? fichiersCoches() : [], pieces: pieces.map(function (p) { return p.id; }), cle: cleEnCours };
    envoiEnCours = true;
    envoyerBtn.disabled = true;
    envoyerBtn.setAttribute('aria-busy', 'true');
    recu.textContent = 'Envoi en cours…';
    api(conv('instruction'), corps).then(function (r) {
      // reçu conservé AVANT de vider la saisie
      recu.textContent = (r.rejoue ? 'Demande déjà reçue (même envoi) · ' : '') + r.avis + ' · Message n° ' + r.message_id;
      var nouveau = r.thread_id !== fil;
      var ancienContexte = brouillonContexte;
      fil = r.thread_id;
      roleAcces = 'proprietaire';
      cleEnCours = null;
      saisie.value = '';
      ajusterSaisie();
      clearTimeout(brouillonMinuteur); brouillonMinuteur = null;
      brouillonContexte = ancienContexte; enregistrerBrouillon(true);   // brouillon effacé (texte vide) : il a été envoyé
      brouillonContexte = contexteBrouillon();
      if (!(garder && garder.checked)) { pieces = []; rendrePieces(); }
      if (nouveau) chargerFils(false);
      garderEtat();
      return chargerFil(fil).then(rafraichirSuivi);
    }).catch(function (err) {
      if (err.reseau) recu.textContent = 'Connexion interrompue : votre texte est conservé. Renvoyez : la demande ne sera pas enregistrée deux fois.';
      else {
        recu.textContent = err.message;
        if (err.code !== 'operation_en_cours') cleEnCours = null;
      }
    }).then(function () {
      envoiEnCours = false;
      envoyerBtn.removeAttribute('aria-busy');
      verrouillerComposeur();
    });
  }
  form.addEventListener('submit', function (ev) { ev.preventDefault(); envoyer(); });

  // ------------------------------------------------------------------ suivi du travail (état réel) et arrêt
  function rafraichirSuivi() {
    clearTimeout(minuteur);
    return api(conv('suivi') + (fil ? '?fil=' + encodeURIComponent(fil) : '')).then(function (d) {
      demande = d.demande;
      var txt = 'Aucune demande en cours.';
      if (d.pause && d.pause.globale) txt = 'L’IA est en pause : les demandes attendent la reprise.';
      if (demande) txt = (d.pause && d.pause.globale ? 'IA en pause · ' : '') + 'Dernière demande : ' + demande.etat_libelle + '.';
      suiviTxt.textContent = txt;
      arreterBtn.hidden = !(demande && demande.arretable);
      rendreMissions(d.missions || []);
      if (demande && ACTIFS.indexOf(demande.etat) >= 0) minuteur = setTimeout(function () { rafraichirSuivi().then(function () { if (!(demande && ACTIFS.indexOf(demande.etat) >= 0) && fil) chargerFil(fil); }); }, 2500);
    }).catch(function (err) { suiviTxt.textContent = err.message; arreterBtn.hidden = true; });
  }
  arreterBtn.addEventListener('click', function () {
    if (!demande || !fil) return;
    arreterBtn.disabled = true;
    api(conv('arreter'), { fil: fil, message: demande.message_id }).then(function (r) { toast(r.message); })
      .catch(function (err) { toast(err.message, true); })
      .then(function () { arreterBtn.disabled = false; rafraichirSuivi(); if (fil) chargerFil(fil); });
  });

  // ------------------------------------------------------------------ missions (moteur taches5614)
  var listeMissions = $('[data-c111-missions]');
  function rendreMissions(items) {
    if (!listeMissions) return;
    listeMissions.replaceChildren();
    if (!items.length) { listeMissions.appendChild(el('li', { 'class': 'ax-muted c111-vide-liste', text: 'Aucune mission sur ce dossier.' })); return; }
    var peut = role === 'administrateur' || role === 'avocat';
    items.forEach(function (x) {
      var p = x.progression || {};
      var actions = el('div', { 'class': 'c111-mission-actions' });
      [['pause_possible', 'pause', 'Pause'], ['reprise_possible', 'resume', 'Reprendre'], ['arret_possible', 'cancel', 'Arrêter']].forEach(function (b) {
        if (peut && x[b[0]]) actions.appendChild(el('button', { type: 'button', 'class': 'ax-btn ghost', 'data-c111-mission': x.id, 'data-c111-action': b[1], text: b[2] }));
      });
      if (x.url) actions.appendChild(el('a', { href: x.url, text: 'Détail' }));
      listeMissions.appendChild(el('li', { 'class': 'c111-mission', 'data-c111-mission-id': x.id }, [
        el('div', {}, [el('strong', { translate: 'no', text: x.titre || x.objectif.slice(0, 80) }), el('span', { 'class': 'c111-badge', text: ' ' + x.libelle })]),
        el('div', { 'class': 'ax-muted', text: (x.prochaine || '').slice(0, 160) + ' · ' + (p.accepted || 0) + '/' + (p.required || 0) + ' étape(s) acceptée(s)' }), actions]));
    });
  }
  racine.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-c111-mission][data-c111-action]');
    if (!b) return;
    var action = b.getAttribute('data-c111-action');
    if (action === 'cancel' && !window.confirm(tr('Arrêter cette mission ? Ses tâches en attente et leurs travaux sont annulés ; les fichiers déjà produits sont gardés.'))) return;
    b.disabled = true;
    api('m568/mission5614/control', { id: b.getAttribute('data-c111-mission'), action: action }).then(function (m) { toast('Mission : ' + (m.label || m.state)); })
      .catch(function (err) { toast(err.message, true); }).then(function () { b.disabled = false; rafraichirSuivi(); });
  });
  var formRecherche = $('[data-c111-recherche]');
  if (formRecherche) {
    var cleRecherche = null;
    formRecherche.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var champ = $('#c111-recherche-q'), bouton = formRecherche.querySelector('button[type=submit]');
      cleRecherche = cleRecherche || cle();
      bouton.disabled = true;
      api(conv('recherche'), { question: champ.value, cle: cleRecherche }).then(function (r) { toast(r.message); champ.value = ''; cleRecherche = null; })
        .catch(function (err) { toast(err.message, true); if (!err.reseau) cleRecherche = null; })
        .then(function () { bouton.disabled = false; rafraichirSuivi(); });
    });
  }
  var formPlan = $('[data-c111-plan]');
  if (formPlan) {
    var clePlan = null;
    formPlan.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var doc = $('#c111-plan-doc').value, bouton = formPlan.querySelector('button[type=submit]');
      var plan = [], budget = 0;
      try { var brut = JSON.parse(formPlan.getAttribute('data-c111-plan-json') || '{}'); plan = brut.plan || []; budget = brut.budget_tokens || 0; } catch (e) { plan = []; }
      clePlan = clePlan || cle();
      bouton.disabled = true;
      api('m56109/livrables/' + encodeURIComponent(doc) + '/plan', { plan: plan, budget_tokens: budget, cle: clePlan }).then(function (r) { toast(r.message); clePlan = null; })
        .catch(function (err) { toast(err.message, true); if (!err.reseau) clePlan = null; })
        .then(function () { bouton.disabled = false; rafraichirSuivi(); });
    });
  }

  // ------------------------------------------------------------------ navigateur supervisé (séance existante, poste seulement)
  var navEtat = $('[data-c111-nav-etat]'), navBoutons = $('[data-c111-nav-boutons]'), navLancer = $('[data-c111-nav-lancer]');
  var ETATS_NAV = { demarrage: 'Démarrage', en_cours: 'L’agent travaille', validation: 'Votre accord est attendu', site_a_autoriser: 'Site à autoriser',
    besoin_utilisateur: 'Action de votre part attendue', attente_avocat: 'Question en attente', main_a_l_avocat: 'La main est à vous',
    transfert_de_controle: 'Transfert de la main en cours', termine: 'Terminé', arrete: 'Arrêté', erreur: 'Interrompu', limite_atteinte: 'Limite atteinte', a_verifier: 'À vérifier' };
  var MAINS = { ia: 'L’agent pilote la page.', transfert: 'Transfert de la main en cours : attendez la confirmation avant toute saisie.', avocat: 'La main est à vous : plus aucune commande de l’agent n’est en cours.' };
  function rendreSeance(s) {
    seance = s;
    if (!navEtat) return;
    navEtat.replaceChildren();
    if (!s) { navEtat.appendChild(el('p', { 'class': 'ax-muted', text: 'Aucune séance du navigateur en cours.' })); navBoutons.hidden = true; return; }
    if (s.dossier && s.dossier !== mid) {
      navEtat.appendChild(el('p', { text: 'Une séance du navigateur est en cours sur un autre dossier : un seul agent pilote le navigateur à la fois.' }));
      navBoutons.hidden = true; return;
    }
    navEtat.appendChild(el('p', {}, [el('strong', { text: ETATS_NAV[s.etat] || s.etat }), el('span', { text: ' · ' + (MAINS[s.main_etat] || '') })]));
    navEtat.appendChild(el('p', { 'class': 'ax-muted', translate: 'no', text: s.tache || '' }));
    if (s.adresse) navEtat.appendChild(el('p', { 'class': 'ax-muted', translate: 'no', text: s.adresse }));
    if (s.attente) {
      var q = el('div', { 'class': 'c111-nav-attente', role: 'alert' }, [el('p', { translate: 'no', text: (s.attente.question || s.attente.description || 'Décision attendue') })]);
      [['autoriser', 'Autoriser'], ['refuser', 'Refuser']].concat(s.attente.kind === 'site' ? [['toujours', 'Toujours autoriser ce site']] : [])
        .concat(s.attente.kind === 'blocage' ? [['reprendre', 'J’ai terminé : reprendre']] : []).forEach(function (c) {
          var b = el('button', { type: 'button', 'class': 'ax-btn ghost', text: c[1] });
          b.addEventListener('click', function () { api('m5673/decision', { id: s.id, choix: c[0] }).then(function (r) { toast(r.message); }, function (err) { toast(err.message, true); }).then(lireSeance); });
          q.appendChild(b);
        });
      navEtat.appendChild(q);
    }
    var pas = (s.pas || []).slice(-8);
    if (pas.length) {
      var ol = el('ol', { 'class': 'c111-nav-pas', 'aria-label': 'Dernières étapes' });
      pas.forEach(function (x) { ol.appendChild(el('li', { translate: 'no', text: (x.t || '') + ' ' + (x.a || '') + (x.d ? ' — ' + x.d : '') })); });
      navEtat.appendChild(ol);
    }
    var fini = ['termine', 'arrete', 'erreur', 'limite_atteinte', 'a_verifier'].indexOf(s.etat) >= 0;
    navBoutons.hidden = fini;
    $$('[data-c111-nav-main="1"]').forEach(function (b) { b.hidden = !!s.main; });
    $$('[data-c111-nav-main="0"]').forEach(function (b) { b.hidden = !s.main; });
  }
  function lireSeance() {
    return api('m56109/navigateur/etat').then(function (d) { rendreSeance(d.seance || null); }).catch(function (err) {
      if (navEtat) navEtat.replaceChildren(el('p', { 'class': 'c111-alerte', text: err.message }));
    });
  }
  function suivreNavigateur() {
    if (!poste || !navEtat) return;
    clearInterval(navMinuteur);
    lireSeance();
    navMinuteur = setInterval(function () { if (!document.hidden) lireSeance(); }, 3000);
  }
  function arreterNavigateur() { clearInterval(navMinuteur); navMinuteur = null; }
  $$('[data-c111-nav-main]').forEach(function (b) {
    b.addEventListener('click', function () {
      if (!seance) return;
      b.disabled = true;
      // appel direct de la route existante : identifiant de la séance et intention exacts (la barrière est acquittée par le serveur)
      api('m56109/navigateur/main', { id: seance.id, prendre: b.getAttribute('data-c111-nav-main') }).then(function (r) { toast(r.message); rendreSeance(r.seance); })
        .catch(function (err) { toast(err.message, true); }).then(function () { b.disabled = false; });
    });
  });
  var navStop = $('[data-c111-nav-arreter]');
  if (navStop) {
    navStop.addEventListener('click', function () {
      if (!seance || !window.confirm(tr('Arrêter la séance du navigateur ?'))) return;
      api('m5673/arreter', { id: seance.id }).then(function (r) { toast(r.message); }, function (err) { toast(err.message, true); }).then(lireSeance);
    });
  }
  if (navLancer) {
    navLancer.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var champ = navLancer.querySelector('textarea');
      api('m5673/lancer', { tache: champ.value, dossier: mid }).then(function (r) { toast(r.message); champ.value = ''; }, function (err) { toast(err.message, true); }).then(lireSeance);
    });
  }

  // ------------------------------------------------------------------ démarrage : même discussion, même document, même page, même brouillon
  var demarre = false;
  function demarrer() {
    if (demarre) return;
    demarre = true;
    brancherDictee();
    majContexte();
    choisirMode(etat.mode === 'navigateur' ? 'navigateur' : 'document');
    choisirZone(etat.zone === 'historique' || etat.zone === 'missions' ? etat.zone : 'discussion');
    poserAgrandi(!!etat.agrandi);
    choisirVue(etat.vue === 'apercu' ? 'apercu' : 'discussion');
    if (fil) choisirFil(fil, 'proprietaire', ''); else { chargerBrouillon(); rafraichirSuivi(); }
    if (docCourant) rouvrirDocument(docCourant, etat.page || 1);
    surApercu();
    ajusterHauteur();
    amenerSurOnglets();
  }
  // après TOUS les scripts différés (la dictée commune, v56106-redaction.js, est chargée par le gabarit, parfois après ce script)
  if (document.readyState === 'complete') demarrer();
  else { document.addEventListener('DOMContentLoaded', demarrer); window.addEventListener('load', demarrer); }
})();
