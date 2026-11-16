/* AxiorHub 5.6.100 (lot B · P1, R02, R03, R09) — liste « À relire » et lecteur commun.
   Une seule liste à gauche (propositions et brouillons), un lecteur à droite, une barre d'actions fixe. Seule la sélection courante peut
   changer le panneau (numéro de sélection + AbortController) ; une saisie non enregistrée n'est jamais perdue (Enregistrer / Rester /
   Abandonner) ; une décision n'est appliquée à l'écran qu'au reçu du serveur ; un 409 garde le texte. Aucun HTML reçu n'est injecté :
   textContent et textarea.value seulement. Le même lecteur se monte dans la fenêtre d'Aujourd'hui :
   window.AX100.lecteur.monter(hote, {id, retour}) → {fermer()}.
   5.6.101 (lot R · R01-R05) : la même liste reçoit les brouillons Gmail, les documents produits et les notes de l'IA ; onglets, libellés et
   commandes viennent du serveur (Valider, Rejeter avec motif, Demander une correction à l'IA, décider d'une proposition, Ouvrir) ; filtres
   par source et par type, mémorisés par personne (POST). Contrat lecteur → mesure : `ax101:ouvert` {type} quand un élément prêt est affiché,
   `ax101:decide` {type, commande} après une décision acceptée par le serveur ; jamais d'identifiant, d'objet ni de texte. */
(function (racine) {
  /* 5.6.103 : les remarques du lecteur (dossier, destinataires, motifs) ne prennent plus la hauteur du projet : deux lignes, puis « Plus de détails ». */
  function ajusterMeta(meta) {
    if (!meta || !meta.parentNode) return;
    var b = meta.nextElementSibling;
    if (!b || !b.hasAttribute('data-ax100-plus')) {
      b = document.createElement('button');
      b.type = 'button'; b.className = 'ax-btn ghost ax100-plus'; b.setAttribute('data-ax100-plus', '');
      b.addEventListener('click', function () {
        var ouvert = meta.classList.toggle('ax100-meta-ouverte');
        b.setAttribute('aria-expanded', ouvert ? 'true' : 'false');
        b.textContent = ouvert ? 'Moins de détails' : 'Plus de détails';
      });
      meta.parentNode.insertBefore(b, meta.nextSibling);
    }
    meta.classList.remove('ax100-meta-ouverte');
    b.setAttribute('aria-expanded', 'false');
    b.textContent = 'Plus de détails';
    b.hidden = true;
    if (!meta.textContent) return;
    var mesurer = function () {   /* hauteur du texte entier (copie hors écran, sans limite de lignes) comparée à deux lignes */
      var lh = parseFloat(getComputedStyle(meta).lineHeight) || 20, copie = meta.cloneNode(true);
      copie.classList.add('ax100-meta-ouverte');
      copie.style.cssText = 'position:absolute;visibility:hidden;max-block-size:none;inline-size:' + meta.clientWidth + 'px';
      meta.parentNode.appendChild(copie);
      var trop = meta.clientWidth > 0 && copie.scrollHeight > lh * 2.5;
      copie.remove();
      b.hidden = !trop;
    };
    if (window.requestAnimationFrame) requestAnimationFrame(mesurer); else mesurer();
  }

  'use strict';
  var AX100 = racine.AX100 = racine.AX100 || {};
  if (AX100.lecteur) return;

  var TEXTES = {
    introuvable: 'Cet élément n’est plus accessible.',
    chargement_impossible: 'Ouverture impossible pour le moment. Réessayez.',
    version_perimee: 'Ce projet a changé depuis son ouverture. Votre saisie est conservée ; rouvrez le projet pour voir la version actuelle.',
    source_indisponible: 'Source momentanément indisponible. Le projet reste lisible ; réessayez.',
    decision_non_confirmee: 'Décision non confirmée par le serveur. Votre texte est conservé ; vous pouvez réessayer sans risque de doublon.',
    sauvegarde_non_confirmee: 'Enregistrement non confirmé. Votre texte reste à l’écran.',
    enregistre: 'Texte enregistré. La réponse reste à relire.',
    retenu: 'Version relue retenue. Rien n’est envoyé ni déposé.',
    ecarte: 'Écarté. Le courriel reçu et ses pièces restent dans la messagerie.',
    reporte: 'Reporté pour vous seul.',
    depose: 'Déposé dans Brouillons et relu dans la messagerie. Rien n’est envoyé.',
    a_verifier: 'Dépôt incertain : vérifiez la messagerie avant toute nouvelle demande. Le projet reste affiché.',
    en_cours: 'Un dépôt de ce projet est déjà en cours.',
    role_insuffisant: 'Votre compte ne permet pas cette action.',
    fin: 'Tout est relu dans ce filtre.',
    liste_impossible: 'La liste n’a pas pu être chargée. Réessayez.',
    correction_demandee: 'Correction demandée à l’IA : la proposition (avant/après) apparaîtra sous « Propositions de l’IA ». Rien n’est modifié sans votre décision.',
    appliquee: 'Proposition appliquée : la nouvelle version du document est à relire.',
    refusee: 'Proposition refusée. Le document n’a pas changé.',
    editeur: 'Le document est ouvert dans l’éditeur : la proposition attend sa fermeture.',
    conflit_edition_concurrente: 'Le document a changé pendant ce temps : la proposition n’a pas été appliquée et rien n’a été écrasé.',
    motif_requis: 'Indiquez le motif du rejet (3 caractères au moins).',
    instruction_invalide: 'Décrivez la correction souhaitée (3 caractères au moins).'
  };
  /* Contrat lecteur → mesure (feuille 5.6.101) : type de l'élément seulement, jamais d'identifiant, d'objet ni de texte. */
  var TYPE_CONTRAT = { proposition: 'courriel', imap: 'courriel', gmail: 'gmail', document: 'document', note_ia: 'note_ia' };
  var DECISIONS = ['retenir', 'ecarter', 'reporter', 'valider', 'rejeter'];
  function emettreDefaut(nom, detail) {
    try {
      if (typeof document !== 'undefined' && typeof racine.CustomEvent === 'function') document.dispatchEvent(new racine.CustomEvent(nom, { detail: detail }));
    } catch (e) { /* écouteur en erreur : la relecture continue */ }
  }

  /* ---------------------------------------------------------------------------------------------- contrôleur (sans DOM) */
  function creerLecteur(o) {
    var api = o.api, ui = o.ui, confirmerBrouillon = o.confirmerBrouillon, nouvelleCle = o.nouvelleCle;
    var emettre = o.emettre || emettreDefaut;
    var typeDe = function (d) { return TYPE_CONTRAT[d && d.type] || ''; };
    var seq = 0, requete = null, projet = null, texte = '', enCours = false, decision = null;
    var onglet = 'projet', reqOnglet = null, seqOnglet = 0, chargement = false;
    var sale = function () { return Boolean(projet && texte !== projet.corps); };
    var annonce = function (code, texte) { ui.annoncer(code, texte); };
    var abandonner = function (r) { if (r) { try { r.abort(); } catch (e) { /* déjà terminée */ } } };
    var invalider = function () { ++seq; ++seqOnglet; abandonner(requete); abandonner(reqOnglet); };
    var oublier = function () { projet = null; texte = ''; decision = null; };

    async function ouvrir(id, options) {
      var forcer = Boolean(options && options.forcer), silencieux = Boolean(options && options.silencieux);
      if (enCours) return false;
      if (sale() && !forcer && !(await confirmerBrouillon('changer'))) return false;
      if (enCours) return false;
      invalider();
      var ticket = seq;
      requete = new AbortController();
      chargement = true;
      ui.attendre(id);
      try {
        var data = await api.detail(id, requete.signal);
        if (ticket !== seq || !data || data.id !== id) return false;          // réponse d'une sélection abandonnée : ignorée
        projet = data; texte = data.corps || ''; decision = null; onglet = 'projet';
        ui.rendre(data);
        if (!silencieux && typeDe(data)) emettre('ax101:ouvert', { type: typeDe(data) });   // élément prêt, texte affiché
        return true;
      } catch (err) {
        if (ticket !== seq || (err && err.name === 'AbortError')) return false;
        if (err && err.status === 404) {                                       // droits retirés ou objet disparu : rien ne reste affiché
          oublier();
          ui.retirerContenu();
        }
        annonce(err && err.status === 404 ? 'introuvable' : 'chargement_impossible');
        return false;
      } finally {
        if (ticket === seq) { chargement = false; ui.finAttente(); }
      }
    }

    function saisir(valeur) {
      if (!projet || enCours || chargement) return false;
      if (texte !== valeur) decision = null;
      texte = valeur;
      return true;
    }

    async function voirSection(section) {
      if (!projet || chargement || ['projet', 'message', 'pieces', 'sources', 'propositions'].indexOf(section) < 0) return;
      onglet = section;
      abandonner(reqOnglet);
      var n = ++seqOnglet;
      ui.selectionnerOnglet(section);                                          // la zone de saisie reste montée, seulement masquée
      if (section === 'projet') return;
      reqOnglet = new AbortController();
      var id = projet.id, revision = projet.revision;
      try {
        var data = await api.section(id, revision, section, reqOnglet.signal);
        if (n === seqOnglet && projet && projet.id === id && onglet === section) ui.rendreSection(section, data);
      } catch (err) {
        if (n !== seqOnglet || (err && err.name === 'AbortError')) return;
        if (err && err.status === 404) {
          invalider(); oublier(); ui.retirerContenu(); annonce('introuvable');
        } else annonce(err && err.status === 409 ? 'version_perimee' : 'source_indisponible');
      }
    }

    async function executer(id, route, req, signatureExtra) {
      var signature = JSON.stringify([id, route, req, signatureExtra || '']);
      if (!decision || decision.signature !== signature) decision = { signature: signature, cle: nouvelleCle() };
      enCours = true;
      invalider();                                                             // un chargement lancé avant l'action ne repeint plus le panneau
      ui.bloquerCommandes(true);
      try {
        var cmd = {}; Object.keys(req).forEach(function (k) { cmd[k] = req[k]; }); cmd.cle = decision.cle;
        return { recu: await api.commande(id, route, cmd) };
      } catch (err) {
        if (err && err.status === 404) {
          oublier(); ui.retirerContenu(); annonce('introuvable');
        } else if (err && err.status === 403) {
          annonce('role_insuffisant');
        } else {
          if (err && err.status === 409 && err.code === 'conflit_edition_concurrente') annonce('conflit_edition_concurrente', err.message);
          else if (err && err.status === 422 && err.code && TEXTES[err.code]) annonce(err.code, err.message);
          else annonce(err && err.status === 409 ? 'version_perimee' : 'decision_non_confirmee');   // texte et sélection restent ; même clé au nouvel essai
        }
        return { erreur: err };
      } finally {
        enCours = false;
        ui.bloquerCommandes(false);
      }
    }

    async function apresRecu(id, recu, type, commande) {
      if (!recu || recu.etat === 'en_cours' || recu.etat === 'a_verifier') {
        annonce(recu ? recu.etat : 'decision_non_confirmee', recu && recu.message);   // aucune disparition avant la preuve d'un effet externe
        return false;
      }
      if (type) emettre('ax101:decide', { type: type, commande: commande });   // décision acceptée par le serveur
      oublier();
      ui.retirerContenu();
      var suivant = await ui.appliquerRecu(id, recu);                          // première ligne encore visible dans la même liste filtrée
      annonce(recu.etat, recu.message);
      if (suivant) await ouvrir(suivant, { forcer: true });
      else ui.aucunSuivant();
      return true;
    }

    async function decider(action, jusqu_a, options) {
      if (enCours || chargement || !projet || DECISIONS.indexOf(action) < 0) return false;
      var motif = String((options && options.motif) || '').trim();
      if (action === 'rejeter' && motif.length < 3) { annonce('motif_requis'); return false; }
      var id = projet.id, type = typeDe(projet);
      if (action !== 'retenir' && sale() && !(options && options.abandonConfirme)) {
        if (!(await confirmerBrouillon(action))) return false;
      }
      if (!projet || projet.id !== id || enCours || chargement) return false;
      var req = { action: action, revision: projet.revision };                 // version relue APRÈS un éventuel enregistrement
      if (action === 'retenir') req.corps = texte;
      if (action === 'reporter') req.jusqu_a = jusqu_a;
      if (action === 'rejeter') req.motif = motif;
      var sortie = await executer(id, 'decision', req);
      if (sortie.erreur) return false;
      return apresRecu(id, sortie.recu, type, action);
    }

    async function corriger(instruction, zone) {
      if (enCours || chargement || !projet || (projet.actions || []).indexOf('corriger') < 0) return false;
      instruction = String(instruction || '').trim();
      if (instruction.length < 3) { annonce('instruction_invalide'); return false; }
      var id = projet.id;
      var sortie = await executer(id, 'corriger', { revision: projet.revision, instruction: instruction, zone: String(zone || '') });
      if (sortie.erreur || !sortie.recu) return false;
      annonce('correction_demandee', sortie.recu.message);                     // l'élément reste ouvert : rien n'est modifié
      if (projet && projet.id === id) voirSection('propositions');
      return true;
    }

    async function deciderProposition(proposition, choix) {
      if (enCours || chargement || !projet || ['accepter', 'refuser'].indexOf(choix) < 0) return false;
      var id = projet.id;
      var sortie = await executer(id, 'proposition', { revision: projet.revision, proposition: String(proposition), decision: choix });
      if (sortie.erreur || !sortie.recu) return false;
      var recu = sortie.recu;
      if (recu.etat === 'appliquee') {                                         // nouvelle version : rouverte, toujours à relire
        await ouvrir(id, { forcer: true, silencieux: true });
        annonce('appliquee', recu.message);
      } else {
        annonce(recu.etat, recu.message);
        if (projet && projet.id === id) voirSection('propositions');
      }
      return true;
    }

    async function deposer() {
      if (enCours || chargement || !projet || (projet.actions || []).indexOf('deposer') < 0) return false;
      var id = projet.id, type = typeDe(projet);
      var req = { revision: projet.revision, corps: texte };                   // texte et destinataires relus, gelés par le serveur
      var sortie = await executer(id, 'deposer', req);
      if (sortie.erreur) return false;
      return apresRecu(id, sortie.recu, type, 'deposer');
    }

    async function enregistrer() {
      if (!projet || enCours || chargement) return false;
      if (!sale()) return true;
      var id = projet.id, corps = texte, revision = projet.revision;
      var sortie = await executer(id, 'enregistrer', { revision: revision, corps: corps });
      if (sortie.erreur) return false;
      var recu = sortie.recu;
      if (recu && (recu.etat === 'a_verifier' || recu.etat === 'en_cours')) { annonce(recu.etat); return false; }
      if (!recu || recu.id !== id || !recu.revision || typeof recu.corps !== 'string') {
        annonce('sauvegarde_non_confirmee');
        return false;
      }
      if (!projet || projet.id !== id) return false;
      var saisieDepuis = texte;                                                // saisie poursuivie pendant l'envoi : conservée
      projet = Object.assign({}, projet, { revision: recu.revision, corps: recu.corps });
      texte = saisieDepuis === corps ? recu.corps : saisieDepuis;
      decision = null;
      ui.rendre(projet, { texte: texte });
      annonce('enregistre', recu.message);
      return true;
    }

    async function fermer() {
      if (enCours || (sale() && !(await confirmerBrouillon('fermer'))) || enCours) return false;
      invalider();
      chargement = false;
      oublier();
      ui.fermerEtRestaurer();
      return true;
    }

    return { ouvrir: ouvrir, saisir: saisir, voirSection: voirSection, decider: decider, deposer: deposer, enregistrer: enregistrer,
             corriger: corriger, deciderProposition: deciderProposition, fermer: fermer, sale: sale, projet: function () { return projet; }, occupe: function () { return enCours || chargement; } };
  }

  function creerApi(o) {
    var base = (o.prefix || '') + '/api440/m56100/relecture';
    var fetcher = o.fetcher || racine.fetch.bind(racine);
    async function demande(path, options) {
      options = options || {};
      var headers = { 'Accept': 'application/json' };
      Object.keys(options.headers || {}).forEach(function (k) { headers[k] = options.headers[k]; });
      var init = { credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: headers };
      ['method', 'body', 'signal'].forEach(function (k) { if (options[k] !== undefined) init[k] = options[k]; });
      var r = await fetcher(base + path, init);
      if (!r.ok) {
        var ex = new Error('requete_non_confirmee');
        ex.status = r.status;
        try { var corps = await r.json(); ex.code = corps.error; ex.message = corps.message || ex.message; } catch (e) { /* corps illisible */ }
        throw ex;
      }
      return r.json();
    }
    var idPath = function (id) { return '/' + encodeURIComponent(id); };
    return {
      liste: function (params, signal) { return demande('?' + new URLSearchParams(params).toString(), { signal: signal }); },
      detail: function (id, signal) { return demande(idPath(id), { signal: signal }); },
      section: function (id, rev, section, signal) {
        return demande(idPath(id) + '/' + section + '?' + new URLSearchParams({ revision: rev }).toString(), { signal: signal });
      },
      commande: function (id, route, cmd) {
        return demande(idPath(id) + '/' + route, { method: 'POST', body: JSON.stringify(cmd),
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': o.csrf || '' } });
      },
      filtre: function (source) {                                               // préférence de la personne (POST, jamais au GET)
        return demande('/filtres', { method: 'POST', body: JSON.stringify({ source: source }),
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': o.csrf || '' } });
      }
    };
  }

  /* ---------------------------------------------------------------------------------------------- outils DOM */
  function el(tag, attrs, enfants) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) n.setAttribute(k, attrs[k] === true ? '' : attrs[k]);
    });
    (enfants || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }

  function nouvelleCle() {
    var octets = new Uint8Array(16);
    racine.crypto.getRandomValues(octets);
    return Array.prototype.map.call(octets, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }

  function echeanceReport(choix, maintenant) {
    var d = new Date(maintenant || Date.now());
    var cible = new Date(d.getTime());
    if (choix === 'apres_midi') {
      cible.setHours(14, 0, 0, 0);
      if (cible <= d) cible = new Date(d.getTime() + 3 * 3600 * 1000);
    } else if (choix === 'lundi') {
      cible.setDate(d.getDate() + (((8 - d.getDay()) % 7) || 7));
      cible.setHours(9, 0, 0, 0);
    } else {
      cible.setDate(d.getDate() + 1);
      cible.setHours(9, 0, 0, 0);
    }
    return Math.floor(cible.getTime() / 1000);
  }

  var GABARIT = [
    '<header class="ax100-entete"><button type="button" class="ax-btn ghost ax100-retour" data-ax100-retour>Retour à la liste</button>',
    '<h2 data-ax100-sujet tabindex="-1">Ouverture…</h2><p class="ax100-meta" data-ax100-meta></p>',
    '<div class="ax100-onglets" role="group" aria-label="Affichage">',
    '<button type="button" class="ax-btn ghost" data-section="projet" aria-pressed="true">Projet de réponse</button>',
    '<button type="button" class="ax-btn ghost" data-section="message" aria-pressed="false">Message reçu</button>',
    '<button type="button" class="ax-btn ghost" data-section="pieces" aria-pressed="false">Pièces</button>',
    '<button type="button" class="ax-btn ghost" data-section="sources" aria-pressed="false">Sources</button>',
    '<button type="button" class="ax-btn ghost" data-section="propositions" aria-pressed="false" hidden>Propositions de l’IA</button>',
    '<a class="ax-btn ghost ax100-plein" data-ax100-plein hidden href="#">Ouvrir en pleine page</a></div></header>',
    '<div class="ax100-corps" data-ax100-corps data-vue-texte="projet"><div class="ax100-projet" data-ax100-projet>',
    '<label data-ax100-label-texte>Texte du projet</label><textarea data-ax100-texte rows="12" spellcheck="true" readonly></textarea></div>',
    '<div class="ax100-secondaire" data-ax100-secondaire tabindex="0" hidden></div></div>',
    '<footer class="ax100-actions" data-ax100-actions><p role="status" aria-live="polite" class="ax100-statut" data-ax100-statut></p>',
    '<div class="ax101-panneau" data-ax101-motif hidden><label data-ax101-label-motif>Motif du rejet (obligatoire)</label>',
    '<textarea data-ax101-motif-texte rows="2" maxlength="500"></textarea>',
    '<button type="button" class="ax-btn" data-ax101-confirmer-rejet>Rejeter la note</button></div>',
    '<div class="ax101-panneau" data-ax101-correction hidden><label data-ax101-label-instruction>Correction souhaitée</label>',
    '<textarea data-ax101-instruction rows="3" maxlength="2000"></textarea>',
    '<label data-ax101-label-zone>Passage visé (facultatif)</label><input data-ax101-zone maxlength="4000">',
    '<p class="ax101-note">Le texte avant/après sera montré pour décision ; rien n’est modifié sans vous.</p>',
    '<button type="button" class="ax-btn" data-ax101-demander-correction>Demander la proposition</button></div>',
    '<button type="button" class="ax-btn" data-action="valider" data-ax101-optionnel disabled hidden>Valider</button>',
    '<button type="button" class="ax-btn ghost" data-action="enregistrer" disabled>Enregistrer</button>',
    '<button type="button" class="ax-btn" data-action="retenir" disabled>Retenir</button>',
    '<button type="button" class="ax-btn ghost" data-action="ecarter" disabled>Écarter</button>',
    '<span class="ax100-report"><button type="button" class="ax-btn ghost" data-action="reporter" aria-expanded="false" disabled>Reporter</button>',
    '<span class="ax100-report-choix" data-ax100-report hidden role="group" aria-label="Reporter jusqu’à">',
    '<button type="button" class="ax-btn ghost" data-report="apres_midi">Cet après-midi (14 h)</button>',
    '<button type="button" class="ax-btn ghost" data-report="demain">Demain 9 h</button>',
    '<button type="button" class="ax-btn ghost" data-report="lundi">Lundi 9 h</button></span></span>',
    '<button type="button" class="ax-btn ghost" data-action="rejeter" data-ax101-optionnel aria-expanded="false" disabled hidden>Rejeter avec motif</button>',
    '<button type="button" class="ax-btn ghost" data-action="corriger" data-ax101-optionnel aria-expanded="false" disabled hidden>Demander une correction à l’IA</button>',
    '<a class="ax-btn ghost" data-ax101-ouvrir hidden href="#">Ouvrir</a>',
    '<button type="button" class="ax-btn ghost ax100-deposer" data-action="deposer" disabled hidden>Déposer dans Brouillons (sans envoi)</button>',
    '</footer>'
  ].join('');

  var compteur = 0;
  function construire(hote) {
    /* Gabarit statique (aucune donnée reçue) ; identifiants propres à cette instance pour les étiquettes. */
    var article = document.createElement('article');
    article.className = 'ax100-lecteur';
    article.setAttribute('data-ax100-lecteur', '');
    article.innerHTML = GABARIT;
    var n = 'ax100-m' + (++compteur);
    var sujet = article.querySelector('[data-ax100-sujet]'); sujet.id = n + '-sujet';
    article.setAttribute('aria-labelledby', sujet.id);
    var zone = article.querySelector('[data-ax100-texte]'); zone.id = n + '-texte';
    article.querySelector('[data-ax100-label-texte]').setAttribute('for', zone.id);
    [['motif-texte', 'label-motif'], ['instruction', 'label-instruction'], ['zone', 'label-zone']].forEach(function (c) {
      var champ = article.querySelector('[data-ax101-' + c[0] + ']'); champ.id = n + '-' + c[0];
      article.querySelector('[data-ax101-' + c[1] + ']').setAttribute('for', champ.id);
    });
    hote.appendChild(article);
    return article;
  }

  function dialogueSaisie(hote, getLecteur) {
    /* Saisie non enregistrée : Enregistrer, Rester ou Abandonner ; jamais de disparition silencieuse. */
    return function (motif) {
      return new Promise(function (resoudre) {
        var Dialog = racine.HTMLDialogElement;
        if (!Dialog) { resoudre(racine.confirm('Votre saisie n’est pas enregistrée. L’abandonner ?')); return; }
        var retour = document.activeElement;
        var dlg = el('dialog', { 'class': 'ax100-dialogue', 'aria-labelledby': 'ax100-dlg-t' }, [
          el('h2', { id: 'ax100-dlg-t', text: 'Saisie non enregistrée' }),
          el('p', { text: motif === 'fermer' ? 'Vous fermez ce projet alors que votre texte n’est pas enregistré.' :
            'Votre texte n’est pas enregistré. Que voulez-vous faire ?' })]);
        var fin = function (valeur) { try { dlg.close(); } catch (e) { /* déjà fermé */ } dlg.remove(); if (retour && retour.focus) retour.focus(); resoudre(valeur); };
        var b1 = el('button', { type: 'button', 'class': 'ax-btn', text: 'Enregistrer' });
        var b2 = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Rester' });
        var b3 = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Abandonner la saisie' });
        b1.addEventListener('click', function () {
          b1.disabled = true;
          getLecteur().enregistrer().then(function (ok) { fin(Boolean(ok)); }, function () { fin(false); });
        });
        b2.addEventListener('click', function () { fin(false); });
        b3.addEventListener('click', function () { fin(true); });
        dlg.addEventListener('cancel', function (ev) { ev.preventDefault(); fin(false); });
        dlg.appendChild(el('div', { 'class': 'ax100-dialogue-actions' }, [b1, b2, b3]));
        (hote.ownerDocument || document).body.appendChild(dlg);
        dlg.showModal();
        b2.focus();
      });
    };
  }

  function uiDom(article, options) {
    var q = function (s) { return article.querySelector(s); };
    var sujet = q('[data-ax100-sujet]'), meta = q('[data-ax100-meta]'), zone = q('[data-ax100-texte]');
    var corps = q('[data-ax100-corps]'), projetZone = q('[data-ax100-projet]'), secondaire = q('[data-ax100-secondaire]');
    var statut = q('[data-ax100-statut]'), plein = q('[data-ax100-plein]'), report = q('[data-ax100-report]');
    var boutons = Array.prototype.slice.call(article.querySelectorAll('[data-action]'));
    var onglets = Array.prototype.slice.call(article.querySelectorAll('[data-section]'));
    var labelTexte = article.querySelector('label[for="' + zone.id + '"]') || article.querySelector('[data-ax100-label-texte]');
    var ouvrirLien = q('[data-ax101-ouvrir]'), panneaux = Array.prototype.slice.call(article.querySelectorAll('.ax101-panneau'));
    var labelDefaut = labelTexte ? labelTexte.textContent : '';
    boutons.concat(onglets).forEach(function (b) { b.setAttribute('data-ax101-defaut', b.textContent); });
    var actions = [], restreint = false;
    var appliquer = function (bloque) {
      boutons.forEach(function (b) {
        var permis = actions.indexOf(b.getAttribute('data-action')) >= 0;
        if (b.getAttribute('data-action') === 'deposer' || b.hasAttribute('data-ax101-optionnel') || restreint) b.hidden = !permis;
        b.disabled = bloque || !permis;
      });
      onglets.forEach(function (b) { b.disabled = bloque; });
    };
    var fermerPanneaux = function () {
      panneaux.forEach(function (x) { x.hidden = true; });
      boutons.forEach(function (b) { if (b.hasAttribute('aria-expanded') && b.getAttribute('data-action') !== 'reporter') b.setAttribute('aria-expanded', 'false'); });
    };
    var ui = {
      annoncer: function (code, texte) { statut.textContent = texte || TEXTES[code] || code || ''; },
      attendre: function () {
        article.setAttribute('aria-busy', 'true');
        statut.textContent = 'Ouverture…';
        appliquer(true);
      },
      finAttente: function () { article.removeAttribute('aria-busy'); },
      rendre: function (d, extra) {
        actions = (d.actions || []).slice();
        restreint = d.type === 'document' || d.type === 'note_ia';             // commandes inutiles masquées, pas seulement grisées
        article.setAttribute('data-type', d.type || '');
        boutons.forEach(function (b) { b.textContent = (d.libelles || {})[b.getAttribute('data-action')] || b.getAttribute('data-ax101-defaut'); });
        onglets.forEach(function (b) {
          var s = b.getAttribute('data-section'), trouve = null;
          (d.onglets || []).forEach(function (o) { if (o[0] === s) trouve = o; });
          b.hidden = d.onglets ? !trouve : s === 'propositions';
          b.textContent = trouve ? trouve[1] : b.getAttribute('data-ax101-defaut');
        });
        if (labelTexte) labelTexte.textContent = d.libelle_texte || labelDefaut;
        fermerPanneaux();
        if (ouvrirLien) {
          ouvrirLien.hidden = !(d.ouvrir && d.ouvrir.href);
          if (d.ouvrir && d.ouvrir.href) { ouvrirLien.setAttribute('href', d.ouvrir.href); ouvrirLien.textContent = d.ouvrir.libelle || 'Ouvrir'; }
        }
        article.setAttribute('data-etat', d.etat || '');
        article.setAttribute('data-id', d.id || '');
        sujet.textContent = d.sujet || '(sans objet)';
        var a = ((d.destinataires || {}).a || []).join(', '), cc = ((d.destinataires || {}).cc || []).join(', ');
        meta.textContent = [d.type_libelle, (d.dossier || {}).libelle, a ? 'À : ' + a : '', cc ? 'Cc : ' + cc : '', d.etat_libelle]
          .filter(Boolean).join(' · ') + (d.motif ? ' — ' + d.motif : '') +
          (d.pieces_jointes_rangement ? ' · ' + d.pieces_jointes_rangement : '');   /* 5.6.102 (lot P) : rangement des pièces jointes du courriel */
        ajusterMeta(meta);   /* 5.6.103 : remarques sur deux lignes, « Plus de détails » si elles débordent */
        zone.value = extra && typeof extra.texte === 'string' ? extra.texte : (d.corps || '');
        zone.readOnly = actions.indexOf('enregistrer') < 0;
        var lien = (d.liens || {}).pleine_page || (d.liens || {}).messagerie || '';
        plein.hidden = !lien;
        if (lien) plein.setAttribute('href', lien);
        if (lien && !(d.liens || {}).pleine_page) { plein.textContent = 'Ouvrir la messagerie'; plein.setAttribute('target', '_blank'); plein.setAttribute('rel', 'noopener'); }
        else { plein.textContent = 'Ouvrir en pleine page'; plein.removeAttribute('target'); }
        ui.selectionnerOnglet('projet');
        statut.textContent = d.etat && d.etat !== 'a_relire' ? (d.etat_libelle || '') : '';
        appliquer(false);
      },
      retirerContenu: function () {
        actions = [];
        restreint = false;
        fermerPanneaux();
        if (ouvrirLien) ouvrirLien.hidden = true;
        if (labelTexte) labelTexte.textContent = labelDefaut;
        sujet.textContent = 'Choisissez un élément';
        meta.textContent = '';
        ajusterMeta(meta);
        zone.value = '';
        zone.readOnly = true;
        secondaire.textContent = '';
        plein.hidden = true;
        article.removeAttribute('data-id');
        appliquer(false);
      },
      bloquerCommandes: function (b) { appliquer(b); },
      selectionnerOnglet: function (section) {
        corps.setAttribute('data-vue-texte', section);
        onglets.forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-section') === section)); });
        projetZone.hidden = section !== 'projet';
        secondaire.hidden = section === 'projet';
        if (section !== 'projet') { secondaire.textContent = ''; secondaire.appendChild(el('p', { 'class': 'ax-muted', text: 'Chargement…' })); }
      },
      rendreSection: function (section, d) {
        secondaire.textContent = '';
        if (d.indisponible) {
          var essai = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Réessayer' });
          essai.addEventListener('click', function () { if (options.reessayer) options.reessayer(section); });
          secondaire.appendChild(el('p', { 'class': 'ax100-limite', text: d.raison || TEXTES.source_indisponible }));
          secondaire.appendChild(essai);
          return;
        }
        if (section === 'message') {
          secondaire.appendChild(el('h3', { text: d.objet || '(sans objet)' }));
          secondaire.appendChild(el('p', { 'class': 'ax-muted', text: [d.expediteur, (d.recu_le || '').slice(0, 16).replace('T', ' ')].filter(Boolean).join(' · ') }));
          secondaire.appendChild(el('pre', { 'class': 'ax100-texte-recu', text: (d.texte || '') + (d.tronque ? '\n\n[… texte tronqué à l’affichage]' : '') }));
        } else if (section === 'pieces') {
          var pieces = d.pieces || [];
          if (!pieces.length) secondaire.appendChild(el('p', { text: 'Aucune pièce jointe.' }));
          var ul = el('ul', { 'class': 'ax100-pieces' });
          pieces.forEach(function (p) { ul.appendChild(el('li', { text: p.nom + ' · ' + (p.type || 'type inconnu') + ' · ' + Math.max(1, Math.round((p.taille || 0) / 1024)) + ' Ko' })); });
          secondaire.appendChild(ul);
          if (d.rangement) secondaire.appendChild(el('p', { 'class': 'ax-muted', text: d.rangement }));   /* 5.6.102 (lot P) */
        } else if (section === 'sources') {
          var ls = d.sources || [];
          secondaire.appendChild(el('h3', { text: ls.length ? 'Sources utilisées' : 'Aucune source enregistrée' }));
          var ul2 = el('ul', { 'class': 'ax100-sources' });
          ls.forEach(function (s) { ul2.appendChild(el('li', { text: (s.titre || s.id) + (s.type ? ' (' + s.type + ')' : '') })); });
          secondaire.appendChild(ul2);
          if ((d.pieces_lues || []).length || (d.pieces_non_lues || []).length) {   /* 5.6.102 (lot P) : fiche de contrôle des pièces jointes */
            secondaire.appendChild(el('h3', { text: 'Pièces jointes du courriel' }));
            var ulp = el('ul', { 'class': 'ax100-sources' });
            (d.pieces_lues || []).forEach(function (p) { ulp.appendChild(el('li', { text: p.nom + (p.partielle ? ' : lue en partie' : ' : lue') })); });
            (d.pieces_non_lues || []).forEach(function (p) { ulp.appendChild(el('li', { 'class': 'ax100-limite', text: p.nom + ' : non lue' + (p.motif ? ' (' + p.motif + ')' : '') })); });
            secondaire.appendChild(ulp);
          }
          if ((d.limites || []).length) {
            secondaire.appendChild(el('h3', { text: 'Limites signalées' }));
            var ul3 = el('ul', { 'class': 'ax100-limites' });
            d.limites.forEach(function (x) { ul3.appendChild(el('li', { text: x })); });
            secondaire.appendChild(ul3);
          }
        } else if (section === 'propositions') {                               // avant / après, écrits (jamais la couleur seule)
          var props = d.propositions || [];
          if (!props.length) secondaire.appendChild(el('p', { text: 'Aucune proposition pour ce document. « Demander une correction à l’IA » en prépare une, à comparer avant toute décision.' }));
          var ol = el('ol', { 'class': 'ax101-propositions' });
          props.forEach(function (p) {
            var li = el('li', { 'class': 'ax101-proposition' }, [el('h3', { text: p.titre || 'Proposition' })]);
            if (p.instruction) li.appendChild(el('p', { 'class': 'ax-muted', text: 'Consigne : ' + p.instruction }));
            (p.comparaison || []).forEach(function (c) {
              li.appendChild(el('div', { 'class': 'ax101-avant-apres' }, [
                el('p', {}, [el('strong', { text: 'Avant : ' }), el('del', { text: c.avant || '(rien)' })]),
                el('p', {}, [el('strong', { text: 'Après : ' }), el('ins', { text: c.apres || '(passage retiré)' })]),
                c.motif ? el('p', { 'class': 'ax-muted', text: 'Motif : ' + c.motif }) : null]));
            });
            if (p.note) li.appendChild(el('p', { 'class': 'ax100-limite', text: p.note }));
            if ((p.actions || []).length) {
              li.appendChild(el('div', { 'class': 'ax101-proposition-actions', role: 'group', 'aria-label': 'Décision sur cette proposition' },
                p.actions.map(function (a) {
                  return el('button', { type: 'button', 'class': a === 'accepter' ? 'ax-btn' : 'ax-btn ghost', 'data-ax101-proposition': p.id,
                                        'data-ax101-decision': a, text: a === 'accepter' ? 'Appliquer cette proposition' : 'Refuser la proposition' });
                })));
            }
            ol.appendChild(li);
          });
          secondaire.appendChild(ol);
          if (d.note) secondaire.appendChild(el('p', { 'class': 'ax-muted', text: d.note }));
        }
      },
      basculerPanneau: function (nom, bouton) {
        var cible = q('[data-ax101-' + nom + ']'), ouvert = cible && cible.hidden;
        fermerPanneaux();
        if (!cible) return;
        cible.hidden = !ouvert;
        if (bouton) bouton.setAttribute('aria-expanded', String(Boolean(ouvert)));
        if (ouvert) { var champ = cible.querySelector('textarea'); if (champ) champ.focus(); }
      },
      fermerPanneaux: fermerPanneaux,
      appliquerRecu: function (id, recu) { return options.appliquerRecu ? options.appliquerRecu(id, recu) : null; },
      aucunSuivant: function () { statut.textContent = TEXTES.fin; if (options.aucunSuivant) options.aucunSuivant(); },
      fermerEtRestaurer: function () { ui.retirerContenu(); if (options.fermer) options.fermer(); }
    };
    report.hidden = true;
    return ui;
  }

  function relier(article, lecteur, ui101) {
    var zone = article.querySelector('[data-ax100-texte]');
    var report = article.querySelector('[data-ax100-report]');
    var boutonReport = article.querySelector('[data-action="reporter"]');
    zone.addEventListener('input', function () {
      if (!lecteur.saisir(zone.value)) { var p = lecteur.projet(); if (p) zone.value = p.corps; }
    });
    article.addEventListener('click', function (ev) {
      var cible = ev.target.closest ? ev.target.closest('button') : null;
      if (!cible || !article.contains(cible)) return;
      var section = cible.getAttribute('data-section');
      if (section) { lecteur.voirSection(section); return; }
      var proposition = cible.getAttribute('data-ax101-proposition');
      if (proposition) { lecteur.deciderProposition(proposition, cible.getAttribute('data-ax101-decision')); return; }
      if (cible.hasAttribute('data-ax101-confirmer-rejet')) {
        var motif = article.querySelector('[data-ax101-motif-texte]');
        lecteur.decider('rejeter', null, { motif: motif.value }).then(function (ok) { if (ok) motif.value = ''; });
        return;
      }
      if (cible.hasAttribute('data-ax101-demander-correction')) {
        var instr = article.querySelector('[data-ax101-instruction]'), zonePassage = article.querySelector('[data-ax101-zone]');
        lecteur.corriger(instr.value, zonePassage.value).then(function (ok) {
          if (ok) { instr.value = ''; zonePassage.value = ''; if (ui101) ui101.fermerPanneaux(); }
        });
        return;
      }
      var choix = cible.getAttribute('data-report');
      if (choix) {
        report.hidden = true; boutonReport.setAttribute('aria-expanded', 'false');
        lecteur.decider('reporter', echeanceReport(choix));
        return;
      }
      var action = cible.getAttribute('data-action');
      if (action === 'reporter') {
        report.hidden = !report.hidden;
        boutonReport.setAttribute('aria-expanded', String(!report.hidden));
        if (!report.hidden) { var premier = report.querySelector('button'); if (premier) premier.focus(); }
      } else if (action === 'enregistrer') lecteur.enregistrer();
      else if (action === 'deposer') lecteur.deposer();
      else if (action === 'retenir' || action === 'ecarter' || action === 'valider') lecteur.decider(action);
      else if ((action === 'rejeter' || action === 'corriger') && ui101) ui101.basculerPanneau(action === 'rejeter' ? 'motif' : 'correction', cible);
    });
    article.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && !report.hidden) { report.hidden = true; boutonReport.setAttribute('aria-expanded', 'false'); boutonReport.focus(); ev.stopPropagation(); }
      if (ev.key === 'Escape' && ui101 && article.querySelector('.ax101-panneau:not([hidden])')) { ui101.fermerPanneaux(); ev.stopPropagation(); }
      if ((ev.ctrlKey || ev.metaKey) && (ev.key === 's' || ev.key === 'S')) { ev.preventDefault(); lecteur.enregistrer(); }
    });
  }

  function meta(nom) { var m = document.querySelector('meta[name="' + nom + '"]'); return m ? m.content : ''; }

  /* ---------------------------------------------------------------------------------------------- montage seul (fenêtre d'Aujourd'hui) */
  function monter(hote, options) {
    options = options || {};
    var article = hote.querySelector('[data-ax100-lecteur]') || construire(hote);
    var api = options.api || creerApi({ prefix: meta('axiorhub-prefix'), csrf: meta('axiorhub-csrf') });
    var lecteur = null;
    var ui = uiDom(article, {
      appliquerRecu: function () { return null; },
      aucunSuivant: function () { if (options.apresDecision) options.apresDecision(); },
      fermer: function () { if (options.retour) options.retour(); },
      reessayer: function (s) { lecteur.voirSection(s); }
    });
    lecteur = creerLecteur({ api: api, ui: ui, confirmerBrouillon: options.confirmerBrouillon || dialogueSaisie(hote, function () { return lecteur; }),
                             nouvelleCle: options.nouvelleCle || nouvelleCle });
    relier(article, lecteur, ui);
    if (options.id) lecteur.ouvrir(String(options.id));
    return { fermer: function () { return lecteur.fermer(); }, ouvrir: function (id) { return lecteur.ouvrir(id); }, sale: lecteur.sale, lecteur: lecteur };
  }

  /* ---------------------------------------------------------------------------------------------- page Courriels « À relire » */
  function monterPage(section) {
    var api = creerApi({ prefix: meta('axiorhub-prefix'), csrf: meta('axiorhub-csrf') });
    var article = section.querySelector('[data-ax100-lecteur]');
    var liste = section.querySelector('[data-ax100-lignes]'), zoneListe = section.querySelector('[data-ax100-zone-liste]');
    var vide = section.querySelector('[data-ax100-vide]'), suite = section.querySelector('[data-ax100-suite]');
    var totalEl = section.querySelector('[data-ax100-total]'), imapEl = section.querySelector('[data-ax100-imap]');
    var gmailEl = section.querySelector('[data-ax101-gmail]'), alerteEl = section.querySelector('[data-ax101-alerte]');
    var LOCALES = { tous: 1, courriel: 1, proposition: 1, document: 1, note_ia: 1 };
    var inclutImap = function (s) { return s === 'tous' || s === 'courriel' || s === 'brouillon'; };
    var inclutGmail = function (s) { return s === 'tous' || s === 'gmail'; };
    var alertes = {};
    var majAlerte = function (cle, texte) {
      if (!alerteEl) return;
      if (texte) alertes[cle] = texte; else delete alertes[cle];
      var t = Object.keys(alertes).map(function (k) { return alertes[k]; }).join(' ');
      alerteEl.textContent = t; alerteEl.hidden = !t;
    };
    var recherche = section.querySelector('[data-ax100-recherche]');
    var etat = { source: section.getAttribute('data-source') || 'tous', q: section.getAttribute('data-q') || '',
                 curseur: section.getAttribute('data-curseur') || '', totalLocal: parseInt(totalEl.textContent, 10), nbImap: 0, imapLu: false,
                 nbGmail: 0, gmailLu: false,
                 seqListe: 0, reqListe: null, courant: '', declencheur: null };
    if (isNaN(etat.totalLocal)) etat.totalLocal = null;
    var lecteur = null;

    var afficherTotal = function () {
      var t = etat.totalLocal === null ? null : etat.totalLocal + (etat.imapLu ? etat.nbImap : 0) + (etat.gmailLu ? etat.nbGmail : 0);
      var attente = (inclutImap(etat.source) && !etat.imapLu) || (inclutGmail(etat.source) && !etat.gmailLu);
      totalEl.textContent = t === null ? '…' : String(t) + (attente ? ' +' : '');
    };
    var lignes = function () { return Array.prototype.slice.call(liste.querySelectorAll('li[data-review-id]')); };
    var marquer = function (id) {
      etat.courant = id;
      lignes().forEach(function (li) {
        var a = li.querySelector('a');
        if (li.getAttribute('data-review-id') === id) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
      });
    };
    var adresse = function () {
      var p = new URLSearchParams();
      if (etat.source && etat.source !== 'tous') p.set('source', etat.source);
      if (etat.q) p.set('q', etat.q);
      if (etat.courant) p.set('item', etat.courant);
      var u = location.pathname + (p.toString() ? '?' + p.toString() : '');
      try { history.replaceState(null, '', u); } catch (e) { /* adresse inchangée */ }
    };
    var ligne = function (x) {
      var a = el('a', { href: x.href, 'data-review-id': x.id }, [
        el('span', { 'class': 'ax100-l1' }, [el('strong', { text: x.objet }), el('em', { 'class': 'ax100-badge ax100-' + x.type, text: x.type_libelle })]),
        el('span', { 'class': 'ax100-l2', text: [x.dossier, x.date_affichee, x.statut_libelle].filter(Boolean).join(' · ') + (x.a ? ' · À : ' + x.a : '') })]);
      return el('li', { 'data-review-id': x.id, 'data-maj': String(x.maj || 0), 'data-type': x.type }, [a]);
    };
    var inserer = function (x) {   // tri unique : date décroissante, puis identifiant
      if (liste.querySelector('li[data-review-id="' + (racine.CSS && CSS.escape ? CSS.escape(x.id) : x.id) + '"]')) return;
      var noeud = ligne(x), avant = null;
      lignes().some(function (li) {
        var m = parseInt(li.getAttribute('data-maj'), 10) || 0, id = li.getAttribute('data-review-id');
        if (m < (x.maj || 0) || (m === (x.maj || 0) && id < x.id)) { avant = li; return true; }
        return false;
      });
      liste.insertBefore(noeud, avant);
    };
    var majVide = function () {
      vide.hidden = lignes().length > 0 || (inclutImap(etat.source) && !etat.imapLu) || (inclutGmail(etat.source) && !etat.gmailLu);
    };

    function chargerListe(continuer) {
      if (etat.reqListe) { try { etat.reqListe.abort(); } catch (e) { /* déjà terminée */ } }
      var n = ++etat.seqListe;
      etat.reqListe = new AbortController();
      var params = { source: etat.source };
      if (etat.q) params.q = etat.q;
      if (continuer && etat.curseur) params.curseur = etat.curseur;
      if (!LOCALES[etat.source]) { liste.textContent = ''; etat.totalLocal = 0; etat.curseur = ''; suite.hidden = true; return Promise.resolve(); }
      suite.disabled = true;
      return api.liste(params, etat.reqListe.signal).then(function (d) {
        if (n !== etat.seqListe) return;
        if (!continuer) { liste.textContent = ''; etat.totalLocal = d.total === null || d.total === undefined ? null : d.total; }
        d.lignes.forEach(inserer);
        etat.curseur = d.curseur || '';
        suite.hidden = !etat.curseur;
        afficherTotal(); majVide(); if (etat.courant) marquer(etat.courant);
      }).catch(function (err) {
        if (n !== etat.seqListe || (err && err.name === 'AbortError')) return;
        if (err && err.code === 'curseur_invalide') { etat.curseur = ''; return chargerListe(false); }
        vide.hidden = false; vide.textContent = TEXTES.liste_impossible;
      }).then(function () { suite.disabled = false; });
    }

    function chargerBrouillons() {
      if (!inclutImap(etat.source)) return Promise.resolve();
      var n = etat.seqListe;
      imapEl.textContent = 'Brouillons de la messagerie : lecture en cours…';
      var params = { source: 'brouillon' };
      if (etat.q) params.q = etat.q;
      return api.liste(params).then(function (d) {
        if (n !== etat.seqListe) return;
        var s = (d.sources || {}).imap || {};
        etat.imapLu = true;
        etat.nbImap = d.lignes.length;
        d.lignes.forEach(inserer);
        imapEl.textContent = s.etat === 'indisponible' ? 'Brouillons de la messagerie : ' + (s.raison || 'indisponibles.') :
          'Brouillons de la messagerie lus' + (s.complet === false ? ' (40 plus récents seulement)' : '') + '.';
        if (s.etat === 'indisponible') { totalEl.title = 'Brouillons de la messagerie indisponibles : total partiel.'; }
        majAlerte('imap', s.etat === 'indisponible' ? 'Brouillons de la messagerie indisponibles : ' + (s.raison || 'total partiel.') : '');
        afficherTotal(); majVide(); if (etat.courant) marquer(etat.courant);
      }).catch(function (err) {
        if (n !== etat.seqListe) return;
        etat.imapLu = false;
        imapEl.textContent = 'Brouillons de la messagerie : lecture impossible (' + ((err && err.message) || 'erreur') + '). Les propositions restent disponibles.';
        majAlerte('imap', 'Brouillons de la messagerie : lecture impossible ; le reste de la liste est disponible.');
        majVide();
      });
    }

    function chargerGmail() {                                                  // 5.6.101 (R01) : appel séparé, une panne Gmail ne bloque rien
      if (!inclutGmail(etat.source)) return Promise.resolve();
      var n = etat.seqListe;
      if (gmailEl) gmailEl.textContent = 'Brouillons Gmail : lecture en cours…';
      var params = { source: 'gmail' };
      if (etat.q) params.q = etat.q;
      return api.liste(params).then(function (d) {
        if (n !== etat.seqListe) return;
        var s = (d.sources || {}).gmail || {};
        etat.gmailLu = true;
        etat.nbGmail = d.lignes.length;
        d.lignes.forEach(inserer);
        var texte = s.etat === 'indisponible' ? 'Brouillons Gmail masqués : ' + (s.raison || 'Gmail indisponible.') :
          (s.etat === 'a_jour' ? 'Brouillons Gmail lus.' : 'Gmail non relié pour votre compte : aucun brouillon Gmail.');
        if (gmailEl) gmailEl.textContent = texte;
        majAlerte('gmail', s.etat === 'indisponible' ? texte : '');
        afficherTotal(); majVide(); if (etat.courant) marquer(etat.courant);
      }).catch(function () {
        if (n !== etat.seqListe) return;
        etat.gmailLu = true; etat.nbGmail = 0;
        if (gmailEl) gmailEl.textContent = 'Brouillons Gmail : lecture impossible.';
        majAlerte('gmail', 'Brouillons Gmail masqués : lecture impossible pour le moment ; le reste de la liste est disponible.');
        afficherTotal(); majVide();
      });
    }

    function suivantDe(id) {
      var ls = lignes();
      var i = ls.findIndex(function (li) { return li.getAttribute('data-review-id') === id; });
      var apres = i >= 0 ? ls[i + 1] : null;
      return apres ? apres.getAttribute('data-review-id') : null;
    }

    var ui = uiDom(article, {
      appliquerRecu: function (id, recu) {
        if (['retenu', 'ecarte', 'depose', 'reporte'].indexOf(recu.etat) < 0) return null;
        var suivant = suivantDe(id);
        var li = liste.querySelector('li[data-review-id="' + id + '"]');
        if (li) {
          var type = li.getAttribute('data-type');
          li.remove();
          if (type === 'imap') etat.nbImap = Math.max(0, etat.nbImap - 1);
          else if (type === 'gmail') etat.nbGmail = Math.max(0, etat.nbGmail - 1);
          else if (etat.totalLocal !== null) etat.totalLocal = Math.max(0, etat.totalLocal - 1);
        }
        afficherTotal();
        if (suivant) { marquer(suivant); return suivant; }
        if (etat.curseur) {
          return chargerListe(true).then(function () {
            var premier = lignes()[0];
            if (premier) { marquer(premier.getAttribute('data-review-id')); return premier.getAttribute('data-review-id'); }
            return null;
          });
        }
        var reste = lignes()[0];
        if (reste) { marquer(reste.getAttribute('data-review-id')); return reste.getAttribute('data-review-id'); }
        return null;
      },
      aucunSuivant: function () {
        etat.courant = ''; adresse(); majVide();
        section.removeAttribute('data-vue');
        vide.hidden = false; vide.textContent = TEXTES.fin;
        var filtre = section.querySelector('[data-ax100-filtre][aria-current="page"]') || vide;
        filtre.focus();
      },
      fermer: function () {
        section.removeAttribute('data-vue');
        var li = etat.courant ? liste.querySelector('li[data-review-id="' + etat.courant + '"] a') : null;
        etat.courant = ''; marquer(''); adresse();
        if (li) li.focus({ preventScroll: false });
      },
      reessayer: function (s) { lecteur.voirSection(s); }
    });
    lecteur = creerLecteur({ api: api, ui: ui, confirmerBrouillon: dialogueSaisie(section, function () { return lecteur; }), nouvelleCle: nouvelleCle });
    relier(article, lecteur, ui);

    function ouvrirLigne(id, declencheur) {
      var precedent = etat.courant;
      marquer(id);
      section.setAttribute('data-vue', 'detail');
      etat.declencheur = declencheur || null;
      return lecteur.ouvrir(id).then(function (ok) {
        if (!ok && lecteur.projet() && lecteur.projet().id === precedent) { marquer(precedent); return false; }   // l'avocat est resté sur sa saisie
        if (ok) { adresse(); var t = article.querySelector('[data-ax100-sujet]'); if (racine.matchMedia && racine.matchMedia('(max-width: 64rem)').matches) t.focus(); }
        return ok;
      });
    }

    liste.addEventListener('click', function (ev) {
      var a = ev.target.closest ? ev.target.closest('a[data-review-id]') : null;
      if (!a || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button) return;   // clic ordinaire : lecteur ; clic avec modificateur : page de repli
      ev.preventDefault();
      ouvrirLigne(a.getAttribute('data-review-id'), a);
    });
    liste.addEventListener('keydown', function (ev) {
      if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
      var liens = Array.prototype.slice.call(liste.querySelectorAll('a[data-review-id]'));
      var i = liens.indexOf(document.activeElement);
      if (i < 0) return;
      ev.preventDefault();
      var cible = liens[ev.key === 'ArrowDown' ? Math.min(liens.length - 1, i + 1) : Math.max(0, i - 1)];
      if (cible) cible.focus();
    });
    suite.addEventListener('click', function () { chargerListe(true); });

    async function changerFiltre(source, q) {
      if (lecteur.sale() && !(await dialogueSaisie(section, function () { return lecteur; })('changer'))) return;
      if (lecteur.projet()) await lecteur.fermer();
      if (source !== etat.source) { try { api.filtre(source).catch(function () { /* préférence non mémorisée : le filtre s'applique quand même */ }); } catch (e) { /* idem */ } }
      etat.source = source; etat.q = q; etat.curseur = ''; etat.imapLu = false; etat.nbImap = 0; etat.gmailLu = false; etat.nbGmail = 0; etat.courant = '';
      majAlerte('imap', ''); majAlerte('gmail', '');
      section.setAttribute('data-source', source);
      Array.prototype.forEach.call(section.querySelectorAll('[data-ax100-filtre]'), function (f) {
        if (f.getAttribute('data-ax100-filtre') === source) f.setAttribute('aria-current', 'page'); else f.removeAttribute('aria-current');
      });
      if (recherche && recherche.elements.source) recherche.elements.source.value = source;
      vide.textContent = TEXTES.fin;
      adresse();
      liste.textContent = '';
      await chargerListe(false);
      await chargerBrouillons();
      await chargerGmail();
    }
    section.addEventListener('click', function (ev) {
      var f = ev.target.closest ? ev.target.closest('[data-ax100-filtre]') : null;
      if (f && !ev.ctrlKey && !ev.metaKey) { ev.preventDefault(); changerFiltre(f.getAttribute('data-ax100-filtre'), etat.q); }
      if (ev.target.closest && ev.target.closest('[data-ax100-retour]')) { ev.preventDefault(); lecteur.fermer(); }
    });
    if (recherche) recherche.addEventListener('submit', function (ev) {
      ev.preventDefault();
      changerFiltre(etat.source, String(recherche.elements.q.value || '').slice(0, 120));
    });
    section.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && section.getAttribute('data-vue') === 'detail' && !document.querySelector('dialog[open]')) lecteur.fermer();
    });
    racine.addEventListener('beforeunload', function (ev) { if (lecteur.sale()) { ev.preventDefault(); ev.returnValue = ''; } });

    /* Hauteur : la grille occupe l'espace restant du poste ; seules la liste et le corps du lecteur défilent. */
    var ajuster = function () {
      var haut = Math.max(0, Math.round(section.getBoundingClientRect().top + (racine.scrollY || 0)));
      section.style.setProperty('--ax100-haut', haut + 'px');
    };
    ajuster();
    racine.addEventListener('resize', ajuster);

    etat.courant = section.getAttribute('data-item') || '';
    afficherTotal();
    var depart = !LOCALES[etat.source] ? chargerListe(false) : Promise.resolve();
    depart.then(chargerBrouillons).then(chargerGmail);
    if (etat.courant) ouvrirLigne(etat.courant, null);
    return { lecteur: lecteur, recharger: function () { return changerFiltre(etat.source, etat.q); } };
  }

  AX100.lecteur = { creerLecteur: creerLecteur, creerApi: creerApi, monter: monter, monterPage: monterPage, echeanceReport: echeanceReport, TEXTES: TEXTES,
                    TYPE_CONTRAT: TYPE_CONTRAT };

  if (typeof document !== 'undefined') {
    var demarrer = function () {
      Array.prototype.forEach.call(document.querySelectorAll('[data-ax100-relecture]'), function (s) {
        if (!s.__ax100) s.__ax100 = monterPage(s);
      });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', demarrer); else demarrer();
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = AX100.lecteur;
})(typeof window !== 'undefined' ? window : globalThis);
