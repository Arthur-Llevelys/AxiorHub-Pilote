/* AxiorHub 5.6.96 (lot accueil) — Aujourd'hui : salutation et heure en direct (F01, F02, I01), carte météo (F04), réglage de la météo (F03),
   actions ouvertes en fenêtre flottante (I02).
   - Heure : Intl.DateTimeFormat dans le fuseau du compte (attribut data-fuseau), mêmes seuils que le serveur (5 h, 12 h, 18 h) ; mise à jour au
     changement de minute et au retour sur l'onglet, sans recharger la page ni appeler l'IA ; pas d'aria-live (aucune annonce à chaque minute).
   - Météo : chargée après l'affichage par la route légère (réponse conditionnelle ETag/304, revalidée par le navigateur) ; rien n'est chargé si la carte
     est absente (météo désactivée ou masquée par la personne). Icônes dessinées ici en SVG, aucun média.
   - Fenêtre flottante : <dialog> natif unique ; le détail vient de /api440/m5689/accueil/action (HTML construit et échappé par le serveur, avec les
     mêmes formulaires que la page complète) ; jamais le HTML d'un courriel ni une iframe générique. Fermer et Échap passent par le même contrôle de
     saisie non enregistrée ; la fermeture annule la lecture en attente, jamais une tâche confiée. Retour : focus et position sur la même ligne.
   Les textes viennent du catalogue (script JSON data-f96-messages) ; tout le reste passe par textContent. */
(function (racine) {
  'use strict';

  /* ------------------------------------------------------------------ fonctions pures (testées sous Node : tests/accueil5696.cjs) */
  function formule(h) { return h >= 5 && h < 12 ? 'matin' : (h >= 12 && h < 18 ? 'apres_midi' : 'soir'); }
  function remplir(gabarit, valeurs) {
    return String(gabarit || '').replace(/\{(\w+)\}/g, function (m, k) { return Object.prototype.hasOwnProperty.call(valeurs, k) ? String(valeurs[k]) : m; });
  }
  /* Heure, minute et jour dans le fuseau donné (cycle de 24 heures) : Intl seul, sans calcul de décalage à la main. */
  function heureLocale(instant, fuseau) {
    var parts = {};
    new Intl.DateTimeFormat('en-GB', { timeZone: fuseau, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      .formatToParts(instant).forEach(function (p) { parts[p.type] = p.value; });
    var h = parseInt(parts.hour, 10) % 24;
    return { h: h, mm: parts.minute, jour: parts.year + '-' + parts.month + '-' + parts.day, datetime: parts.year + '-' + parts.month + '-' + parts.day + 'T' + (h < 10 ? '0' : '') + h + ':' + parts.minute };
  }
  function texteBandeau(instant, reglage, messages) {
    var l = heureLocale(instant, reglage.fuseau);
    var mot = messages['formule.' + formule(l.h)] || '';
    var salut = reglage.prenom ? remplir(messages.salut, { formule: mot, prenom: reglage.prenom }) : mot;
    var date = new Intl.DateTimeFormat(reglage.lang || 'fr', { timeZone: reglage.fuseau, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).format(instant);
    return { texte: remplir(messages.phrase, { salut: salut, date: date, heure: remplir(messages.heure, { h: l.h, mm: l.mm }) }), datetime: l.datetime, jour: l.jour, formule: formule(l.h) };
  }
  /* Délai jusqu'au prochain changement de minute (+ 50 ms de marge), borné : une horloge de veille ne dérive jamais d'une minute. */
  function prochainDelai(ms) { return 60000 - (ms % 60000) + 50; }

  var exportes = { formule: formule, remplir: remplir, heureLocale: heureLocale, texteBandeau: texteBandeau, prochainDelai: prochainDelai };
  if (typeof module === 'object' && module && module.exports) { module.exports = exportes; }
  var doc = racine.document;
  if (!doc || !doc.querySelector) return;

  /* ------------------------------------------------------------------ outils */
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix') || '', csrf = meta('axiorhub-csrf');
  var M = {};
  Array.prototype.forEach.call(doc.querySelectorAll('script[data-f96-messages]'), function (s) {
    try { var d = JSON.parse(s.textContent || '{}'); Object.keys(d).forEach(function (k) { M[k] = d[k]; }); } catch (e) { /* catalogue illisible : textes du serveur conservés */ }
  });
  function m(cle, valeurs) { return remplir(M[cle] || '', valeurs || {}); }
  function poster(url, corps, signal) {
    return racine.fetch(url, { method: 'POST', credentials: 'same-origin', signal: signal, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) })
      .then(lireJson);
  }
  function lireJson(r) {
    return r.json().catch(function () { return { error: 'reponse_invalide', message: m('erreur.refus') }; }).then(function (d) {
      if (!r.ok || (d && d.error)) { var e = new Error((d && d.message) || m('erreur.refus')); e.code = (d && d.error) || ('http_' + r.status); e.statut = r.status; throw e; }
      return d;
    });
  }
  function annoncer(zone, texte, erreur) { if (!zone) return; zone.textContent = texte || ''; zone.setAttribute('data-erreur', erreur ? '1' : '0'); }

  /* ------------------------------------------------------------------ salutation et heure (F01, F02) */
  var horloge = doc.querySelector('[data-f96-horloge]');
  var minuterie = null;
  function majHorloge() {
    if (!horloge) return;
    var t = horloge.querySelector('time');
    try {
      var r = texteBandeau(new Date(), { fuseau: horloge.getAttribute('data-fuseau') || 'Europe/Paris', lang: horloge.getAttribute('data-lang') || 'fr', prenom: horloge.getAttribute('data-prenom') || '' }, M);
      if (r.texte && M.phrase) { t.textContent = r.texte; t.setAttribute('datetime', r.datetime); }
    } catch (e) { /* fuseau inconnu du navigateur : le texte du serveur reste */ }
    if (minuterie) racine.clearTimeout(minuterie);
    minuterie = racine.setTimeout(majHorloge, prochainDelai(Date.now()));
  }
  if (horloge) {
    majHorloge();
    doc.addEventListener('visibilitychange', function () { if (!doc.hidden) majHorloge(); });
  }

  /* Options d'accueil : prénom affiché et carte météo, pour la personne connectée. */
  var options = doc.querySelector('form[data-f96-options]');
  if (options) {
    options.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var champ = options.querySelector('[name=prenom]'), visible = options.querySelector('[name=meteo_visible]'), zone = options.querySelector('[data-f96-message]');
      var corps = { prenom: champ.value };
      if (visible) corps.meteo_visible = visible.checked;
      champ.removeAttribute('aria-invalid');
      poster(prefix + '/api440/m5689/accueil/options', corps).then(function (d) {
        annoncer(zone, d.message || '');
        if (horloge) { horloge.setAttribute('data-prenom', d.prenom || ''); majHorloge(); }
        champ.defaultValue = champ.value = d.prenom || '';
        if (visible && visible.checked === !!d.meteo_masquee) racine.setTimeout(function () { racine.location.reload(); }, 600);
      }).catch(function (e) {
        annoncer(zone, e.message, true);
        if (e.code === 'prenom_invalide') { champ.setAttribute('aria-invalid', 'true'); champ.focus(); }
      });
    });
  }

  /* ------------------------------------------------------------------ carte météo (F04) */
  var SVG = 'http://www.w3.org/2000/svg';
  var NUAGE = 'M8 18h11a4 4 0 0 0 0-8 5.5 5.5 0 0 0-10.6 1.5A3.3 3.3 0 0 0 8 18z';
  var DESSINS = {
    soleil: [['circle', { cx: 12, cy: 12, r: 4.5 }], ['path', { d: 'M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1' }]],
    eclaircies: [['circle', { cx: 8, cy: 8, r: 3.2 }], ['path', { d: 'M8 1.5v1.8M1.5 8h1.8M3.4 3.4l1.3 1.3M12.6 3.4l-1.3 1.3' }], ['path', { d: 'M9 20h10a3.6 3.6 0 0 0 0-7.2 5 5 0 0 0-9.6 1.4A2.9 2.9 0 0 0 9 20z' }]],
    nuages: [['path', { d: NUAGE }]],
    brouillard: [['path', { d: 'M4 9h16M2 13h20M4 17h16' }]],
    bruine: [['path', { d: NUAGE }], ['path', { d: 'M9 21v.5M13 21v.5M17 21v.5' }]],
    pluie: [['path', { d: NUAGE }], ['path', { d: 'M9 20l-1 3M13 20l-1 3M17 20l-1 3' }]],
    averses: [['path', { d: NUAGE }], ['path', { d: 'M9 20l-1.5 3M14 20l-1.5 3' }], ['circle', { cx: 19, cy: 4, r: 2 }]],
    neige: [['path', { d: NUAGE }], ['path', { d: 'M9 20.5h.01M13 22h.01M17 20.5h.01' }]],
    orage: [['path', { d: NUAGE }], ['path', { d: 'M13 18l-2 3h3l-2 3' }]],
    inconnu: [['circle', { cx: 12, cy: 12, r: 8 }], ['path', { d: 'M10 9.5a2 2 0 1 1 2.5 2c-.4.2-.5.6-.5 1V14M12 17h.01' }]]
  };
  function icone(nom) {
    var s = doc.createElementNS(SVG, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('width', '28'); s.setAttribute('height', '28'); s.setAttribute('aria-hidden', 'true'); s.setAttribute('focusable', 'false');
    s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '1.6'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
    s.setAttribute('class', 'f96-icone'); s.setAttribute('data-icone', nom);
    (DESSINS[nom] || DESSINS.inconnu).forEach(function (f) {
      var el = doc.createElementNS(SVG, f[0]);
      Object.keys(f[1]).forEach(function (k) { el.setAttribute(k, f[1][k]); });
      s.appendChild(el);
    });
    return s;
  }
  function el(nom, classe, texte) { var x = doc.createElement(nom); if (classe) x.className = classe; if (texte !== undefined) x.textContent = texte; return x; }
  function minMax(j) { return j.min === null || j.max === null || j.min === undefined ? m('carte.sans_valeur') : m('carte.min_max', { min: j.min, max: j.max }); }
  function nomJour(iso, lang, fuseau, premier) {
    if (premier) return m('carte.aujourdhui');
    try { return new Intl.DateTimeFormat(lang, { weekday: 'long', day: 'numeric', timeZone: 'UTC' }).format(new Date(iso + 'T12:00:00Z')); } catch (e) { return iso; }
  }
  var carte = doc.querySelector('[data-f96-meteo]');
  function rendreMeteo(d) {
    var jour = carte.querySelector('[data-f96-jour]'), liste = carte.querySelector('[data-f96-jours]');
    var consulte = carte.querySelector('[data-f96-consulte]'), ancien = carte.querySelector('[data-f96-ancien]');
    var lang = (horloge && horloge.getAttribute('data-lang')) || 'fr';
    carte.setAttribute('data-statut', d.statut || 'indisponible');
    while (jour.firstChild) jour.removeChild(jour.firstChild);
    while (liste.firstChild) liste.removeChild(liste.firstChild);
    if ((d.statut === 'ok' || d.statut === 'perime') && d.jours && d.jours.length) {
      var j0 = d.jours[0];
      jour.appendChild(icone(j0.icone));
      jour.appendChild(el('span', 'f96-meteo-libelle', j0.libelle));
      jour.appendChild(el('span', 'f96-meteo-temp', minMax(j0)));
      d.jours.slice(1).forEach(function (j) {
        var li = el('li', 'f96-jour');
        li.appendChild(el('span', 'f96-jour-nom', nomJour(j.date, lang, d.fuseau, false)));
        li.appendChild(icone(j.icone));
        li.appendChild(el('span', 'f96-jour-libelle', j.libelle));
        li.appendChild(el('span', 'f96-jour-temp', minMax(j)));
        liste.appendChild(li);
      });
      var h = d.consulte_le ? d.consulte_le.slice(11, 16) : '';
      consulte.textContent = h ? m('carte.consulte', { heure: h }) : '';
      ancien.hidden = d.statut !== 'perime';
    } else {
      jour.appendChild(el('span', 'f96-meteo-etat', d.statut === 'refuse' ? m('carte.refuse') : d.statut === 'desactive' ? m('carte.desactive') : m('carte.indisponible')));
      consulte.textContent = '';
      ancien.hidden = true;
    }
  }
  function chargerMeteo() {
    if (!carte || doc.hidden) return;
    racine.fetch(carte.getAttribute('data-url'), { credentials: 'same-origin', cache: 'no-cache' }).then(lireJson).then(function (d) {
      if (d.statut === 'masque') { carte.remove(); return; }
      rendreMeteo(d);
    }).catch(function () { rendreMeteo({ statut: 'indisponible' }); });
  }
  if (carte) {
    /* Après le premier affichage : la page est utilisable avant la météo. */
    if (doc.readyState === 'complete') racine.setTimeout(chargerMeteo, 0); else racine.addEventListener('load', function () { racine.setTimeout(chargerMeteo, 0); });
    racine.setInterval(chargerMeteo, 30 * 60 * 1000);
    var masquer = carte.querySelector('[data-f96-masquer]');
    if (masquer) masquer.addEventListener('click', function () {
      poster(prefix + '/api440/m5689/accueil/options', { meteo_visible: false }).then(function () {
        var zone = doc.querySelector('form[data-f96-options] [data-f96-message]');
        carte.remove(); carte = null;
        annoncer(zone, m('carte.masquee'));
        var titre = doc.querySelector('.f96-identite h1'); if (titre) { titre.setAttribute('tabindex', '-1'); titre.focus({ preventScroll: true }); }
      }).catch(function (e) { masquer.setAttribute('title', e.message); });
    });
  }

  /* ------------------------------------------------------------------ réglage de la météo (F03, administrateur) */
  Array.prototype.forEach.call(doc.querySelectorAll('[data-f96-reglage]'), function (section) {
    var base = section.getAttribute('data-api');
    function revision() { var r = section.querySelector('input[name=revision]'); return r ? r.value : ''; }
    function recharger() { racine.setTimeout(function () { racine.location.reload(); }, 900); }
    Array.prototype.forEach.call(section.querySelectorAll('form[data-f96-etape]'), function (form) {
      var etape = form.getAttribute('data-f96-etape'), zone = form.querySelector('[data-f96-message]');
      var lieux = [];
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var bouton = form.querySelector('button[type=submit]');
        if (bouton && bouton.disabled) return;
        var corps = { revision: revision() }, route = etape;
        if (etape === 'tester') corps.cle = form.querySelector('[name=cle]').value.trim();
        if (etape === 'activer') corps.actif = form.querySelector('[name=actif]').checked;
        if (etape === 'lieux') { corps = { ville: form.querySelector('[name=ville]').value.trim(), pays: form.querySelector('[name=pays]').value.trim() }; }
        if (etape === 'effacer' && !racine.confirm(form.getAttribute('data-confirmer') || '')) return;
        if (bouton) bouton.disabled = true;
        annoncer(zone, '');
        poster(base + route, corps).then(function (d) {
          if (etape === 'tester') form.querySelector('[name=cle]').value = '';
          if (etape === 'lieux') { lieux = d.lieux || []; montrerLieux(form, lieux, zone); return; }
          annoncer(zone, d.message || ''); recharger();
        }).catch(function (e) {
          annoncer(zone, e.message, true);
          var champ = form.querySelector(etape === 'tester' ? '[name=cle]' : etape === 'lieux' ? '[name=ville]' : 'input:not([type=hidden])');
          if (champ) { champ.setAttribute('aria-invalid', 'true'); champ.focus(); }
        }).then(function () { if (bouton) bouton.disabled = false; });
      });
      var choisir = form.querySelector('[data-f96-choisir]');
      if (choisir) choisir.addEventListener('click', function () {
        var coche = form.querySelector('input[name=f96-lieu]:checked');
        if (!coche) { annoncer(zone, m('reglage.homonymes'), true); return; }
        choisir.disabled = true;
        poster(base + 'lieu', { lieu: lieux[parseInt(coche.value, 10)], revision: revision() }).then(function (d) { annoncer(zone, d.message || ''); recharger(); })
          .catch(function (e) { annoncer(zone, e.message, true); }).then(function () { choisir.disabled = false; });
      });
    });
    function montrerLieux(form, lieux, zone) {
      var cadre = form.querySelector('[data-f96-homonymes]'), liste = form.querySelector('[data-f96-liste]');
      while (liste.firstChild) liste.removeChild(liste.firstChild);
      if (!lieux.length) { cadre.hidden = true; annoncer(zone, m('reglage.aucun_lieu'), true); return; }
      lieux.forEach(function (l, i) {
        var label = el('label', 'f96-case'), radio = el('input');
        radio.type = 'radio'; radio.name = 'f96-lieu'; radio.value = String(i); if (lieux.length === 1) radio.checked = true;
        label.appendChild(radio);
        label.appendChild(doc.createTextNode(' ' + [l.ville, l.region, l.pays].filter(Boolean).join(', ') + ' (' + l.latitude + ', ' + l.longitude + ')'));
        liste.appendChild(label);
      });
      cadre.hidden = false;
      annoncer(zone, m('reglage.lieux_trouves'));
      liste.querySelector('input').focus();
    }
  });

  /* ------------------------------------------------------------------ fenêtre flottante (I02) */
  var fenetre = null, demande = null, retour = null, actionFaite = false;
  function bouton(classe, texte) { var b = el('button', 'ax-btn ghost ' + classe, texte); b.type = 'button'; return b; }
  function construire() {
    var d = el('dialog', 'f96-fenetre');
    d.setAttribute('aria-labelledby', 'f96-fenetre-titre');
    var barre = el('div', 'f96-fenetre-barre'), tete = el('div', 'f96-fenetre-tete');
    var titre = el('h2', 'f96-fenetre-titre'); titre.id = 'f96-fenetre-titre';
    tete.appendChild(titre); tete.appendChild(el('p', 'f96-fenetre-dossier'));
    var cmd = el('div', 'f96-fenetre-commandes');
    var page = el('a', 'ax-btn ghost f96-fenetre-page', m('fenetre.page_complete'));
    var taille = bouton('f96-fenetre-taille', m('fenetre.plein_ecran')); taille.setAttribute('aria-pressed', 'false');
    var fermer = bouton('f96-fenetre-fermer', m('fenetre.fermer'));
    cmd.appendChild(page); cmd.appendChild(taille); cmd.appendChild(fermer);
    barre.appendChild(tete); barre.appendChild(cmd);
    var corps = el('div', 'f96-fenetre-corps'); corps.tabIndex = -1;
    var etat = el('p', 'f96-fenetre-etat'); etat.setAttribute('role', 'status');
    corps.appendChild(etat); corps.appendChild(el('div', 'f96-fenetre-contenu'));
    var quitter = el('div', 'f96-quitter'); quitter.hidden = true; quitter.setAttribute('role', 'group'); quitter.setAttribute('aria-labelledby', 'f96-quitter-titre');
    var qt = el('p', 'f96-quitter-titre', m('fenetre.quitter_titre')); qt.id = 'f96-quitter-titre';
    quitter.appendChild(qt); quitter.appendChild(el('p', '', m('fenetre.quitter_texte')));
    var sauver = bouton('f96-q-sauver', m('fenetre.enregistrer')), abandon = bouton('f96-q-abandon', m('fenetre.abandonner')), rester = bouton('f96-q-rester', m('fenetre.rester'));
    quitter.appendChild(sauver); quitter.appendChild(abandon); quitter.appendChild(rester);
    d.appendChild(barre); d.appendChild(quitter); d.appendChild(corps);
    (doc.getElementById('ws-main') || doc.body).appendChild(d);
    taille.addEventListener('click', function () { pleinEcran(!d.classList.contains('f96-plein')); });
    fermer.addEventListener('click', function () { demanderFermeture(); });
    d.addEventListener('cancel', function (ev) { ev.preventDefault(); demanderFermeture(); });   /* Échap : même contrôle que Fermer */
    abandon.addEventListener('click', function () { fermerVraiment(); });
    rester.addEventListener('click', function () { quitter.hidden = true; var c = premierModifie(); if (c) c.focus(); });
    sauver.addEventListener('click', function () {
      var champ = premierModifie(), form = champ && champ.form;
      quitter.hidden = true;
      if (form && form.requestSubmit) form.requestSubmit(); else if (form) form.dispatchEvent(new racine.Event('submit', { bubbles: true, cancelable: true }));
    });
    /* Une action envoyée depuis la fenêtre : la ligne et les compteurs seront actualisés à la fermeture (pas de rechargement d'Aujourd'hui). */
    d.addEventListener('submit', function (ev) { if (ev.target && ev.target.closest && ev.target.closest('.f96-fenetre-contenu')) { actionFaite = true; racine.setTimeout(marquerPropre, 1500); } }, true);
    return d;
  }
  function pleinEcran(oui) {
    fenetre.classList.toggle('f96-plein', !!oui);
    var b = fenetre.querySelector('.f96-fenetre-taille');
    b.setAttribute('aria-pressed', oui ? 'true' : 'false');
    b.textContent = oui ? m('fenetre.taille_normale') : m('fenetre.plein_ecran');
  }
  function champsSaisie() { return fenetre ? fenetre.querySelectorAll('.f96-fenetre-contenu form textarea, .f96-fenetre-contenu form input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=search])') : []; }
  function premierModifie() {
    return Array.prototype.filter.call(champsSaisie(), function (f) { return !f.readOnly && !f.disabled && f.value !== f.defaultValue; })[0] || null;
  }
  function marquerPropre() { Array.prototype.forEach.call(champsSaisie(), function (f) { f.defaultValue = f.value; }); }
  function demanderFermeture() {
    if (premierModifie()) { var q = fenetre.querySelector('.f96-quitter'); q.hidden = false; q.querySelector('.f96-q-rester').focus(); return; }
    fermerVraiment();
  }
  function fermerVraiment() {
    if (demande) { demande.abort(); demande = null; }        /* la lecture du détail en attente est annulée ; une tâche confiée continue */
    fenetre.querySelector('.f96-quitter').hidden = true;
    if (fenetre.open) fenetre.close();
  }
  function ouvrir(lien) {
    if (!fenetre) {
      fenetre = construire();
      fenetre.addEventListener('close', function () { restaurer(); });
    }
    if (demande) demande.abort();
    demande = new racine.AbortController();
    actionFaite = false;
    var ligne = lien.closest('[data-f96-ligne]');
    retour = { lien: lien, y: racine.scrollY, ligne: ligne ? ligne.getAttribute('data-f96-ligne') : '', zone: ligne ? ligne.parentNode : null };
    fenetre.querySelector('.f96-fenetre-titre').textContent = lien.getAttribute('data-f96-titre') || lien.textContent;
    var dossier = lien.getAttribute('data-f96-dossier');
    fenetre.querySelector('.f96-fenetre-dossier').textContent = dossier ? m('fenetre.dossier', { dossier: dossier }) : '';
    var page = fenetre.querySelector('.f96-fenetre-page'); page.href = lien.href;
    var contenu = fenetre.querySelector('.f96-fenetre-contenu'), etat = fenetre.querySelector('.f96-fenetre-etat');
    while (contenu.firstChild) contenu.removeChild(contenu.firstChild);
    etat.textContent = m('fenetre.chargement'); etat.removeAttribute('data-erreur');
    fenetre.setAttribute('aria-busy', 'true');
    pleinEcran(racine.matchMedia && racine.matchMedia('(max-width: 40rem)').matches);   /* mobile : plein écran d'emblée */
    fenetre.showModal();
    fenetre.querySelector('.f96-fenetre-fermer').focus();
    var url = prefix + '/api440/m5689/accueil/action?nature=' + encodeURIComponent(lien.getAttribute('data-f96-action')) + '&cle=' + encodeURIComponent(lien.getAttribute('data-f96-cle'));
    var signal = demande.signal;
    racine.fetch(url, { credentials: 'same-origin', signal: signal }).then(lireJson).then(function (d) {
      if (signal.aborted) return;
      fenetre.querySelector('.f96-fenetre-titre').textContent = d.titre || '';
      if (d.page && !lien.getAttribute('href')) page.href = d.page;     /* le lien d'origine garde son retour contextuel (return_to) */
      contenu.innerHTML = d.html;      /* HTML construit et échappé par le serveur (mêmes générateurs que la page complète) */
      /* Pas de rechargement d'Aujourd'hui après une action dans la fenêtre : la ligne et les compteurs sont actualisés à la fermeture. */
      Array.prototype.forEach.call(contenu.querySelectorAll('form[data-reload]'), function (f) { f.removeAttribute('data-reload'); });
      etat.textContent = '';
      fenetre.removeAttribute('aria-busy');
      fenetre.querySelector('.f96-fenetre-corps').focus({ preventScroll: true });
    }).catch(function (e) {
      if (e && e.name === 'AbortError') return;
      fenetre.removeAttribute('aria-busy');
      etat.setAttribute('data-erreur', '1');
      etat.textContent = m('fenetre.erreur', { message: e.message });
    });
  }
  function restaurer() {
    var r = retour; retour = null;
    if (demande) { demande.abort(); demande = null; }
    if (!r) return;
    racine.scrollTo(0, r.y);
    if (r.lien && r.lien.isConnected) r.lien.focus({ preventScroll: true });
    if (actionFaite) actualiser(r);
  }
  /* Après une action : seule la zone des lignes et les compteurs d'« Actuellement » sont relus ; si la ligne a disparu, le focus va à la suivante. */
  function actualiser(r) {
    var section = doc.getElementById('f96-decider'), zone = doc.getElementById('f96-lignes-zone');
    var annonce = section && section.querySelector('[data-f96-annonce]');
    var compteurs = doc.querySelector('[data-ax95="actualiser"]');
    if (compteurs) compteurs.click();
    if (!section || !zone) return;
    var avant = Array.prototype.map.call(zone.querySelectorAll('[data-f96-ligne]'), function (li) { return li.getAttribute('data-f96-ligne'); });
    racine.fetch(section.getAttribute('data-url'), { credentials: 'same-origin' }).then(lireJson).then(function (d) {
      if (typeof d.html !== 'string') return;
      zone.innerHTML = d.html;      /* HTML construit et échappé par le serveur (accueil_web5696.lignes_html) */
      var cles = Array.prototype.map.call(zone.querySelectorAll('[data-f96-ligne]'), function (li) { return li.getAttribute('data-f96-ligne'); });
      if (r.ligne && cles.indexOf(r.ligne) < 0) {
        var i = avant.indexOf(r.ligne), suivante = null;
        for (var k = Math.max(i, 0); k < avant.length && !suivante; k++) if (cles.indexOf(avant[k]) >= 0) suivante = avant[k];
        var cible = suivante ? zone.querySelector('[data-f96-ligne="' + suivante.replace(/"/g, '') + '"] a') : section.querySelector('h2');
        if (cible) { if (!suivante) cible.setAttribute('tabindex', '-1'); cible.focus({ preventScroll: true }); }
        annoncer(annonce, m('rang.retiree'));
      } else {
        var meme = r.ligne && zone.querySelector('[data-f96-ligne="' + r.ligne.replace(/"/g, '') + '"] a');
        if (meme) meme.focus({ preventScroll: true });
        annoncer(annonce, m('rang.actualisee'));
      }
    }).catch(function () { /* la page reste utilisable ; la liste sera relue au prochain affichage */ });
  }
  doc.addEventListener('click', function (ev) {
    var lien = ev.target.closest && ev.target.closest('a[data-f96-action][data-f96-cle]');
    if (!lien || ev.defaultPrevented || ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || !racine.HTMLDialogElement) return;
    if (['reponse', 'echeance', 'document'].indexOf(lien.getAttribute('data-f96-action')) < 0) return;
    ev.preventDefault();
    ouvrir(lien);
  });
})(typeof window !== 'undefined' ? window : globalThis);
