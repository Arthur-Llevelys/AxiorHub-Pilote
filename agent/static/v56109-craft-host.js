/* AxiorHub 5.6.109 (lot V, F16) — pont hôte des éditeurs Craft facultatifs (GridCraft, PdfCraft, DeckCraft), désactivés par défaut.
   Portage sans module ES (CSP, chargement « defer ») de reference/craft_host109.mjs du dossier technique, mêmes contrats :
   - messages du cadre acceptés seulement de la même origine ET de la fenêtre du cadre, types préfixés par l'application ;
   - « save » : nom simple (jamais un chemin), format admis, octets copiés et bornés ; deux enregistrements en attente au plus, sérialisés,
     révision attendue ; la réservation précède tout await (un « dirty:false » reçu pendant la garde ne nettoie pas l'état) ;
   - un acquittement ancien ne nettoie pas une saisie plus récente ; « saved » n'est renvoyé au cadre qu'avec un identifiant reçu et après
     le reçu durable du serveur ; réponse perdue ou refus : copie récupérable gardée, aucun rejeu automatique ;
   - un export (PDF d'un classeur…) part vers le service d'export distinct, jamais vers l'enregistrement du document ;
   - commandeIA : liste fermée (plan validé par le serveur), pause et révision contrôlées avant et après la validation, résultat incertain
     jamais relancé.
   Les textes reçus passent par textContent ; aucun HTML n'est injecté. Sous Node (tests), le module exporte ses fonctions. */
(function () {
  'use strict';
  var FORMATS = Object.freeze({
    gridcraft: { document: ['xlsx'], export: ['pdf', 'csv', 'tsv'] },
    pdfcraft: { document: ['pdf'], export: ['png', 'jpg', 'jpeg', 'txt'] },
    deckcraft: { document: ['pptx'], export: ['pdf', 'png', 'jpg', 'jpeg', 'txt', 'md'] }
  });

  function copieBytes(value, maximum) {
    var array;
    if (Object.prototype.toString.call(value) === '[object ArrayBuffer]') {
      array = new Uint8Array(value);
    } else if (ArrayBuffer.isView(value) && Object.prototype.toString.call(value.buffer) === '[object ArrayBuffer]') {
      array = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    } else {
      throw new Error('octets_invalides');
    }
    if (!array.byteLength || array.byteLength > maximum) { throw new Error('taille_refusee'); }
    return array.slice().buffer;
  }

  function nomSimple(value) {
    if (typeof value !== 'string' || !value || value.length > 160 || /[\\/:\x00-\x1f\x7f]/.test(value) || value.charAt(0) === '.' || value.slice(-1) === '.') {
      throw new Error('nom_refuse');
    }
    return value;
  }

  class CraftHost {
    constructor(o) {
      var app = o.app, frame = o.frame;
      if (!FORMATS[app] || !frame || !frame.contentWindow) { throw new Error('editeur_inconnu'); }
      var parsed = new URL(o.origin);
      if (['https:', 'http:'].indexOf(parsed.protocol) < 0 || parsed.origin !== o.origin) { throw new Error('origine_invalide'); }
      if (!o.session || !Number.isSafeInteger(o.revision) || o.revision < 0) { throw new Error('seance_invalide'); }
      if (typeof o.sauvegarder !== 'function' || typeof o.exporter !== 'function' || typeof o.autoriser !== 'function') { throw new Error('services_requis'); }
      var maxBytes = o.maxBytes === undefined ? 25000000 : o.maxBytes;
      if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || maxBytes > 50000000) { throw new Error('limite_invalide'); }
      this.app = app; this.frame = frame; this.origin = o.origin; this.session = o.session; this.revision = o.revision;
      this.sauvegarder = o.sauvegarder; this.exporter = o.exporter; this.autoriser = o.autoriser;
      this.onState = o.onState || function () {};
      this.maxBytes = maxBytes;
      this.idOperation = o.idOperation || function () { return globalThis.crypto.randomUUID(); };
      this.ready = false; this.generation = 0; this.acquittee = 0; this.appDirty = false; this.pending = 0;
      this.erreur = ''; this.ferme = false; this.file = Promise.resolve(); this.recuperation = null;
    }

    etat() {
      return { ready: this.ready, revision: this.revision, pending: this.pending,
        dirty: this.appDirty || this.generation > this.acquittee || this.pending > 0 || !!this.erreur, erreur: this.erreur, ferme: this.ferme };
    }

    notifier() { this.onState(this.etat()); }

    async recevoir(event) {
      if (this.ferme || event.origin !== this.origin || event.source !== this.frame.contentWindow) { return { accepted: false }; }
      var d = event.data;
      if (!d || typeof d !== 'object') { return { accepted: false }; }
      var app = this.app, self = this;
      if (d.type === app + ':ready') {
        this.ready = true;
        this.frame.contentWindow.postMessage({ type: app + ':host', version: '1', handoff: false }, this.origin);
        this.notifier();
        return { accepted: true, ready: true };
      }
      if (d.type === app + ':dirty' && typeof d.dirty === 'boolean') {
        this.appDirty = d.dirty;
        if (d.dirty) { this.generation += 1; }
        // un simple dirty:false n'acquitte aucun contenu (il peut venir d'un ancien enregistrement)
        this.notifier();
        return { accepted: true };
      }
      if (d.type === app + ':failed') {
        this.erreur = 'editeur_indisponible';
        this.notifier();
        return { accepted: true, saved: false };
      }
      if (d.type !== app + ':save') { return { accepted: false }; }
      if (!this.ready) { return { accepted: false }; }
      var bytes, name, extension;
      try {
        name = nomSimple(d.name);
        extension = name.split('.').pop().toLowerCase();
        var formats = FORMATS[app];
        if (formats.document.concat(formats.export).indexOf(extension) < 0) { throw new Error('format_refuse'); }
        bytes = copieBytes(d.bytes, this.maxBytes);
      } catch (err) {
        this.erreur = 'enregistrement_refuse';
        this.notifier();
        return { accepted: true, saved: false };
      }
      var isExport = FORMATS[app].document.indexOf(extension) < 0 || d.export === true;
      var snapshot = { bytes: bytes, name: name, generation: this.generation, isExport: isExport };
      this.recuperation = snapshot;
      if (this.erreur || this.pending >= 2) {
        this.erreur = this.erreur || 'trop_enregistrements_en_attente';
        this.notifier();
        return { accepted: true, saved: false };
      }
      // réservation AVANT tout await : un dirty:false suivant ne masque pas une sauvegarde encore en attente
      this.pending += 1;
      this.notifier();
      var travail = async function () {
        try {
          if (self.erreur || self.ferme) { throw new Error('reconciliation_requise'); }
          var action = isExport ? 'exporter_copie' : 'sauvegarder_candidat';
          var expected = self.revision;
          await self.autoriser({ session: self.session, app: app, action: action, revision: expected });
          var operation = self.idOperation();
          if (typeof operation !== 'string' || operation.length < 8 || operation.length > 100) { throw new Error('cle_invalide'); }
          var payload = { session: self.session, app: app, expectedRevision: expected, operation: operation, bytes: bytes, nameHint: name,
                          extension: extension, saveAs: d.saveAs === true };
          var receipt = await (isExport ? self.exporter(payload) : self.sauvegarder(payload));
          if (!receipt || receipt.durable !== true) { throw new Error('acquittement_absent'); }
          if (!isExport) {
            if (!Number.isSafeInteger(receipt.revision) || receipt.revision <= expected) { throw new Error('revision_invalide'); }
            self.revision = receipt.revision;
            self.acquittee = Math.max(self.acquittee, snapshot.generation);
          }
          if (typeof d.id === 'string' && d.id.length > 0 && d.id.length <= 100) {
            self.frame.contentWindow.postMessage({ type: app + ':saved', id: d.id }, self.origin);
          }
          return { accepted: true, saved: true, exported: isExport };
        } catch (err) {
          // un délai réseau peut cacher une écriture réussie : aucun rejeu implicite, conciliation côté serveur
          self.erreur = 'enregistrement_indetermine';
          return { accepted: true, saved: false, error: self.erreur };
        } finally {
          self.pending -= 1;
          self.notifier();
        }
      };
      var result = this.file.then(travail);
      this.file = result.then(function () { return undefined; }, function () { return undefined; });
      return result;
    }

    async ouvrir(o) {
      if (!this.ready || this.ferme || this.etat().dirty) { throw new Error('ouverture_refusee'); }
      await this.autoriser({ session: this.session, app: this.app, action: 'lire_version', revision: this.revision });
      if (this.ferme || this.etat().dirty) { throw new Error('ouverture_refusee'); }
      var copy = copieBytes(o.bytes, this.maxBytes);
      this.frame.contentWindow.postMessage({ type: this.app + ':open', name: nomSimple(o.name), bytes: copy }, this.origin, [copy]);
    }

    fermer() {
      if (this.etat().dirty) { return false; }
      this.ferme = true;
      this.ready = false;
      this.notifier();
      return true;
    }
  }

  /* Courtier d'une commande IA validée par le serveur : une fonction de schéma par commande autorisée, jamais de commande générique. */
  async function commandeIA(o) {
    await o.checkpoint();
    if (!Object.prototype.hasOwnProperty.call(o.schema, o.command) || typeof o.schema[o.command] !== 'function') { throw new Error('commande_non_autorisee'); }
    if (o.currentRevision() !== o.expectedRevision) { throw new Error('revision_perimee'); }
    await o.schema[o.command](o.params);
    await o.checkpoint();
    if (o.currentRevision() !== o.expectedRevision) { throw new Error('revision_perimee'); }
    try {
      // l'API web des éditeurs résout directement le résultat et rejette les erreurs (pas de seconde enveloppe {ok, result})
      var result = await o.api.execute(o.command, o.params);
      return { statut: 'applique', resultat: result };
    } catch (err) {
      return { statut: 'indetermine', relanceAutomatique: false };
    }
  }

  /* Schéma tiré du plan VALIDÉ par le serveur : seule la commande prévue, avec exactement ses paramètres, est acceptée. */
  function schemaDuPlan(etape) {
    var attendu = JSON.stringify(etape && etape.parametres);
    var schema = {};
    if (etape && typeof etape.commande === 'string') {
      schema[etape.commande] = function (params) { if (JSON.stringify(params) !== attendu) { throw new Error('parametres_modifies'); } };
    }
    return schema;
  }

  var api = { FORMATS: FORMATS, CraftHost: CraftHost, commandeIA: commandeIA, schemaDuPlan: schemaDuPlan, nomSimple: nomSimple, copieBytes: copieBytes };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  window.AxhCraft = api;
  var racine = document.documentElement;

  // ------------------------------------------------------------------------------------------------ 1. cadre (même origine, ?host=parent)
  if (racine.getAttribute('data-c109') === 'cadre') {
    window.open = function () { return null; };
    var base = racine.getAttribute('data-c109-base') || '';
    var glue = racine.getAttribute('data-c109-glue') || '';
    var wasm = racine.getAttribute('data-c109-wasm') || '';
    var appCadre = racine.getAttribute('data-c109-app') || '';
    var chargement = document.querySelector('[data-c109-chargement]');
    var echec = function () {
      if (chargement) { chargement.textContent = 'L’éditeur n’a pas pu démarrer dans ce navigateur. Utilisez un autre éditeur du cabinet.'; }
      try { window.parent.postMessage({ type: appCadre + ':failed', error: 'demarrage' }, location.origin); } catch (err) { /* page seule */ }
    };
    if (!glue || !wasm) { echec(); return; }
    import(base + glue).then(function (m) { return m.default({ module_or_path: base + wasm }); })
      .then(function () { if (chargement) { chargement.hidden = true; } }, echec);
    return;
  }

  function meta(nom) { var m = document.querySelector('meta[name="' + nom + '"]'); return m ? m.getAttribute('content') : ''; }
  function cle() {
    var a = new Uint8Array(12); window.crypto.getRandomValues(a);
    return 'c109-' + Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function reponse(r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, statut: r.status, j: j }; }); }
  function posterJson(url, donnees) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: JSON.stringify(donnees) }).then(reponse);
  }
  function posterOctets(url, octets) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: octets }).then(reponse);
  }
  function el(tag, classe, texte) { var n = document.createElement(tag); if (classe) { n.className = classe; } if (texte !== undefined) { n.textContent = texte; } return n; }
  function message(r, defaut) { return (r && r.j && r.j.message) || defaut || 'Demande refusée.'; }

  // ------------------------------------------------------------------------------------------------ 2. réglages (administrateur)
  var reglages = document.querySelector('[data-c109-reglages]');
  if (reglages) {
    var apiR = reglages.getAttribute('data-c109-reglages');
    var etatR = document.querySelector('[data-c109-etat-reglage]');
    document.addEventListener('change', function (ev) {
      var b = ev.target.closest && ev.target.closest('[data-c109-reglage="activer"]'); if (!b) { return; }
      posterJson(apiR + 'reglage', { app: b.getAttribute('data-c109-app'), active: !!b.checked, cle: cle() }).then(function (r) {
        if (etatR) { etatR.textContent = message(r, r.ok ? 'Réglage enregistré.' : ''); }
        if (!r.ok) { b.checked = false; }
      });
    });
    document.addEventListener('click', function (ev) {
      var b = ev.target.closest && ev.target.closest('[data-c109-reglage="installer"]'); if (!b) { return; }
      b.disabled = true;
      posterJson(apiR + 'installer', { app: b.getAttribute('data-c109-app'), cle: cle() }).then(function (r) {
        if (etatR) { etatR.textContent = r.ok ? 'Installé : rechargez la page pour l’activer.' : message(r); }
        b.disabled = false;
      });
    });
  }

  // ------------------------------------------------------------------------------------------------ 3. page d'édition
  var conf = document.querySelector('[data-c109-config]');
  if (!conf) { return; }
  var cfg;
  try { cfg = JSON.parse(conf.getAttribute('data-c109-config')); } catch (err) { return; }
  var zoneEtat = document.querySelector('[data-c109-etat]');
  var controle = document.querySelector('[data-c109-controle]');
  var rapport = document.querySelector('[data-c109-rapport]');
  var iframe = document.querySelector('[data-c109-iframe]');
  var etat = { seance: '', candidat: 0, incertain: null, instantanes: 0, host: null };
  function dire(texte, alerte) { if (zoneEtat) { zoneEtat.textContent = texte || ''; zoneEtat.classList.toggle('c109-erreur', !!alerte); } }
  function bouton(nom) { return document.querySelector('[data-c109="' + nom + '"]'); }

  function montrerControle(res) {
    var c = res.controle || {};
    etat.candidat = res.revision;
    rapport.textContent = '';
    rapport.appendChild(el('p', c.pertes && c.pertes.length ? 'c109-resume c109-attention' : 'c109-resume', c.resume || ''));
    (c.pertes || []).forEach(function (p) { rapport.appendChild(el('p', 'c109-perte', p.libelle + ' : ' + p.avant + ' → ' + p.apres)); });
    var t = c.texte || {};
    rapport.appendChild(el('p', '', 'Texte : ' + (t.modifies || 0) + ' ' + (t.unite || 'partie') + '(s) modifiée(s), ' + (t.ajoutes || 0) + ' ajoutée(s), ' + (t.supprimes || 0) + ' supprimée(s).'));
    controle.hidden = false;
    var h = controle.querySelector('h2'); if (h) { h.focus(); }
  }
  function etatStockage(s) {
    if (s.erreur) { dire(s.erreur === 'editeur_indisponible' ? 'L’éditeur n’a pas pu démarrer : utilisez un autre éditeur du cabinet.'
                                                            : 'À vérifier : enregistrement non confirmé. Votre copie est conservée dans la page ; rien n’est rejoué automatiquement.', true); return; }
    if (s.pending) { dire('Enregistrement en cours…'); return; }
    dire(s.dirty ? 'Modifications non enregistrées.' : (s.ready ? 'Enregistré sur le serveur (version candidate ' + s.revision + ').' : 'Chargement de l’éditeur…'));
  }
  function autoriser(demande) {
    // garde locale (le serveur relit droits, séance et révision à chaque appel) : séance courante, action connue, révision attendue
    if (!etat.seance || demande.session !== etat.seance) { return Promise.reject(new Error('seance')); }
    if (['sauvegarder_candidat', 'exporter_copie', 'lire_version'].indexOf(demande.action) < 0) { return Promise.reject(new Error('action')); }
    if (etat.host && demande.revision !== etat.host.revision) { return Promise.reject(new Error('revision')); }
    return Promise.resolve();
  }
  function sauvegarder(p) {
    var numero = (etat.instantanes += 1);
    var q = '?revision=' + encodeURIComponent(p.expectedRevision) + '&cle=' + encodeURIComponent(p.operation) + '&instantane=' + numero;
    return posterOctets(cfg.api + 'seances/' + encodeURIComponent(etat.seance) + '/candidats' + q, p.bytes).then(function (r) {
      if (!r.ok || (r.j.instantane !== undefined && Number(r.j.instantane) !== numero)) { throw new Error(message(r)); }
      montrerControle(r.j);
      return { durable: r.j.durable === true, revision: r.j.revision };
    });
  }
  function exporter(p) {
    var q = '?format=' + encodeURIComponent(p.extension) + '&cle=' + encodeURIComponent(p.operation);
    return posterOctets(cfg.api + 'seances/' + encodeURIComponent(etat.seance) + '/exports' + q, p.bytes).then(function (r) {
      dire(message(r), !r.ok || r.j.etat !== 'verifie');
      if (!r.ok || r.j.etat !== 'verifie') { throw new Error('export'); }
      return { durable: true };
    });
  }
  function envoyerAuCadre() {
    fetch(cfg.api + 'seances/' + encodeURIComponent(etat.seance) + '/contenu', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (!r.ok) { throw new Error('lecture'); }
      return r.arrayBuffer();
    }).then(function (octets) { return etat.host.ouvrir({ name: cfg.nom, bytes: octets }); })
      .then(function () { dire('Document ouvert. Enregistrez dans l’éditeur pour faire contrôler une nouvelle version.'); })
      .catch(function () { dire('Le document n’a pas pu être transmis à l’éditeur : rechargez la page.', true); });
  }
  window.addEventListener('message', function (ev) {
    if (!etat.host) { return; }
    var type = ev.data && ev.data.type;
    etat.host.recevoir(ev).then(function (r) { if (r.ready && type === cfg.app + ':ready') { envoyerAuCadre(); } });
  });
  function ouvrir(b) {
    b.disabled = true; dire('Ouverture…');
    posterJson(cfg.api + 'seances', { app: cfg.app, s: cfg.s, p: cfg.p, cle: cle() }).then(function (r) {
      if (!r.ok) { dire(message(r), true); b.disabled = false; return; }
      etat.seance = r.j.seance;
      var neuf = iframe.cloneNode(false); neuf.src = cfg.cadre; iframe.replaceWith(neuf); iframe = neuf;
      etat.host = new CraftHost({ app: cfg.app, frame: iframe, origin: location.origin, session: etat.seance, revision: r.j.revision || 0,
        autoriser: autoriser, sauvegarder: sauvegarder, exporter: exporter, onState: etatStockage, idOperation: cle });
      document.querySelector('[data-c109-zone]').hidden = false;
      var f = bouton('fermer'); if (f) { f.hidden = false; }
      dire('Chargement de l’éditeur…');
    }, function () { dire('Serveur injoignable.', true); b.disabled = false; });
  }
  function decider(decision) {
    var revision = (etat.incertain && etat.incertain.revision) || etat.candidat;
    if (!revision) { dire('Aucune version à décider : enregistrez d’abord.', true); return; }
    posterJson(cfg.api + 'seances/' + encodeURIComponent(etat.seance) + '/decision', { decision: decision, revision: revision, cle: cle() }).then(function (r) {
      if (r.ok && r.j.etat === 'a_verifier') { etat.incertain = { revision: revision }; bouton('verifier').hidden = false; bouton('garder').hidden = true; dire(message(r), true); return; }
      if (!r.ok) { dire(message(r), true); return; }
      etat.incertain = null; etat.candidat = 0; bouton('verifier').hidden = true; bouton('garder').hidden = false; controle.hidden = true;
      dire(message(r, 'Fait.'));
    }, function () {
      if (decision === 'garder') { etat.incertain = { revision: revision }; bouton('verifier').hidden = false; bouton('garder').hidden = true; }
      dire('À vérifier : le résultat n’est pas connu. « Vérifier » relit le fichier attendu ; rien d’autre n’est écrit.', true);
    });
  }
  function telechargerCopie() {
    var s = etat.host && etat.host.recuperation; if (!s) { return; }
    var a = el('a'); a.href = URL.createObjectURL(new Blob([s.bytes])); a.download = s.name; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  function fermer() {
    if (!etat.seance) { return; }
    if (etat.host && !etat.host.fermer()) {
      dire('Modifications non confirmées : enregistrez, ou téléchargez votre copie avant de fermer.', true);
      if (etat.host.recuperation && !document.querySelector('[data-c109="copie"]')) {
        var b = el('button', 'ax-btn ghost', 'Télécharger ma copie'); b.type = 'button'; b.setAttribute('data-c109', 'copie'); zoneEtat.after(b);
      }
      return;
    }
    posterJson(cfg.api + 'seances/' + encodeURIComponent(etat.seance) + '/fermer', { cle: cle() }).then(function (r) {
      dire(r.ok ? 'Éditeur fermé : copies de travail retirées. L’original est intact.' : message(r), !r.ok);
      if (r.ok) { etat.seance = ''; document.querySelector('[data-c109-zone]').hidden = true; }
    });
  }
  function appliquerPlan() {
    var zone = document.querySelector('[data-c109-plan]');
    var plan;
    try { plan = JSON.parse(zone.value || '[]'); } catch (err) { dire('Plan illisible (JSON attendu).', true); return; }
    if (!etat.host || !etat.host.ready) { dire('Ouvrez d’abord le document.', true); return; }
    var host = etat.host, editeur = iframe.contentWindow && iframe.contentWindow[cfg.app];
    posterJson(cfg.api + 'seances/' + encodeURIComponent(etat.seance) + '/actions-ia', { plan: plan, revision: host.revision, cle: cle() }).then(function (r) {
      if (!r.ok) { dire(message(r), true); return; }
      if (!editeur || typeof editeur.execute !== 'function') { dire('Cet éditeur n’expose pas de commandes dans ce navigateur : rien n’a été appliqué.', true); return; }
      iframe.style.pointerEvents = 'none';                      // exclusion brève de la saisie pendant le lot
      var etapes = r.j.commandes.slice(), revision = r.j.revision;
      var suite = function () {
        var x = etapes.shift();
        if (!x) { dire('Commandes appliquées : enregistrez puis comparez avant de conserver.'); return Promise.resolve(); }
        return commandeIA({ api: editeur, command: x.commande, params: x.parametres, schema: schemaDuPlan(x),
          checkpoint: function () { return host.etat().pending ? Promise.reject(new Error('enregistrement')) : Promise.resolve(); },
          currentRevision: function () { return host.revision; }, expectedRevision: revision }).then(function (res) {
          if (res.statut !== 'applique') { dire('Résultat à vérifier pour « ' + x.commande + ' » : les commandes suivantes ne sont pas appliquées ; rien n’est rejoué.', true); return; }
          return suite();
        });
      };
      return suite().catch(function () { dire('Application arrêtée (pause, révision ou enregistrement en cours) : rien d’autre n’est appliqué.', true); })
        .then(function () { iframe.style.pointerEvents = ''; });
    });
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-c109]'); if (!b || b.disabled) { return; }
    var a = b.getAttribute('data-c109');
    if (a === 'ouvrir') { ouvrir(b); }
    else if (a === 'garder' || a === 'abandonner') { decider(a); }
    else if (a === 'verifier') { decider('garder'); }
    else if (a === 'fermer') { fermer(); }
    else if (a === 'plan') { appliquerPlan(); }
    else if (a === 'copie') { telechargerCopie(); }
    else if (a === 'retour' && etat.host && etat.host.etat().dirty) { ev.preventDefault(); dire('Modifications non confirmées : enregistrez ou téléchargez votre copie.', true); }
  });
  window.addEventListener('beforeunload', function (ev) {
    if (etat.seance && ((etat.host && etat.host.etat().dirty) || etat.candidat || etat.incertain)) { ev.preventDefault(); ev.returnValue = ''; }
  });
})();
