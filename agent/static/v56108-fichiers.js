/* AxiorHub 5.6.108 (lot F) — onglet Fichiers du dossier : « Ajouter un fichier » (sélection ou glisser-déposer), boutons PDF de l’aperçu,
   page « Remplir ce formulaire » et boîte à outils PDF (annoter, pages, découper, fusionner, numéroter, caviarder, compresser).
   Aucun HTML venant du serveur n’est injecté : tout est écrit avec textContent. Chaque commande garde sa clé d’idempotence tant que son
   contenu ne change pas : un double clic ou un nouvel essai après une coupure rejoue la même commande, sans second fichier.
   Les phrases sont entières, avec « # » (nombre) et « %s » (donnée) : le dictionnaire des écrans les traduit telles quelles (v5634t.js). */
(function () {
  'use strict';
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var API = prefix + '/api440/m56108/fichiers-dossier/';

  function fmt(modele) {   // « # » : nombre, « %s » : donnée, remplacés dans l’ordre
    var args = Array.prototype.slice.call(arguments, 1), i = 0;
    return modele.replace(/#|%s/g, function (m) { return i < args.length ? String(args[i++]) : m; });
  }
  function nouvelleCle() { return 'f108' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12); }
  function el(tag, texte, cls) { var n = document.createElement(tag); if (texte !== undefined) n.textContent = texte; if (cls) n.className = cls; return n; }
  function statut(zone, texte, erreur) {
    if (!zone) return;
    zone.textContent = texte || '';
    zone.classList.toggle('f108-erreur', !!erreur);
  }
  function envoyer(url, options) {
    options.credentials = 'same-origin';
    options.headers = Object.assign({ 'X-CSRF-Token': csrf }, options.headers || {});
    return fetch(url, options).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, statut: r.status, data: d }; });
    });
  }
  function json(op, corps) {
    return envoyer(API + op, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corps) });
  }
  var cles = {};
  function cleDe(op, contenu) {   // même contenu : même clé (rejeu sans effet double) ; contenu changé : nouvelle commande
    var empreinte = JSON.stringify(contenu);
    if (!cles[op] || cles[op].contenu !== empreinte) cles[op] = { contenu: empreinte, cle: nouvelleCle() };
    return cles[op].cle;
  }
  function erreurDe(r) { return (r.data && (r.data.message || r.data.error)) || fmt('Erreur #.', r.statut); }
  var RESEAU = 'Réseau indisponible : rien n’est perdu, réessayez (la même demande sera reprise).';

  /* ---------------------------------------------------------------------------------------------- onglet Fichiers : ajouter, déposer */
  var depot = document.querySelector('[data-f108-depot]');
  if (depot) {
    var dossier = depot.getAttribute('data-f108-d');
    var barre = depot.querySelector('[data-f108-barre]');
    var zone = barre && barre.querySelector('[data-f108-statut]');
    var liste = barre && barre.querySelector('[data-f108-envois]');
    var entree = barre && barre.querySelector('[data-f108-entree]');
    var envoisCles = new WeakMap();
    var envoyerFichiers = function (fichiers) {
      if (!fichiers || !fichiers.length) return;
      liste.hidden = false;
      var reussis = 0, finis = 0, total = fichiers.length;
      statut(zone, fmt('Envoi de # fichier(s)…', total));
      Array.prototype.forEach.call(fichiers, function (f) {
        var ligne = el('li', fmt('%s : envoi…', f.name));
        liste.append(ligne);
        if (!envoisCles.has(f)) envoisCles.set(f, nouvelleCle());
        var q = 'd=' + encodeURIComponent(dossier) + '&nom=' + encodeURIComponent(f.name) + '&cle=' + encodeURIComponent(envoisCles.get(f));
        f.arrayBuffer().then(function (buf) {
          return envoyer(API + 'ajouter?' + q, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: buf });
        }).then(function (r) {
          finis += 1;
          if (r.ok) { reussis += 1; ligne.textContent = (r.data && r.data.message) || fmt('Fichier ajouté au dossier : %s.', f.name); ligne.className = 'f108-ok'; }
          else { ligne.textContent = fmt('%s : %s', f.name, erreurDe(r)); ligne.className = 'f108-erreur'; }
        }).catch(function () { finis += 1; ligne.textContent = fmt('%s : %s', f.name, RESEAU); ligne.className = 'f108-erreur'; })
          .then(function () {
            if (finis < total) return;
            statut(zone, fmt('# fichier(s) ajouté(s) sur #.', reussis, total), reussis < total);
            if (reussis && /[?&]sous=repertoire/.test(location.search)) window.setTimeout(function () { location.reload(); }, 1200);
          });
      });
    };
    if (entree) entree.addEventListener('change', function () { envoyerFichiers(entree.files); entree.value = ''; });
    var profondeur = 0;
    depot.addEventListener('dragenter', function (ev) {
      if (!ev.dataTransfer || Array.prototype.indexOf.call(ev.dataTransfer.types || [], 'Files') < 0) return;
      ev.preventDefault(); profondeur += 1; depot.classList.add('f108-survol');
    });
    depot.addEventListener('dragover', function (ev) {
      if (!ev.dataTransfer || Array.prototype.indexOf.call(ev.dataTransfer.types || [], 'Files') < 0) return;
      ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy';
    });
    depot.addEventListener('dragleave', function () { profondeur = Math.max(0, profondeur - 1); if (!profondeur) depot.classList.remove('f108-survol'); });
    depot.addEventListener('drop', function (ev) {
      if (!ev.dataTransfer || !ev.dataTransfer.files || !ev.dataTransfer.files.length) return;
      ev.preventDefault(); profondeur = 0; depot.classList.remove('f108-survol');
      envoyerFichiers(ev.dataTransfer.files);
    });
    // sélection de PDF → boîte à outils (fusion, etc.)
    var bouton = barre && barre.querySelector('[data-f108-selection]');
    var choisis = function () { return Array.prototype.map.call(depot.querySelectorAll('[data-f108-choix]:checked'), function (c) { return c.value; }); };
    depot.addEventListener('change', function (ev) {
      if (!ev.target.matches || !ev.target.matches('[data-f108-choix]') || !bouton) return;
      var n = choisis().length;
      bouton.disabled = n === 0;
      bouton.textContent = n ? fmt('Outils PDF pour la sélection (#)', n) : 'Outils PDF pour la sélection';
    });
    if (bouton) bouton.addEventListener('click', function () {
      var p = choisis().slice(0, 20), q = 'd=' + encodeURIComponent(dossier);
      p.forEach(function (c, i) { q += '&' + (i ? 'p' + (i + 1) : 'p') + '=' + encodeURIComponent(c); });
      if (p.length) location.assign(bouton.getAttribute('data-f108-outils-url') + '?' + q);
    });
  }

  /* ---------------------------------------------------------------------------------------------- aperçu du dossier : boutons d'un PDF */
  var pane = document.getElementById('d79-preview');
  if (pane) {
    var dl = pane.querySelector('[data-d79-dl]'), remplir = pane.querySelector('[data-f108-apercu-remplir]'), outils = pane.querySelector('[data-f108-apercu-outils]');
    var maj = function () {
      if (!dl || !remplir || !outils) return;
      var montrer = false;
      try {
        var u = new URL(dl.href, location.href);
        var m = u.searchParams.get('matter') || '', p = u.searchParams.get('path') || '';
        if (u.origin === location.origin && m && /\.pdf$/i.test(p)) {
          remplir.href = prefix + '/dossier/remplir-pdf?d=' + encodeURIComponent(m) + '&p=' + encodeURIComponent(p);
          outils.href = prefix + '/dossier/outils-pdf?d=' + encodeURIComponent(m) + '&p=' + encodeURIComponent(p);
          montrer = true;
        }
      } catch (err) { montrer = false; }
      remplir.hidden = outils.hidden = !montrer;
    };
    if (dl) new MutationObserver(maj).observe(dl, { attributes: true, attributeFilter: ['href'] });
    var titre = pane.querySelector('[data-d79-title]');
    if (titre) new MutationObserver(function () { if (remplir) remplir.hidden = true; if (outils) outils.hidden = true; window.setTimeout(maj, 0); })
      .observe(titre, { childList: true, characterData: true, subtree: true });
  }

  /* ---------------------------------------------------------------------------------------------- PDF.js (même copie que la visionneuse 5.6.82) */
  var pdfLib = null;
  function versionne(nom) {
    var lien = document.querySelector('link[href*="/static/"]'), v = lien ? /[?&]v=([^&]+)/.exec(lien.href) : null;
    return prefix + '/static/' + nom + (v ? '?v=' + v[1] : '');
  }
  function chargerPdf() {
    if (pdfLib) return pdfLib;
    pdfLib = new Promise(function (ok, ko) {
      if (window.pdfjsLib) { ok(window.pdfjsLib); return; }
      var s = document.createElement('script'); s.src = versionne('pdfjs5682.js');
      s.onload = function () { window.pdfjsLib.GlobalWorkerOptions.workerSrc = versionne('pdf.worker5682.js'); ok(window.pdfjsLib); };
      s.onerror = ko; document.head.append(s);
    });
    return pdfLib;
  }

  /* ---------------------------------------------------------------------------------------------- Remplir ce formulaire */
  var formulaire = document.querySelector('[data-f108-remplir]');
  if (formulaire) {
    var apercu = document.querySelector('[data-f108-apercu]');
    if (apercu) {   // après tous les scripts différés : la visionneuse PDF.js de v5682.js est alors chargée
      document.addEventListener('DOMContentLoaded', function () {
        apercu.replaceChildren();
        var v = { url: apercu.getAttribute('data-f108-apercu'), title: apercu.getAttribute('data-f108-titre') };
        if (!(window.ax82Pdf && window.ax82Pdf(apercu, v, 1))) {
          var cadre = document.createElement('iframe'); cadre.src = v.url; cadre.title = fmt('Aperçu : %s', v.title); cadre.className = 'd79-frame'; apercu.append(cadre);
        }
      });
    }
    var allerPage = function (n) {
      var num = apercu && apercu.querySelector('input[type=number]');
      if (num && String(num.value) !== String(n)) { num.value = n; num.dispatchEvent(new Event('change')); }
    };
    formulaire.addEventListener('focusin', function (ev) {
      var c = ev.target.closest && ev.target.closest('[data-f108-page]');
      if (c) allerPage(c.getAttribute('data-f108-page'));
    });
    var utiliser = function (bouton) {
      var cible = document.getElementById(bouton.getAttribute('data-f108-utiliser'));
      var valeur = bouton.parentNode.querySelector('[data-f108-prop-valeur]');
      if (!cible || !valeur || cible.disabled) return;
      var texte = valeur.textContent;
      if (cible.type === 'date' && /^\d{2}\/\d{2}\/\d{4}$/.test(texte)) texte = texte.split('/').reverse().join('-');
      cible.value = texte;
      cible.dispatchEvent(new Event('input', { bubbles: true }));
      bouton.textContent = 'Utilisée';
    };
    formulaire.addEventListener('click', function (ev) {
      var b = ev.target.closest && ev.target.closest('[data-f108-utiliser]');
      if (b) { utiliser(b); return; }
      if (ev.target.closest && ev.target.closest('[data-f108-tout]')) formulaire.querySelectorAll('[data-f108-utiliser]').forEach(utiliser);
    });
    formulaire.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (formulaire.getAttribute('aria-busy') === 'true') return;
      var valeurs = {};
      formulaire.querySelectorAll('[data-f108-champ]').forEach(function (c) {
        var nom = c.getAttribute('data-f108-champ');
        if (c.tagName === 'FIELDSET') {
          var coche = c.querySelector('input[type=radio]:checked');
          if (coche && !coche.disabled) valeurs[nom] = coche.value;
          return;
        }
        if (c.disabled) return;
        if (c.type === 'checkbox') valeurs[nom] = c.checked;
        else if (c.multiple) valeurs[nom] = Array.prototype.filter.call(c.options, function (o) { return o.selected; }).map(function (o) { return o.value; });
        else if (c.value !== '') valeurs[nom] = c.value;
      });
      var aplatir = !!(formulaire.querySelector('[data-f108-aplatir]') || {}).checked;
      var corps = { d: formulaire.getAttribute('data-f108-d'), p: formulaire.getAttribute('data-f108-p'), empreinte: formulaire.getAttribute('data-f108-empreinte'),
                    valeurs: valeurs, aplatir: aplatir };
      corps.cle = cleDe('remplir', corps);
      var zone = formulaire.querySelector('[data-f108-statut]');
      formulaire.setAttribute('aria-busy', 'true');
      statut(zone, 'Enregistrement de la copie…');
      json('remplir', corps).then(function (r) {
        formulaire.removeAttribute('aria-busy');
        if (!r.ok) { statut(zone, erreurDe(r), true); return; }
        zone.replaceChildren(el('span', r.data.message || 'Copie enregistrée.'), document.createTextNode(' '));
        var lien = el('a', 'Voir les fichiers du dossier'); lien.href = formulaire.getAttribute('data-f108-retour'); zone.append(lien);
        zone.classList.remove('f108-erreur');
      }).catch(function () { formulaire.removeAttribute('aria-busy'); statut(zone, RESEAU, true); });
    });
  }

  /* ---------------------------------------------------------------------------------------------- boîte à outils PDF */
  var boite = document.querySelector('[data-f108-boite]');
  if (!boite) return;
  var donnees = {};
  try { donnees = JSON.parse(boite.getAttribute('data-f108-donnees') || '{}'); } catch (err) { donnees = {}; }
  var zoneStatut = boite.querySelector('[data-f108-statut]');
  var source = (donnees.sources || [])[0] || {};
  var feuille = document.querySelector('[data-f108-feuille]'), canvas = document.querySelector('[data-f108-canvas]'), calque = document.querySelector('[data-f108-calque]');
  var numero = document.querySelector('[data-f108-numero]');
  var doc = null, page = 1, traces = [], zones = [], ongletActif = 'annoter', imageJpeg = '';

  // onglets (clavier : flèches gauche et droite)
  var onglets = Array.prototype.slice.call(boite.querySelectorAll('[data-f108-onglet]'));
  var choisir = function (b, focus) {
    onglets.forEach(function (x) {
      var actif = x === b;
      x.setAttribute('aria-selected', actif ? 'true' : 'false'); x.tabIndex = actif ? 0 : -1;
      document.getElementById(x.getAttribute('aria-controls')).hidden = !actif;
    });
    ongletActif = b.getAttribute('data-f108-onglet');
    if (focus) b.focus();
    dessiner();
  };
  onglets.forEach(function (b, i) {
    b.addEventListener('click', function () { choisir(b); });
    b.addEventListener('keydown', function (ev) {
      var d = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0;
      if (d) { ev.preventDefault(); choisir(onglets[(i + d + onglets.length) % onglets.length], true); }
    });
  });

  // visionneuse
  function rendre() {
    if (!doc) return;
    page = Math.min(Math.max(1, page), doc.numPages);
    if (numero) numero.textContent = fmt('Page # sur #', page, doc.numPages);
    doc.getPage(page).then(function (p) {
      var largeur = Math.min(900, Math.max(320, feuille.parentNode.clientWidth - 8));
      var vp0 = p.getViewport({ scale: 1 }), echelle = largeur / vp0.width, vp = p.getViewport({ scale: echelle });
      var dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(vp.width * dpr); canvas.height = Math.floor(vp.height * dpr);
      canvas.style.width = Math.floor(vp.width) + 'px'; canvas.style.height = Math.floor(vp.height) + 'px';
      feuille.style.width = Math.floor(vp.width) + 'px'; feuille.style.height = Math.floor(vp.height) + 'px';
      var ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return p.render({ canvasContext: ctx, viewport: vp, canvas: canvas }).promise;
    }).then(dessiner).catch(function () { statut(zoneStatut, 'Aperçu indisponible : les outils restent utilisables.', true); });
  }
  if (canvas && donnees.apercu) {
    chargerPdf().then(function (lib) { return lib.getDocument({ url: donnees.apercu, isEvalSupported: false, withCredentials: true }).promise; })
      .then(function (d) { doc = d; rendre(); }).catch(function () { statut(zoneStatut, 'Aperçu indisponible : les outils restent utilisables.', true); });
  }
  var prec = document.querySelector('[data-f108-prec]'), suiv = document.querySelector('[data-f108-suiv]');
  if (prec) prec.addEventListener('click', function () { page -= 1; rendre(); });
  if (suiv) suiv.addEventListener('click', function () { page += 1; rendre(); });

  // tracés (fractions de la page affichée)
  var outil = function () { var r = boite.querySelector('input[name="f108-outil"]:checked'); return r ? r.value : 'texte'; };
  var modeZones = function () { var c = boite.querySelector('[data-f108-zone-active]'); return ongletActif === 'caviarder' && c && c.checked; };
  var LIBELLES = { texte: 'Texte libre', surligner: 'Surlignage', dessin: 'Dessin', image: 'Image', tampon: 'Tampon' };
  function dessiner() {
    if (!calque) return;
    calque.replaceChildren();
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'f108-svg'); svg.setAttribute('viewBox', '0 0 1 1'); svg.setAttribute('preserveAspectRatio', 'none');
    calque.append(svg);
    var montrer = ongletActif === 'caviarder' ? zones : (ongletActif === 'annoter' ? traces : []);
    montrer.filter(function (t) { return t.page === page; }).forEach(function (t) {
      if (t.type === 'dessin') {
        t.traits.forEach(function (trait) {
          var pl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
          pl.setAttribute('points', trait.map(function (pt) { return pt[0] + ',' + pt[1]; }).join(' '));
          pl.setAttribute('class', 'f108-trait'); pl.setAttribute('vector-effect', 'non-scaling-stroke'); svg.append(pl);
        });
        return;
      }
      var r = t.rect, d = el('div', t.type === 'texte' ? t.texte : (t.type === 'tampon' ? t.libelle : ''), 'f108-forme f108-forme-' + (t.type || 'zone'));
      d.style.left = (r[0] * 100) + '%'; d.style.top = (r[1] * 100) + '%';
      d.style.width = ((r[2] - r[0]) * 100) + '%'; d.style.height = ((r[3] - r[1]) * 100) + '%';
      calque.append(d);
    });
    lister();
  }
  function lister() {
    [[boite.querySelector('[data-f108-traces]'), traces, function (t) { return LIBELLES[t.type] || t.type; }],
     [boite.querySelector('[data-f108-zones]'), zones, function () { return 'Zone à caviarder'; }]].forEach(function (x) {
      var ul = x[0], items = x[1];
      if (!ul) return;
      ul.replaceChildren();
      items.forEach(function (t, i) {
        var li = el('li', fmt('Page # · %s', t.page, x[2](t)) + ' ');
        var b = el('button', 'Retirer', 'ax-btn ghost'); b.type = 'button';
        b.setAttribute('aria-label', fmt('Retirer : page # · %s', t.page, x[2](t)));
        b.addEventListener('click', function () { items.splice(i, 1); dessiner(); });
        li.append(b); ul.append(li);
      });
    });
  }
  function position(ev) {
    var r = calque.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))];
  }
  var encours = null, apercuForme = null;
  if (calque) {
    calque.addEventListener('pointerdown', function (ev) {
      var zonesActives = modeZones();
      if (!(ongletActif === 'annoter' || zonesActives) || ev.button !== 0) return;
      ev.preventDefault(); calque.setPointerCapture(ev.pointerId);
      var o = zonesActives ? 'zone' : outil(), p = position(ev);
      encours = { outil: o, depart: p, points: [p] };
      if (o !== 'dessin') { apercuForme = el('div', '', 'f108-forme f108-forme-' + o); calque.append(apercuForme); }
    });
    calque.addEventListener('pointermove', function (ev) {
      if (!encours) return;
      var p = position(ev);
      if (encours.outil === 'dessin') { encours.points.push(p); if (encours.points.length % 3 === 0) { traces.push({ type: 'dessin', page: page, traits: [encours.points.slice()], _tmp: true }); dessiner(); traces = traces.filter(function (t) { return !t._tmp; }); } return; }
      var a = encours.depart;
      apercuForme.style.left = Math.min(a[0], p[0]) * 100 + '%'; apercuForme.style.top = Math.min(a[1], p[1]) * 100 + '%';
      apercuForme.style.width = Math.abs(p[0] - a[0]) * 100 + '%'; apercuForme.style.height = Math.abs(p[1] - a[1]) * 100 + '%';
      encours.fin = p;
    });
    calque.addEventListener('pointerup', function (ev) {
      if (!encours) return;
      var e0 = encours; encours = null;
      if (apercuForme) { apercuForme.remove(); apercuForme = null; }
      if (e0.outil === 'dessin') {
        if (e0.points.length > 1) traces.push({ type: 'dessin', page: page, traits: [e0.points.slice(0, 2000)], couleur: valeur('[data-f108-couleur]') });
        dessiner(); return;
      }
      var f = e0.fin || position(ev), a = e0.depart;
      var rect = [Math.min(a[0], f[0]), Math.min(a[1], f[1]), Math.max(a[0], f[0]), Math.max(a[1], f[1])];
      if (rect[2] - rect[0] < 0.005 || rect[3] - rect[1] < 0.005) { dessiner(); return; }
      if (e0.outil === 'zone') { zones.push({ page: page, rect: rect }); dessiner(); return; }
      var t = { type: e0.outil, page: page, rect: rect, couleur: valeur('[data-f108-couleur]') };
      if (t.type === 'texte') {
        t.texte = valeur('[data-f108-note]').trim();
        if (!t.texte) { statut(zoneStatut, 'Écrivez d’abord le texte de la note, puis tracez son cadre.', true); dessiner(); return; }
      }
      if (t.type === 'tampon') {
        t.tampon = valeur('[data-f108-tampon]');
        if (t.tampon === 'piece') { t.piece = valeur('[data-f108-piece-tampon]').trim(); t.libelle = fmt('Pièce n° %s', t.piece); }
        else { var s = boite.querySelector('[data-f108-tampon]'); t.libelle = s.options[s.selectedIndex].textContent; }
      }
      if (t.type === 'image') {
        if (!imageJpeg) { statut(zoneStatut, 'Choisissez d’abord l’image ou la signature, puis tracez son cadre.', true); dessiner(); return; }
        t.image = imageJpeg;
      }
      statut(zoneStatut, '');
      traces.push(t); dessiner();
    });
  }
  function valeur(sel) { var n = boite.querySelector(sel); return n ? n.value : ''; }
  var champImage = boite.querySelector('[data-f108-image]');
  if (champImage) champImage.addEventListener('change', function () {
    var f = champImage.files && champImage.files[0];
    imageJpeg = '';
    if (!f) return;
    var img = new Image(), url = URL.createObjectURL(f);
    img.onload = function () {   // JPEG sur fond blanc, 1600 px au plus : le serveur n'accepte que du JPEG contrôlé
      var k = Math.min(1, 1600 / Math.max(img.width, img.height)), c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(img.width * k)); c.height = Math.max(1, Math.round(img.height * k));
      var ctx = c.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height); ctx.drawImage(img, 0, 0, c.width, c.height);
      imageJpeg = c.toDataURL('image/jpeg', 0.9); URL.revokeObjectURL(url);
      statut(zoneStatut, 'Image prête : tracez son cadre sur la page.');
    };
    img.onerror = function () { statut(zoneStatut, 'Image illisible : choisissez un JPEG ou un PNG.', true); };
    img.src = url;
  });

  // fusion : ordre des PDF choisis
  var fusion = boite.querySelector('[data-f108-fusion]'), ordreFusion = (donnees.sources || []).slice();
  function listerFusion() {
    if (!fusion) return;
    fusion.replaceChildren();
    ordreFusion.forEach(function (s, i) {
      var li = el('li', fmt('%s (# p.)', s.nom, s.pages) + ' ');
      [['↑', -1, 'Monter : %s'], ['↓', 1, 'Descendre : %s']].forEach(function (x) {
        var b = el('button', x[0], 'ax-btn ghost'); b.type = 'button'; b.setAttribute('aria-label', fmt(x[2], s.nom));
        b.disabled = (i + x[1] < 0) || (i + x[1] >= ordreFusion.length);
        b.addEventListener('click', function () { var t = ordreFusion[i]; ordreFusion[i] = ordreFusion[i + x[1]]; ordreFusion[i + x[1]] = t; listerFusion(); });
        li.append(b);
      });
      fusion.append(li);
    });
  }
  listerFusion();
  var ajoutPdf = boite.querySelector('[data-f108-ajout-pdf]');
  if (ajoutPdf) ajoutPdf.addEventListener('change', function () {   // la page est rouverte avec ce PDF : son empreinte est relevée par le serveur
    if (!ajoutPdf.value) return;
    var u = new URL(location.href), n = 2;
    while (u.searchParams.has('p' + n)) n += 1;
    u.searchParams.set('p' + n, ajoutPdf.value);
    location.assign(u.toString());
  });

  // commandes
  function sources(liste) { return liste.map(function (s) { return { p: s.p, empreinte: s.empreinte }; }); }
  function corpsDe(op) {
    var c = { d: donnees.d, sources: sources([source]) };
    if (op === 'annoter') c.annotations = traces.map(function (t) { var x = Object.assign({}, t); delete x.libelle; return x; });
    if (op === 'organiser') {
      var lignes = Array.prototype.slice.call(boite.querySelectorAll('[data-f108-page]')).filter(function (li) { return li.querySelector('[data-f108-garder]').checked; });
      lignes.sort(function (a, b) { return Number(a.querySelector('[data-f108-position]').value) - Number(b.querySelector('[data-f108-position]').value); });
      c.ordre = lignes.map(function (li) { return { page: Number(li.getAttribute('data-f108-page')), rotation: Number(li.querySelector('[data-f108-rotation]').value) }; });
    }
    if (op === 'decouper') c.plages = valeur('[data-f108-plages]');
    if (op === 'fusionner') c.sources = sources(ordreFusion);
    if (op === 'numeroter') {
      c.pages = !!(boite.querySelector('[data-f108-num-pages]') || {}).checked; c.position = valeur('[data-f108-num-position]');
      c.piece = valeur('[data-f108-num-piece]').trim(); c.piece_partout = !!(boite.querySelector('[data-f108-num-partout]') || {}).checked;
    }
    if (op === 'caviarder') {
      c.zones = {};
      zones.forEach(function (z) { (c.zones[z.page] = c.zones[z.page] || []).push(z.rect); });
      c.termes = valeur('[data-f108-termes]').split('\n').map(function (x) { return x.trim(); }).filter(Boolean);
    }
    if (op === 'compresser') c.qualite = valeur('[data-f108-qualite]');
    return c;
  }
  boite.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-f108-op]');
    if (!b || b.disabled || b.getAttribute('aria-busy') === 'true') return;
    var op = b.getAttribute('data-f108-op'), corps = corpsDe(op);
    corps.cle = cleDe(op, corps);
    b.setAttribute('aria-busy', 'true');
    statut(zoneStatut, op === 'caviarder' ? 'Caviardage en cours (pages transformées en images, reconnaissance du texte, contrôle)… cela peut prendre une minute.' : 'Enregistrement…');
    json(op, corps).then(function (r) {
      b.removeAttribute('aria-busy');
      if (!r.ok) { statut(zoneStatut, erreurDe(r), true); return; }
      zoneStatut.replaceChildren(el('span', r.data.message || 'Enregistré.'), document.createTextNode(' '));
      var lien = el('a', 'Voir les fichiers du dossier'); lien.href = donnees.retour; zoneStatut.append(lien);
      zoneStatut.classList.remove('f108-erreur');
    }).catch(function () { b.removeAttribute('aria-busy'); statut(zoneStatut, RESEAU, true); });
  });
  var caseZones = boite.querySelector('[data-f108-zone-active]');
  if (caseZones) caseZones.addEventListener('change', dessiner);
  window.addEventListener('resize', function () { window.clearTimeout(rendre._t); rendre._t = window.setTimeout(rendre, 200); });
})();
