/* AxiorHub 5.6.103 (lot T) — « Tableau blanc » de la discussion (Messagerie instantanée), dans le volet de droite.
   - Excalidraw embarqué (agent/static/excalidraw56103.js, module ES chargé à la première ouverture ; polices servies par AxiorHub :
     window.EXCALIDRAW_ASSET_PATH posé ici avant le chargement). Aucun CDN, aucun service Excalidraw.
   - Temps réel fait par AxiorHub : les éléments changés partent au serveur (m56103/tableau/envoyer, clé d'idempotence par lot, version de
     base) ; ceux des autres arrivent par un sondage léger (toutes les 2 s, volet ouvert et onglet visible). Fusion par élément avec la règle
     d'Excalidraw (reconcileElements : version, puis versionNonce) ; un 409 apporte les changements manqués, fusionnés ici puis renvoyés.
   - Images incrustées : PNG, JPEG, WebP, GIF, 1 Mo au plus (bornes reçues du serveur) ; une image refusée est retirée et la raison affichée.
   - Proposition de Pilote (« @pilote dessine … ») : le texte Mermaid validé par le serveur est converti ici (convertisseur chargé à la
     demande) et affiché comme aperçu local, jamais envoyé tel quel ; « Ajouter au tableau » l'envoie (une seule fois pour tous), « Annuler » le retire.
   - Export PNG / SVG sur le poste (aucun envoi). Volet ancré à droite sur grand écran (≥ 1200 px), fenêtre sinon ; mêmes classes que le
     volet de la 5.6.101 (f96-fenetre, v101-ancre). Tout texte par textContent ; aucun HTML reçu n'est injecté. */
(function () {
  'use strict';
  var doc = document, racine = window;
  var bouton = doc.querySelector('[data-t103-ouvrir]'), source = doc.querySelector('script[data-t103-config]');
  if (!bouton || !source) return;
  var cfg;
  try { cfg = JSON.parse(source.textContent || '{}'); } catch (e) { return; }
  function meta(n) { var x = doc.querySelector('meta[name="' + n + '"]'); return x ? x.content : ''; }
  var csrf = meta('axiorhub-csrf'), L = cfg.limites || {};
  var MARQUE = 't103Proposition', SONDAGE = 2000, DELAI = 400, LARGE = 1200;

  function el(tag, cls, text) { var n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function cle() {
    var t = new Uint8Array(16), c = racine.crypto;
    if (c && c.getRandomValues) c.getRandomValues(t); else for (var i = 0; i < 16; i++) t[i] = Math.floor(Math.random() * 256);
    return 't103-' + Array.prototype.map.call(t, function (o) { return ('0' + o.toString(16)).slice(-2); }).join('');
  }
  function lire(r) {
    return r.json().catch(function () { return {}; }).then(function (d) {
      if (!r.ok || (d && d.error)) { var x = new Error((d && d.message) || ('HTTP ' + r.status)); x.code = d && d.error; x.statut = r.status; x.donnees = d; throw x; }
      return d;
    });
  }
  function get(route) { return racine.fetch(cfg.api + route, { credentials: 'same-origin', cache: 'no-store' }).then(lire); }
  function post(route, corps) {
    return racine.fetch(cfg.api + route, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) }).then(lire);
  }
  function sombre() {
    var t = doc.documentElement.getAttribute('data-theme');
    if (t) return t === 'dark';
    return !!(racine.matchMedia && racine.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  /* ------------------------------------------------------------------ état */
  var mod = null, chargement = null, api = null, demonter = null;
  var version = 0, connus = new Map(), imagesServeur = new Set(), imagesRefusees = new Set();
  var minuterieEnvoi = null, minuterieSondage = null, envoiEnCours = false, encore = false, reessai = false, lotEnCours = null;
  var apercu = null, refusees = new Set(), conversion = false;
  var fenetre = null, statut = null, barre = null, scene = null, bascule = false;

  function dire(texte, erreur) {
    if (!statut) return;
    statut.textContent = texte || '';
    if (erreur) statut.setAttribute('data-erreur', '1'); else statut.removeAttribute('data-erreur');
  }
  function estApercu(e) { return !!(e && e.customData && e.customData[MARQUE] !== undefined); }

  /* ------------------------------------------------------------------ volet : ancré à droite ou fenêtre flottante */
  function icone(texte, libelle, cls) { var b = el('button', 'f96-icone-btn ' + (cls || ''), texte); b.type = 'button'; b.setAttribute('aria-label', libelle); b.title = libelle; return b; }
  function construire() {
    var d = el('dialog', 'f96-fenetre t103-fenetre');
    d.setAttribute('aria-labelledby', 't103-titre');
    var tete = el('div', 'f96-fenetre-barre'), titres = el('div', 'f96-fenetre-tete');
    var h = el('h2', 'f96-fenetre-titre', 'Tableau blanc'); h.id = 't103-titre';
    titres.appendChild(h); titres.appendChild(el('p', 'f96-fenetre-dossier', 'Commun aux participants de la discussion'));
    var cmd = el('div', 'f96-fenetre-commandes');
    var png = icone('PNG', 'Exporter en image PNG (sur ce poste)', 't103-export'), svg = icone('SVG', 'Exporter en SVG (sur ce poste)', 't103-export');
    var plein = icone('⤢', 'Agrandir ou réduire le volet'), fermer = icone('✕', 'Fermer le tableau blanc', 'f96-fenetre-fermer');
    [png, svg, plein, fermer].forEach(function (b) { cmd.appendChild(b); });
    tete.appendChild(titres); tete.appendChild(cmd);
    statut = el('p', 'f96-fenetre-etat t103-etat'); statut.setAttribute('role', 'status'); statut.setAttribute('aria-live', 'polite');
    barre = el('div', 't103-proposition'); barre.hidden = true; barre.setAttribute('role', 'region'); barre.setAttribute('aria-label', 'Proposition de Pilote');
    scene = el('div', 't103-scene');
    d.appendChild(tete); d.appendChild(statut); d.appendChild(barre); d.appendChild(scene);
    (doc.getElementById('ws-main') || doc.body).appendChild(d);
    fermer.addEventListener('click', function () { d.close(); });
    plein.addEventListener('click', function () { d.classList.toggle('f96-plein'); });
    png.addEventListener('click', function () { exporter('png'); });
    svg.addEventListener('click', function () { exporter('svg'); });
    d.addEventListener('cancel', function (ev) { if (scene.contains(doc.activeElement)) ev.preventDefault(); });   /* Échap appartient au dessin */
    d.addEventListener('close', function () {
      if (bascule) { bascule = false; return; }
      fermerTableau();
    });
    return d;
  }
  function placer() {
    var ancre = (racine.innerWidth || 0) >= LARGE;
    if (fenetre.open && fenetre.classList.contains('v101-ancre') !== ancre) { bascule = true; fenetre.close(); }
    fenetre.classList.toggle('v101-ancre', ancre);
    doc.body.classList.toggle('v101-volet-ouvert', ancre);
    if (!fenetre.open) { if (ancre) fenetre.show(); else fenetre.showModal(); }
    return ancre;
  }

  /* ------------------------------------------------------------------ chargement d'Excalidraw (une fois par page) */
  function chargerModule() {
    if (chargement) return chargement;
    racine.EXCALIDRAW_ASSET_PATH = new URL(cfg.ressources, racine.location.href).href;   /* polices : dossier local seulement */
    if (!doc.querySelector('link[data-t103-feuille]')) {
      var l = doc.createElement('link'); l.rel = 'stylesheet'; l.href = cfg.feuille; l.setAttribute('data-t103-feuille', '1'); doc.head.appendChild(l);
    }
    chargement = import(cfg.module).then(function (m) { mod = m; return m; }, function (e) { chargement = null; throw e; });
    return chargement;
  }
  function chargerImages(ids) {
    var manquants = (ids || []).filter(function (id) { return !imagesServeur.has(id); });
    return Promise.all(manquants.map(function (id) {
      return get('fichier?fil=' + encodeURIComponent(cfg.fil) + '&id=' + encodeURIComponent(id)).then(function (f) { imagesServeur.add(id); return f; }, function () { return null; });
    })).then(function (liste) { return liste.filter(Boolean); });
  }

  function ouvrir() {
    Array.prototype.forEach.call(doc.querySelectorAll('dialog.f96-fenetre[open]'), function (d) { if (d !== fenetre) d.close(); });
    if (!fenetre) fenetre = construire();
    placer();
    if (api) { demarrerSondage(); return; }
    dire('Chargement du tableau blanc…');
    fenetre.setAttribute('aria-busy', 'true');
    Promise.all([chargerModule(), get('scene?fil=' + encodeURIComponent(cfg.fil) + '&depuis=0')]).then(function (r) {
      var d = r[1];
      return chargerImages(d.fichiers).then(function (images) { monterScene(d, images); });
    }).catch(function (e) {
      dire('Tableau blanc indisponible : ' + e.message, true);
    }).then(function () { fenetre.removeAttribute('aria-busy'); });
  }
  function monterScene(d, images) {
    version = d.version || 0;
    connus = new Map();
    d.elements.forEach(function (e) { connus.set(e.id, e.version); });
    var fichiers = {};
    images.forEach(function (f) { fichiers[f.id] = f; });
    var R = mod.React, Menu = mod.MainMenu;
    var menu = R.createElement(Menu, null, R.createElement(Menu.DefaultItems.SaveAsImage), R.createElement(Menu.DefaultItems.ClearCanvas),
      R.createElement(Menu.DefaultItems.ChangeCanvasBackground), R.createElement(Menu.DefaultItems.Help));
    demonter = mod.monter(scene, {
      initialData: { elements: mod.restoreElements(d.elements, null), files: fichiers, scrollToContent: true, appState: { viewBackgroundColor: '#ffffff' } },
      excalidrawAPI: function (a) { api = a; },
      onChange: function () { planifierEnvoi(); },
      langCode: cfg.langue || 'fr-FR',
      theme: sombre() ? 'dark' : 'light',
      name: 'Tableau blanc',
      validateEmbeddable: function () { return false; },          /* aucun contenu externe intégré (refusé aussi par le serveur) */
      UIOptions: { canvasActions: { loadScene: false, saveToActiveFile: false, export: false, toggleTheme: null, saveAsImage: true } },
      children: menu
    });
    dire(d.elements.length ? '' : 'Tableau vide : dessinez, ou écrivez « @pilote dessine … » dans la discussion.');
    traiterPropositions(d.propositions || []);
    demarrerSondage();
  }
  function fermerTableau() {
    doc.body.classList.remove('v101-volet-ouvert');
    racine.clearInterval(minuterieSondage); minuterieSondage = null;
    if (api) { racine.clearTimeout(minuterieEnvoi); envoyer(); }
  }

  /* ------------------------------------------------------------------ fusion des changements des autres */
  function appliquerDistants(liste) {
    if (!api || !liste || !liste.length) return;
    var restaures = mod.restoreElements(liste, null);
    var fusion = mod.reconcileElements(api.getSceneElementsIncludingDeleted(), restaures, api.getAppState());
    liste.forEach(function (e) { connus.set(e.id, e.version); });   /* un élément local plus récent garde l'écart : il partira au prochain envoi */
    api.updateScene({ elements: fusion, captureUpdate: mod.CaptureUpdateAction.NEVER });
  }
  function recevoirImages(ids) {
    if (!ids || !ids.length) return;
    chargerImages(ids).then(function (images) { if (images.length && api) api.addFiles(images); });
  }

  /* ------------------------------------------------------------------ envoi des changements locaux */
  function planifierEnvoi() {
    racine.clearTimeout(minuterieEnvoi);
    minuterieEnvoi = racine.setTimeout(envoyer, DELAI);
  }
  function tailleDataUrl(u) { var i = String(u || '').indexOf(','); return i < 0 ? 0 : Math.floor((u.length - i - 1) * 3 / 4); }
  function retirerImage(e, raison) {
    imagesRefusees.add(e.id);
    var copie = Object.assign({}, e, { isDeleted: true, version: e.version + 1, versionNonce: Math.floor(Math.random() * 2147483647) });
    api.updateScene({ elements: api.getSceneElementsIncludingDeleted().map(function (x) { return x.id === e.id ? copie : x; }), captureUpdate: mod.CaptureUpdateAction.NEVER });
    dire(raison, true);
  }
  function envoyerImages(lot) {
    var fichiers = api.getFiles(), attente = [];
    var prets = lot.filter(function (e) {
      if (e.type !== 'image' || e.isDeleted || !e.fileId || imagesServeur.has(e.fileId)) return true;
      var f = fichiers[e.fileId];
      if (!f || !f.dataURL) return false;                         /* image pas encore lue par Excalidraw : au prochain envoi */
      if ((L.types_image || []).indexOf(f.mimeType) < 0) { retirerImage(e, 'Type d’image refusé (PNG, JPEG, WebP ou GIF seulement) : image retirée.'); return false; }
      if (tailleDataUrl(f.dataURL) > (L.image_octets || 1000000)) { retirerImage(e, 'Image trop lourde pour le tableau (1 Mo au plus) : image retirée.'); return false; }
      attente.push(post('fichier', { fil: cfg.fil, id: e.fileId, mimeType: f.mimeType, dataURL: f.dataURL, cle: cle() }).then(function () { imagesServeur.add(e.fileId); return true; },
        function (x) { retirerImage(e, x.message); return false; }));
      return false;
    });
    return Promise.all(attente).then(function () { return prets; });
  }
  function envoyer() {
    if (!api) return Promise.resolve();
    if (envoiEnCours) { encore = true; return Promise.resolve(); }
    var lot = api.getSceneElementsIncludingDeleted().filter(function (e) { return !estApercu(e) && connus.get(e.id) !== e.version; });
    if (!lot.length) return Promise.resolve();
    envoiEnCours = true; encore = false;
    return envoyerImages(lot).then(function (prets) {
      prets = prets.slice(0, L.lot || 400);
      if (!prets.length) return null;
      var signature = prets.map(function (e) { return e.id + ':' + e.version; }).join(',') + '@' + version;
      if (!lotEnCours || lotEnCours.signature !== signature) lotEnCours = { signature: signature, cle: cle() };   /* même lot après un échec réseau : même clé */
      return post('envoyer', { fil: cfg.fil, base: version, elements: prets, cle: lotEnCours.cle }).then(function (d) {
        lotEnCours = null;
        prets.forEach(function (e) { connus.set(e.id, e.version); });
        version = Math.max(version, d.version || 0);
        appliquerDistants(d.elements); recevoirImages(d.fichiers);
        if (d.refuses && d.refuses.length) dire(d.refuses.length + ' élément(s) refusé(s) par le serveur (' + d.refuses[0].motif + ').', true);
        else if (statut && statut.getAttribute('data-erreur')) dire('');
        if (prets.length < lot.length) encore = true;
      }, function (x) {
        if (x.statut === 409 && x.code === 'version_perimee') {   /* changements manqués : fusion ici, puis nouvel envoi */
          lotEnCours = null;
          appliquerDistants(x.donnees.elements); recevoirImages(x.donnees.fichiers);
          version = x.donnees.version || version; encore = true; return;
        }
        if (x.statut === 409 && x.code === 'version_inconnue') { lotEnCours = null; version = 0; encore = true; return sonder(); }
        if (x.statut === 413) { prets.forEach(function (e) { connus.set(e.id, e.version); }); lotEnCours = null; dire(x.message, true); return; }
        dire('Modifications non enregistrées : ' + x.message + ' Nouvel essai dans quelques secondes.', true);
        reessai = true;
      });
    }).catch(function (x) { dire('Modifications non enregistrées : ' + x.message, true); reessai = true; }).then(function () {
      envoiEnCours = false;
      if (reessai) { reessai = false; racine.clearTimeout(minuterieEnvoi); minuterieEnvoi = racine.setTimeout(envoyer, 5000); }
      else if (encore) planifierEnvoi();
    });
  }

  /* ------------------------------------------------------------------ sondage */
  function sonder() {
    if (!api || envoiEnCours) return Promise.resolve();
    var demande = version;
    return get('scene?fil=' + encodeURIComponent(cfg.fil) + '&depuis=' + demande).then(function (d) {
      if (!d.inchange) {
        appliquerDistants(d.elements); recevoirImages(d.fichiers);
        version = Math.max(version, d.version || 0);
      }
      traiterPropositions(d.propositions || []);
    }).catch(function (x) { if (x.statut === 404) { dire('Ce tableau n’est plus accessible.', true); racine.clearInterval(minuterieSondage); } });
  }
  function demarrerSondage() {
    racine.clearInterval(minuterieSondage);
    minuterieSondage = racine.setInterval(function () { if (!doc.hidden && fenetre && fenetre.open) sonder(); }, SONDAGE);
  }

  /* ------------------------------------------------------------------ propositions de Pilote */
  function typeMermaid(texte) {
    var ligne = String(texte || '').split('\n').map(function (l) { return l.trim(); }).filter(function (l) { return l && l.indexOf('%%') !== 0; })[0] || '';
    return ligne.split(/\s+/)[0];
  }
  function traiterPropositions(liste) {
    var attente = liste.filter(function (p) { return p.etat === 'en_file' || p.etat === 'en_cours'; });
    var proposees = liste.filter(function (p) { return p.etat === 'proposee' && !refusees.has(p.id); });
    if (apercu && !proposees.some(function (p) { return p.id === apercu.id; })) retirerApercu('Proposition traitée par un autre participant.');
    if (!apercu && !conversion && proposees.length) afficherProposition(proposees[0]);
    else if (!apercu && attente.length) peindreBarre(null, 'Pilote prépare un schéma…');
    else if (!apercu && !conversion) { barre.hidden = true; }
  }
  function peindreBarre(p, texte) {
    while (barre.firstChild) barre.removeChild(barre.firstChild);
    barre.hidden = false;
    barre.appendChild(el('p', 't103-proposition-texte', texte));
    if (!p) return;
    var oui = el('button', 'ax-btn', 'Ajouter au tableau'), non = el('button', 'ax-btn ghost', 'Annuler');
    oui.type = non.type = 'button';
    oui.addEventListener('click', function () { oui.disabled = non.disabled = true; accepter(); });
    non.addEventListener('click', function () { oui.disabled = non.disabled = true; retirer(); });
    var actions = el('p', 't103-proposition-actions'); actions.appendChild(oui); actions.appendChild(non);
    barre.appendChild(actions);
  }
  function afficherProposition(p) {
    if ((L.types_mermaid || []).indexOf(typeMermaid(p.mermaid)) < 0 || String(p.mermaid || '').length > (L.mermaid_caracteres || 4000)) {
      refusees.add(p.id); dire('Proposition de Pilote ignorée : type de schéma non permis.', true); return;
    }
    conversion = true;
    peindreBarre(null, 'Préparation du schéma proposé par Pilote…');
    mod.chargerMermaid().then(function (convertir) {
      return convertir(p.mermaid, { themeVariables: { fontSize: '16px' } });
    }).then(function (res) {
      if (res.files && Object.keys(res.files).length) throw new Error('ce type de schéma ne donne pas d’éléments modifiables');
      var neufs = mod.convertToExcalidrawElements(res.elements, { regenerateIds: true });
      var existants = api.getSceneElements().filter(function (e) { return !estApercu(e); });
      var dx = 0, dy = 0;
      if (existants.length && neufs.length) {
        var a = mod.getCommonBounds(existants), b = mod.getCommonBounds(neufs);
        dx = a[2] + 80 - b[0]; dy = a[1] - b[1];
      }
      neufs = neufs.map(function (e) {
        var cd = Object.assign({}, e.customData || {}); cd[MARQUE] = p.id;
        return Object.assign({}, e, { x: e.x + dx, y: e.y + dy, customData: cd });
      });
      api.updateScene({ elements: api.getSceneElementsIncludingDeleted().concat(neufs), captureUpdate: mod.CaptureUpdateAction.NEVER });
      api.scrollToContent(neufs, { fitToViewport: true, animate: true });
      apercu = { id: p.id, titre: p.titre };
      peindreBarre(p, 'Proposition de Pilote : « ' + (p.titre || 'Schéma') + ' » (aperçu sur ce poste, pas encore dans le tableau commun).');
    }).catch(function (e) {
      refusees.add(p.id);
      peindreBarre(null, 'La proposition de Pilote n’a pas pu être affichée : ' + e.message + '.');
    }).then(function () { conversion = false; });
  }
  function apercuElements() { return api.getSceneElementsIncludingDeleted().filter(function (e) { return estApercu(e) && apercu && e.customData[MARQUE] === apercu.id; }); }
  function retirerApercu(message) {
    if (!apercu) return;
    var id = apercu.id;
    api.updateScene({ elements: api.getSceneElementsIncludingDeleted().filter(function (e) { return !(estApercu(e) && e.customData[MARQUE] === id); }),
      captureUpdate: mod.CaptureUpdateAction.NEVER });
    refusees.add(id); apercu = null; barre.hidden = true;
    if (message) dire(message);
  }
  function accepter(essai) {
    if (!apercu) return;
    var id = apercu.id, a = apercuElements();
    var finals = a.filter(function (e) { return !e.isDeleted; }).map(function (e) {
      var cd = Object.assign({}, e.customData); delete cd[MARQUE];
      var c = Object.assign({}, e, { version: e.version + 1, versionNonce: Math.floor(Math.random() * 2147483647) });
      if (Object.keys(cd).length) c.customData = cd; else delete c.customData;
      return c;
    });
    var k = (accepter.cles = accepter.cles || {})[id] || (accepter.cles[id] = cle());
    post('proposition/accepter', { fil: cfg.fil, proposition: id, base: version, elements: finals, cle: k }).then(function (d) {
      if (d.deja) { retirerApercu(d.message); return; }
      var parId = new Map(finals.map(function (e) { return [e.id, e]; }));
      api.updateScene({ elements: api.getSceneElementsIncludingDeleted().filter(function (e) { return !(estApercu(e) && e.customData[MARQUE] === id) || parId.has(e.id); })
        .map(function (e) { return parId.get(e.id) || e; }), captureUpdate: mod.CaptureUpdateAction.IMMEDIATELY });
      finals.forEach(function (e) { connus.set(e.id, e.version); });
      version = Math.max(version, d.version || 0);
      appliquerDistants(d.elements);
      refusees.add(id); apercu = null; barre.hidden = true;
      dire(d.message || 'Schéma ajouté au tableau.');
    }, function (x) {
      if (x.statut === 409 && x.code === 'version_perimee' && !essai) {
        delete accepter.cles[id];
        appliquerDistants(x.donnees.elements); version = x.donnees.version || version;
        return accepter(true);
      }
      peindreBarre({ id: id, titre: apercu && apercu.titre }, 'Ajout impossible : ' + x.message);
    });
  }
  function retirer() {
    if (!apercu) return;
    var id = apercu.id;
    retirerApercu('');
    post('proposition/retirer', { fil: cfg.fil, proposition: id, cle: cle() }).then(function (d) { dire(d.message || 'Proposition annulée.'); },
      function (x) { dire('Annulation non enregistrée : ' + x.message, true); });
  }

  /* ------------------------------------------------------------------ export sur le poste */
  function telecharger(blob, nom) {
    var url = URL.createObjectURL(blob), a = el('a');
    a.href = url; a.download = nom; doc.body.appendChild(a); a.click(); a.remove();
    racine.setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }
  function exporter(format) {
    if (!api) return;
    var elements = api.getSceneElements().filter(function (e) { return !estApercu(e); });
    if (!elements.length) { dire('Tableau vide : rien à exporter.'); return; }
    var etat = Object.assign({}, api.getAppState(), { exportBackground: true }), fichiers = api.getFiles();
    var nom = 'tableau-blanc-' + cfg.fil + '.' + format;
    var travail = format === 'png'
      ? mod.exportToBlob({ elements: elements, appState: etat, files: fichiers, mimeType: 'image/png' }).then(function (b) { telecharger(b, nom); })
      : mod.exportToSvg({ elements: elements, appState: etat, files: fichiers }).then(function (s) {
        telecharger(new Blob([new XMLSerializer().serializeToString(s)], { type: 'image/svg+xml' }), nom);
      });
    travail.then(function () { dire('Export ' + format.toUpperCase() + ' enregistré sur ce poste.'); }, function (e) { dire('Export impossible : ' + e.message, true); });
  }

  bouton.addEventListener('click', ouvrir);
  racine.addEventListener('resize', function () { if (fenetre && fenetre.open) placer(); });
  if (/(?:^|[?&])tableau=1(?:&|$)/.test(racine.location.search)) ouvrir();
})();
