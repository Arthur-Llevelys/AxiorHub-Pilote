/* AxiorHub 5.6.101 (lot V) — volet de relecture d'Aujourd'hui : contenu proposé d'abord, puis une barre de quatre actions identique pour toutes
   les natures (Valider, Modifier manuellement, Instruction complémentaire, Écarter), collée en bas du volet.
   - Le volet lui-même (ancré à droite ou fenêtre flottante, Précédent/Suivant, Détacher/Ancrer) est tenu par v5696-accueil.js, qui appelle
     window.AX101volet.monter() avec le détail servi par /api440/m5689/accueil/action (HTML construit et échappé par le serveur).
   - Chaque bouton exécute le descripteur fourni par le serveur (volet56101.barre_*) : POST JSON sur la route EXISTANTE de la nature, avec une
     clé d'idempotence tirée ici et gardée pour une nouvelle tentative de la même action ; 409 = « une autre version existe », saisie conservée.
     Un bouton « lien » est un lien ordinaire ; un bouton « indisponible » dit pourquoi (aria-disabled, texte dans la ligne d'état).
   - Courriels : rendu HTML dans un cadre isolé (<iframe sandbox="">, route du serveur à politique stricte) ; bascule « HTML · Texte brut »
     mémorisée par navigateur (localStorage dans try/catch : l'écran fonctionne sans).
   - Document Word ou PDF : cadre de l'éditeur (ONLYOFFICE / Euro-Office) en lecture, puis en édition sur « Modifier » ; l'état vient du cadre
     par postMessage (même origine seulement) ; sans réponse en 10 s ou en cas d'échec : aperçu simplifié existant et lien « Ouvrir dans l'éditeur ».
     Changer d'élément retire le cadre (une seule instance d'éditeur à la fois).
   Aucun envoi, aucun dépôt externe : seules les routes de décision existantes sont appelées, sur un geste de la personne. */
(function (racine) {
  'use strict';

  /* ------------------------------------------------------------------ fonctions pures (testées sous Node : tests/volet56101.cjs) */
  var ACTIONS = ['valider', 'modifier', 'instruction', 'ecarter'];
  var ACTIF = { actif: 1, edition: 1, office: 1, echeance: 1 };
  function nouvelleCle(alea) {
    var octets = alea ? alea(16) : null;
    if (!octets) {
      octets = [];
      var c = racine.crypto || (racine.msCrypto);
      if (c && c.getRandomValues) { var t = new Uint8Array(16); c.getRandomValues(t); for (var i = 0; i < 16; i++) octets.push(t[i]); }
      else { for (var j = 0; j < 16; j++) octets.push(Math.floor(Math.random() * 256)); }
    }
    return 'v101-' + Array.prototype.map.call(octets, function (o) { return ('0' + (o & 255).toString(16)).slice(-2); }).join('');
  }
  /* Corps envoyé pour une action : champs fixes du serveur, clé d'idempotence, texte, consigne, motif ou échéance du report. Jamais d'autre champ. */
  function corpsAction(desc, saisie, maintenantMs, cle) {
    var corps = {}, k;
    for (k in (desc.corps || {})) if (Object.prototype.hasOwnProperty.call(desc.corps, k)) corps[k] = desc.corps[k];
    corps.cle = cle;
    saisie = saisie || {};
    if (desc.texte) corps[desc.texte] = String(saisie.texte === undefined ? '' : saisie.texte);
    if (desc.champ) corps[desc.champ] = String(saisie.consigne || '').replace(/\s+/g, ' ').trim();
    if (desc.motif) corps[desc.motif] = String(saisie.motif || '').trim();
    if (desc.choix && saisie.choix !== undefined) corps[desc.choix.nom] = String(saisie.choix);
    if (desc.jusqu_a) corps.jusqu_a = Math.floor(maintenantMs / 1000) + Number(desc.jusqu_a);
    if (saisie.date) { corps.new_due = String(saisie.date); corps.reason = String(saisie.motif || '').trim(); }
    return corps;
  }
  /* Contrôle avant envoi (mêmes bornes que les services) ; '' si la saisie convient, sinon la clé du message. */
  function controle(desc, saisie) {
    saisie = saisie || {};
    if (desc.texte && !String(saisie.texte || '').trim()) return 'texte_requis';
    if (desc.champ) { var n = String(saisie.consigne || '').replace(/\s+/g, ' ').trim().length; if (n < 3 || n > 2000) return 'consigne_courte'; }
    if (desc.motif) { var l = String(saisie.motif || '').trim().length; if (l < 3 || l > 500) return 'motif_requis'; }
    if (saisie.date !== undefined) { if (!/^\d{4}-\d{2}-\d{2}$/.test(String(saisie.date))) return 'texte_requis'; var r = String(saisie.motif || '').trim().length; if (r < 3) return 'motif_requis'; }
    return '';
  }
  /* Élément voisin dans l'ordre des lignes visibles (clés « nature:cle »), sans doublon ; null aux bornes. */
  function voisin(cles, courant, sens) {
    var vues = [];
    cles.forEach(function (c) { if (vues.indexOf(c) < 0) vues.push(c); });
    var i = vues.indexOf(courant);
    if (i < 0) return sens > 0 ? (vues[0] || null) : null;
    var j = i + (sens > 0 ? 1 : -1);
    return j >= 0 && j < vues.length ? vues[j] : null;
  }
  function avecFormat(url, format) {
    return String(url || '').replace(/([?&])format=[^&]*/, '$1format=' + (format === 'texte' ? 'texte' : 'html'));
  }
  function etatBouton(desc) { return !desc ? 'indisponible' : (ACTIF[desc.etat] ? 'actif' : (desc.etat === 'lien' ? 'lien' : 'indisponible')); }

  var exportes = { nouvelleCle: nouvelleCle, corpsAction: corpsAction, controle: controle, voisin: voisin, avecFormat: avecFormat, etatBouton: etatBouton, ACTIONS: ACTIONS };
  if (typeof module === 'object' && module && module.exports) { module.exports = exportes; }
  var doc = racine.document;
  if (!doc || !doc.querySelector) return;

  /* ------------------------------------------------------------------ outils */
  function meta(n) { var x = doc.querySelector('meta[name="' + n + '"]'); return x ? x.content : ''; }
  var prefix = meta('axiorhub-prefix') || '', csrf = meta('axiorhub-csrf');
  function lire(cle, defaut) { try { var v = racine.localStorage.getItem(cle); return v === null ? defaut : v; } catch (e) { return defaut; } }
  function ecrire(cle, v) { try { racine.localStorage.setItem(cle, v); } catch (e) { /* navigation privée : préférence non gardée */ } }
  function el(nom, classe, texte) { var x = doc.createElement(nom); if (classe) x.className = classe; if (texte !== undefined) x.textContent = texte; return x; }
  function poster(api, corps) {
    return racine.fetch(prefix + '/api440/' + api, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok || (d && d.error)) { var e = new Error((d && d.message) || ('HTTP ' + r.status)); e.statut = r.status; e.code = d && d.error; throw e; }
          return d;
        });
      });
  }

  /* ------------------------------------------------------------------ état du volet monté */
  var courant = null;      // { fenetre, contenu, d, ctx, pied, panneau, statut, office, minuterie, ecoute }

  function demonter() {
    if (!courant) return;
    if (courant.minuterie) racine.clearTimeout(courant.minuterie);
    if (courant.ecoute) racine.removeEventListener('message', courant.ecoute);
    var o = courant.contenu && courant.contenu.querySelector('[data-v101-office]');
    if (o) o.remove();                                      // l'éditeur précédent est détruit avec son cadre
    if (courant.pied) { courant.pied.hidden = true; }
    var rep = courant.fenetre && courant.fenetre.querySelector('.v101-reporter');
    if (rep) rep.hidden = true;
    courant = null;
  }

  function annoncer(texte, erreur) {
    if (!courant) return;
    courant.statut.textContent = texte || '';
    courant.statut.setAttribute('data-erreur', erreur ? '1' : '0');
  }

  function preferenceFormat() { return lire('ax101:format', 'html') === 'texte' ? 'texte' : 'html'; }
  function appliquerFormat(contenu, format) {
    Array.prototype.forEach.call(contenu.querySelectorAll('[data-v101-format]'), function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-v101-format') === format ? 'true' : 'false'); });
    Array.prototype.forEach.call(contenu.querySelectorAll('iframe[data-v101-cadre]'), function (f) {
      if (f.getAttribute('src')) f.setAttribute('src', avecFormat(f.getAttribute('src'), format));
      else if (f.getAttribute('data-src')) f.setAttribute('data-src', avecFormat(f.getAttribute('data-src'), format));
    });
  }
  function relierCourriel(contenu) {
    appliquerFormat(contenu, preferenceFormat());
    contenu.addEventListener('click', function (ev) {
      var b = ev.target.closest && ev.target.closest('[data-v101-format]');
      if (!b) return;
      var f = b.getAttribute('data-v101-format');
      ecrire('ax101:format', f);
      appliquerFormat(contenu, f);
    });
    Array.prototype.forEach.call(contenu.querySelectorAll('details[data-v101-recu]'), function (pli) {
      pli.addEventListener('toggle', function () {
        var f = pli.querySelector('iframe[data-src]');
        if (pli.open && f && !f.getAttribute('src')) f.setAttribute('src', f.getAttribute('data-src'));   // chargé au dépli seulement
      });
    });
  }

  /* ------------------------------------------------------------------ cadre de l'éditeur (ONLYOFFICE / Euro-Office) */
  function officeMonter(mode) {
    var c = courant, o = c.d.office || {};
    var zone = c.contenu.querySelector('[data-v101-office]');
    if (zone) zone.remove();
    if (c.ecoute) { racine.removeEventListener('message', c.ecoute); c.ecoute = null; }
    if (c.minuterie) racine.clearTimeout(c.minuterie);
    zone = el('div', 'v101-office');
    zone.setAttribute('data-v101-office', mode);
    var cadre = el('iframe', 'v101-cadre-office');
    cadre.title = c.d.titre || '';
    cadre.src = mode === 'edit' ? o.edition : o.vue;
    var etat = el('p', 'v101-office-etat', c.ctx.m('volet.office_chargement') || '');
    etat.setAttribute('role', 'status');
    zone.appendChild(etat); zone.appendChild(cadre);
    c.contenu.insertBefore(zone, c.contenu.firstChild);
    c.officeEtat = 'chargement';
    c.ecoute = function (ev) {
      if (ev.origin !== racine.location.origin || ev.source !== cadre.contentWindow || !ev.data || !ev.data.ax101office) return;
      if (ev.data.ax101office === 'pret') {
        racine.clearTimeout(c.minuterie);
        c.officeEtat = 'pret'; etat.textContent = mode === 'edit' ? (c.ctx.m('volet.office_edition') || '') : '';
        zone.setAttribute('data-etat', 'pret');
        Array.prototype.forEach.call(c.contenu.querySelectorAll('.l89-cadre, .l89-image'), function (x) { x.hidden = true; });   // aperçu simplifié inutile
      } else if (ev.data.ax101office === 'echec') { officeRepli(); }
    };
    racine.addEventListener('message', c.ecoute);
    c.minuterie = racine.setTimeout(function () { if (courant === c && c.officeEtat !== 'pret') officeRepli(); }, 10000);
  }
  function officeRepli() {
    var c = courant;
    if (!c) return;
    if (c.ecoute) { racine.removeEventListener('message', c.ecoute); c.ecoute = null; }
    if (c.minuterie) racine.clearTimeout(c.minuterie);
    c.officeEtat = 'repli';
    var zone = c.contenu.querySelector('[data-v101-office]');
    if (zone) zone.remove();
    Array.prototype.forEach.call(c.contenu.querySelectorAll('.l89-cadre, .l89-image'), function (x) { x.hidden = false; });
    var avis = el('p', 'v101-avis');
    avis.setAttribute('data-v101-repli', '1');
    avis.appendChild(doc.createTextNode((c.ctx.m('volet.apercu_simplifie') || '') + ' '));
    var o = c.d.office || {};
    if (o.editeur) { var a = el('a', '', c.ctx.m('volet.ouvrir_editeur') || ''); a.href = o.editeur; avis.appendChild(a); }
    c.contenu.insertBefore(avis, c.contenu.firstChild);
    var b = c.d.barre || {};
    if (b.modifier && b.modifier.etat === 'office') {        // sans cadre : l'édition passe par la page éditeur existante
      b.modifier = o.editeur ? { etat: 'lien', href: o.editeur } : { etat: 'indisponible', explication: c.ctx.m('volet.apercu_simplifie') };
      dessinerBoutons();
    }
  }

  /* ------------------------------------------------------------------ barre de décision */
  var LIBELLES = { valider: 'volet.valider', modifier: 'volet.modifier', instruction: 'volet.instruction', ecarter: 'volet.ecarter' };
  var LONGS = { valider: 'volet.valider', modifier: 'volet.modifier_long', instruction: 'volet.instruction_long', ecarter: 'volet.ecarter' };

  function construirePied(fenetre, m) {
    var pied = fenetre.querySelector('.v101-pied');
    if (pied) return pied;
    pied = el('div', 'v101-pied');
    pied.setAttribute('role', 'group');
    pied.setAttribute('aria-label', m('volet.commandes') || 'Décision');
    var panneau = el('div', 'v101-panneau'); panneau.hidden = true;
    var boutons = el('div', 'v101-boutons');
    var statut = el('p', 'v101-statut'); statut.setAttribute('role', 'status');
    pied.appendChild(panneau); pied.appendChild(boutons); pied.appendChild(statut);
    fenetre.appendChild(pied);
    pied.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && !panneau.hidden) { ev.stopPropagation(); ev.preventDefault(); fermerPanneau(); }
    });
    return pied;
  }

  function dessinerBoutons() {
    var c = courant, b = c.d.barre || {}, m = c.ctx.m;
    var zone = c.pied.querySelector('.v101-boutons');
    while (zone.firstChild) zone.removeChild(zone.firstChild);
    ACTIONS.forEach(function (nom) {
      var desc = b[nom] || { etat: 'indisponible', explication: m('volet.pas_de_modification') };
      var libelle = desc.libelle && nom !== 'modifier' ? desc.libelle : m(LIBELLES[nom]);
      var classe = 'ax-btn ' + (nom === 'valider' ? '' : 'ghost ') + 'v101-action';
      var x;
      if (desc.etat === 'lien' && desc.href) {
        x = el('a', classe, libelle); x.href = desc.href;
      } else {
        x = el('button', classe, libelle); x.type = 'button';
        if (etatBouton(desc) === 'indisponible') x.setAttribute('aria-disabled', 'true');
      }
      x.setAttribute('data-v101-action', nom);
      x.setAttribute('data-etat', etatBouton(desc));
      var titre = m(LONGS[nom]) + (desc.explication ? ' — ' + desc.explication : '');
      x.title = titre;
      if (titre !== libelle) x.setAttribute('aria-label', titre);
      zone.appendChild(x);
    });
    var rep = c.fenetre.querySelector('.v101-reporter');
    if (rep) rep.hidden = !(b.reporter && b.reporter.etat === 'actif');
  }

  function fermerPanneau() {
    var c = courant;
    if (!c) return;
    var p = c.pied.querySelector('.v101-panneau');
    while (p.firstChild) p.removeChild(p.firstChild);
    p.hidden = true;
    var ed = c.contenu.querySelector('.v101-edition');
    if (ed) ed.remove();
    var art = c.contenu.querySelector('[data-v101-contenu]');
    if (art) art.hidden = false;
    c.pied.querySelector('.v101-boutons').hidden = false;
  }

  /* Petit formulaire déplié sur place (consigne, motif, confirmation, date) ; ses champs sont dans un <form> : la fenêtre sait qu'une saisie
     n'est pas enregistrée et le demande avant de fermer. */
  function ouvrirPanneau(champs, libelleOk, surOk) {
    var c = courant, m = c.ctx.m;
    var p = c.pied.querySelector('.v101-panneau');
    while (p.firstChild) p.removeChild(p.firstChild);
    var form = el('form', 'v101-form');
    form.noValidate = true;
    champs.forEach(function (ch) { form.appendChild(ch); });
    var ligne = el('div', 'v101-form-boutons');
    var ok = el('button', 'ax-btn', libelleOk); ok.type = 'submit';
    var annuler = el('button', 'ax-btn ghost', m('volet.annuler')); annuler.type = 'button';
    annuler.addEventListener('click', function () { fermerPanneau(); });
    ligne.appendChild(ok); ligne.appendChild(annuler);
    form.appendChild(ligne);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); surOk(form); });
    p.appendChild(form);
    p.hidden = false;
    c.pied.querySelector('.v101-boutons').hidden = true;
    var premier = form.querySelector('textarea, input, select') || ok;
    premier.focus();
    return form;
  }
  function champ(type, nom, libelle, valeur) {
    var lab = el('label', 'v101-champ');
    lab.appendChild(el('span', '', libelle));
    var x;
    if (type === 'textarea') { x = el('textarea'); x.rows = 3; x.maxLength = 2000; }
    else if (type === 'select') { x = el('select'); }
    else { x = el('input'); x.type = type; }
    x.name = nom;
    if (valeur !== undefined && type !== 'select') { x.value = valeur; x.defaultValue = type === 'textarea' ? '' : valeur; }
    lab.appendChild(x);
    return lab;
  }

  /* ``rester`` : l'élément reste ouvert après l'action (consigne envoyée, correction de date) ; sinon la ligne disparaît et l'élément suivant s'ouvre. */
  function executer(nom, desc, saisie, rester) {
    var c = courant, m = c.ctx.m;
    var erreur = controle(desc, saisie);
    if (erreur) { annoncer(m('volet.' + erreur), true); return Promise.resolve(false); }
    desc._cle = desc._cle || nouvelleCle();               // même clé pour une nouvelle tentative de la même action
    var corps = corpsAction(desc, saisie, Date.now(), desc._cle);
    var boutons = c.pied.querySelectorAll('button');
    Array.prototype.forEach.call(boutons, function (x) { x.disabled = true; });
    annoncer(m('volet.en_cours'));
    var moi = c;
    return poster(desc.api, corps).then(function (d) {
      desc._cle = null;
      if (courant !== moi) return true;
      var texte = desc.message || (d && d.message) || m('volet.fait');
      annoncer(texte);
      emettre(nom);
      if (rester) { Array.prototype.forEach.call(boutons, function (x) { x.disabled = false; }); return true; }
      c.ctx.apres(nom, texte);
      return true;
    }).catch(function (e) {
      if (e && e.statut) desc._cle = null;                 // refus du serveur : nouvelle clé pour la prochaine demande
      if (courant !== moi) return false;
      Array.prototype.forEach.call(boutons, function (x) { x.disabled = false; });
      if (e && e.statut === 409) {
        annoncer(m('volet.conflit'), true);
        var r = el('button', 'v101-lien', m('volet.recharger')); r.type = 'button';
        r.addEventListener('click', function () { c.ctx.recharger(); });
        c.statut.appendChild(doc.createTextNode(' ')); c.statut.appendChild(r);
      } else {
        annoncer((c.ctx.m('volet.refus') || '{message}').replace('{message}', e && e.message ? e.message : ''), true);
      }
      return false;
    });
  }
  function emettre(nom) {   // contrat lecteur → mesure (lot M) : nature et commande seulement, jamais d'identifiant ni de texte
    var types = { reponse: 'courriel', gmail: 'gmail', document: 'document', note_ia: 'note_ia' };
    var t = types[courant.d.nature];
    if (!t) return;
    try { doc.dispatchEvent(new CustomEvent('ax101:decide', { detail: { type: t, commande: nom } })); } catch (e) { /* navigateur ancien */ }
  }

  function agir(nom) {
    var c = courant, m = c.ctx.m, b = c.d.barre || {}, desc = nom === 'reporter' ? b.reporter : b[nom];
    if (!desc) return;
    if (etatBouton(desc) === 'indisponible') { annoncer(desc.explication || m('volet.pas_de_modification')); return; }
    if (desc.etat === 'lien') return;                       // lien ordinaire : le navigateur le suit
    annoncer('');
    if (desc.etat === 'edition') return editer(desc);
    if (desc.etat === 'office') return officeEditer(desc);
    if (desc.etat === 'echeance') {
      var date = champ('date', 'date', m('volet.nouvelle_date'), desc.valeur || '');
      var motif = champ('text', 'motif', m('volet.motif_correction'), '');
      return ouvrirPanneau([date, motif], m('volet.enregistrer'), function (f) {
        executer('modifier', desc, { date: f.elements.date.value, motif: f.elements.motif.value }, true).then(function (ok) { if (ok) c.ctx.recharger(c.ctx.m('volet.enregistre')); });
      });
    }
    if (nom === 'instruction') {
      var champs = [];
      if (desc.choix && desc.choix.options) {
        var sel = champ('select', 'choix', m('volet.fournisseur'));
        desc.choix.options.forEach(function (o) { var opt = el('option', '', o[1]); opt.value = o[0]; sel.querySelector('select').appendChild(opt); });
        champs.push(sel);
      }
      champs.push(champ('textarea', 'consigne', m('volet.consigne'), ''));
      return ouvrirPanneau(champs, m('volet.envoyer_ia'), function (f) {
        executer('instruction', desc, { consigne: f.elements.consigne.value, choix: f.elements.choix ? f.elements.choix.value : undefined }, true).then(function (ok) {
          if (ok) { fermerPanneau(); annoncer(desc.message || m('volet.reprise')); }
        });
      });
    }
    var saisie = { texte: c.texte };
    if (desc.motif) {
      var t = el('p', 'v101-question', desc.confirmer || '');
      return ouvrirPanneau([t, champ('text', 'motif', m('volet.motif'), '')], desc.libelle || m('volet.confirmer'), function (f) {
        saisie.motif = f.elements.motif.value; executer(nom, desc, saisie);
      });
    }
    if (desc.confirmer) {
      return ouvrirPanneau([el('p', 'v101-question', desc.confirmer)], desc.libelle || m('volet.confirmer'), function () { executer(nom, desc, saisie); });
    }
    executer(nom, desc, saisie);
  }

  /* Modifier manuellement un texte (courriel, note) : la zone de texte remplace l'aperçu sur place ; Enregistrer appelle la route existante avec
     la révision affichée ; l'aperçu est relu après l'enregistrement. */
  function editer(desc) {
    var c = courant, m = c.ctx.m;
    fermerPanneau();
    var art = c.contenu.querySelector('[data-v101-contenu]');
    var form = el('form', 'v101-edition');
    var lab = champ('textarea', 'texte', m('volet.modifier_long'), desc.valeur || '');
    var zone = lab.querySelector('textarea'); zone.rows = 16; zone.maxLength = 120000; zone.defaultValue = desc.valeur || '';
    form.appendChild(lab);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      executerTexte(desc, zone);
    });
    if (art) { art.hidden = true; art.parentNode.insertBefore(form, art); } else c.contenu.insertBefore(form, c.contenu.firstChild);
    var p = c.pied.querySelector('.v101-panneau');
    while (p.firstChild) p.removeChild(p.firstChild);
    var ligne = el('div', 'v101-form-boutons');
    var ok = el('button', 'ax-btn', m('volet.enregistrer')); ok.type = 'button';
    var annuler = el('button', 'ax-btn ghost', m('volet.annuler')); annuler.type = 'button';
    ok.addEventListener('click', function () { executerTexte(desc, zone); });
    annuler.addEventListener('click', function () { zone.value = zone.defaultValue; fermerPanneau(); });
    ligne.appendChild(ok); ligne.appendChild(annuler); p.appendChild(ligne); p.hidden = false;
    c.pied.querySelector('.v101-boutons').hidden = true;
    zone.focus();
  }
  function executerTexte(desc, zone) {
    var c = courant;
    var v = zone.value;
    var d2 = { etat: 'actif', api: desc.api, corps: desc.corps, texte: desc.texte || 'corps' };
    executer('enregistrer', d2, { texte: v }, true).then(function (ok) { if (ok) { zone.defaultValue = v; c.ctx.recharger(c.ctx.m('volet.enregistre')); } });
  }
  function officeEditer(desc) {
    var c = courant, m = c.ctx.m;
    officeMonter('edit');
    var p = c.pied.querySelector('.v101-panneau');
    while (p.firstChild) p.removeChild(p.firstChild);
    var ligne = el('div', 'v101-form-boutons');
    var fin = el('button', 'ax-btn', m('volet.fin_edition')); fin.type = 'button';
    fin.addEventListener('click', function () { c.ctx.recharger(); });   // la révision est relue : la nouvelle version apparaît, rien n'est validé en silence
    ligne.appendChild(fin);
    if (desc.editeur) { var a = el('a', 'ax-btn ghost', m('volet.ouvrir_editeur')); a.href = desc.editeur; ligne.appendChild(a); }
    p.appendChild(ligne); p.hidden = false;
    c.pied.querySelector('.v101-boutons').hidden = true;
    fin.focus();
  }

  /* ------------------------------------------------------------------ montage */
  function monter(o) {
    demonter();
    var fenetre = o.fenetre, contenu = o.contenu, d = o.donnees || {}, m = o.ctx.m;
    var pied = construirePied(fenetre, m);
    pied.hidden = false;
    courant = { fenetre: fenetre, contenu: contenu, d: d, ctx: o.ctx, pied: pied, statut: pied.querySelector('.v101-statut'),
                texte: (d.barre && d.barre.modifier && typeof d.barre.modifier.valeur === 'string') ? d.barre.modifier.valeur : '' };
    while (contenu.firstChild) contenu.removeChild(contenu.firstChild);
    if (d.apercu) contenu.insertAdjacentHTML('beforeend', d.apercu);     /* HTML construit et échappé par le serveur (volet56101) */
    if (d.html) {
      if (d.replier) {
        var pli = el('details', 'v101-details');
        pli.appendChild(el('summary', '', m('volet.details')));
        var dedans = el('div'); dedans.innerHTML = d.html;                /* même origine : générateurs de la page complète */
        pli.appendChild(dedans); contenu.appendChild(pli);
      } else {
        var bloc = el('div', 'v101-html'); bloc.innerHTML = d.html; contenu.appendChild(bloc);
      }
    }
    relierCourriel(contenu);
    fermerPanneau();
    annoncer('');
    dessinerBoutons();
    if (d.office && d.office.actif && d.office.vue) officeMonter('view');
    var typeMesure = { reponse: 'courriel', gmail: 'gmail', document: 'document', note_ia: 'note_ia' }[d.nature];   // contrat lecteur → mesure : quatre types seulement
    if (typeMesure) { try { doc.dispatchEvent(new CustomEvent('ax101:ouvert', { detail: { type: typeMesure } })); } catch (e) { /* ancien navigateur */ } }
    return courant;
  }

  doc.addEventListener('click', function (ev) {
    if (!courant) return;
    var b = ev.target.closest && ev.target.closest('.v101-pied [data-v101-action], .v101-reporter');
    if (!b || !courant.fenetre.contains(b)) return;
    var nom = b.classList.contains('v101-reporter') ? 'reporter' : b.getAttribute('data-v101-action');
    var desc = nom === 'reporter' ? (courant.d.barre || {}).reporter : (courant.d.barre || {})[nom];
    if (desc && desc.etat === 'lien') return;
    ev.preventDefault();
    agir(nom);
  });

  racine.AX101volet = { monter: monter, demonter: demonter, annoncer: annoncer, courant: function () { return courant; } };
})(typeof window !== 'undefined' ? window : globalThis);
