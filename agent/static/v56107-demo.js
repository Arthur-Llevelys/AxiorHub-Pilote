/* AxiorHub 5.6.107 (lot M) — mode Démo : bandeau permanent, visite guidée, « Essayer la démo ».
   - Bandeau (#demo107, servi seulement en démo) : « Recommencer la démo » (POST m56107/demo/reinitialiser, un clic), « Quitter »
     (POST m56107/demo/quitter puis message de fin dans la page), « Visite guidée ». Jeton CSRF et clé d'idempotence sur chaque POST.
   - Visite guidée : 8 bulles (courriel → dossier → projet de réponse → À relire → agenda → assistant → Fichiers → fin), passables et
     désactivables, au clavier (Entrée ou → : suivant, ← : précédent, Échap : passer). Étape en cours gardée dans sessionStorage ;
     « Ne plus proposer » dans localStorage (préférence de ce navigateur seulement ; tout accès au stockage est protégé).
   - « Essayer la démo » (accueil d'une installation neuve du poste) : POST m56107/demo/lancer ; la démo s'ouvre dans une autre fenêtre.
   Tout texte passe par textContent ; aucun style en ligne écrit dans le HTML (positions par CSSOM, compatibles avec la CSP). */
(function () {
  'use strict';
  var doc = document;
  if (window.__demo107) return;
  window.__demo107 = true;
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  function lire(stock, cle) { try { return window[stock].getItem(cle); } catch (ex) { return null; } }
  function ecrire(stock, cle, valeur) { try { if (valeur === null) window[stock].removeItem(cle); else window[stock].setItem(cle, valeur); } catch (ex) { /* stockage indisponible */ } }
  function cle() {
    var a = new Uint8Array(16); window.crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function poster(route, corps) {
    corps = corps || {}; corps.cle = cle();
    return window.fetch(prefix + '/api440/m56107/demo/' + route, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (v) {
        if (!r.ok || v.error) throw new Error(v.message || ('Erreur ' + r.status));
        return v;
      });
    });
  }
  function statut(zone, texte, erreur) {
    var s = zone && zone.querySelector('[data-demo107-statut]');
    if (!s) return;
    s.textContent = texte; s.dataset.etat = erreur ? 'erreur' : '';
  }

  /* ------------------------------------------------------------ « Essayer la démo » (installation réelle, premier lancement) */
  var modele = doc.getElementById('demo107-essai-modele'), principal = doc.getElementById('ws-main');
  if (modele && principal && modele.content) {
    var titre = principal.querySelector('.page-title');
    var carte = modele.content.firstElementChild.cloneNode(true);
    if (titre && titre.parentNode === principal) principal.insertBefore(carte, titre.nextSibling); else principal.insertBefore(carte, principal.firstChild);
  }
  var essai = doc.querySelector('[data-demo107-essai]');
  if (essai) {
    var lancer = essai.querySelector('[data-demo107-lancer]');
    if (lancer) lancer.addEventListener('click', function () {
      lancer.disabled = true; statut(essai, 'Ouverture de la démo…', false);
      poster('lancer').then(function (v) { statut(essai, v.message || 'La démo s’ouvre.', false); })
        .catch(function (ex) { statut(essai, ex.message, true); lancer.disabled = false; });
    });
  }

  var bandeau = doc.getElementById('demo107');
  if (!bandeau) return;

  /* ------------------------------------------------------------ bandeau */
  var recommencer = bandeau.querySelector('[data-demo107-recommencer]');
  var quitter = bandeau.querySelector('[data-demo107-quitter]');
  var visiteBouton = bandeau.querySelector('[data-demo107-visite]');
  if (recommencer) recommencer.addEventListener('click', function () {
    recommencer.disabled = true; recommencer.setAttribute('aria-busy', 'true');
    statut(bandeau, 'Remise à neuf de la démo…', false);
    poster('reinitialiser').then(function (v) {
      statut(bandeau, v.message || 'Démo remise à neuf.', false);
      ecrire('sessionStorage', 'demo107-etape', null);
      window.setTimeout(function () { window.location.assign(prefix + '/aujourdhui'); }, 700);
    }).catch(function (ex) { statut(bandeau, ex.message, true); recommencer.disabled = false; recommencer.removeAttribute('aria-busy'); });
  });
  if (quitter) quitter.addEventListener('click', function () {
    quitter.disabled = true;
    poster('quitter').then(function (v) {
      var main = doc.getElementById('ws-main') || doc.body;
      while (main.firstChild) main.removeChild(main.firstChild);
      var fin = doc.createElement('section'); fin.className = 'demo107-fin'; fin.setAttribute('role', 'status');
      var h = doc.createElement('h1'); h.textContent = 'Merci d’avoir essayé AxiorHub Pilote';
      var p = doc.createElement('p'); p.textContent = v.message || 'Démonstration terminée : vous pouvez fermer cette fenêtre.';
      fin.appendChild(h); fin.appendChild(p); main.appendChild(fin);
      ecrire('sessionStorage', 'demo107-etape', null);
    }).catch(function (ex) { statut(bandeau, ex.message, true); quitter.disabled = false; });
  });

  /* ------------------------------------------------------------ visite guidée */
  var ETAPES = [
    { chemin: '/courriels', cibles: ['[data-l106-liste]', '.l106-liste', '#m5625-liste', '.m5625-list', 'main table'],
      titre: '1. Le courriel d’abord', texte: 'Voici la boîte de démonstration : une quarantaine de messages fictifs de clients, de confrères et d’une juridiction. Ouvrez « DURAND c. MARTIN — conclusions adverses reçues » : AxiorHub le rattache à son dossier.' },
    { chemin: '/dossier?id=DOS-001', cibles: ['.page-title', 'h1'],
      titre: '2. Son dossier', texte: 'Le dossier DURAND c. MARTIN réunit les parties, la juridiction (TJ Valbrune, RG 24/01234), les pièces et la chronologie. Tout vient des courriels et des fichiers du cabinet.' },
    { chemin: '/a-relire', cibles: ['[data-v100-item]', '.v100-item', 'main article', 'main li'],
      titre: '3. Le projet de réponse', texte: 'L’IA a préparé un projet de réponse au client. En démonstration, il est marqué « Réponse de démonstration » : aucun modèle n’a été appelé.' },
    { chemin: '/a-relire', cibles: ['.page-title', 'h1'],
      titre: '4. À relire', texte: 'Rien ne part sans vous : vous relisez, corrigez puis envoyez. L’envoi laisse quelques secondes pour annuler ; ici, le message va seulement dans « Envoyés (démo) ».' },
    { chemin: '/planning', cibles: ['.page-title', 'h1'],
      titre: '5. L’agenda', texte: 'Les audiences et rendez-vous de la semaine, calculés à partir d’aujourd’hui : référé KERVILAC c. ORLANDEC, signature ORLANDEC, réunion d’expertise ZORVAL…' },
    { chemin: '/assistant', cibles: ['textarea', '.page-title', 'h1'],
      titre: '6. L’assistant', texte: 'Posez une question, par exemple « Quelles audiences cette semaine ? » ou « Ce courriel de changement de RIB est-il suspect ? ». Les réponses sont préparées pour la démonstration.' },
    { chemin: '/fichiers', cibles: ['.page-title', 'h1'],
      titre: '7. Les fichiers', texte: 'Les pièces des dossiers (PDF fictifs) sont rangées par dossier, comme sur le stockage du cabinet.' },
    { chemin: '/aujourdhui', cibles: ['.page-title', 'h1'],
      titre: '8. À vous de jouer', texte: 'Explorez librement. « Recommencer la démo » remet tout à neuf en un clic ; « Quitter » termine la démonstration. Rien n’est envoyé, rien ne quitte ce poste.' }
  ];
  /* 5.6.107 : premier dossier de la démo selon sa langue (DOS-001, MAT-001, AKT-001, EXP-001), donné par le bandeau */
  if (bandeau.dataset.demo107Dossier) ETAPES[1].chemin = '/dossier?id=' + encodeURIComponent(bandeau.dataset.demo107Dossier);
  var bulle = null;
  function cheminCourant() {
    var p = window.location.pathname;
    if (prefix && p.indexOf(prefix) === 0) p = p.slice(prefix.length);
    return p + window.location.search;
  }
  function memePage(chemin) {
    var c = cheminCourant(), base = chemin.split('?')[0];
    return c === chemin || (chemin.indexOf('?') < 0 && c.split('?')[0] === base);
  }
  function fermer(passer) {
    if (bulle) { bulle.parentNode.removeChild(bulle); bulle = null; }
    doc.removeEventListener('keydown', clavier, true);
    Array.prototype.forEach.call(doc.querySelectorAll('.demo107-cible'), function (x) { x.classList.remove('demo107-cible'); });
    if (passer) ecrire('sessionStorage', 'demo107-etape', null);
    if (visiteBouton) visiteBouton.focus();
  }
  function aller(n) {
    if (n < 0 || n >= ETAPES.length) { fermer(true); return; }
    ecrire('sessionStorage', 'demo107-etape', String(n));
    if (!memePage(ETAPES[n].chemin)) { window.location.assign(prefix + ETAPES[n].chemin); return; }
    montrer(n);
  }
  function clavier(ev) {
    if (!bulle) return;
    var n = Number(bulle.dataset.etape || 0);
    if (ev.key === 'Escape') { ev.preventDefault(); fermer(true); }
    else if (ev.key === 'ArrowRight') { ev.preventDefault(); aller(n + 1); }
    else if (ev.key === 'ArrowLeft') { ev.preventDefault(); aller(n - 1); }
  }
  function bouton(texte, action, classe) {
    var b = doc.createElement('button'); b.type = 'button'; b.className = 'demo107-bouton' + (classe ? ' ' + classe : '');
    b.textContent = texte; b.addEventListener('click', action); return b;
  }
  function montrer(n) {
    fermer(false);
    var etape = ETAPES[n], cible = null;
    for (var i = 0; i < etape.cibles.length && !cible; i++) cible = doc.querySelector('#ws-main ' + etape.cibles[i]) || null;
    bulle = doc.createElement('div');
    bulle.className = 'demo107-bulle'; bulle.id = 'demo107-bulle'; bulle.dataset.etape = String(n);
    bulle.setAttribute('role', 'dialog'); bulle.setAttribute('aria-modal', 'false'); bulle.setAttribute('aria-labelledby', 'demo107-bulle-titre');
    bulle.tabIndex = -1;
    var t = doc.createElement('h2'); t.id = 'demo107-bulle-titre'; t.textContent = etape.titre;
    var p = doc.createElement('p'); p.textContent = etape.texte;
    var pas = doc.createElement('p'); pas.className = 'demo107-pas'; pas.textContent = 'Étape ' + (n + 1) + ' sur ' + ETAPES.length + ' · Entrée ou → : suivant · ← : précédent · Échap : passer';
    var actions = doc.createElement('div'); actions.className = 'demo107-bulle-actions';
    if (n > 0) actions.appendChild(bouton('Précédent', function () { aller(n - 1); }));
    var suivant = bouton(n === ETAPES.length - 1 ? 'Terminer' : 'Suivant', function () { aller(n + 1); }, 'demo107-principal');
    actions.appendChild(suivant);
    var passer = bouton('Passer la visite', function () { fermer(true); });
    passer.setAttribute('data-demo107-passer', '');   // repère stable (vidéo, lot V)
    actions.appendChild(passer);
    var plus = doc.createElement('label'); plus.className = 'demo107-plus';
    var case_ = doc.createElement('input'); case_.type = 'checkbox'; case_.checked = lire('localStorage', 'demo107-visite-off') === '1';
    case_.addEventListener('change', function () { ecrire('localStorage', 'demo107-visite-off', case_.checked ? '1' : null); });
    plus.appendChild(case_); plus.appendChild(doc.createTextNode(' Ne plus proposer la visite'));
    bulle.appendChild(t); bulle.appendChild(p); bulle.appendChild(pas); bulle.appendChild(actions); bulle.appendChild(plus);
    doc.body.appendChild(bulle);
    if (cible) {
      cible.classList.add('demo107-cible');
      try { cible.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (ex) { cible.scrollIntoView(); }
      var r = cible.getBoundingClientRect();
      var haut = Math.min(Math.max(12, r.bottom + 12), window.innerHeight - 260);
      bulle.style.top = Math.round(haut) + 'px';
      bulle.style.left = Math.round(Math.min(Math.max(12, r.left), Math.max(12, window.innerWidth - 400))) + 'px';
    } else {
      bulle.classList.add('demo107-centre');
    }
    doc.addEventListener('keydown', clavier, true);
    suivant.focus();
  }
  if (visiteBouton) visiteBouton.addEventListener('click', function () { ecrire('localStorage', 'demo107-vue', '1'); aller(0); });
  var enCours = lire('sessionStorage', 'demo107-etape');
  if (enCours !== null && ETAPES[Number(enCours)] && memePage(ETAPES[Number(enCours)].chemin)) {
    montrer(Number(enCours));
  } else if (lire('localStorage', 'demo107-vue') === null && lire('localStorage', 'demo107-visite-off') !== '1') {
    ecrire('localStorage', 'demo107-vue', '1');           // première ouverture : la visite est proposée une fois
    aller(0);
  }
}());
