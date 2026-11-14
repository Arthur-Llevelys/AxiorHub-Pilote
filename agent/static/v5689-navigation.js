/* AxiorHub 5.6.89 — navigation commune : retour contextuel, panneaux, onglets de filtre, actualisation discrète d'Aujourd'hui.
   L'adresse de retour est revalidée par le serveur (ui_navigation5689) ; ici, seuls le chemin et les paramètres déjà visibles en partent. Défilement, focus et filtres locaux restent dans
   sessionStorage, pour l'onglet en cours, et ne quittent jamais le poste. Aucun HTML venant de l'utilisateur n'est injecté. */
(function (root) {
  'use strict';
  var doc = root.document;
  var CLE = 'ui89-etat:', RESTAURER = 'ui89-restaurer', PRECEDENT = 'ui96-precedent';
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix') || '', csrf = meta('axiorhub-csrf');
  function lire(k) { try { return root.sessionStorage.getItem(k); } catch (e) { return null; } }
  function ecrire(k, v) { try { root.sessionStorage.setItem(k, v); } catch (e) { /* navigation privée : retour sans position */ } }
  function oublier(k) { try { root.sessionStorage.removeItem(k); } catch (e) { /* idem */ } }
  function ici() { return root.location.pathname + root.location.search; }
  function sansRetour(url) { url.searchParams.delete('return_to'); url.searchParams.delete('retour'); return url.pathname + (url.search || ''); }

  /* Détails : « /mail| », « /facturation|document » : chemin et paramètre qui distingue le détail de la liste. */
  var details = meta('axiorhub-details').split(',').filter(Boolean).map(function (x) { var p = x.split('|'); return { route: p[0], param: p[1] || '' }; });
  function estDetail(url) {
    var chemin = url.pathname.indexOf(prefix) === 0 ? url.pathname.slice(prefix.length) : url.pathname;
    return details.some(function (d) { return (d.route.slice(-1) === '/' ? chemin.indexOf(d.route) === 0 : chemin === d.route) && (!d.param || url.searchParams.has(d.param)); });
  }
  function filtresLocaux() {
    var valeurs = {};
    Array.prototype.forEach.call(doc.querySelectorAll('#ws-main input[type=search], #ws-main select[data-filter], #ws-main input[data-filter]'), function (f, i) {
      if (f.value && f.value.length <= 200) valeurs[f.id || f.name || i] = f.value;
    });
    return valeurs;
  }
  /* 5.6.100 (lot F, R08) : contexte de retour complet. Ancre stable de la ligne (``data-ax100-ancre`` : un identifiant, jamais un titre),
     liens des lignes suivantes (si la ligne a disparu au retour, la suivante reçoit le focus), défilement des zones qui défilent seules.
     Curseur, filtre et tri serveur sont déjà dans l'adresse de la liste (clé de ce contexte) ; rien de cela ne part dans une adresse. */
  var ZONES = '#ws-main [data-ax100-zone], #ws-main .ax-rows, #ws-main .ax-list, #ws-main .ax-table-wrap';
  function ancreDe(el) { var a = el && el.closest && el.closest('[data-ax100-ancre]'); var v = a ? a.getAttribute('data-ax100-ancre') : ''; return v && v.length <= 120 ? v : ''; }
  function zonesDefilees() {
    var z = {};
    Array.prototype.slice.call(doc.querySelectorAll(ZONES), 0, 20).forEach(function (el, i) { if (el.scrollTop > 0) z[el.id || ('#' + i)] = el.scrollTop; });
    return z;
  }
  function suivantes(lien) {
    if (!lien) return [];
    var liens = Array.prototype.filter.call(doc.querySelectorAll('#ws-main a[href]'), function (a) { return estDetail(new URL(a.href, root.location.href)); });
    var i = liens.indexOf(lien), propre = sansRetour(new URL(lien.href, root.location.href)), vus = {}, sortie = [];
    liens.slice(i + 1).concat(liens.slice(0, Math.max(0, i)).reverse()).some(function (a) {   // les suivantes, puis les précédentes
      var h = sansRetour(new URL(a.href, root.location.href));
      if (h !== propre && !vus[h]) { vus[h] = 1; sortie.push({ href: h, ancre: ancreDe(a) }); }
      return sortie.length >= 6;
    });
    return sortie;
  }
  function memoriser(lien) {
    ecrire(CLE + ici(), JSON.stringify({ y: root.scrollY, focus: lien ? sansRetour(new URL(lien.href, root.location.href)) : '', valeurs: filtresLocaux(),
                                         ancre: ancreDe(lien), suivantes: suivantes(lien), zones: zonesDefilees() }));
    /* 5.6.96 (U07) : liste quittée, de même origine et pour ce compte : le bouton Retour du détail pourra revenir par l'historique */
    ecrire(PRECEDENT, JSON.stringify({ liste: ici(), compte: meta('axiorhub-compte'), detail: lien ? sansRetour(new URL(lien.href, root.location.href)) : '' }));
  }
  /* 5.6.96 (U07) : retour par l'historique seulement si l'entrée précédente connue est cette liste, ouverte par le même compte, sur la même
     origine, et si le navigateur vient bien d'elle ; sinon le lien validé par le serveur est suivi comme avant. */
  function retourParHistorique(cible) {
    var prec;
    try { prec = JSON.parse(lire(PRECEDENT) || 'null'); } catch (e) { prec = null; }
    if (!prec || !prec.liste || prec.compte !== meta('axiorhub-compte') || cible.origin !== root.location.origin) return false;
    if (sansRetour(cible) !== sansRetour(new URL(prec.liste, root.location.href)) || prec.detail !== sansRetour(new URL(root.location.href))) return false;
    var venu;
    try { venu = doc.referrer ? new URL(doc.referrer) : null; } catch (e) { venu = null; }
    if (!venu || venu.origin !== root.location.origin || sansRetour(venu) !== sansRetour(cible) || root.history.length < 2) return false;
    root.history.back();
    return true;
  }
  function saisieNonEnregistree() {
    return Array.prototype.some.call(doc.querySelectorAll('#ws-main form textarea, #ws-main form input:not([type=hidden]):not([type=search]):not([type=checkbox]):not([type=radio])'), function (f) {
      return !f.readOnly && !f.disabled && f.value !== f.defaultValue;
    });
  }
  doc.addEventListener('click', function (ev) {
    var retour = ev.target.closest && ev.target.closest('[data-ui89-retour]');
    if (retour) {
      if (saisieNonEnregistree() && !root.confirm('Une saisie de cette page n’est pas enregistrée et sera perdue. Revenir quand même ?')) { ev.preventDefault(); return; }
      ecrire(RESTAURER, new URL(retour.href, root.location.href).pathname + new URL(retour.href, root.location.href).search);
      if (retourParHistorique(new URL(retour.href, root.location.href))) ev.preventDefault();
      return;
    }
    var lien = ev.target.closest && ev.target.closest('#ws-main a[href]');
    if (!lien || ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button !== 0 || lien.target === '_blank' || lien.closest('.ws-topbar, .ws-tabs')) return;
    var url = new URL(lien.href, root.location.href);
    if (url.origin !== root.location.origin || !estDetail(url)) return;
    memoriser(lien);
    /* Une adresse qui porterait un jeton ne part jamais dans le contexte de retour ; le serveur la refuserait de toute façon. */
    if (!url.searchParams.has('return_to') && !url.searchParams.has('retour') && ici().length < 500 && !/[?&](?:token|jeton|csrf|secret|password|code)=/i.test(ici()) && !estDetail(new URL(root.location.href))) {
      url.searchParams.set('return_to', ici());
      lien.href = url.pathname + url.search + url.hash;
    }
  }, true);

  /* Au retour : filtres locaux, position, puis focus sur l'élément ouvert. */
  function restaurer() {
    var nav = root.performance && root.performance.getEntriesByType && root.performance.getEntriesByType('navigation')[0];
    var demande = lire(RESTAURER) === ici();
    if (!demande && !(nav && nav.type === 'back_forward')) return;
    oublier(RESTAURER);
    var brut = lire(CLE + ici());
    if (!brut) return;
    var etat;
    try { etat = JSON.parse(brut); } catch (e) { return; }
    Array.prototype.forEach.call(doc.querySelectorAll('#ws-main input[type=search], #ws-main select[data-filter], #ws-main input[data-filter]'), function (f, i) {
      var v = (etat.valeurs || {})[f.id || f.name || i];
      if (v !== undefined && v !== f.value) { f.value = v; f.dispatchEvent(new root.Event('input', { bubbles: true })); f.dispatchEvent(new root.Event('change', { bubbles: true })); }
    });
    /* 5.6.100 (lot F) : la ligne est cherchée tout de suite, puis, si la liste se charge après la page (rendu différé, volet déplié), à chaque
       changement du contenu pendant 4 s ou à l'annonce « ax100:liste-prete » ; défilement et focus sont remis une fois la ligne trouvée. */
    var fini = false, observateur = null, delai = null;
    function essayer() {
      if (fini) return true;
      var trouve = retrouver(etat);
      if (!trouve && etat.focus) return false;
      fini = true;
      if (observateur) observateur.disconnect();
      if (delai) root.clearTimeout(delai);
      doc.removeEventListener('ax100:liste-prete', essayer);
      repositionner(etat);
      if (trouve) {
        var parent = trouve.el.closest('[role=tabpanel][hidden]'); if (parent) montrerOnglet(parent.getAttribute('data-filtre-panneau'));
        trouve.el.focus({ preventScroll: true });
        if (trouve.suivante) { trouve.el.setAttribute('data-ax100-retrouve', ''); root.setTimeout(function () { trouve.el.removeAttribute('data-ax100-retrouve'); }, 4000); }
      }
      return true;
    }
    repositionner(etat);
    if (essayer()) return;
    doc.addEventListener('ax100:liste-prete', essayer);
    var main = doc.getElementById('ws-main');
    if (main && root.MutationObserver) { observateur = new root.MutationObserver(function () { essayer(); }); observateur.observe(main, { childList: true, subtree: true }); }
    delai = root.setTimeout(function () { if (!fini) { fini = true; if (observateur) observateur.disconnect(); doc.removeEventListener('ax100:liste-prete', essayer); } }, 4000);
  }
  function repositionner(etat) {
    root.scrollTo(0, etat.y || 0);
    var zones = etat.zones || {};
    Array.prototype.slice.call(doc.querySelectorAll(ZONES), 0, 20).forEach(function (el, i) { var y = zones[el.id || ('#' + i)]; if (y) el.scrollTop = y; });
  }
  /* Ligne ouverte (ancre stable, sinon même lien) ; disparue : la première des suivantes encore présente. */
  function retrouver(etat) {
    var liens = Array.prototype.slice.call(doc.querySelectorAll('#ws-main a[href]'));
    function parAncre(v) { if (!v) return null; var l = Array.prototype.filter.call(doc.querySelectorAll('#ws-main [data-ax100-ancre]'), function (x) { return x.getAttribute('data-ax100-ancre') === v; })[0]; return l ? (l.matches && l.matches('a[href],button,[tabindex]') ? l : (l.querySelector('a[href],button') || l)) : null; }
    function parLien(h) { return h ? liens.filter(function (a) { return sansRetour(new URL(a.href, root.location.href)) === h; })[0] || null : null; }
    var el = parAncre(etat.ancre) || parLien(etat.focus);
    if (el) return { el: el, suivante: false };
    var reste = etat.suivantes || [];
    for (var i = 0; i < reste.length; i++) { el = parAncre(reste[i].ancre) || parLien(reste[i].href); if (el) return { el: el, suivante: true }; }
    return null;
  }
  root.addEventListener('pageshow', restaurer);
  root.AX100 = root.AX100 || {};
  root.AX100.navigation = { listePrete: function () { doc.dispatchEvent(new root.Event('ax100:liste-prete')); }, retrouver: retrouver };
  /* 5.6.96 (U07) : page rendue par le cache du navigateur (retour arrière) : session et compte revalidés auprès du serveur ; session fermée
     ou autre compte : la page est rechargée (le serveur redemande la connexion ou rend la page du bon compte). 5.6.100 (lot F) : le contenu
     privé est masqué pendant la vérification, et ne réapparaît que pour le même compte ; sinon l'écran reste vide jusqu'au rechargement. */
  root.addEventListener('pageshow', function (ev) {
    if (!ev.persisted || !meta('axiorhub-compte')) return;
    var main = doc.getElementById('ws-main');
    if (main) { main.hidden = true; main.setAttribute('aria-busy', 'true'); }
    function montrer() { if (main) { main.hidden = false; main.removeAttribute('aria-busy'); } }
    root.fetch(prefix + '/api440/m5689/affichage/session', { credentials: 'same-origin', cache: 'no-store', redirect: 'manual' }).then(function (r) {
      if (!r.ok) throw new Error('session');
      return r.json();
    }).then(function (v) { if (v && v.compte === meta('axiorhub-compte')) montrer(); else root.location.reload(); }).catch(function () { root.location.reload(); });
  });

  /* Panneaux : <dialog> natif pour le piège du focus, Échap et le retour du focus ; fermeture aussi par le bouton et le fond. */
  doc.addEventListener('click', function (ev) {
    var t = ev.target;
    var ouvre = t.closest && t.closest('[data-ui89-ouvre]');
    if (ouvre) { var d = doc.getElementById(ouvre.getAttribute('data-ui89-ouvre')); if (d && d.showModal && !d.open) d.showModal(); return; }
    var ferme = t.closest && t.closest('[data-ui89-ferme]');
    if (ferme) { var boite = ferme.closest('dialog'); if (boite) boite.close(); return; }
    var ancre = t.closest && t.closest('dialog a[href^="#"]');
    if (ancre) { ancre.closest('dialog').close(); return; }
    if (t.tagName === 'DIALOG' && t.classList.contains('ui89-panneau')) { var r = t.getBoundingClientRect(); if (ev.clientX < r.left) t.close(); }
    var vue = t.closest && t.closest('[data-tab-open]');
    if (vue) { var rapports = doc.getElementById('a89-rapports'); if (rapports && rapports.showModal && !rapports.open) rapports.showModal(); }
    var pilote = t.closest && t.closest('[data-a89-pilote]');
    if (pilote) { var l = doc.getElementById('ws-ai-launcher'); if (l) l.click(); }
  });
  doc.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape' || ev.defaultPrevented) return;
    var tout = doc.getElementById('f5631-all');
    if (tout && tout.open) { tout.open = false; var s = doc.getElementById('f5631-toggle'); if (s) s.focus(); return; }
    var dock = doc.getElementById('ws-ai-dock');
    if (dock && !dock.hidden && dock.contains(doc.activeElement)) { var fermer = doc.getElementById('ws-ai-close'); if (fermer) fermer.click(); var lanceur = doc.getElementById('ws-ai-launcher'); if (lanceur) lanceur.focus(); }
  });

  /* Onglets de filtre : flèches, Début, Fin ; le choix est gardé pour l'onglet du navigateur. */
  function montrerOnglet(cle) {
    Array.prototype.forEach.call(doc.querySelectorAll('[role=tab][data-filtre]'), function (b) {
      var actif = b.getAttribute('data-filtre') === cle;
      b.setAttribute('aria-selected', actif ? 'true' : 'false'); b.tabIndex = actif ? 0 : -1;
    });
    Array.prototype.forEach.call(doc.querySelectorAll('[data-filtre-panneau]'), function (p) { p.hidden = p.getAttribute('data-filtre-panneau') !== cle; });
    ecrire('ui89-filtre:' + root.location.pathname, cle);
  }
  doc.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[role=tab][data-filtre]');
    if (b) montrerOnglet(b.getAttribute('data-filtre'));
  });
  doc.addEventListener('keydown', function (ev) {
    var b = ev.target.closest && ev.target.closest('[role=tab][data-filtre]');
    if (!b || ['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(ev.key) < 0) return;
    var liste = Array.prototype.slice.call(b.parentNode.querySelectorAll('[role=tab]')), i = liste.indexOf(b), n = liste.length;
    var k = ev.key === 'Home' ? 0 : ev.key === 'End' ? n - 1 : (i + (ev.key === 'ArrowRight' ? 1 : -1) + n) % n;
    ev.preventDefault(); montrerOnglet(liste[k].getAttribute('data-filtre')); liste[k].focus();
  });
  var garde = lire('ui89-filtre:' + root.location.pathname);
  if (garde && doc.querySelector('[role=tab][data-filtre="' + garde.replace(/[^a-z]/g, '') + '"]')) montrerOnglet(garde);

  /* Travail préparé : un jeton léger est interrogé ; la zone n'est remplacée que s'il change, ni sous le clavier, ni pendant une saisie. Le filtre choisi et le défilement sont conservés. */
  var travail = doc.getElementById('a89-travail');
  if (travail) {
    var connu = travail.getAttribute('data-revision');
    var actualiser = function () {
      if (doc.hidden) return;
      var zone = doc.getElementById('a89-travail-zone');
      if (zone.contains(doc.activeElement) || doc.querySelector('dialog[open]')) return;
      root.fetch(prefix + '/api440/m5689/livrables/revision', { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        if (!d || !d.revision || d.revision === connu) return null;
        return root.fetch(prefix + '/api440/m530/part?name=travail&prefix=' + encodeURIComponent(prefix), { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (p) {
          if (!p || typeof p.html !== 'string' || zone.contains(doc.activeElement)) return;
          var filtre = doc.querySelector('[role=tab][data-filtre][aria-selected=true]');
          var cle = filtre ? filtre.getAttribute('data-filtre') : '';
          zone.innerHTML = p.html;                /* HTML construit et échappé par le serveur (accueil5689) */
          connu = d.revision;
          if (cle) montrerOnglet(cle);
        });
      }).catch(function () { /* hors ligne : nouvel essai au prochain passage */ });
    };
    root.setInterval(actualiser, 20000);
  }
  void csrf;
})(window);
