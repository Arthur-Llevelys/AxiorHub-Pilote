/* AxiorHub 5.6.96 (lot affichage) — volet de l'assistant (U01, I04) et blocs repliés d'Aujourd'hui chargés à l'ouverture (U06).
   Volet flottant (pas la variante montée dans une page) :
   - modal : fond inerte (attribut inert sur les autres enfants de <body>), Tab et Maj+Tab restent dans le volet, focus rendu à l'élément
     qui l'a ouvert et position de la page rétablie à la fermeture ; Échap reste traité par v567.js ;
   - une fenêtre d'action <dialog> déjà ouverte : le volet s'y loge le temps de la conversation (une seule fenêtre modale à la fois) ;
   - Ctrl+Entrée dans l'instruction : un seul envoi (touche maintenue ignorée ; aucun autre raccourci de la page ne reçoit la touche) ;
   - champ Instruction qui grandit jusqu'à sa hauteur maximale (CSS), puis défile ;
   - brouillon gardé pour l'onglet, par compte, dossier et courriel : jamais repris dans un autre dossier ;
   - en-tête : dossier et courriel réellement repris, « Changer le contexte » mène au choix du dossier sans effacer le texte ;
   - clavier logiciel : hauteur utile suivie par visualViewport (variables CSS, aucun style injecté).
   Rien ici n'envoie de demande au modèle ; l'envoi reste celui de v567.js. Tout texte passe par textContent. */
(function () {
  'use strict';
  var doc = document, root = window;
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix'), compte = meta('axiorhub-compte') || 'cabinet';
  var dock = doc.getElementById('ws-ai-dock');

  /* ------------------------------------------------------------ U06 : bloc replié d'Aujourd'hui chargé à l'ouverture (mêmes droits) */
  function chargerPart(zone) {
    if (zone.dataset.a96Etat === 'charge' || zone.dataset.a96Etat === 'encours') return;
    zone.dataset.a96Etat = 'encours';
    var attente = zone.querySelector('.a96-differe');
    root.fetch(prefix + '/api440/m530/part?name=' + encodeURIComponent(zone.dataset.a96Part) + '&prefix=' + encodeURIComponent(prefix), { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.json().then(function (v) { if (!r.ok || v.error) throw new Error(v.message || v.error || ('HTTP ' + r.status)); return v; }); })
      .then(function (v) {
        zone.innerHTML = v.html || '';                 // HTML échappé par le serveur (mêmes fonctions que la page complète)
        zone.dataset.a96Etat = 'charge';
        if (!zone.textContent.trim()) { var vide = doc.createElement('p'); vide.className = 'a96-differe'; vide.textContent = 'Rien à afficher pour l’instant.'; zone.append(vide); }
      })
      .catch(function (e) {
        zone.dataset.a96Etat = '';
        if (attente) { attente.dataset.etat = 'panne'; attente.textContent = 'Chargement impossible pour l’instant (' + e.message + '). Repliez puis dépliez pour réessayer.'; }
      });
  }
  doc.addEventListener('toggle', function (ev) {
    var pli = ev.target;
    if (!(pli instanceof HTMLDetailsElement) || !pli.open) return;
    Array.prototype.forEach.call(pli.querySelectorAll('[data-a96-part]'), chargerPart);
  }, true);
  Array.prototype.forEach.call(doc.querySelectorAll('details[open] [data-a96-part]'), chargerPart);

  if (!dock) return;
  var question = dock.querySelector('#ws-ai-question'), matter = dock.querySelector('#ws-ai-matter'), send = dock.querySelector('#ws-ai-send');
  var portee = dock.querySelector('#ws-ai-portee-dossier'), changer = dock.querySelector('#ws-ai-changer'), recherche = dock.querySelector('#ws-ai-matter-search');
  var corps = dock.querySelector('.ws-ai-body');
  var q = new URLSearchParams(root.location.search);
  function integre() { return dock.classList.contains('ws-ai-dock--embedded'); }

  /* ------------------------------------------------------------ Ctrl+Entrée : un seul envoi */
  dock.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Enter' || !(ev.ctrlKey || ev.metaKey) || ev.target !== question) return;
    ev.preventDefault(); ev.stopPropagation();          // ni v567.js ni le raccourci d'enregistrement d'une fiche (v490.js) ne le reçoivent aussi
    if (!ev.repeat && send && !send.disabled) send.click();
  });

  /* ------------------------------------------------------------ champ qui grandit */
  function ajuster() {
    if (!question) return;
    question.style.height = 'auto';                   // CSSOM : permis par la CSP (aucun attribut style écrit dans le HTML)
    question.style.height = question.scrollHeight + 2 + 'px';
  }
  if (question) question.addEventListener('input', ajuster);

  /* ------------------------------------------------------------ brouillon : même compte, même dossier, même courriel */
  var cleCourriel = /^[0-9a-f]{64}$/.test(q.get('key') || '') ? q.get('key').slice(0, 16) : '';
  function cle(dossier) { return 'ax96-brouillon:' + prefix + ':' + compte + ':' + (dossier || '') + ':' + cleCourriel; }
  function lire(k) { try { return root.sessionStorage.getItem(k) || ''; } catch (e) { return ''; } }
  function ecrire(k, v) { try { if (v) root.sessionStorage.setItem(k, v.slice(0, 12000)); else root.sessionStorage.removeItem(k); } catch (e) { /* navigation privée */ } }
  var dossierCourant = matter ? matter.value : '';
  if (question && !question.value) { question.value = lire(cle(dossierCourant)); if (question.value) ajuster(); }
  if (question) question.addEventListener('input', function () { ecrire(cle(dossierCourant), question.value); });
  if (send) send.addEventListener('click', function () { ecrire(cle(dossierCourant), ''); }, true);   // demande partie : plus de brouillon
  function libelleDossier() {
    var o = matter && matter.options[matter.selectedIndex];
    return o && o.value ? 'Dossier : ' + o.textContent : 'Aucun dossier';
  }
  if (matter) matter.addEventListener('change', function () {
    // Le texte en cours suit le choix de la personne ; le brouillon de l'ancien dossier n'est jamais repris ailleurs.
    ecrire(cle(dossierCourant), '');
    dossierCourant = matter.value;
    if (question && question.value) ecrire(cle(dossierCourant), question.value);
    if (portee) portee.textContent = general && general.getAttribute('aria-pressed') === 'true' ? portee.textContent : libelleDossier();
  });
  /* Mode « Général » (R01) : question sans dossier, sans pièce ni historique ; l'en-tête dit ce qui sera réellement repris */
  var general = dock.querySelector('#ws-ai-general'), porteeTexte = dock.querySelector('#ws-ai-portee');
  if (general) general.addEventListener('click', function () {
    var actif = general.getAttribute('aria-pressed') !== 'true';
    general.setAttribute('aria-pressed', actif ? 'true' : 'false');
    dock.toggleAttribute('data-a96-general', actif);
    if (portee) portee.textContent = actif ? 'Question générale : ni dossier, ni pièce, ni historique' : libelleDossier();
  });
  if (changer && recherche) changer.addEventListener('click', function () { dock.dataset.a96Contexte = '1'; recherche.focus({ preventScroll: true }); recherche.select(); });

  /* ------------------------------------------------------------ volet modal (variante flottante) */
  var ouvreur = null, defilement = null, inertes = [], hote = null, place = null, retour = null, position = null;
  defilement = [root.scrollX, root.scrollY];
  doc.addEventListener('focusin', function (ev) { if (dock.hidden && !dock.contains(ev.target)) ouvreur = ev.target; }, true);
  doc.addEventListener('pointerdown', function (ev) {
    if (!dock.hidden || !ev.target.closest) return;
    var b = ev.target.closest('button, a, [tabindex]');
    if (b && !dock.contains(b)) ouvreur = b;
  }, true);
  root.addEventListener('scroll', function () { if (dock.hidden) defilement = [root.scrollX, root.scrollY]; }, { passive: true });
  function focusables() {
    return Array.prototype.filter.call(dock.querySelectorAll('button, [href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])'), function (n) {
      return !n.disabled && !n.closest('[hidden]') && n.getClientRects().length && !(n.closest('details:not([open])') && n.tagName !== 'SUMMARY' && !n.closest('summary'));
    });
  }
  dock.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && hote) ev.preventDefault();   // Échap ferme le volet (v567.js), pas la fenêtre d'action qui l'accueille
    if (ev.key !== 'Tab' || dock.hidden || integre()) return;
    var liste = focusables();
    if (!liste.length) return;
    var premier = liste[0], dernier = liste[liste.length - 1];
    if (ev.shiftKey && doc.activeElement === premier) { ev.preventDefault(); dernier.focus(); }
    else if (!ev.shiftKey && doc.activeElement === dernier) { ev.preventDefault(); premier.focus(); }
  });
  function rendreInerte() {
    inertes = [];
    Array.prototype.forEach.call(doc.body.children, function (n) {
      if (n === dock || n.contains(dock) || n.classList.contains('s5636-backdrop') || /^(SCRIPT|TEMPLATE|STYLE|LINK)$/.test(n.tagName) || n.inert) return;
      n.inert = true; inertes.push(n);
    });
  }
  function rendreActif() { inertes.forEach(function (n) { n.inert = false; }); inertes = []; }
  function hauteurUtile() {
    var vv = root.visualViewport;
    if (!vv || dock.hidden || integre()) { dock.style.removeProperty('--ws-ai-vh'); dock.style.removeProperty('--ws-ai-haut'); return; }
    // Clavier logiciel ouvert : la zone visible rétrécit ; le volet suit, l'envoi reste au-dessus du clavier.
    if (vv.height < root.innerHeight - 1 || vv.offsetTop > 0) {
      dock.style.setProperty('--ws-ai-vh', Math.round(vv.height) + 'px');
      dock.style.setProperty('--ws-ai-haut', Math.round(vv.offsetTop) + 'px');
    } else { dock.style.removeProperty('--ws-ai-vh'); dock.style.removeProperty('--ws-ai-haut'); }
  }
  if (root.visualViewport) { root.visualViewport.addEventListener('resize', hauteurUtile); root.visualViewport.addEventListener('scroll', hauteurUtile); }
  function ouvert() {
    if (integre()) return;
    retour = ouvreur; position = defilement;          // figés à l'ouverture : le focus rendu au lanceur par v567.js ne les remplace pas
    var modale = null;
    try { modale = doc.querySelector('dialog[open]:modal'); } catch (e) { modale = null; }
    if (modale && !modale.contains(dock)) {           // fenêtre d'action ouverte : le volet s'y loge (une seule fenêtre modale)
      hote = modale; place = [dock.parentNode, dock.nextSibling]; modale.append(dock); dock.dataset.a96Hote = '1';
    } else if (!modale) rendreInerte();
    if (question) { ajuster(); if (dock.contains(doc.activeElement) === false) question.focus({ preventScroll: true }); }
    if (portee) portee.textContent = libelleDossier();
    hauteurUtile();
  }
  function ferme() {
    rendreActif();
    delete dock.dataset.a96Contexte;
    if (hote && place) { place[0].insertBefore(dock, place[1]); delete dock.dataset.a96Hote; }
    hote = null; place = null;
    hauteurUtile();
    var cible = retour && retour.isConnected && !retour.closest('[inert]') ? retour : doc.getElementById('ws-ai-launcher');
    if (position) root.scrollTo(position[0], position[1]);
    if (cible && cible.focus) cible.focus({ preventScroll: true });
  }
  var etait = !dock.hidden;
  new MutationObserver(function () {
    var maintenant = !dock.hidden;
    if (maintenant === etait) return;
    etait = maintenant;
    if (maintenant) ouvert(); else ferme();
  }).observe(dock, { attributes: true, attributeFilter: ['hidden'] });
  if (etait) ouvert();
  if (corps) corps.setAttribute('aria-label', 'Fil de la conversation et résultats');
})();
