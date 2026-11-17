/* AxiorHub 5.6.106 (lot V) — lecteur des courriels : contenu « Texte / Mis en forme » (cadre isolé), impression, .eml, pièces (télécharger,
   aperçu), en-têtes complets, conversation ; « Résumer avec l'IA » les courriels cochés (volet de droite, à la place du lecteur) ; courriel →
   tâche ou agenda (formulaires préremplis, rien n'est créé sans validation).
   Sûreté : aucun HTML de courriel n'entre dans le DOM de la page. Le contenu mis en forme est servi par le serveur (HTML assaini, politique
   sans script ni ressource extérieure) et affiché dans un <iframe sandbox> SANS allow-scripts NI allow-same-origin. Tout le reste est
   construit ici avec textContent. Point de montage : v5625.js appelle window.axh56106lecteur.afficher(panneau, courriel) au rendu. */
(function () {
  'use strict';
  if (window.__axiorhub56106v) return;
  window.__axiorhub56106v = true;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var R = 'm56106/lecteur/';
  // Cadre du contenu mis en forme : liens dans un nouvel onglet seulement. Jamais allow-scripts, jamais allow-same-origin.
  var SANDBOX = 'allow-popups allow-popups-to-escape-sandbox';

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined) node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) { box = el('div', { id: 'ax-toast', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(box); }
    var t = el('div', { 'class': 'ax-toast' + (bad ? ' bad' : ''), text: message });
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 5000);
  }
  function api(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) { if (d && d.error) { var x = new Error(d.message || d.error); x.code = d.error; throw x; } return d; });
  }
  function busy(b, on) { if (b) { b.disabled = on; b.setAttribute('aria-busy', on ? 'true' : 'false'); } }
  function cle() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return 'lx-' + Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function ctx() { return window.axh5625Ctx ? window.axh5625Ctx() : null; }
  function qs(o) {
    var p = new URLSearchParams();
    Object.keys(o).forEach(function (k) { if (o[k] !== '' && o[k] !== undefined && o[k] !== null) p.set(k, o[k]); });
    return p.toString();
  }
  function adresse(route, ref, extra) { return prefix + '/api440/' + R + route + '?' + qs(Object.assign({}, ref, extra || {})); }
  function refDe(m) { return { uid: String(m.uid), dossier: m.folder || m.dossier || '', uidvalidity: String(m.uidvalidity || '') }; }
  function quand(iso) { return String(iso || '').slice(0, 16).replace('T', ' '); }
  function taille(n) { n = Number(n) || 0; return n > 1048576 ? (n / 1048576).toFixed(1).replace('.', ',') + ' Mo' : Math.max(1, Math.round(n / 1024)) + ' Ko'; }
  function cacher(n) { if (n) { n.hidden = true; n.setAttribute('data-lx56106', 'remplace'); } }
  // 5.6.107 (lot F2) : puce de la ligne d'avertissements (texte masqué, images bloquées) : résumé court sur la ligne, détail dépliable au clic
  // ou au clavier (<details>) ; « action » (bouton) reste visible à côté du résumé. Rouge seulement pour une consigne cachée adressée à l'IA.
  function puce(resume, detail, alerte, action) {
    var pli = el('details', { 'class': 'lx107-pli' }, [
      el('summary', {}, [document.createTextNode(resume + ' '), el('span', { 'class': 'lx107-fleche', 'aria-hidden': 'true', text: '▾' })]),
      el('p', { 'class': 'lx107-detail', text: detail })]);
    return el('span', { 'class': 'lx56106-bandeau lx107-puce' + (alerte ? ' lx56106-alerte' : ''), role: 'status' }, [pli, action || null]);
  }

  // ------------------------------------------------------------------ listes des formulaires (membres, dossiers, agendas), lues une fois
  var listes = null;
  function chargerListes() {
    if (listes) return Promise.resolve(listes);
    return api(R + 'formulaires').then(function (d) { listes = d; return d; });
  }
  function options(select, items, valeur, vide) {
    if (vide !== undefined) select.appendChild(el('option', { value: '', text: vide }));
    items.forEach(function (x) { var o = el('option', { value: x.id, text: x.label || x.nom || x.libelle || x.id }); if (x.id === valeur) o.selected = true; select.appendChild(o); });
  }

  // ------------------------------------------------------------------ ouvrir un courriel (résumé, conversation, lien de « Mes tâches »)
  function ouvrir(item) {
    var c = ctx();
    if (!c || !item || !item.uid) return;
    var cible = item.dossier || '', courant = c.folder || '';
    var reception = item.reception === true || !cible;
    if ((reception && !courant) || (!reception && cible === courant)) { c.open(String(item.uid)); return; }
    if (!reception && window.axh5625Liste && window.axh5625Liste.choisir) {
      window.axh5625Liste.choisir(cible);
    } else {
      var b = $$('#bx5625-flist .bx5625-folder').filter(function (x) {
        return reception ? (x.getAttribute('data-role') === 'inbox' || x.title === 'INBOX') : x.title === cible;
      })[0];
      if (b) b.click();
    }
    var n = ctx();
    if (n) n.open(String(item.uid));
  }

  // ------------------------------------------------------------------ formulaires : tâche et événement (validation humaine)
  function statut(zone, texte, erreur) {
    zone.textContent = texte; zone.className = 'lx56106-statut' + (erreur ? ' lx56106-erreur' : '');
  }
  function formulaireTache(ref, prop, reperage, fermer) {
    var cleForm = cle();   // créée à l'ouverture : un second clic ou une reprise réseau ne crée pas une deuxième tâche
    var titre = el('input', { maxlength: '500', minlength: '3', required: 'required', value: prop.titre || '' });
    var dossier = el('select', { 'aria-label': 'Dossier' }), responsable = el('select', { 'aria-label': 'Responsable' });
    var echeance = el('input', { type: 'date', value: prop.echeance || '' });
    var creer = el('button', { type: 'submit', 'class': 'ax-btn', text: 'Créer la tâche' });
    var annuler = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Annuler' });
    var etat = el('p', { 'class': 'lx56106-statut', role: 'status', 'aria-live': 'polite' });
    var note = reperage && reperage.etat === 'sure' && prop.echeance ? 'Échéance repérée dans le texte : « ' + reperage.expression + ' ». Vérifiez-la.'
      : (reperage && reperage.etat === 'ambigue' ? 'Plusieurs dates possibles dans le texte : échéance laissée vide.' : 'Aucune échéance sûre repérée : à compléter si besoin.');
    var form = el('form', { 'class': 'lx56106-formulaire', 'aria-label': 'Créer une tâche depuis ce courriel' }, [
      el('h3', { text: 'Créer une tâche' }), el('label', {}, [el('span', { text: 'Intitulé' }), titre]),
      el('div', { 'class': 'lx56106-ligne' }, [el('label', {}, [el('span', { text: 'Dossier' }), dossier]), el('label', {}, [el('span', { text: 'Échéance' }), echeance]),
        el('label', {}, [el('span', { text: 'Responsable' }), responsable])]),
      el('p', { 'class': 'lx56106-note', text: note }),
      el('p', { 'class': 'lx56106-note', text: 'La tâche garde le lien vers ce courriel ; elle apparaît dans « Mes tâches ». Rien n’est envoyé.' }),
      el('div', { 'class': 'lx56106-actions' }, [creer, annuler]), etat]);
    chargerListes().then(function (l) {
      options(dossier, l.dossiers, prop.dossier || '', 'Cabinet, sans dossier');
      options(responsable, l.membres, '', 'Sans responsable');
    }).catch(function (e) { statut(etat, e.message, true); });
    annuler.addEventListener('click', function () { form.remove(); if (fermer) fermer(); });
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (titre.value.trim().length < 3) { titre.focus(); statut(etat, 'Indiquez un intitulé (3 caractères au moins).', true); return; }
      busy(creer, true); statut(etat, 'Création…');
      api(R + 'tache', Object.assign({}, ref, { titre: titre.value, matter: dossier.value, echeance: echeance.value, responsable: responsable.value, cle: cleForm }))
        .then(function (r) {
          statut(etat, r.message);
          $$('input,select,button', form).forEach(function (x) { if (x !== annuler) x.disabled = true; });
          annuler.textContent = 'Fermer';
          form.appendChild(el('a', { 'class': 'ax-btn ghost', href: prefix + '/mes-taches', text: 'Ouvrir « Mes tâches »' }));
          toast('Tâche créée.');
        }).catch(function (e) { statut(etat, e.message, true); busy(creer, false); });
    });
    setTimeout(function () { titre.focus(); }, 0);
    return form;
  }
  function formulaireEvenement(ref, prop, reperage, fermer) {
    var cleForm = cle();
    var titre = el('input', { maxlength: '200', required: 'required', value: prop.titre || '' });
    var jour = el('input', { type: 'date', value: prop.date || '' }), heure = el('input', { type: 'time', value: prop.heure || '' });
    var duree = el('input', { type: 'number', min: '5', max: '1440', step: '5', value: String(prop.duree_minutes || 60) });
    var dossier = el('select', { 'aria-label': 'Dossier' }), agenda = el('select', { 'aria-label': 'Agenda' });
    var lieu = el('input', { maxlength: '200' });
    var creer = el('button', { type: 'submit', 'class': 'ax-btn', text: 'Enregistrer l’événement' });
    var annuler = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Annuler' });
    var etat = el('p', { 'class': 'lx56106-statut', role: 'status', 'aria-live': 'polite' });
    var note = reperage && reperage.etat === 'sure' && prop.date ? 'Date repérée dans le texte : « ' + reperage.expression + ' ». Vérifiez-la.'
      : (reperage && reperage.etat === 'ambigue' ? 'Plusieurs dates possibles dans le texte : date laissée vide.' : 'Aucune date sûre repérée : indiquez-la.');
    var form = el('form', { 'class': 'lx56106-formulaire', 'aria-label': 'Ajouter un événement depuis ce courriel' }, [
      el('h3', { text: 'Ajouter à l’agenda' }), el('label', {}, [el('span', { text: 'Titre' }), titre]),
      el('div', { 'class': 'lx56106-ligne' }, [el('label', {}, [el('span', { text: 'Date' }), jour]), el('label', {}, [el('span', { text: 'Heure (vide : journée entière)' }), heure]),
        el('label', {}, [el('span', { text: 'Durée (minutes)' }), duree])]),
      el('div', { 'class': 'lx56106-ligne' }, [el('label', {}, [el('span', { text: 'Dossier' }), dossier]), el('label', {}, [el('span', { text: 'Agenda' }), agenda]),
        el('label', {}, [el('span', { text: 'Lieu' }), lieu])]),
      el('p', { 'class': 'lx56106-note', text: note }),
      el('p', { 'class': 'lx56106-note', text: 'Rien n’est créé sans votre validation. Agenda personnel : sur AxiorHub seulement ; agenda en ligne : copie vérifiée après l’enregistrement.' }),
      el('div', { 'class': 'lx56106-actions' }, [creer, annuler]), etat]);
    chargerListes().then(function (l) {
      options(dossier, l.dossiers, prop.dossier || '', 'Sans dossier');
      options(agenda, l.calendriers, 'local');
    }).catch(function (e) { statut(etat, e.message, true); });
    annuler.addEventListener('click', function () { form.remove(); if (fermer) fermer(); });
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (!titre.value.trim()) { titre.focus(); statut(etat, 'Indiquez le titre.', true); return; }
      if (!jour.value) { jour.focus(); statut(etat, 'Indiquez la date de l’événement.', true); return; }
      busy(creer, true); statut(etat, 'Enregistrement…');
      api(R + 'evenement', Object.assign({}, ref, { titre: titre.value, date: jour.value, heure: heure.value, duree_minutes: duree.value, matter: dossier.value,
                                                     calendrier: agenda.value || 'local', lieu: lieu.value, cle: cleForm }))
        .then(function (r) {
          statut(etat, r.message || 'Événement enregistré.');
          $$('input,select,button', form).forEach(function (x) { if (x !== annuler) x.disabled = true; });
          annuler.textContent = 'Fermer';
          form.appendChild(el('a', { 'class': 'ax-btn ghost', href: prefix + '/agenda', text: 'Ouvrir l’agenda' }));
          toast(r.message || 'Événement enregistré.');
        }).catch(function (e) { statut(etat, e.message, true); busy(creer, false); });
    });
    setTimeout(function () { titre.focus(); }, 0);
    return form;
  }
  // formulaire d'un courriel désigné seulement par sa référence (actions groupées, résumé) : préremplissage lu au serveur, droits compris
  function formulairePour(genre, item, zone) {
    var attente = el('p', { 'class': 'lx56106-note', text: 'Préparation du formulaire' + (item.objet ? ' pour « ' + item.objet + ' »' : '') + '…' });
    zone.appendChild(attente);
    api(R + 'courriel?' + qs(refDe(item))).then(function (info) {
      var ref = { uid: info.uid, dossier: info.dossier, uidvalidity: info.uidvalidity };
      var form = genre === 'tache' ? formulaireTache(ref, info.proposition.tache, info.proposition.reperage)
        : formulaireEvenement(ref, info.proposition.evenement, info.proposition.reperage);
      attente.replaceWith(form);
    }).catch(function (e) { attente.textContent = e.message; attente.className = 'lx56106-statut lx56106-erreur'; });
  }


  // ------------------------------------------------------------------ impression et « Enregistrer sous »
  var courant = null;          // lecteur affiché : son impression répond à Ctrl+P sur la page Courriels
  function urlLot(route, items, extra) {
    return prefix + '/api440/' + R + route + '?' + qs(Object.assign({ lot: JSON.stringify(items.map(function (x) { return { uid: String(x.uid), dossier: x.dossier || '' }; })) }, extra || {}));
  }
  function imprimerVue(url, pdf) {
    var w = window.open(url, '_blank');
    if (!w) { toast('Le navigateur a bloqué l’onglet d’impression : autorisez les fenêtres pour AxiorHub, ou utilisez Ctrl+P dans l’onglet.', true); return null; }
    if (pdf) toast('Pour un PDF : choisissez « Enregistrer au format PDF » comme imprimante.');
    if (window.axh56106lecteur && window.axh56106lecteur.impressionAuto === false) return w;   // parcours du banc : jamais de vraie impression
    var essais = 0, attendre = setInterval(function () {   // page sans script, même origine : la page de l'application lance l'impression
      essais += 1;
      try {
        if (w.closed || essais > 60) { clearInterval(attendre); return; }
        if (w.document && w.document.readyState === 'complete' && w.location.href.indexOf(R + 'imprimer') >= 0) { clearInterval(attendre); w.focus(); w.print(); }
      } catch (e) { clearInterval(attendre); }
    }, 250);
    return w;
  }
  function telecharger(url) {
    var a = el('a', { href: url, download: '', hidden: 'hidden' });
    document.body.appendChild(a); a.click(); a.remove();
  }
  function choisirDossierNextcloud(surChoix) {
    var dlg = el('div', { 'class': 'c5670-picker lx56106-nextcloud', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Enregistrer dans un dossier Nextcloud' });
    var ou = el('p', { 'class': 'c5670-where ax-muted' }), liste = el('ul', { 'class': 'c5670-files' });
    var contenu = el('select', { 'aria-label': 'Contenu à enregistrer' }, [el('option', { value: 'eml', text: 'Courriel .eml (original)' }), el('option', { value: 'pieces', text: 'Pièces jointes' })]);
    var ici = el('button', { type: 'button', 'class': 'ax-btn', text: 'Enregistrer ici', disabled: 'disabled' });
    var annuler = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Annuler' });
    var chemin = '';
    dlg.appendChild(el('div', { 'class': 'c5670-picker-box' }, [el('h3', { text: 'Enregistrer dans un dossier Nextcloud' }), ou, liste,
      el('label', {}, [el('span', { text: 'Contenu ' }), contenu]),
      el('p', { 'class': 'ax-muted', text: 'Aucun fichier existant n’est remplacé : un nom déjà pris reçoit « (2) », « (3) »…' }),
      el('div', { 'class': 'lx56106-actions' }, [ici, annuler])]));
    document.body.appendChild(dlg);
    var fermer = function () { dlg.remove(); };
    var ouvrirDossier = function (p) {
      liste.textContent = ''; liste.appendChild(el('li', { 'class': 'ax-muted', text: 'Lecture…' }));
      api('m5670/fichiers/parcourir?chemin=' + encodeURIComponent(p || '')).then(function (d) {
        chemin = d.chemin || ''; ici.disabled = !chemin;
        ou.textContent = chemin ? 'Dossier : ' + chemin : 'Espaces du cabinet : choisissez un dossier';
        liste.textContent = '';
        if (d.parent) { var haut = el('button', { type: 'button', 'class': 'c5670-entry', text: '⬑ Dossier parent' }); haut.addEventListener('click', function () { ouvrirDossier(d.parent); }); liste.appendChild(el('li', {}, [haut])); }
        (d.entrees || []).filter(function (x) { return x.dossier; }).forEach(function (x) {
          var b = el('button', { type: 'button', 'class': 'c5670-entry', text: '📁 ' + x.nom });
          b.addEventListener('click', function () { ouvrirDossier(x.chemin); });
          liste.appendChild(el('li', {}, [b]));
        });
      }).catch(function (e) { liste.textContent = ''; liste.appendChild(el('li', { 'class': 'ax-alert', text: e.message })); });
    };
    ici.addEventListener('click', function () { if (chemin) { fermer(); surChoix(chemin, contenu.value); } });
    annuler.addEventListener('click', fermer);
    dlg.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') fermer(); });
    ouvrirDossier('');
    setTimeout(function () { annuler.focus(); }, 0);
  }
  function enregistrerNextcloud(items) {
    choisirDossierNextcloud(function (chemin, contenu) {
      api(R + 'enregistrer', { elements: items.map(function (x) { return { uid: String(x.uid), dossier: x.dossier || '' }; }), chemin: chemin, contenu: contenu, cle: cle() })
        .then(function (r) { toast(r.message); }).catch(function (e) { toast(e.message, true); });
    });
  }
  // menu « Enregistrer sous » : lecteur (un courriel) ou barre d'actions groupées (courriels cochés)
  function menuEnregistrer(cibles, lecteur) {
    var menu = el('details', { 'class': 'lx56106-menu' }, [el('summary', { 'class': 'ax-btn ghost', text: '💾 Enregistrer sous', title: '.eml, PDF, dossier client, Nextcloud, pièces jointes' })]);
    var corps = el('div', { 'class': 'lx56106-menu-corps', role: 'menu' });
    var entree = function (texte, action, titre) {
      var b = el('button', { type: 'button', role: 'menuitem', text: texte, title: titre || '' });
      b.addEventListener('click', function () {
        var items = cibles();
        menu.open = false;
        if (!items.length) { toast('Cochez au moins un courriel.', true); return; }
        action(items);
      });
      corps.appendChild(b);
      return b;
    };
    entree('Fichier .eml (original)', function (items) {
      if (items.length > 200) { toast('200 courriels au plus par archive.', true); return; }
      telecharger(items.length === 1 ? adresse('eml', refDe(items[0])) : urlLot('eml-zip', items));
    }, 'Le message complet ; plusieurs courriels : une archive ZIP de .eml');
    entree('PDF', function (items) {
      if (items.length > 25) { toast('25 courriels au plus par impression.', true); return; }
      var extra = lecteur && lecteur.options ? lecteur.options() : {};
      imprimerVue(items.length === 1 ? adresse('imprimer', refDe(items[0]), extra) : urlLot('imprimer', items), true);
    }, 'Vue d’impression : choisissez « Enregistrer au format PDF »');
    entree('Dans le dossier client…', function () {
      var bouton = lecteur ? $('#bx5625-panel .bx5625-classer-btn') : null;
      if (bouton) { if (bouton.getAttribute('aria-expanded') !== 'true') bouton.click(); bouton.scrollIntoView({ block: 'nearest' }); bouton.focus(); return; }
      var client = $('#bx5625 .bx56106-client');
      if (client) { client.open = true; var s = $('summary', client); if (s) s.focus(); return; }
      toast('Classement dans le dossier client indisponible ici.', true);
    }, 'Classer : .eml et pièces dans le bon sous-dossier du dossier (proposition automatique)');
    entree('Dans un dossier Nextcloud…', enregistrerNextcloud, 'Écriture sans écrasement : nom suffixé si le fichier existe déjà');
    if (lecteur) {
      var zip = entree('Toutes les pièces jointes (ZIP)', function (items) { telecharger(adresse('pieces-zip', refDe(items[0]))); });
      lecteur.pieces = function (n) { zip.hidden = !n; };
    }
    menu.appendChild(corps);
    menu.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { menu.open = false; $('summary', menu).focus(); } });
    return menu;
  }

  // ------------------------------------------------------------------ lecteur
  function construire(m) {
    var ref = refDe(m), info = null, images = false, mode = 'texte';
    var bloc = el('section', { 'class': 'lx56106', 'aria-label': 'Courriel', 'data-uid': String(m.uid) });
    var tete = el('div', { 'class': 'lx56106-tete' }, [el('h2', { text: m.subject || '(sans objet)' }),
      el('p', { 'class': 'lx56106-meta', text: 'De : ' + (m.from || '—') + ' · ' + quand(m.date) }),
      el('p', { 'class': 'lx56106-meta', text: 'À : ' + (m.to || '—') + (m.cc ? ' · Cc : ' + m.cc : '') })]);
    if (m.status_label) tete.appendChild(el('p', { 'class': 'lx56106-meta' }, [el('em', { 'class': 'ax-badge', text: m.status_label })]));
    var bascule = el('span', { 'class': 'lx56106-bascule', role: 'group', 'aria-label': 'Affichage du courriel', hidden: 'hidden' });
    var bHtml = el('button', { type: 'button', 'aria-pressed': 'false', text: 'Mis en forme' }), bTexte = el('button', { type: 'button', 'aria-pressed': 'true', text: 'Texte' });
    bascule.appendChild(bHtml); bascule.appendChild(bTexte);
    var imprimer = el('button', { type: 'button', 'class': 'ax-btn ghost lx56106-imprimer', text: '🖨 Imprimer', title: 'Imprimer ce courriel (Ctrl+P) : vue d’impression propre, nouvel onglet', 'aria-keyshortcuts': 'Control+P' });
    var lecteur = { options: function () { return Object.assign({ mode: mode }, images ? { images: '1' } : {}); } };
    var enregistrer = menuEnregistrer(function () { return [ref]; }, lecteur);
    var conversation = el('button', { type: 'button', 'class': 'ax-btn ghost', 'aria-expanded': 'false', text: 'Conversation', title: 'Les messages du même fil' });
    var tache = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Créer une tâche' });
    var agenda = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Ajouter à l’agenda' });
    var barre = el('div', { 'class': 'lx56106-barre', role: 'toolbar', 'aria-label': 'Outils du lecteur' }, [bascule, imprimer, enregistrer, conversation, tache, agenda]);
    // 5.6.107 (lot F2) : avertissements regroupés sur une ligne de puces (alerte anti-fraude repliée, texte masqué, images bloquées)
    var bandeaux = el('div', { 'class': 'lx56106-bandeaux', role: 'group', 'aria-label': 'Avertissements du courriel', 'aria-live': 'polite' });
    var corps = el('div', { 'class': 'lx56106-corps' });
    var pieces = el('div', { 'class': 'lx56106-pieces' });
    var entetes = el('details', { 'class': 'lx56106-entetes', hidden: 'hidden' }, [el('summary', { text: 'En-têtes complets' })]);
    var fil = el('div', { 'class': 'lx56106-fil', hidden: 'hidden' });
    var formulaires = el('div', { 'class': 'lx56106-formulaires' });
    [tete, barre, bandeaux, formulaires, corps, pieces, entetes, fil].forEach(function (n) { bloc.appendChild(n); });

    function texteBrut() {
      return el('pre', { 'class': 'lx56106-texte', tabindex: '0', text: (m.text || '') + (m.truncated ? '\n\n[… texte tronqué à l’affichage]' : '') });
    }
    function cadre() {
      var f = document.createElement('iframe');
      f.setAttribute('sandbox', SANDBOX);              // posé AVANT l'adresse : le document ne se charge jamais hors du bac à sable
      f.setAttribute('referrerpolicy', 'no-referrer');
      f.className = 'lx56106-cadre';
      f.title = 'Contenu mis en forme du courriel (isolé : aucun script, images extérieures ' + (images ? 'chargées par le serveur' : 'bloquées') + ')';
      f.setAttribute('src', adresse('corps', ref, images ? { images: '1' } : {}));
      return f;
    }
    function montrer(nouveau) {
      mode = nouveau;
      corps.textContent = '';
      corps.appendChild(mode === 'html' ? cadre() : texteBrut());
      bHtml.setAttribute('aria-pressed', String(mode === 'html')); bTexte.setAttribute('aria-pressed', String(mode !== 'html'));
      bandeauImages();
    }
    var zoneImages = el('span', { hidden: 'hidden' });
    function bandeauImages() {
      var n = info ? Number(info.images_externes) || 0 : 0, nouvelle = null;
      if (n && mode === 'html' && images) {
        nouvelle = puce('🖼 Images affichées', 'Images extérieures affichées pour ce courriel seulement (téléchargées par le serveur, sans cookie ni adresse de provenance).');
        nouvelle.classList.add('lx56106-info');
      } else if (n && mode === 'html') {
        var afficher = el('button', { type: 'button', 'class': 'lx107-action', text: 'Afficher', title: 'Afficher les images extérieures de ce courriel' });
        afficher.addEventListener('click', function () { images = true; montrer('html'); });
        nouvelle = puce('🖼 ' + n + ' image' + (n > 1 ? 's' : '') + ' bloquée' + (n > 1 ? 's' : ''),
                        'Images bloquées (' + n + ') : des images extérieures peuvent signaler à l’expéditeur que vous avez lu ce courriel.', false, afficher);
      } else {
        nouvelle = el('span', { hidden: 'hidden' });
      }
      if (zoneImages.parentNode) zoneImages.parentNode.replaceChild(nouvelle, zoneImages);
      zoneImages = nouvelle;
    }
    bandeaux.appendChild(zoneImages);
    corps.appendChild(texteBrut());

    [[bHtml, 'html'], [bTexte, 'texte']].forEach(function (x) {
      x[0].addEventListener('click', function () {
        if (mode === x[1]) return;
        montrer(x[1]);
        api(R + 'affichage', { affichage: x[1] }).catch(function (e) { toast('Préférence non enregistrée : ' + e.message, true); });   // mémorisée pour ce compte
      });
    });
    var imprimerCeCourriel = function () { return imprimerVue(adresse('imprimer', ref, lecteur.options())); };
    imprimer.addEventListener('click', imprimerCeCourriel);
    courant = { bloc: bloc, imprimer: imprimerCeCourriel };
    tache.addEventListener('click', function () {
      if (!info) { toast('Lecture du courriel en cours : réessayez dans un instant.', true); return; }
      formulaires.textContent = '';
      formulaires.appendChild(formulaireTache(ref, info.proposition.tache, info.proposition.reperage));
    });
    agenda.addEventListener('click', function () {
      if (!info) { toast('Lecture du courriel en cours : réessayez dans un instant.', true); return; }
      formulaires.textContent = '';
      formulaires.appendChild(formulaireEvenement(ref, info.proposition.evenement, info.proposition.reperage));
    });
    conversation.addEventListener('click', function () {
      var ouvert = !fil.hidden;
      fil.hidden = ouvert; conversation.setAttribute('aria-expanded', String(!ouvert));
      if (ouvert || fil.childNodes.length) return;
      fil.appendChild(el('p', { 'class': 'lx56106-note', text: 'Lecture de la conversation…' }));
      busy(conversation, true);
      api(R + 'fil?' + qs(ref)).then(function (d) {
        fil.textContent = '';
        fil.appendChild(el('h3', { text: 'Conversation (' + d.messages.length + ' message' + (d.messages.length > 1 ? 's' : '') + ')' }));
        if (d.message) fil.appendChild(el('p', { 'class': 'lx56106-note', role: 'status', text: d.message }));
        var imprimerFil = el('button', { type: 'button', 'class': 'ax-btn ghost', text: '🖨 Imprimer le fil', title: 'Tout le fil, dans l’ordre chronologique' });
        imprimerFil.addEventListener('click', function () { imprimerVue(adresse('imprimer', ref, Object.assign({ fil: '1' }, lecteur.options()))); });
        fil.appendChild(imprimerFil);
        d.messages.forEach(function (x, i) {
          var ouvrirMsg = x.courant ? null : el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Ouvrir ce message' });
          if (ouvrirMsg) ouvrirMsg.addEventListener('click', function () { ouvrir(x); });
          var d1 = el('details', { 'class': x.courant ? 'lx56106-courant' : '' }, [
            el('summary', { text: (x.de || '—') + ' · ' + x.date_locale + ' · ' + (x.objet || '(sans objet)') + (x.courant ? ' (ce courriel)' : '') }),
            el('pre', { 'class': 'lx56106-texte', text: x.texte + (x.tronque ? '\n[…]' : '') }), ouvrirMsg]);
          if (i === d.messages.length - 1) d1.open = true;      // repliés sauf le dernier
          fil.appendChild(d1);
        });
      }).catch(function (e) { fil.textContent = ''; fil.appendChild(el('p', { 'class': 'lx56106-statut lx56106-erreur', role: 'alert', text: e.message })); })
        .then(function () { busy(conversation, false); });
    });

    function piecesAfficher(liste) {
      pieces.textContent = '';
      if (!liste.length) return;
      pieces.appendChild(el('h3', { text: 'Pièces jointes (' + liste.length + ')' }));
      liste.forEach(function (a) {
        var vue = el('div', { 'class': 'lx56106-vue', hidden: 'hidden' });
        var dl = el('a', { 'class': 'ax-btn ghost', href: adresse('piece', ref, { index: a.index, dl: '1' }), download: a.name || '', text: 'Télécharger' });
        var actions = el('span', { 'class': 'lx56106-actions' }, [dl]);
        if (a.apercu) {
          var voir = el('button', { type: 'button', 'class': 'ax-btn ghost', 'aria-expanded': 'false', text: 'Aperçu' });
          voir.addEventListener('click', function () {
            var ouvert = !vue.hidden;
            vue.hidden = ouvert; voir.setAttribute('aria-expanded', String(!ouvert));
            if (ouvert || vue.childNodes.length) return;
            var src = adresse('piece', ref, { index: a.index });
            if (a.apercu === 'image') vue.appendChild(el('img', { 'class': 'lx56106-apercu-image', src: src, alt: 'Aperçu de ' + a.name }));
            else if (a.apercu === 'pdf') vue.appendChild(el('iframe', { 'class': 'lx56106-apercu', src: src, title: 'Aperçu de ' + a.name }));   // lecteur PDF du navigateur (cadre de l'application, CSP stricte)
            else {
              var f = document.createElement('iframe');
              f.setAttribute('sandbox', '');                     // texte Word mis en forme côté serveur : aucune permission
              f.className = 'lx56106-apercu'; f.title = 'Aperçu de ' + a.name; f.setAttribute('src', src);
              vue.appendChild(f);
            }
          });
          actions.appendChild(voir);
        }
        var lire = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Lire le texte' });
        var sortie = el('pre', { 'class': 'lx56106-texte', hidden: 'hidden' });
        lire.addEventListener('click', function () {
          if (!sortie.hidden) { sortie.hidden = true; return; }
          busy(lire, true);
          api('m5625/boite/piece?' + qs({ uid: ref.uid, index: a.index, dossier: ref.dossier, uidvalidity: ref.uidvalidity })).then(function (d) {
            sortie.textContent = d.text + (d.truncated ? '\n[…]' : ''); sortie.hidden = false;
          }).catch(function (e) { toast(e.message, true); }).then(function () { busy(lire, false); });
        });
        actions.appendChild(lire);
        pieces.appendChild(el('div', { 'class': 'lx56106-piece' }, [el('span', { text: (a.name || 'pièce') + ' · ' + taille(a.size) }), actions]));
        pieces.appendChild(vue); pieces.appendChild(sortie);
      });
    }
    piecesAfficher(m.attachments || []);

    api(R + 'courriel?' + qs(ref)).then(function (d) {
      if (!bloc.isConnected) return;                          // un autre courriel a été ouvert entre-temps
      info = d;
      ref.uidvalidity = d.uidvalidity || ref.uidvalidity;
      if (d.texte_masque) bandeaux.insertBefore(puce('👁 Texte masqué retiré' + (d.texte_masque_ia ? ' — consignes pour l’IA' : ''),   // 5.6.107 (lot F2) : puce
        'Texte masqué par la mise en forme, retiré de l’affichage : ' + d.texte_masque + '.', !!d.texte_masque_ia), zoneImages);
      if (d.confidentiel) tete.querySelector('h2').appendChild(el('span', { 'class': 'lx56106-badge', text: 'confidentiel — usage interne' }));
      if (d.dossier_reconnu) tete.appendChild(el('p', { 'class': 'lx56106-meta', text: 'Dossier : ' + d.dossier_reconnu.label + ' (' + d.dossier_reconnu.motif + ')' }));
      if (d.html) { bascule.hidden = false; montrer(d.affichage === 'html' ? 'html' : 'texte'); }
      piecesAfficher(d.pieces || []);
      if (lecteur.pieces) lecteur.pieces((d.pieces || []).length);
      if (d.entetes && d.entetes.length) {
        entetes.appendChild(el('pre', { text: d.entetes.map(function (h) { return h[0] + ': ' + h[1]; }).join('\n') + (d.entetes_complets ? '' : '\n[…]') }));
        entetes.hidden = false;
      }
      conversation.hidden = !d.fil;
    }).catch(function (e) {
      if (!bloc.isConnected) return;
      bandeaux.appendChild(el('p', { 'class': 'lx56106-bandeau lx56106-alerte', role: 'alert', text: 'Affichage enrichi indisponible : ' + e.message }));
    });
    return bloc;
  }

  function poser(zone, m) {
    if (!zone || !m) return;
    var tete = null;
    $$('.bx5625-mhead', zone).forEach(function (n) { if (n.parentNode === zone && !tete) tete = n; });
    var bloc = construire(m);
    if (!tete) { zone.appendChild(bloc); return bloc; }
    zone.insertBefore(bloc, tete);
    var corps = tete.nextElementSibling;
    cacher(tete);
    if (corps && corps.tagName === 'PRE' && corps.classList.contains('bx5625-text')) cacher(corps);
    Array.prototype.forEach.call(zone.children, function (n) {
      if (n.classList.contains('bx5625-pj')) cacher(n);
      var a = n.tagName === 'P' && n.children.length === 1 && n.querySelector('a[href*="/mes-taches?"]');
      if (a) cacher(n);                                       // 5.6.88 « Créer une tâche » : remplacé par le formulaire prérempli
    });
    if (window.axh56106fraude) window.axh56106fraude.placer();   // 5.6.106 (lot F) : bandeau d'alerte anti-fraude en tête des bandeaux du lecteur
    return bloc;
  }
  // appelé par v5625.js au début du rendu : le lecteur se pose juste après, sans remplacer la barre, les réponses ni « Répondre avec l'IA »
  function afficher(zone, m) {
    var faire = function () { try { poser(zone, m); } catch (e) { /* présentation d'origine gardée */ } };
    if (window.queueMicrotask) window.queueMicrotask(faire); else Promise.resolve().then(faire);
  }

  // ------------------------------------------------------------------ actions groupées : Résumer avec l'IA, → Tâche, → Agenda
  function selection() {
    if (window.axh56106liste && window.axh56106liste.selection) {
      return window.axh56106liste.selection().map(function (x) { return { uid: String(x.uid), dossier: x.dossier || '', objet: x.objet || '' }; });
    }
    var c = ctx() || {};
    return $$('#bx5625-rows .c5670-pick input:checked').map(function (b) { return { uid: b.getAttribute('data-uid'), dossier: c.folder || '' }; });
  }
  function volet(titre) {
    var c = ctx();
    if (!c) return null;
    c.panel.textContent = '';
    var fermer = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Fermer' });
    var outils = el('span', { 'class': 'lx56106-actions' }, [fermer]);
    var sec = el('section', { 'class': 'lx56106-resume', 'aria-label': titre }, [el('div', { 'class': 'lx56106-resume-tete' }, [el('h2', { tabindex: '-1', text: titre }), outils])]);
    fermer.addEventListener('click', function () { var n = ctx(); if (n && n.clear) n.clear(); });
    c.panel.appendChild(sec);
    $('h2', sec).focus();
    return { sec: sec, outils: outils };
  }
  function voletFormulaires(genre) {
    var items = selection();
    if (!items.length) { toast('Cochez au moins un courriel.', true); return; }
    if (items.length > 10) { toast('10 courriels au plus à la fois pour ces formulaires.', true); return; }
    var v = volet(genre === 'tache' ? 'Créer des tâches depuis les courriels cochés' : 'Ajouter à l’agenda depuis les courriels cochés');
    if (!v) return;
    v.sec.appendChild(el('p', { 'class': 'lx56106-note', text: 'Un formulaire par courriel, prérempli ; chacun est créé seulement quand vous le validez.' }));
    items.forEach(function (it) { var zone = el('div', {}); v.sec.appendChild(zone); formulairePour(genre, it, zone); });
  }
  function texteResume(r, n) {
    var lignes = ['Résumé par l’IA (' + n + ' courriel' + (n > 1 ? 's' : '') + ')', '', r.synthese, ''];
    r.courriels.forEach(function (x) {
      lignes.push('• ' + x.date + ' — ' + x.expediteur + ' — ' + x.objet + (x.etiquette ? ' [' + x.etiquette + ']' : ''));
      if (x.non_transmis) lignes.push('  Non transmis à l’IA (correspondance confidentielle).');
      else if (x.demande) lignes.push('  ' + x.demande);
      if (x.echeance || x.echeance_ia) lignes.push('  Échéance : ' + (x.echeance || x.echeance_ia + ' (indiquée par l’IA, à vérifier)'));
    });
    if (r.actions.length) { lignes.push('', 'Actions suggérées'); r.actions.forEach(function (a) { lignes.push('• ' + a.nom + ' : ' + a.libelle); }); }
    return lignes.join('\n');
  }
  function resumer() {
    var items = selection();
    if (!items.length) { toast('Cochez au moins un courriel à résumer.', true); return; }
    if (items.length > 25) { toast('25 courriels au plus par résumé.', true); return; }
    var v = volet('Résumé par l’IA — ' + items.length + ' courriel' + (items.length > 1 ? 's' : ''));
    if (!v) return;
    var etat = el('p', { 'class': 'lx56106-statut', role: 'status', 'aria-live': 'polite', text: 'Envoi de la demande…' });
    var contenu = el('div', { 'class': 'lx56106-resume-corps' });
    v.sec.appendChild(etat); v.sec.appendChild(contenu);
    api(R + 'resumer', { elements: items.map(function (x) { return { uid: x.uid, dossier: x.dossier }; }), cle: cle() }).then(function (r) {
      if (r.etat === 'bloque') { statut(etat, r.motif, true); return; }
      if (r.ecartes) etat.textContent = r.ecartes + ' courriel(s) introuvable(s) ou non accessible(s) écarté(s). ';
      suivre(r.id, v, etat, contenu);
    }).catch(function (e) { statut(etat, e.message, true); });
  }
  function suivre(id, v, etat, contenu) {
    var essais = 0, cleNote = cle();
    var tour = function () {
      if (!v.sec.isConnected) return;                         // volet fermé ou autre courriel ouvert
      api(R + 'resume?' + qs({ id: id })).then(function (r) {
        if (!v.sec.isConnected) return;
        if (r.etat === 'en_file' || r.etat === 'en_cours') {
          statut(etat, r.message);
          if (++essais < 150) setTimeout(tour, essais < 10 ? 1500 : 4000);
          else statut(etat, 'Le résumé prend plus de temps que prévu : il reste en file. Rouvrez ce volet plus tard depuis les courriels cochés.', true);
          return;
        }
        if (r.etat !== 'fait') { statut(etat, r.motif || 'Le résumé n’a pas pu être fait.', true); return; }
        rendreResume(r, v, etat, contenu, cleNote);
      }).catch(function (e) { statut(etat, e.message, true); });
    };
    tour();
  }
  function rendreResume(r, v, etat, contenu, cleNote) {
    var res = r.resultat;
    statut(etat, 'Résumé prêt' + (res.non_lus ? ' — ' + res.non_lus + ' courriel(s) non lu(s) : supprimé(s), déplacé(s) ou réservé(s)' : '') + '.');
    contenu.textContent = '';
    contenu.appendChild(el('h3', { text: 'Synthèse' }));
    contenu.appendChild(el('p', { 'class': 'lx56106-synthese', text: res.synthese }));
    contenu.appendChild(el('h3', { text: 'Courriel par courriel' }));
    var liste = el('ul', { 'class': 'lx56106-points' });
    res.courriels.forEach(function (x) {
      var titre = el('p', {}, [el('strong', { text: x.objet })]);
      if (x.etiquette) titre.appendChild(el('span', { 'class': 'lx56106-badge', text: x.etiquette }));
      var li = el('li', { 'class': 'lx56106-point' }, [titre, el('p', { 'class': 'lx56106-meta', text: 'De : ' + x.expediteur + ' · ' + x.date + (x.dossier_reconnu ? ' · Dossier : ' + x.dossier_reconnu.label : '') })]);
      li.appendChild(el('p', { text: x.non_transmis ? 'Non transmis à l’IA : correspondance confidentielle entre avocats (politique des données). Lisez-le directement.'
        : (x.demande || 'L’IA n’a rien relevé pour ce courriel.') }));
      if (x.echeance) li.appendChild(el('p', { text: 'Échéance repérée : ' + x.echeance.split('-').reverse().join('/') }));
      else if (x.echeance_ia) li.appendChild(el('p', { text: 'Échéance indiquée par l’IA (à vérifier) : ' + x.echeance_ia.split('-').reverse().join('/') }));
      var voir = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Ouvrir le courriel' });
      var faire = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Créer une tâche' });
      var zone = el('div', {});
      voir.addEventListener('click', function () { ouvrir(x); });
      faire.addEventListener('click', function () { zone.textContent = ''; formulairePour('tache', { uid: x.uid, dossier: x.dossier, uidvalidity: x.uidvalidity, objet: x.objet }, zone); });
      li.appendChild(el('div', { 'class': 'lx56106-actions' }, [voir, faire])); li.appendChild(zone);
      liste.appendChild(li);
    });
    contenu.appendChild(liste);
    if (res.actions.length) {
      contenu.appendChild(el('h3', { text: 'Actions suggérées' }));
      var ul = el('ul', { 'class': 'lx56106-points' });
      var parRef = {};
      res.courriels.forEach(function (x) { parRef[x.ref] = x; });
      res.actions.forEach(function (a) {
        var x = parRef[a.ref];
        var bouton = el('button', { type: 'button', 'class': 'ax-btn ghost', text: a.type === 'creer_tache' ? 'Créer la tâche…' : 'Ouvrir le courriel' });
        var zone = el('div', {});
        bouton.addEventListener('click', function () {
          if (a.type === 'creer_tache') { zone.textContent = ''; formulairePour('tache', { uid: x.uid, dossier: x.dossier, uidvalidity: x.uidvalidity, objet: x.objet }, zone); }
          else ouvrir(x);
        });
        ul.appendChild(el('li', { 'class': 'lx56106-point' }, [el('p', {}, [el('strong', { text: a.nom + ' : ' }), document.createTextNode(a.libelle)]),
          el('p', { 'class': 'lx56106-meta', text: x ? x.objet : '' }), el('div', { 'class': 'lx56106-actions' }, [bouton]), zone]));
      });
      contenu.appendChild(ul);
    }
    contenu.appendChild(el('p', { 'class': 'lx56106-note', text: 'Préparé par l’IA (' + (res.fournisseur.local ? 'modèle local' : 'service ' + res.fournisseur.nom) +
      ') à partir des courriels choisis : vérifiez avant d’agir. Rien n’a été envoyé ni classé.' }));
    var copier = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Copier' });
    copier.addEventListener('click', function () {
      var texte = texteResume(res, r.nombre);
      if (!navigator.clipboard) { toast('Copie impossible dans ce navigateur : sélectionnez le texte du volet.', true); return; }
      navigator.clipboard.writeText(texte).then(function () { toast('Résumé copié.'); }, function () { toast('Copie refusée par le navigateur.', true); });
    });
    v.outils.insertBefore(copier, v.outils.firstChild);
    if (res.dossiers && res.dossiers.length) {
      var choix = el('select', { 'aria-label': 'Dossier de la note' });
      options(choix, res.dossiers, res.dossiers.length === 1 ? res.dossiers[0].id : '', res.dossiers.length > 1 ? 'Choisir le dossier…' : undefined);
      var noter = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Enregistrer comme note du dossier' });
      noter.addEventListener('click', function () {
        if (!choix.value) { choix.focus(); toast('Choisissez le dossier de la note.', true); return; }
        busy(noter, true);
        api(R + 'resume/note', { id: r.id, matter: choix.value, cle: cleNote }).then(function (x) { toast(x.message); noter.textContent = 'Note enregistrée'; })
          .catch(function (e) { toast(e.message, true); busy(noter, false); });
      });
      v.outils.insertBefore(noter, v.outils.firstChild); v.outils.insertBefore(choix, noter);
    } else {
      contenu.appendChild(el('p', { 'class': 'lx56106-note', text: 'Aucun dossier reconnu dans ces courriels : l’enregistrement comme note du dossier n’est pas proposé.' }));
    }
  }

  function boutonsGroupes() {
    var resume = el('button', { type: 'button', 'class': 'ax-btn', text: '✨ Résumer avec l’IA', title: 'Résumé des courriels cochés (25 au plus), dans le volet de droite ; rien n’est envoyé' });
    var tache = el('button', { type: 'button', 'class': 'ax-btn ghost', text: '→ Tâche', title: 'Un formulaire de tâche prérempli par courriel coché' });
    var agenda = el('button', { type: 'button', 'class': 'ax-btn ghost', text: '→ Agenda', title: 'Un formulaire d’événement prérempli par courriel coché' });
    resume.addEventListener('click', resumer);
    tache.addEventListener('click', function () { voletFormulaires('tache'); });
    agenda.addEventListener('click', function () { voletFormulaires('evenement'); });
    var imprimer = el('button', { type: 'button', 'class': 'ax-btn ghost', text: '🖨 Imprimer', title: 'Les courriels cochés (25 au plus), un par page' });
    imprimer.addEventListener('click', function () {
      var items = selection();
      if (!items.length) { toast('Cochez au moins un courriel.', true); return; }
      if (items.length > 25) { toast('25 courriels au plus par impression.', true); return; }
      imprimerVue(urlLot('imprimer', items));
    });
    return el('span', { 'class': 'lx56106-v' }, [resume, tache, agenda, imprimer, menuEnregistrer(selection, null)]);
  }
  var groupes = null;
  function monterGroupes() {
    var boite = $('#bx5625');
    if (!boite) return true;
    var place = $('.bx56106-actions-v', boite);                 // emplacement de la barre d'actions groupées (lot L)
    if (place) {
      if (!groupes) groupes = boutonsGroupes();
      if (groupes.parentNode !== place) place.appendChild(groupes);
      return true;
    }
    return false;
  }
  function repliGroupes() {                                     // barre du lot L absente : à côté de « Traiter avec l'IA » (5.6.70)
    if (monterGroupes()) return;
    var tete = $('#bx5625 .bx5625-head');
    if (!tete) return;
    if (!groupes) groupes = boutonsGroupes();
    var traiter = $('.c5670-traiter', tete);
    if (!groupes.parentNode) tete.insertBefore(groupes, traiter ? traiter.nextSibling : tete.firstChild);
  }

  // ------------------------------------------------------------------ lien « Courriel d'origine » de « Mes tâches » : /courriels?courriel=<clé>
  function depuisLien() {
    var c = new URLSearchParams(location.search).get('courriel') || '';
    if (!/^[0-9a-f]{64}$/.test(c) || !$('#bx5625')) return;
    var essais = 0;
    var go = function () {
      if (!ctx()) { if (++essais < 30) setTimeout(go, 100); return; }
      api(R + 'origine?' + qs({ cle: c })).then(ouvrir).catch(function (e) { toast('Courriel d’origine : ' + e.message, true); });
    };
    go();
  }

  window.axh56106lecteur = { afficher: afficher, ouvrir: ouvrir, resumer: resumer, SANDBOX: SANDBOX, impressionAuto: true };
  // Ctrl+P sur la page Courriels : la vue d'impression propre du courriel ouvert (hors champ de saisie ; sinon, l'impression du navigateur)
  document.addEventListener('keydown', function (ev) {
    if (!(ev.ctrlKey || ev.metaKey) || ev.altKey || ev.shiftKey || String(ev.key).toLowerCase() !== 'p') return;
    var cible = ev.target, saisie = cible && (cible.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(cible.tagName));
    if (saisie || !courant || !courant.bloc.isConnected || courant.bloc.offsetParent === null) return;
    ev.preventDefault();
    courant.imprimer();
  });
  function boot() {
    if (!monterGroupes()) {
      var boite = $('#bx5625');
      if (boite) {
        var obs = new MutationObserver(function () { if (monterGroupes()) obs.disconnect(); });
        obs.observe(boite, { childList: true, subtree: true });
        setTimeout(repliGroupes, 2500);
      }
    }
    depuisLien();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
