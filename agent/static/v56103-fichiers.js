/* AxiorHub 5.6.103 (lot F) — page « Fichiers » : deux volets à onglets, favoris, filtre instantané, recherche bornée dans les sous-dossiers,
   renommage par lot avec aperçu, copier / déplacer d'un volet à l'autre (glisser ou boutons), ZIP, propriétés et empreinte, versions,
   notes et couleurs, verrou. Aucune donnée n'est injectée en HTML : tout le texte passe par textContent. Chaque écriture est un POST
   (jeton CSRF) avec une clé d'idempotence neuve ; le serveur refait tous les contrôles (droits, chemins, versions). */
(function () {
  'use strict';
  var racine = document.getElementById('f103');
  if (!racine || !racine.getAttribute('data-f103-config')) { return; }
  var cfg;
  try { cfg = JSON.parse(racine.getAttribute('data-f103-config')); } catch (err) { return; }
  if (!cfg.sources || !cfg.sources.length) {   /* aucun stockage configuré : la page le dit, rien n'est chargé */
    Array.prototype.forEach.call(racine.querySelectorAll('[data-f103-etat]'), function (z) { z.textContent = 'Aucun stockage de fichiers configuré.'; });
    Array.prototype.forEach.call(racine.querySelectorAll('.f103-volet button, .f103-volet input, .f103-volet select'), function (b) { b.disabled = true; });
    return;
  }
  var volets = [];
  var STOCKAGE = 'f103-onglets';

  /* ---------------------------------------------------------------- outils */
  function meta(nom) { var m = document.querySelector('meta[name="' + nom + '"]'); return m ? m.getAttribute('content') || '' : ''; }
  function cle() {
    var a = new Uint8Array(12); (window.crypto || window.msCrypto).getRandomValues(a);
    return 'f103-' + Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function reponse(r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, statut: r.status, j: j }; }); }
  function lire(route, params, signal) {
    var q = Object.keys(params || {}).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
    return fetch(cfg.api + route + (q ? '?' + q : ''), { credentials: 'same-origin', cache: 'no-store', signal: signal }).then(reponse);
  }
  function poster(route, donnees, signal) {
    return fetch(cfg.api + route, { method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: signal,
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: JSON.stringify(donnees) }).then(reponse);
  }
  function el(tag, attrs, texte) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (texte !== undefined && texte !== null) { n.textContent = texte; }
    return n;
  }
  function vider(n) { while (n.firstChild) { n.removeChild(n.firstChild); } }
  function taille(o) {
    if (!o) { return '—'; }
    var u = ['o', 'Ko', 'Mo', 'Go'], i = 0, v = o;
    while (v >= 1000 && i < u.length - 1) { v /= 1000; i += 1; }
    return (i ? v.toFixed(1).replace('.', ',') : String(v)) + ' ' + u[i];
  }
  function quand(iso) {
    if (!iso) { return '—'; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return iso; }
    return d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  function erreur(r) {
    if (r && r.j && r.j.message) { return r.j.message; }
    return r && r.statut ? 'Refusé (' + r.statut + ').' : 'Serveur injoignable : rien n’a été fait.';
  }
  function libelleSource(id) { var s = (cfg.sources || []).filter(function (x) { return x.id === id; })[0]; return s ? s.libelle : id; }
  function nomDe(chemin) { var p = String(chemin || '').split('/'); return p[p.length - 1] || chemin; }

  /* ---------------------------------------------------------------- boîtes de dialogue */
  function ouvrirDlg(id) {
    var d = document.getElementById(id);
    if (!d) { return null; }
    var st = d.querySelector('.f103-statut'); if (st) { st.textContent = ''; }
    d.f103Retour = document.activeElement;
    if (typeof d.showModal === 'function') { if (!d.open) { d.showModal(); } } else { d.setAttribute('open', ''); }
    var premier = d.querySelector('input:not([type=hidden]), select, textarea, button');
    if (premier) { premier.focus(); }
    return d;
  }
  function fermerDlg(d) {
    if (!d) { return; }
    if (typeof d.close === 'function' && d.open) { d.close(); } else { d.removeAttribute('open'); }
    if (d.f103Retour && d.f103Retour.focus) { d.f103Retour.focus(); }
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-f103-fermer]');
    if (b) { ev.preventDefault(); fermerDlg(b.closest('dialog')); }
  });
  function dire(zone, texte) { if (zone) { zone.textContent = texte || ''; } }
  function confirmer(question) {
    return new Promise(function (resolu) {
      var d = ouvrirDlg('f103-dlg-confirmer');
      if (!d) { resolu(window.confirm(question)); return; }
      d.querySelector('[data-f103-question]').textContent = question;
      var f = d.querySelector('form');
      var fini = function (oui) { f.removeEventListener('submit', sur); d.removeEventListener('close', non); fermerDlg(d); resolu(oui); };
      var sur = function (ev) { ev.preventDefault(); fini(true); };
      var non = function () { f.removeEventListener('submit', sur); d.removeEventListener('close', non); resolu(false); };
      f.addEventListener('submit', sur);
      d.addEventListener('close', non);
    });
  }

  /* ---------------------------------------------------------------- volets et onglets */
  function sauver() {
    try {
      window.localStorage.setItem(STOCKAGE, JSON.stringify(volets.map(function (v) {
        return { actif: v.actif, onglets: v.onglets.map(function (o) { return { source: o.source, chemin: o.chemin }; }) };
      })));
    } catch (err) { /* stockage indisponible : les onglets ne sont pas gardés */ }
  }
  function memoire() {
    try { return JSON.parse(window.localStorage.getItem(STOCKAGE) || '[]') || []; } catch (err) { return []; }
  }
  function onglet(v) { return v.onglets[v.actif]; }
  function autre(v) { return volets.filter(function (x) { return x !== v; })[0] || null; }

  function initVolet(noeud, n, gardes) {
    var premier = (cfg.sources[0] || {}).id || '';
    var garde = gardes[n] || {};
    var onglets = (garde.onglets || []).filter(function (o) { return o && cfg.sources.some(function (s) { return s.id === o.source; }); }).slice(0, 8);
    var v = { n: n, noeud: noeud, onglets: onglets.length ? onglets : [{ source: premier, chemin: '' }], actif: 0, liste: null, selection: {}, filtre: '',
              recherche: null, controle: null,
              lignes: noeud.querySelector('[data-f103-lignes]'), etat: noeud.querySelector('[data-f103-etat]'), fil: noeud.querySelector('[data-f103-fil]'),
              listeOnglets: noeud.querySelector('[data-f103-onglets]'), source: noeud.querySelector('[data-f103-source]'),
              filtreChamp: noeud.querySelector('[data-f103-filtre]'), cherche: noeud.querySelector('[data-f103-chercher]') };
    v.actif = Math.min(Math.max(0, garde.actif || 0), v.onglets.length - 1);
    volets.push(v);
    v.source.addEventListener('change', function () { aller(v, v.source.value, ''); });
    v.filtreChamp.addEventListener('input', function () { v.filtre = v.filtreChamp.value.trim().toLocaleLowerCase('fr'); rendre(v); });
    v.cherche.addEventListener('submit', function (ev) { ev.preventDefault(); chercher(v, false); });
    noeud.addEventListener('click', function (ev) { surClic(v, ev); });
    noeud.addEventListener('keydown', function (ev) { surTouche(v, ev); });
    var zone = noeud.querySelector('[data-f103-zone]');
    zone.addEventListener('dragover', function (ev) {
      if (ev.dataTransfer && Array.prototype.indexOf.call(ev.dataTransfer.types || [], 'application/x-f103') >= 0) { ev.preventDefault(); zone.classList.add('f103-cible'); }
    });
    zone.addEventListener('dragleave', function () { zone.classList.remove('f103-cible'); });
    zone.addEventListener('drop', function (ev) {
      zone.classList.remove('f103-cible');
      var brut = ev.dataTransfer && ev.dataTransfer.getData('application/x-f103');
      if (!brut) { return; }
      ev.preventDefault();
      var paquet; try { paquet = JSON.parse(brut); } catch (err) { return; }
      var depart = volets[paquet.volet];
      if (!depart || depart === v) { return; }
      transfertDemande(depart, v, paquet.elements || []);
    });
    return v;
  }

  function aller(v, source, chemin) {
    var o = onglet(v);
    o.source = source; o.chemin = chemin || '';
    v.filtre = ''; v.filtreChamp.value = ''; v.recherche = null; v.selection = {};
    sauver(); charger(v);
  }

  function charger(v) {
    var o = onglet(v);
    if (v.controle) { v.controle.abort(); }
    v.controle = window.AbortController ? new AbortController() : null;
    v.source.value = o.source;
    dire(v.etat, 'Chargement…');
    rendreOnglets(v);
    lire('lister', { s: o.source, p: o.chemin }, v.controle && v.controle.signal).then(function (r) {
      if (!r.ok) { v.liste = null; vider(v.lignes); dire(v.etat, erreur(r)); rendreFil(v); return; }
      v.liste = r.j; o.chemin = r.j.chemin; o.titre = r.j.chemin ? nomDe(r.j.chemin) : libelleSource(o.source);
      v.selection = {}; rendre(v); rendreOnglets(v); rendreFil(v); majFavori(v);
    }, function (err) { if (!err || err.name !== 'AbortError') { dire(v.etat, 'Serveur injoignable : le dossier n’a pas pu être lu.'); } });
  }

  function rendreOnglets(v) {
    vider(v.listeOnglets);
    v.onglets.forEach(function (o, i) {
      var b = el('button', { type: 'button', role: 'tab', 'aria-selected': i === v.actif ? 'true' : 'false', tabindex: i === v.actif ? '0' : '-1',
                             'data-f103-onglet': String(i), title: libelleSource(o.source) + ' · ' + (o.chemin || 'racines') }, o.titre || (o.chemin ? nomDe(o.chemin) : libelleSource(o.source)));
      var wrap = el('span', { 'class': 'f103-onglet' + (i === v.actif ? ' f103-actif' : '') });
      wrap.appendChild(b);
      if (v.onglets.length > 1) {
        wrap.appendChild(el('button', { type: 'button', 'class': 'f103-fermer-onglet', 'data-f103-fermer-onglet': String(i), 'aria-label': 'Fermer l’onglet ' + b.textContent }, '×'));
      }
      v.listeOnglets.appendChild(wrap);
    });
  }

  function rendreFil(v) {
    vider(v.fil);
    var o = onglet(v);
    var r = el('button', { type: 'button', 'class': 'f103-miette', 'data-f103-aller': '', 'data-source': o.source }, libelleSource(o.source));
    v.fil.appendChild(r);
    ((v.liste && v.liste.fil) || []).forEach(function (m) {
      v.fil.appendChild(el('span', { 'aria-hidden': 'true' }, ' › '));
      v.fil.appendChild(el('button', { type: 'button', 'class': 'f103-miette', 'data-f103-aller': m.chemin, 'data-source': o.source }, m.nom));
    });
  }

  function visibles(v) {
    if (v.recherche && v.recherche.resultats) { return v.recherche.resultats; }
    var tout = (v.liste && v.liste.entrees) || [];
    if (!v.filtre) { return tout; }
    return tout.filter(function (x) { return x.nom.toLocaleLowerCase('fr').indexOf(v.filtre) >= 0; });
  }

  function rendre(v) {
    vider(v.lignes);
    if (!v.liste) { return; }
    var notes = v.liste.notes || {}, verrous = v.liste.verrous || {};
    if (v.liste.parent && !v.recherche) {
      var tr0 = el('tr', { 'class': 'f103-parent', tabindex: '0', 'data-f103-ouvrir': v.liste.parent });
      tr0.appendChild(el('td'));
      var td0 = el('td', { colspan: '4' }); td0.appendChild(el('button', { type: 'button', 'class': 'f103-nom', 'data-f103-ouvrir': v.liste.parent }, '↑ Dossier parent'));
      tr0.appendChild(td0); v.lignes.appendChild(tr0);
    }
    var liste = visibles(v);
    liste.forEach(function (x, i) {
      var tr = el('tr', { tabindex: '0', draggable: 'true', 'data-i': String(i), 'data-chemin': x.chemin, 'aria-selected': v.selection[x.chemin] ? 'true' : 'false' });
      if (v.selection[x.chemin]) { tr.classList.add('f103-choisi'); }
      var td = el('td'); var c = el('input', { type: 'checkbox', 'data-f103-choisir': x.chemin, 'aria-label': 'Sélectionner ' + x.nom });
      c.checked = !!v.selection[x.chemin]; td.appendChild(c); tr.appendChild(td);
      var tdn = el('td', { 'class': 'f103-cellule-nom' });
      var b = el('button', { type: 'button', 'class': 'f103-nom' + (x.dossier ? ' f103-dossier' : ''), 'data-f103-ouvrir': x.dossier ? x.chemin : '',
                             'data-f103-choisir-nom': x.dossier ? '' : x.chemin }, (x.dossier ? '▸ ' : '') + x.nom);
      tdn.appendChild(b);
      if (v.recherche) { tdn.appendChild(el('small', { 'class': 'f103-chemin' }, x.chemin)); }
      if (verrous[x.chemin]) { tdn.appendChild(el('span', { 'class': 'f103-verrou' }, ' verrouillé')); }
      tr.appendChild(tdn);
      tr.appendChild(el('td', { 'class': 'f103-num' }, x.dossier ? '—' : taille(x.taille)));
      tr.appendChild(el('td', {}, quand(x.modifie)));
      var tdnote = el('td', { 'class': 'f103-cellule-note' });
      var nt = notes[x.chemin];
      if (nt && (nt.couleur || nt.note)) {
        if (nt.couleur) { tdnote.appendChild(el('span', { 'class': 'f103-pastille f103-c-' + nt.couleur }, (cfg.couleurs || {})[nt.couleur] || nt.couleur)); }
        if (nt.note) { tdnote.appendChild(el('span', { 'class': 'f103-extrait', title: nt.note }, nt.note.length > 60 ? nt.note.slice(0, 60) + '…' : nt.note)); }
      }
      tr.appendChild(tdnote);
      tr.addEventListener('dragstart', function (ev) {
        if (!v.selection[x.chemin]) { v.selection = {}; v.selection[x.chemin] = x; }
        ev.dataTransfer.setData('application/x-f103', JSON.stringify({ volet: v.n, elements: choix(v) }));
        ev.dataTransfer.effectAllowed = 'copyMove';
      });
      v.lignes.appendChild(tr);
    });
    var n = liste.length, total = ((v.liste && v.liste.entrees) || []).length;
    var texte = v.recherche ? (v.recherche.texte || '') : (v.filtre ? n + ' sur ' + total + ' élément' + (total > 1 ? 's' : '') + ' (filtre)' :
      (total ? total + ' élément' + (total > 1 ? 's' : '') : (v.liste.chemin ? 'Dossier vide.' : 'Aucune racine visible.')));
    if (v.liste.tronque && !v.recherche) { texte += ' Liste limitée aux 2 000 premiers éléments : utilisez le filtre ou la recherche.'; }
    dire(v.etat, texte);
  }

  function choix(v) {
    var tout = visibles(v);
    return tout.filter(function (x) { return v.selection[x.chemin]; }).map(function (x) { return { chemin: x.chemin, etag: x.etag, nom: x.nom, dossier: x.dossier }; });
  }
  function unSeul(v, fichier) {
    var c = choix(v);
    if (c.length !== 1) { dire(v.etat, 'Sélectionnez un seul élément.'); return null; }
    if (fichier && c[0].dossier) { dire(v.etat, 'Cette action porte sur un fichier.'); return null; }
    return c[0];
  }
  function noteDe(v, chemin) { return ((v.liste && v.liste.notes) || {})[chemin] || null; }
  function recharger(source) { volets.forEach(function (x) { if (onglet(x).source === source) { charger(x); } }); }

  function majFavori(v) {
    var b = v.noeud.querySelector('[data-f103="favori"]'), o = onglet(v);
    var oui = (cfg.favoris || []).some(function (f) { return f.source === o.source && f.chemin === o.chemin; });
    b.setAttribute('aria-pressed', oui ? 'true' : 'false'); b.textContent = (oui ? '★ ' : '☆ ') + 'Favori'; b.disabled = !o.chemin;
  }
  function rendreFavoris() {
    var ul = racine.querySelector('[data-f103-favoris]');
    if (!ul) { return; }
    vider(ul);
    if (!(cfg.favoris || []).length) { ul.appendChild(el('li', { 'class': 'f103-vide' }, 'Aucun favori : ouvrez un dossier puis « ☆ Favori ».')); return; }
    cfg.favoris.forEach(function (f) {
      var li = el('li'); li.appendChild(el('button', { type: 'button', 'class': 'ax-btn ghost', 'data-f103-aller': f.chemin, 'data-source': f.source }, f.nom || f.chemin));
      ul.appendChild(li);
    });
  }

  /* ---------------------------------------------------------------- recherche bornée */
  function chercher(v, suite) {
    var o = onglet(v), champ = v.cherche.querySelector('input[name=q]'), q = champ.value.trim();
    var stop = v.cherche.querySelector('[data-f103="chercher-stop"]'), plus = v.cherche.querySelector('[data-f103="chercher-suite"]');
    if (!o.chemin) { dire(v.etat, 'Ouvrez d’abord un dossier.'); return; }
    if (q.length < 2) { dire(v.etat, 'Tapez au moins deux caractères.'); champ.focus(); return; }
    if (v.controle) { v.controle.abort(); }
    v.controle = window.AbortController ? new AbortController() : null;
    var avant = suite && v.recherche ? v.recherche : { q: q, resultats: [], curseur: null };
    stop.hidden = false; plus.hidden = true;
    dire(v.etat, 'Recherche de « ' + q + ' » dans les sous-dossiers…');
    poster('chercher', { s: o.source, p: o.chemin, q: q, curseur: suite ? avant.curseur : null }, v.controle && v.controle.signal).then(function (r) {
      stop.hidden = true;
      if (!r.ok) { dire(v.etat, erreur(r)); return; }
      var vus = {}; avant.resultats.forEach(function (x) { vus[x.chemin] = 1; });
      var res = avant.resultats.concat((r.j.resultats || []).filter(function (x) { return !vus[x.chemin]; }));
      v.recherche = { q: q, resultats: res, curseur: r.j.curseur,
                      texte: res.length + ' résultat' + (res.length > 1 ? 's' : '') + ' pour « ' + q + ' »' + (r.j.partiel ? ' — recherche partielle : « Continuer la recherche » lit les dossiers suivants.' : ' — recherche terminée.') };
      plus.hidden = !r.j.partiel;
      v.selection = {}; rendre(v);
    }, function (err) { stop.hidden = true; dire(v.etat, err && err.name === 'AbortError' ? 'Recherche arrêtée.' : 'Serveur injoignable : recherche interrompue.'); });
  }

  /* ---------------------------------------------------------------- clics et clavier */
  function surClic(v, ev) {
    var t = ev.target;
    var on = t.closest('[data-f103-onglet]');
    if (on) { v.actif = +on.getAttribute('data-f103-onglet'); sauver(); charger(v); return; }
    var fe = t.closest('[data-f103-fermer-onglet]');
    if (fe) { var i = +fe.getAttribute('data-f103-fermer-onglet'); v.onglets.splice(i, 1); v.actif = Math.min(v.actif, v.onglets.length - 1); sauver(); charger(v); return; }
    var ouv = t.closest('[data-f103-ouvrir]');
    if (ouv && ouv.getAttribute('data-f103-ouvrir')) { aller(v, onglet(v).source, ouv.getAttribute('data-f103-ouvrir')); return; }
    var mi = t.closest('[data-f103-aller]');
    if (mi) { aller(v, mi.getAttribute('data-source') || onglet(v).source, mi.getAttribute('data-f103-aller')); return; }
    var c = t.closest('[data-f103-choisir]');
    if (c) { basculer(v, c.getAttribute('data-f103-choisir'), c.checked); return; }
    var cn = t.closest('[data-f103-choisir-nom]');
    if (cn && cn.getAttribute('data-f103-choisir-nom')) { var ch = cn.getAttribute('data-f103-choisir-nom'); basculer(v, ch, !v.selection[ch]); return; }
    var a = t.closest('[data-f103]');
    if (a && !a.disabled) { action(v, a.getAttribute('data-f103')); }
  }
  function basculer(v, chemin, oui) {
    var x = visibles(v).filter(function (y) { return y.chemin === chemin; })[0];
    if (!x) { return; }
    if (oui) { v.selection[chemin] = x; } else { delete v.selection[chemin]; }
    var tr = v.lignes.querySelector('tr[data-chemin="' + (window.CSS && CSS.escape ? CSS.escape(chemin) : chemin.replace(/"/g, '\\"')) + '"]');
    if (tr) { tr.classList.toggle('f103-choisi', !!oui); tr.setAttribute('aria-selected', oui ? 'true' : 'false'); var cb = tr.querySelector('input'); if (cb) { cb.checked = !!oui; } }
  }
  function surTouche(v, ev) {
    var tr = ev.target.closest && ev.target.closest('tbody tr');
    if (ev.target.closest('dialog')) { return; }
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'a' || ev.key === 'A') && tr) {
      ev.preventDefault(); visibles(v).forEach(function (x) { v.selection[x.chemin] = x; }); rendre(v); return;
    }
    if (ev.key === 'F2' && tr) { ev.preventDefault(); action(v, 'renommer'); return; }
    if (ev.key === 'Delete' && tr) { ev.preventDefault(); action(v, 'supprimer'); return; }
    if (!tr || ev.target.tagName === 'INPUT' && ev.target.type !== 'checkbox') { return; }
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      var suiv = ev.key === 'ArrowDown' ? tr.nextElementSibling : tr.previousElementSibling;
      if (suiv) { suiv.focus(); }
    } else if (ev.key === 'Enter' && ev.target === tr) {
      ev.preventDefault();
      var ch = tr.getAttribute('data-f103-ouvrir') || ((visibles(v)[+tr.getAttribute('data-i')] || {}).dossier ? tr.getAttribute('data-chemin') : '');
      if (ch) { aller(v, onglet(v).source, ch); }
    } else if (ev.key === ' ' && ev.target === tr && tr.getAttribute('data-chemin')) {
      ev.preventDefault(); var c = tr.getAttribute('data-chemin'); basculer(v, c, !v.selection[c]);
    }
  }

  /* ---------------------------------------------------------------- actions */
  function action(v, nom) {
    var o = onglet(v);
    if (nom === 'onglet') {
      if (v.onglets.length >= 8) { dire(v.etat, 'Huit onglets au plus par volet.'); return; }
      v.onglets.push({ source: o.source, chemin: o.chemin }); v.actif = v.onglets.length - 1; sauver(); charger(v); return;
    }
    if (nom === 'chercher-stop') { if (v.controle) { v.controle.abort(); } return; }
    if (nom === 'chercher-suite') { chercher(v, true); return; }
    if (nom === 'favori') { return favori(v); }
    if (!o.chemin && nom !== 'proprietes') { dire(v.etat, 'Ouvrez d’abord un dossier.'); return; }
    if (nom === 'dossier') { return nouveauDossier(v); }
    if (nom === 'renommer') { return renommer(v); }
    if (nom === 'copier' || nom === 'deplacer') {
      var cible = autre(v);
      if (!cible) { dire(v.etat, 'Un seul volet est affiché : glissez ou utilisez Nextcloud.'); return; }
      var c = choix(v); if (!c.length) { dire(v.etat, 'Sélectionnez au moins un élément.'); return; }
      if (nom === 'copier') { return transferer(v, cible, c, 'copier'); }
      return confirmer('Déplacer ' + (c.length > 1 ? c.length + ' éléments' : '« ' + c[0].nom + ' »') + ' vers « ' + (onglet(cible).titre || onglet(cible).chemin || '?') + ' » ?')
        .then(function (oui) { if (oui) { transferer(v, cible, c, 'deplacer'); } });
    }
    if (nom === 'wordcraft' || nom === 'traitement' || nom === 'application') {   // 5.6.108 (lot W) : page dédiée (droits, garde et contrôle relus par le serveur)
      var doc = unSeul(v, true); if (!doc) { return; }
      if (nom === 'application' && !/\.pdf$/i.test(doc.nom)) { dire(v.etat, 'Cette action porte sur un PDF.'); return; }
      if (nom !== 'application' && !/\.docx$/i.test(doc.nom)) { dire(v.etat, 'Cette action porte sur un document Word (.docx).'); return; }
      var cible108 = (cfg.editeur || {})[nom];
      if (cible108) { location.href = cible108 + '?s=' + encodeURIComponent(o.source) + '&p=' + encodeURIComponent(doc.chemin); }
      return;
    }
    if (nom === 'zip') { return zip(v); }
    if (nom === 'extraire') { return extraire(v); }
    if (nom === 'proprietes') { return proprietes(v); }
    if (nom === 'versions') { return versions(v); }
    if (nom === 'note') { return noter(v); }
    if (nom === 'verrou') { return verrou(v); }
    if (nom === 'supprimer') { return supprimer(v); }
  }

  function favori(v) {
    var o = onglet(v); if (!o.chemin) { return; }
    var oui = (cfg.favoris || []).some(function (f) { return f.source === o.source && f.chemin === o.chemin; });
    poster('favori', { action: oui ? 'retirer' : 'ajouter', s: o.source, p: o.chemin, cle: cle() }).then(function (r) {
      dire(v.etat, erreur(r));
      if (!r.ok) { return; }
      lire('favoris').then(function (f) { if (f.ok) { cfg.favoris = f.j.favoris || []; rendreFavoris(); volets.forEach(majFavori); } });
    }, function () { dire(v.etat, 'Serveur injoignable : rien n’a été fait.'); });
  }

  function nouveauDossier(v) {
    var d = ouvrirDlg('f103-dlg-dossier'); if (!d) { return; }
    var f = d.querySelector('form'); f.reset();
    f.onsubmit = function (ev) {
      ev.preventDefault();
      var st = d.querySelector('.f103-statut');
      poster('dossier', { s: onglet(v).source, p: onglet(v).chemin, nom: f.elements.nom.value.trim(), cle: cle() }).then(function (r) {
        dire(st, erreur(r)); if (r.ok) { fermerDlg(d); dire(v.etat, r.j.message); recharger(onglet(v).source); }
      }, function () { dire(st, 'Serveur injoignable : aucun dossier créé.'); });
    };
  }

  function transfertDemande(depart, arrivee, elements) {
    if (!elements.length || !onglet(arrivee).chemin) { dire(arrivee.etat, 'Ouvrez d’abord un dossier de destination.'); return; }
    var d = ouvrirDlg('f103-dlg-transfert'); if (!d) { return; }
    d.querySelector('[data-f103-question]').textContent = (elements.length > 1 ? elements.length + ' éléments' : '« ' + elements[0].nom + ' »') +
      ' vers « ' + (onglet(arrivee).titre || onglet(arrivee).chemin) + ' » (' + libelleSource(onglet(arrivee).source) + ') : copier ou déplacer ? ' +
      'Un nom déjà pris n’est jamais remplacé.';
    Array.prototype.forEach.call(d.querySelectorAll('[data-f103-choix]'), function (b) {
      b.onclick = function () {
        var mode = b.getAttribute('data-f103-choix');
        fermerDlg(d);
        if (mode === 'deplacer') {
          confirmer('Confirmer le déplacement ? Les éléments quittent « ' + (onglet(depart).titre || onglet(depart).chemin) + ' ».').then(function (oui) {
            if (oui) { transferer(depart, arrivee, elements, 'deplacer'); }
          });
        } else { transferer(depart, arrivee, elements, 'copier'); }
      };
    });
  }

  function transferer(depart, arrivee, elements, mode) {
    var src = onglet(depart), dst = onglet(arrivee), faits = 0, i = 0;
    function suivant() {
      if (i >= elements.length) {
        dire(depart.etat, faits + ' élément' + (faits > 1 ? 's' : '') + (mode === 'copier' ? ' copié' : ' déplacé') + (faits > 1 ? 's' : '') + '.');
        recharger(src.source); if (dst.source !== src.source) { recharger(dst.source); }
        return;
      }
      var x = elements[i]; i += 1;
      poster('transferer', { mode: mode, s: src.source, p: x.chemin, etag: x.etag || '', source_cible: dst.source, dossier_cible: dst.chemin, cle: cle() }).then(function (r) {
        if (!r.ok) { dire(depart.etat, '« ' + x.nom + ' » : ' + erreur(r) + (faits ? ' (' + faits + ' fait' + (faits > 1 ? 's' : '') + ' avant)' : '')); recharger(src.source); return; }
        faits += 1; suivant();
      }, function () { dire(depart.etat, 'Serveur injoignable : arrêt après ' + faits + ' élément(s).'); });
    }
    dire(depart.etat, mode === 'copier' ? 'Copie en cours…' : 'Déplacement en cours…');
    suivant();
  }

  function renommer(v) {
    var elements = choix(v);
    if (!elements.length) { dire(v.etat, 'Sélectionnez les éléments à renommer.'); return; }
    var d = ouvrirDlg('f103-dlg-renommer'); if (!d) { return; }
    var f = d.querySelector('form'), corps = d.querySelector('[data-f103-apercu]'), st = d.querySelector('.f103-statut');
    var bouton = f.querySelector('button[type=submit]'), ordre = elements.slice(), apercu = null;
    function regle() {
      var mode = (f.querySelector('input[name=mode]:checked') || {}).value || 'pieces';
      return { mode: mode, debut: f.elements.debut.value, zeros: f.elements.zeros.value, garder_nom: f.elements.garder_nom.checked,
               cherche: f.elements.cherche.value, remplace: f.elements.remplace.value, prefixe: f.elements.prefixe.value, suffixe: f.elements.suffixe.value };
    }
    function modes() {
      var m = regle().mode;
      Array.prototype.forEach.call(f.querySelectorAll('.f103-regle'), function (z) { z.hidden = z.getAttribute('data-mode') !== m; });
    }
    function lignes(res) {
      vider(corps);
      ordre.forEach(function (x, i) {
        var l = res ? res.lignes[i] : null;
        var tr = el('tr', l && l.conflit && l.conflit !== 'inchange' ? { 'class': 'f103-conflit' } : {});
        var td = el('td', { 'class': 'f103-ordre' });
        td.appendChild(el('button', { type: 'button', 'class': 'ax-btn ghost', 'data-haut': String(i), 'aria-label': 'Monter ' + x.nom }, '↑'));
        td.appendChild(el('button', { type: 'button', 'class': 'ax-btn ghost', 'data-bas': String(i), 'aria-label': 'Descendre ' + x.nom }, '↓'));
        tr.appendChild(td);
        tr.appendChild(el('td', {}, x.nom));
        tr.appendChild(el('td', {}, l ? l.apres : '…'));
        tr.appendChild(el('td', {}, l ? (l.message || 'OK') : ''));
        corps.appendChild(tr);
      });
    }
    function perime() { apercu = null; bouton.disabled = true; lignes(null); }
    function voir() {
      dire(st, 'Calcul de l’aperçu…');
      poster('apercu', { s: onglet(v).source, p: onglet(v).chemin, elements: ordre.map(function (x) { return { chemin: x.chemin, etag: x.etag }; }), regle: regle() }).then(function (r) {
        if (!r.ok) { dire(st, erreur(r)); return; }
        apercu = r.j; lignes(r.j); bouton.disabled = !r.j.ok;
        dire(st, r.j.conflits ? r.j.conflits + ' conflit' + (r.j.conflits > 1 ? 's' : '') + ' : rien ne sera renommé tant qu’il en reste.' : r.j.a_renommer + ' élément(s) à renommer.');
      }, function () { dire(st, 'Serveur injoignable.'); });
    }
    f.onchange = function (ev) { if (ev.target.name === 'mode') { modes(); } perime(); };
    f.oninput = perime;
    corps.onclick = function (ev) {
      var h = ev.target.closest('[data-haut]'), b = ev.target.closest('[data-bas]'), i, j;
      if (h) { i = +h.getAttribute('data-haut'); j = i - 1; } else if (b) { i = +b.getAttribute('data-bas'); j = i + 1; } else { return; }
      if (j < 0 || j >= ordre.length) { return; }
      var t = ordre[i]; ordre[i] = ordre[j]; ordre[j] = t; perime();
      var cible = corps.querySelector(h ? '[data-haut="' + j + '"]' : '[data-bas="' + j + '"]'); if (cible) { cible.focus(); }
    };
    d.querySelector('[data-f103-voir]').onclick = voir;
    f.onsubmit = function (ev) {
      ev.preventDefault();
      if (!apercu || !apercu.ok) { return; }
      bouton.disabled = true; dire(st, 'Renommage en cours…');
      poster('renommer', { s: onglet(v).source, p: onglet(v).chemin, elements: ordre.map(function (x) { return { chemin: x.chemin, etag: x.etag }; }), regle: regle(), cle: cle() }).then(function (r) {
        if (!r.ok) { if (r.j && r.j.lignes) { lignes({ lignes: r.j.lignes }); } dire(st, erreur(r)); return; }
        dire(st, r.j.message); dire(v.etat, r.j.message); recharger(onglet(v).source);
        if (r.j.ok) { fermerDlg(d); }
      }, function () { dire(st, 'Serveur injoignable : vérifiez le dossier avant de recommencer.'); });
    };
    modes(); lignes(null); bouton.disabled = true;
  }

  function zip(v) {
    var c = choix(v); if (!c.length) { dire(v.etat, 'Sélectionnez les éléments à archiver.'); return; }
    var d = ouvrirDlg('f103-dlg-zip'); if (!d) { return; }
    var f = d.querySelector('form'); f.reset();
    f.onsubmit = function (ev) {
      ev.preventDefault();
      var st = d.querySelector('.f103-statut'); dire(st, 'Création de l’archive…');
      poster('zip', { s: onglet(v).source, p: onglet(v).chemin, chemins: c.map(function (x) { return x.chemin; }), nom: f.elements.nom.value.trim(), cle: cle() }).then(function (r) {
        dire(st, erreur(r)); if (r.ok) { fermerDlg(d); dire(v.etat, r.j.message); recharger(onglet(v).source); }
      }, function () { dire(st, 'Serveur injoignable : vérifiez le dossier.'); });
    };
  }

  function extraire(v) {
    var x = unSeul(v, true); if (!x) { return; }
    if (!/\.zip$/i.test(x.nom)) { dire(v.etat, 'Choisissez une archive .zip.'); return; }
    confirmer('Extraire « ' + x.nom + ' » dans un nouveau dossier voisin ? Une archive dangereuse (chemins sortants, bombe de décompression) est refusée sans rien écrire.').then(function (oui) {
      if (!oui) { return; }
      dire(v.etat, 'Extraction en cours…');
      poster('extraire', { s: onglet(v).source, p: x.chemin, cle: cle() }).then(function (r) { dire(v.etat, erreur(r)); recharger(onglet(v).source); },
        function () { dire(v.etat, 'Serveur injoignable : vérifiez le dossier.'); });
    });
  }

  function proprietes(v) {
    var c = choix(v), o = onglet(v);
    var x = c.length === 1 ? c[0] : (c.length === 0 && o.chemin ? { chemin: o.chemin, nom: nomDe(o.chemin), dossier: true } : null);
    if (!x) { dire(v.etat, 'Sélectionnez un seul élément.'); return; }
    var d = ouvrirDlg('f103-dlg-proprietes'); if (!d) { return; }
    var dl = d.querySelector('[data-f103-props]'), st = d.querySelector('.f103-statut'), bh = d.querySelector('[data-f103-empreinte]');
    vider(dl); dire(st, 'Lecture…');
    function afficher(p) {
      vider(dl);
      [['Nom', p.nom], ['Type', p.dossier ? 'Dossier' : (p.type || 'Inconnu')], ['Taille', p.dossier ? '—' : taille(p.taille) + ' (' + (p.taille || 0).toLocaleString('fr-FR') + ' octets)'],
       ['Modifié le', quand(p.modifie)], ['Créé le', quand(p.cree)], ['Auteur', p.auteur || 'Inconnu'], ['Emplacement', libelleSource(o.source) + ' · ' + p.chemin],
       ['Note', p.note ? ((cfg.couleurs || {})[p.note.couleur] || '') + (p.note.note ? ' — ' + p.note.note : '') : 'Aucune'],
       ['Verrou', p.verrou ? 'Verrouillé par ' + p.verrou.par : 'Aucun']].concat(p.sha256 ? [['Empreinte SHA-256', p.sha256]] : []).forEach(function (l) {
        dl.appendChild(el('dt', {}, l[0])); dl.appendChild(el('dd', l[0] === 'Empreinte SHA-256' ? { 'class': 'f103-empreinte' } : {}, l[1]));
      });
      bh.hidden = !!p.dossier;
    }
    lire('proprietes', { s: o.source, p: x.chemin }).then(function (r) { dire(st, r.ok ? '' : erreur(r)); if (r.ok) { afficher(r.j); } }, function () { dire(st, 'Serveur injoignable.'); });
    bh.onclick = function () {
      dire(st, 'Calcul de l’empreinte (lecture du fichier)…'); bh.disabled = true;
      lire('proprietes', { s: o.source, p: x.chemin, empreinte: '1' }).then(function (r) { bh.disabled = false; dire(st, r.ok ? 'Empreinte calculée.' : erreur(r)); if (r.ok) { afficher(r.j); } },
        function () { bh.disabled = false; dire(st, 'Serveur injoignable.'); });
    };
  }

  function versions(v) {
    var x = unSeul(v, true); if (!x) { return; }
    var o = onglet(v), d = ouvrirDlg('f103-dlg-versions'); if (!d) { return; }
    var corps = d.querySelector('[data-f103-versions]'), st = d.querySelector('.f103-statut');
    vider(corps); dire(st, 'Lecture des versions…');
    lire('versions', { s: o.source, p: x.chemin }).then(function (r) {
      if (!r.ok) { dire(st, erreur(r)); return; }
      if (!r.j.disponible) { dire(st, r.j.message); return; }
      dire(st, r.j.versions.length ? '' : 'Aucune version antérieure.');
      var actuel = r.j.actuel || {};
      var tr0 = el('tr', { 'class': 'f103-actuelle' });
      tr0.appendChild(el('td', {}, quand(actuel.modifie) + ' (version actuelle)')); tr0.appendChild(el('td', {}, actuel.auteur || '—'));
      tr0.appendChild(el('td', {}, taille(actuel.taille))); tr0.appendChild(el('td', {}, '—')); corps.appendChild(tr0);
      r.j.versions.forEach(function (ver) {
        var tr = el('tr');
        tr.appendChild(el('td', {}, quand(ver.date) + (ver.libelle ? ' — ' + ver.libelle : '')));
        tr.appendChild(el('td', {}, ver.auteur || '—'));
        tr.appendChild(el('td', {}, taille(ver.taille)));
        var td = el('td'); var b = el('button', { type: 'button', 'class': 'ax-btn ghost' }, 'Restaurer');
        b.onclick = function () {
          b.disabled = true; dire(st, 'Restauration…');
          poster('restaurer', { s: o.source, p: x.chemin, version: ver.id, etag: actuel.etag || '', cle: cle() }).then(function (rr) {
            dire(st, erreur(rr)); recharger(o.source); if (!rr.ok) { b.disabled = false; }
          }, function () { b.disabled = false; dire(st, 'Serveur injoignable : vérifiez le fichier.'); });
        };
        td.appendChild(b); tr.appendChild(td); corps.appendChild(tr);
      });
    }, function () { dire(st, 'Serveur injoignable.'); });
  }

  function noter(v) {
    var x = unSeul(v, false); if (!x) { return; }
    var d = ouvrirDlg('f103-dlg-note'); if (!d) { return; }
    var f = d.querySelector('form'), nt = noteDe(v, x.chemin) || {};
    f.elements.note.value = nt.note || '';
    Array.prototype.forEach.call(f.querySelectorAll('input[name=couleur]'), function (r) { r.checked = r.value === (nt.couleur || ''); });
    f.onsubmit = function (ev) {
      ev.preventDefault();
      var st = d.querySelector('.f103-statut');
      poster('note', { s: onglet(v).source, p: x.chemin, couleur: (f.querySelector('input[name=couleur]:checked') || {}).value || '', note: f.elements.note.value,
                       version: nt.version || 0, cle: cle() }).then(function (r) {
        dire(st, erreur(r)); if (r.ok) { fermerDlg(d); dire(v.etat, r.j.message); recharger(onglet(v).source); }
      }, function () { dire(st, 'Serveur injoignable : note non enregistrée.'); });
    };
  }

  function verrou(v) {
    var x = unSeul(v, true); if (!x) { return; }
    var deja = ((v.liste && v.liste.verrous) || {})[x.chemin];
    poster('verrou', { s: onglet(v).source, p: x.chemin, action: deja ? 'deverrouiller' : 'verrouiller', cle: cle() }).then(function (r) {
      dire(v.etat, erreur(r)); recharger(onglet(v).source);
    }, function () { dire(v.etat, 'Serveur injoignable : rien n’a été fait.'); });
  }

  function supprimer(v) {
    var c = choix(v); if (!c.length) { dire(v.etat, 'Sélectionnez au moins un élément.'); return; }
    if ((v.liste.capacites || []).indexOf('corbeille') < 0) { dire(v.etat, 'La suppression n’est proposée que pour Nextcloud.'); return; }
    confirmer('Mettre ' + (c.length > 1 ? c.length + ' éléments' : '« ' + c[0].nom + ' »') + ' dans la corbeille de Nextcloud ? Ils resteront restaurables depuis Nextcloud.').then(function (oui) {
      if (!oui) { return; }
      var i = 0, faits = 0;
      (function suivant() {
        if (i >= c.length) { dire(v.etat, faits + ' élément(s) mis à la corbeille.'); recharger(onglet(v).source); return; }
        var x = c[i]; i += 1;
        poster('supprimer', { s: onglet(v).source, p: x.chemin, etag: x.etag || '', cle: cle() }).then(function (r) {
          if (!r.ok) { dire(v.etat, '« ' + x.nom + ' » : ' + erreur(r)); recharger(onglet(v).source); return; }
          faits += 1; suivant();
        }, function () { dire(v.etat, 'Serveur injoignable : arrêt après ' + faits + ' élément(s).'); });
      }());
    });
  }

  /* ---------------------------------------------------------------- volets en onglets (écran étroit) */
  var bascule = racine.querySelector('.f103-bascule'), conteneur = racine.querySelector('.f103-volets');
  function montrer(n) {
    if (!conteneur) { return; }
    conteneur.setAttribute('data-actif', String(n));
    Array.prototype.forEach.call(racine.querySelectorAll('[data-f103-bascule]'), function (b) {
      var oui = b.getAttribute('data-f103-bascule') === String(n);
      b.setAttribute('aria-selected', oui ? 'true' : 'false'); b.tabIndex = oui ? 0 : -1;
    });
  }
  if (bascule) {
    bascule.addEventListener('click', function (ev) { var b = ev.target.closest('[data-f103-bascule]'); if (b) { montrer(+b.getAttribute('data-f103-bascule')); } });
    bascule.addEventListener('keydown', function (ev) {
      if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') { return; }
      var n = conteneur.getAttribute('data-actif') === '1' ? 0 : 1; montrer(n);
      var b = racine.querySelector('[data-f103-bascule="' + n + '"]'); if (b) { b.focus(); }
    });
    montrer(0);
  }
  racine.addEventListener('click', function (ev) {
    var f = ev.target.closest('.f103-favoris [data-f103-aller]');
    if (!f || !volets.length) { return; }
    var v = volets[conteneur && conteneur.getAttribute('data-actif') === '1' && volets[1] ? 1 : 0];
    aller(v, f.getAttribute('data-source'), f.getAttribute('data-f103-aller'));
  });

  /* ---------------------------------------------------------------- réglages du poste */
  var poste = racine.querySelector('[data-f103-form="poste"]');
  if (poste) {
    var liste = poste.querySelector('[data-f103-poste-liste]');
    poste.addEventListener('click', function (ev) {
      if (ev.target.closest('[data-f103-retirer]')) { ev.target.closest('li').remove(); return; }
      if (ev.target.closest('[data-f103-ajouter]')) {
        var li = el('li', { 'class': 'f103-poste-ligne' });
        var l1 = el('label', { 'class': 'f103-champ' }, 'Chemin du dossier '); l1.appendChild(el('input', { name: 'chemin', maxlength: '400' })); li.appendChild(l1);
        var l2 = el('label', { 'class': 'f103-champ' }, 'Nom affiché '); l2.appendChild(el('input', { name: 'nom', maxlength: '80' })); li.appendChild(l2);
        var l3 = el('label'); l3.appendChild(el('input', { type: 'checkbox', name: 'surveille' })); l3.appendChild(document.createTextNode(' Garder une version à chaque changement')); li.appendChild(l3);
        li.appendChild(el('button', { type: 'button', 'class': 'ax-btn ghost', 'data-f103-retirer': '' }, 'Retirer'));
        liste.appendChild(li); li.querySelector('input').focus(); return;
      }
      if (ev.target.closest('[data-f103-scruter]')) {
        var st0 = poste.querySelector('.f103-statut'); dire(st0, 'Vérification du poste…');
        poster('poste_scruter', { cle: cle() }).then(function (r) {
          dire(st0, r.ok ? r.j.message + ' ' + r.j.bilan.versions + ' nouvelle(s) version(s), ' + r.j.bilan.examines + ' fichier(s) examiné(s).' : erreur(r));
        }, function () { dire(st0, 'Serveur injoignable.'); });
      }
    });
    poste.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var st = poste.querySelector('.f103-statut');
      var dossiers = Array.prototype.map.call(liste.querySelectorAll('li'), function (li) {
        return { chemin: li.querySelector('[name=chemin]').value.trim(), nom: li.querySelector('[name=nom]').value.trim(), surveille: li.querySelector('[name=surveille]').checked };
      }).filter(function (x) { return x.chemin; });
      poster('poste', { dossiers: dossiers, reglages: { plafond_mo: poste.elements.plafond_mo.value, jours: poste.elements.jours.value, intervalle: poste.elements.intervalle.value },
                        cle: cle() }).then(function (r) { dire(st, erreur(r)); if (r.ok) { window.setTimeout(function () { window.location.reload(); }, 800); } },
        function () { dire(st, 'Serveur injoignable : réglages non enregistrés.'); });
    });
  }

  /* ---------------------------------------------------------------- démarrage */
  var gardes = memoire();
  Array.prototype.forEach.call(racine.querySelectorAll('.f103-volet'), function (noeud, n) { initVolet(noeud, n, gardes); });
  volets.forEach(charger);
}());
