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
  function memoriser(lien) {
    ecrire(CLE + ici(), JSON.stringify({ y: root.scrollY, focus: lien ? sansRetour(new URL(lien.href, root.location.href)) : '', valeurs: filtresLocaux() }));
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
    root.scrollTo(0, etat.y || 0);
    if (etat.focus) {
      var cible = Array.prototype.filter.call(doc.querySelectorAll('#ws-main a[href]'), function (a) { return sansRetour(new URL(a.href, root.location.href)) === etat.focus; })[0];
      if (cible) { var parent = cible.closest('[role=tabpanel][hidden]'); if (parent) montrerOnglet(parent.getAttribute('data-filtre-panneau')); cible.focus({ preventScroll: true }); }
    }
  }
  root.addEventListener('pageshow', restaurer);
  /* 5.6.96 (U07) : page rendue par le cache du navigateur (retour arrière) : session et compte revalidés auprès du serveur ; session fermée
     ou autre compte : la page est rechargée (le serveur redemande la connexion ou rend la page du bon compte). */
  root.addEventListener('pageshow', function (ev) {
    if (!ev.persisted || !meta('axiorhub-compte')) return;
    root.fetch(prefix + '/api440/m5689/affichage/session', { credentials: 'same-origin', cache: 'no-store', redirect: 'manual' }).then(function (r) {
      if (!r.ok) { root.location.reload(); return null; }
      return r.json();
    }).then(function (v) { if (v && v.compte !== meta('axiorhub-compte')) root.location.reload(); }).catch(function () { root.location.reload(); });
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
