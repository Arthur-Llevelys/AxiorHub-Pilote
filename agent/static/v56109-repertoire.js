/* AxiorHub 5.6.109 (lot L, F02) — onglet Fichiers › Par répertoire.
   Le tri est fait par le serveur sur tout le répertoire (formulaires GET des en-têtes : ils fonctionnent sans ce script). Ce script ajoute :
   - le comptage à la demande du contenu des sous-répertoires visibles (une lecture chacun, l'une après l'autre), au lieu d'un comptage à
     l'ouverture ; une réponse en erreur laisse « contenu non compté » ;
   - le rappel du type, de la taille et de la date du fichier choisi dans le volet d'aperçu, même quand aucun aperçu n'est possible ;
   - la ligne choisie marquée (aria-current, classe r109-choisi) ; au retour sur la page (Précédent, rechargement), la sélection et le
     défilement sont restaurés (sessionStorage, par adresse : tri et page compris) ; après un tri, le bouton d'en-tête reprend le focus.
   L'aperçu lui-même reste celui de v5679.js (liens data-d79-doc, compteur de requêtes) : aucun second service d'aperçu ici.
   Aucun HTML venant du serveur n'est injecté (textContent seulement) ; aucun appel au modèle. */
(function () {
  'use strict';
  if (window.axh56109Repertoire) return;
  window.axh56109Repertoire = true;
  var bloc = document.getElementById('d98-fichiers');
  var table = bloc ? bloc.querySelector('table.d98-arbre') : null;
  if (!table) return;
  var prefixMeta = document.querySelector('meta[name="axiorhub-prefix"]');
  var prefix = prefixMeta ? prefixMeta.getAttribute('content') : '';
  var volet = document.getElementById('d79-preview');
  var resume = volet ? volet.querySelector('[data-r109-resume-volet]') : null;
  var CLE = 'r109:' + location.pathname + location.search;
  var CLE_TRI = 'r109:tri:' + location.pathname;

  function memoire(lire, cle, valeur) {
    try {
      if (lire) return window.sessionStorage.getItem(cle);
      if (valeur === null) window.sessionStorage.removeItem(cle); else window.sessionStorage.setItem(cle, valeur);
    } catch (err) { /* stockage refusé par le navigateur : la page reste utilisable, sans restauration */ }
    return null;
  }
  function taille(n) {
    n = Number(n) || 0;
    var unites = [['Go', 1073741824], ['Mo', 1048576], ['ko', 1024]];
    for (var i = 0; i < unites.length; i += 1) {
      if (n >= unites[i][1]) return (n / unites[i][1]).toFixed(1).replace('.', ',') + ' ' + unites[i][0];
    }
    return n + ' o';
  }
  function pluriel(n, mot) { return n + ' ' + mot + (n === 1 ? '' : 's'); }

  // ------------------------------------------------------------------ comptage à la demande des sous-répertoires
  var zone = table.closest('[data-r109-api]');
  var api = zone ? zone.getAttribute('data-r109-api') : '';
  var file = [], occupe = false, quitte = false;
  function suivant() {
    if (occupe || quitte || !file.length) return;
    var cible = file.shift();
    if (!cible.isConnected || cible.getAttribute('data-r109-etat')) { suivant(); return; }
    occupe = true;
    cible.setAttribute('data-r109-etat', 'lecture');
    cible.textContent = 'comptage…';
    var q = 'rep=' + encodeURIComponent(cible.getAttribute('data-r109-compter')) + '&comptes=0';
    fetch(prefix + '/api440/' + api + '?' + q, { credentials: 'same-origin' }).then(function (r) {
      return r.json().then(function (v) { if (!r.ok || !v || v.etat !== 'ok' || !v.resume) throw new Error('indisponible'); return v.resume; });
    }).then(function (s) {
      var texte = pluriel(Number(s.fichiers) || 0, 'fichier') + (s.fichiers ? ' (' + taille(s.octets_directs) + ')' : '');
      if (s.sous_dossiers) texte += ', ' + pluriel(Number(s.sous_dossiers), 'sous-répertoire');
      if (s.partiel) texte += ' — compte incomplet';
      cible.textContent = texte;
      cible.setAttribute('data-r109-etat', 'compte');
    }).catch(function () {
      cible.textContent = 'contenu non compté';
      cible.setAttribute('data-r109-etat', 'indisponible');
    }).then(function () { occupe = false; suivant(); });
  }
  if (api) {
    var cellules = Array.prototype.slice.call(table.querySelectorAll('[data-r109-compter]'));
    if ('IntersectionObserver' in window) {
      var obs = new IntersectionObserver(function (entrees) {
        entrees.forEach(function (x) { if (x.isIntersecting) { obs.unobserve(x.target); file.push(x.target); } });
        suivant();
      }, { rootMargin: '200px' });
      cellules.forEach(function (c) { obs.observe(c); });
    } else {
      file = cellules; suivant();
    }
    window.addEventListener('pagehide', function () { quitte = true; });
  }

  // ------------------------------------------------------------------ fichier choisi : rappel dans le volet, ligne marquée, retour
  function marquer(lien) {
    Array.prototype.forEach.call(table.querySelectorAll('tr.r109-choisi'), function (tr) { tr.classList.remove('r109-choisi'); });
    Array.prototype.forEach.call(table.querySelectorAll('a[aria-current]'), function (a) { a.removeAttribute('aria-current'); });
    if (resume) { resume.hidden = true; resume.textContent = ''; }
    if (!lien) return;
    var tr = lien.closest('tr');
    if (tr) tr.classList.add('r109-choisi');
    lien.setAttribute('aria-current', 'true');
    if (resume && lien.getAttribute('data-r109-resume')) { resume.textContent = lien.getAttribute('data-r109-resume'); resume.hidden = false; }
  }
  function ouvert() { return !!volet && !volet.hidden; }
  // après le gestionnaire de v5679.js (même phase, enregistré avant) : l'état du volet dit si le clic a ouvert ou refermé l'aperçu
  document.addEventListener('click', function (ev) {
    var lien = ev.target.closest && ev.target.closest('a[data-d79-doc]');
    if (lien && !table.contains(lien)) { if (ev.defaultPrevented) { marquer(null); memoire(false, CLE, null); } return; }   // autre liste de la page
    if (lien) {
      if (!ev.defaultPrevented) return;                  // Ctrl+clic, nouvel onglet : comportement du navigateur
      setTimeout(function () {
        if (ouvert()) {
          marquer(lien);
          memoire(false, CLE, JSON.stringify({ chemin: lien.getAttribute('data-d79-doc'), y: window.scrollY }));
        } else { marquer(null); memoire(false, CLE, null); }
      }, 0);
      return;
    }
    if (ev.target.closest && ev.target.closest('[data-d79-close]')) { marquer(null); memoire(false, CLE, null); }
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') setTimeout(function () { if (!ouvert()) { marquer(null); memoire(false, CLE, null); } }, 0);
  });
  window.addEventListener('scroll', function () {
    var brut = memoire(true, CLE);
    if (!brut) return;
    try { var v = JSON.parse(brut); v.y = window.scrollY; memoire(false, CLE, JSON.stringify(v)); } catch (err) { memoire(false, CLE, null); }
  }, { passive: true });

  function retour() {
    var nav = (window.performance && performance.getEntriesByType) ? performance.getEntriesByType('navigation')[0] : null;
    var revenu = nav && (nav.type === 'back_forward' || nav.type === 'reload');
    var brut = memoire(true, CLE);
    if (!brut) return;
    if (!revenu) { memoire(false, CLE, null); return; }  // arrivée ordinaire sur cette adresse : pas d'aperçu rouvert d'office
    var v = null;
    try { v = JSON.parse(brut); } catch (err) { v = null; }
    if (!v || typeof v.chemin !== 'string') { memoire(false, CLE, null); return; }
    var lien = Array.prototype.filter.call(table.querySelectorAll('a[data-d79-doc]'), function (a) { return a.getAttribute('data-d79-doc') === v.chemin; })[0];
    if (!lien) { memoire(false, CLE, null); return; }    // fichier absent de cette page (renommé, déplacé) : rien n'est rouvert
    if (typeof v.y === 'number') window.scrollTo(0, v.y);
    if (!ouvert()) lien.click();                         // même aperçu commun (v5679.js), même fichier
  }

  // ------------------------------------------------------------------ après un tri : le bouton d'en-tête reprend le focus (clavier)
  Array.prototype.forEach.call(table.querySelectorAll('form.r109-tri'), function (f) {
    f.addEventListener('submit', function () {
      var champ = f.querySelector('input[name="tri"]');
      memoire(false, CLE_TRI, champ ? champ.value : null);
    });
  });
  function focusTri() {
    var champ = memoire(true, CLE_TRI);
    if (!champ) return;
    memoire(false, CLE_TRI, null);
    var entree = Array.prototype.filter.call(table.querySelectorAll('form.r109-tri input[name="tri"]'), function (i) { return i.value === champ; })[0];
    var bouton = entree ? entree.form.querySelector('button') : null;
    if (bouton) bouton.focus({ preventScroll: true });
  }

  function demarrer() { focusTri(); retour(); }
  if (document.readyState === 'complete') demarrer(); else window.addEventListener('load', demarrer);
})();
