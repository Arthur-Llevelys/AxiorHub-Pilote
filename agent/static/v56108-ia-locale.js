/* AxiorHub 5.6.108 (lot L) — assistant « Configurer l'IA locale » (Paramètres › IA et guide de démarrage).
   - Lecture : GET m56108/ia-locale/etat (détection, machine, modèles installés, suggestions) ; aucune écriture côté serveur.
   - Téléchargement : un clic sur « Télécharger… » n'envoie rien ; il montre nom, taille, place libre, licence et l'accès réseau d'Ollama,
     puis « Confirmer le téléchargement » envoie POST telecharger (clé d'idempotence). Progression lue par GET operation ; annulation ; reprise.
   - Réglages : POST appliquer (version lue, clé), puis essai court facultatif ; « Revenir aux réglages précédents » : POST revenir.
   Textes par textContent ; aucun HTML du serveur injecté ; liens externes en rel="noopener noreferrer". */
(function (racine) {
  'use strict';
  function nombre(x) { return x === null || x === undefined || x === '' ? '' : String(Math.round(Number(x) * 10) / 10).replace('.', ','); }
  function go(octets) { return nombre(Number(octets || 0) / 1e9); }
  var api = { nombre: nombre, go: go };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  var doc = document;
  var bloc = doc.getElementById('ia-locale56108');
  if (!bloc || !bloc.getAttribute('data-api') || bloc.getAttribute('data-l108') === '1') return;
  bloc.setAttribute('data-l108', '1');
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf'), base = bloc.getAttribute('data-api');
  var etatCourant = null, adresseSaisie = '', vramSaisie = '', suivi = null;

  function $(id) { return doc.getElementById(id); }
  function el(tag, texte, classe) { var n = doc.createElement(tag); if (texte !== undefined && texte !== null && texte !== '') n.textContent = texte; if (classe) n.className = classe; return n; }
  function vider(n) { while (n && n.firstChild) n.removeChild(n.firstChild); return n; }
  function cle() {
    var c = racine.crypto;
    if (c && c.randomUUID) return 'l108-' + c.randomUUID();
    var b = new Uint8Array(16); c.getRandomValues(b);
    return 'l108-' + Array.prototype.map.call(b, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function statut(texte, mauvais) {
    var s = bloc.querySelector('.l108-statut'); if (!s) return;
    s.textContent = texte || ''; s.classList.toggle('ax-alert', !!mauvais);
  }
  function appel(nom, corps) {
    var opts = corps === undefined ? { credentials: 'same-origin' } : { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) };
    return fetch(prefix + '/api440/' + base + nom, opts).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (d) {
        if (!r.ok || !d || d.error) { throw new Error((d && (d.message || d.error)) || 'Le serveur n’a pas confirmé la demande.'); }
        return d;
      });
    }, function () { throw new Error('Connexion au serveur interrompue.'); });
  }
  function ligneDl(dl, terme, valeur) {
    if (valeur === undefined || valeur === null || valeur === '') return;
    dl.appendChild(el('dt', terme)); var dd = el('dd'); if (typeof valeur === 'string') dd.textContent = valeur; else dd.appendChild(valeur); dl.appendChild(dd);
  }
  function lien(url, texte) { var a = el('a', texte); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; }
  function pastille(texte, sorte) { return el('span', texte, 'l108-pastille l108-' + sorte); }
  function valeurGo(x) { var s = el('span'); s.appendChild(el('span', nombre(x))); s.appendChild(doc.createTextNode(' ')); s.appendChild(el('span', 'Go')); return s; }

  /* ------------------------------------------------------------------ lecture */
  function lire() {
    var q = [];
    if (adresseSaisie) q.push('adresse=' + encodeURIComponent(adresseSaisie));
    if (vramSaisie) q.push('vram=' + encodeURIComponent(vramSaisie));
    vider($('l108-detection')).appendChild(el('p', 'Recherche d’Ollama…', 'ax-muted'));
    return appel('etat' + (q.length ? '?' + q.join('&') : '')).then(function (d) { etatCourant = d; dessiner(d); }, function (e) {
      vider($('l108-detection')).appendChild(el('p', e.message, 'ax-alert'));
    });
  }

  function dessiner(d) {
    dessinerDetection(d.detection);
    dessinerMachine(d.machine, d.detection);
    dessinerInstalles(d);
    dessinerSuggestions(d);
    dessinerReglages(d);
    dessinerEssai(d.essai);
    if (d.telechargement && d.telechargement.etat === 'en_cours') suivre(d.telechargement.id, d.telechargement.modele);
    else if (d.telechargement && !suivi) dessinerOperation(d.telechargement);
  }

  var ETATS = { joignable: ['Ollama joignable', 'ok'], arrete: ['Ollama installé, arrêté', 'alerte'], absent: ['Ollama absent', 'ko'],
    distant_injoignable: ['Ollama du réseau injoignable', 'ko'] };
  function dessinerDetection(det) {
    var box = vider($('l108-detection'));
    var e = ETATS[det.etat] || [det.etat, 'alerte'];
    var tete = el('p'); tete.appendChild(pastille(e[0], e[1])); box.appendChild(tete);
    box.appendChild(el('p', det.message));
    var dl = el('dl', '', 'l108-dl');
    var adr = el('span', det.adresse); adr.setAttribute('translate', 'no');
    ligneDl(dl, 'Adresse', adr);
    ligneDl(dl, 'Version', det.version);
    ligneDl(dl, 'Emplacement', det.locale ? 'Cet ordinateur' : 'Autre ordinateur du réseau');
    if (det.executable) ligneDl(dl, 'Programme Ollama', det.executable.trouve ? (det.executable.service ? 'Installé (service systemd)' : 'Installé') : 'Non trouvé');
    box.appendChild(dl);
    (det.actions || []).forEach(function (a) {
      if (a.type === 'lien') { var p = el('p'); p.appendChild(lien(a.url, 'Télécharger Ollama sur le site officiel')); box.appendChild(p); }
      if (a.type === 'commande') {
        box.appendChild(el('p', 'Commande officielle, à lancer vous-même dans un terminal (AxiorHub ne l’exécute jamais) :'));
        var c = el('code', a.texte, 'l108-commande'); c.setAttribute('translate', 'no'); box.appendChild(c);
      }
    });
    if (det.ollama_host) {
      var p = el('p', 'Variable OLLAMA_HOST présente sur cet ordinateur (non utilisée ; recopiez-la ci-dessus si c’est l’adresse voulue) :', 'ax-muted');
      box.appendChild(p); var v = el('code', det.ollama_host, 'l108-commande'); v.setAttribute('translate', 'no'); box.appendChild(v);
    }
  }

  var CLASSES = { legere: 'Légère', moyenne: 'Moyenne', forte: 'Forte', inconnue: 'Inconnue' };
  function dessinerMachine(m, det) {
    var box = vider($('l108-machine'));
    if (!m) return;
    var dl = el('dl', '', 'l108-dl');
    ligneDl(dl, 'Classe de machine', pastille(CLASSES[m.classe] || m.classe, m.classe === 'inconnue' ? 'alerte' : 'ok'));
    if (m.ram_go) ligneDl(dl, 'Mémoire vive', valeurGo(m.ram_go));
    if (m.coeurs) ligneDl(dl, 'Cœurs du processeur', String(m.coeurs));
    (m.cartes || []).forEach(function (c) { var s = el('span'); s.appendChild(el('span', c.nom)); s.appendChild(doc.createTextNode(' · ')); s.appendChild(valeurGo(c.vram_go)); ligneDl(dl, 'Carte graphique', s); });
    if (det && det.locale && !(m.cartes || []).length) ligneDl(dl, 'Carte graphique', 'Aucune carte reconnue');
    if (m.vram_go) ligneDl(dl, 'Mémoire vidéo retenue', valeurGo(m.vram_go));
    if (m.disque_libre_go !== null && m.disque_libre_go !== undefined) ligneDl(dl, 'Place libre pour les modèles', valeurGo(m.disque_libre_go));
    (m.charges || []).forEach(function (c) { var s = el('span'); s.appendChild(el('span', c.nom)); s.appendChild(doc.createTextNode(' · ')); s.appendChild(valeurGo(c.vram_go)); ligneDl(dl, 'Modèle chargé par Ollama', s); });
    box.appendChild(dl);
    box.appendChild(el('p', m.explication, 'ax-muted'));
  }

  function dessinerInstalles(d) {
    var box = vider($('l108-installes'));
    if (!d.detection.joignable) { box.appendChild(el('p', 'Ollama ne répond pas : les modèles installés ne peuvent pas être lus.', 'ax-muted')); return; }
    if (d.erreur_modeles) { box.appendChild(el('p', 'Liste des modèles illisible pour le moment.', 'ax-alert')); return; }
    if (!d.installes.length) { box.appendChild(el('p', 'Aucun modèle installé dans Ollama.', 'ax-muted')); return; }
    var cadre = el('div', '', 'l108-cadre'), t = el('table', '', 'l108-table'), th = el('tr');
    ['Modèle', 'Paramètres (milliards)', 'Taille (Go)', 'Contexte (jetons)', 'Capacités', 'État'].forEach(function (x) { var c = el('th', x); c.scope = 'col'; th.appendChild(c); });
    var thead = el('thead'); thead.appendChild(th); t.appendChild(thead);
    var tb = el('tbody');
    d.installes.forEach(function (m) {
      var tr = el('tr'), nom = el('td', m.nom); nom.setAttribute('translate', 'no'); tr.appendChild(nom);
      tr.appendChild(el('td', nombre(m.parametres_md))); tr.appendChild(el('td', nombre(m.taille_go)));
      tr.appendChild(el('td', m.contexte ? String(m.contexte) : '—'));
      var caps = el('td', (m.capacites || []).join(', ') || '—'); caps.setAttribute('translate', 'no'); tr.appendChild(caps);
      var etat = el('td');
      if (m.cloud) etat.appendChild(pastille('Non local (cloud) : jamais proposé', 'ko'));
      else if (m.embedding) etat.appendChild(pastille('Local · embeddings', 'ok'));
      else etat.appendChild(pastille(m.petit ? 'Local · petit modèle' : 'Local', 'ok'));
      tr.appendChild(etat); tb.appendChild(tr);
    });
    t.appendChild(tb); cadre.appendChild(t); box.appendChild(cadre);
  }

  var ORIGINES = { actuel: 'Réglage actuel, adapté', catalogue: 'Installé, recommandé par le catalogue', installe: 'Installé, adapté',
    meme_que_analyse: 'Même modèle que la rédaction', repli: 'Repli sur un modèle installé', aucun: 'Aucun modèle installé ne convient' };
  function dessinerSuggestions(d) {
    var box = vider($('l108-suggestions'));
    if (!d.detection.joignable) return;
    var sg = d.suggestions;
    var cadre = el('div', '', 'l108-cadre'), t = el('table', '', 'l108-table'), th = el('tr');
    ['Rôle', 'Réglage actuel', 'Proposé', 'Pourquoi'].forEach(function (x) { var c = el('th', x); c.scope = 'col'; th.appendChild(c); });
    var thead = el('thead'); thead.appendChild(th); t.appendChild(thead);
    var tb = el('tbody');
    sg.roles.forEach(function (r) {
      var tr = el('tr'); tr.appendChild(el('td', r.libelle));
      var a = el('td', r.actuel || '—'); a.setAttribute('translate', 'no'); tr.appendChild(a);
      var p = el('td', r.choisi || '—'); p.setAttribute('translate', 'no'); tr.appendChild(p);
      var w = el('td'); w.appendChild(el('span', ORIGINES[r.origine] || r.origine));
      if (r.telecharger) { w.appendChild(el('br')); w.appendChild(el('span', 'Téléchargement suggéré :')); w.appendChild(doc.createTextNode(' ')); var n = el('strong', r.telecharger.nom); n.setAttribute('translate', 'no'); w.appendChild(n); }
      if (r.note) { w.appendChild(el('br')); w.appendChild(el('span', r.note, 'ax-muted')); }
      tr.appendChild(w); tb.appendChild(tr);
    });
    t.appendChild(tb); cadre.appendChild(t); box.appendChild(cadre);
    var info = el('p', 'Catalogue relevé le', 'ax-muted'); info.appendChild(doc.createTextNode(' ' + (d.catalogue.releve_le || ''))); box.appendChild(info);
    if (d.catalogue.memoire) box.appendChild(el('p', d.catalogue.memoire, 'ax-muted'));
    if (!sg.telechargements.length) { box.appendChild(el('p', 'Aucun téléchargement nécessaire : les modèles installés remplissent chaque rôle.')); return; }
    box.appendChild(el('p', d.reseau, 'l108-avertissement'));
    if (!d.telechargement_permis) box.appendChild(el('p', d.motif_telechargement, 'ax-alert'));
    sg.telechargements.forEach(function (f) { box.appendChild(carteModele(f, d)); });
  }

  function carteModele(f, d) {
    var c = el('div', '', 'l108-carte');
    var titre = el('strong', f.nom); titre.setAttribute('translate', 'no'); c.appendChild(titre);
    var dl = el('dl', '', 'l108-dl');
    ligneDl(dl, 'Pour', (f.pour || []).map(function (r) { return (d.suggestions.roles.filter(function (x) { return x.role === r; })[0] || {}).libelle || r; }).join(' ; '));
    ligneDl(dl, 'Taille du téléchargement', valeurGo(f.telechargement_go));
    ligneDl(dl, 'Mémoire nécessaire (estimation)', valeurGo(f.memoire_go));
    ligneDl(dl, 'Licence', f.licence_libelle);
    ligneDl(dl, 'Fiche officielle', lien(f.fiche, f.fiche));
    c.appendChild(dl);
    var b = el('button', 'Télécharger…', 'ax-btn'); b.type = 'button';
    b.disabled = !d.telechargement_permis || !!suivi;
    b.addEventListener('click', function () { confirmer(f, c); });
    var actions = el('div', '', 'l108-actions'); actions.appendChild(b); c.appendChild(actions);
    return c;
  }

  /* ------------------------------------------------------------------ téléchargement (accord explicite) */
  function confirmer(f, carte) {
    var ancien = bloc.querySelector('.l108-confirmation'); if (ancien) ancien.remove();
    var p = el('div', '', 'l108-carte l108-confirmation'); p.setAttribute('role', 'group'); p.setAttribute('aria-label', 'Confirmer le téléchargement');
    p.appendChild(el('strong', 'Confirmer le téléchargement'));
    var dl = el('dl', '', 'l108-dl');
    var nom = el('span', f.nom); nom.setAttribute('translate', 'no'); ligneDl(dl, 'Modèle', nom);
    ligneDl(dl, 'Taille du téléchargement', valeurGo(f.telechargement_go));
    var libre = etatCourant && etatCourant.machine ? etatCourant.machine.disque_libre_go : null;
    ligneDl(dl, 'Place libre', libre === null || libre === undefined ? 'Inconnue (Ollama sur un autre ordinateur ou dossier introuvable)' : valeurGo(libre));
    ligneDl(dl, 'Licence', f.licence_libelle);
    ligneDl(dl, 'Fiche officielle', lien(f.fiche, f.fiche));
    p.appendChild(dl);
    p.appendChild(el('p', etatCourant.reseau, 'l108-avertissement'));
    var ok = el('button', 'Confirmer le téléchargement', 'ax-btn'); ok.type = 'button';
    var non = el('button', 'Annuler', 'ax-btn ghost'); non.type = 'button';
    non.addEventListener('click', function () { p.remove(); statut('Téléchargement non demandé.'); });
    ok.addEventListener('click', function () {
      ok.disabled = true; non.disabled = true;
      var corps = { cle: cle(), modele: f.nom, confirme: true };
      if (adresseSaisie) corps.adresse = adresseSaisie;
      appel('telecharger', corps).then(function (r) {
        p.remove(); statut(r.message); suivre(r.operation.id, f.nom);
      }, function (e) { ok.disabled = false; non.disabled = false; statut(e.message, true); });
    });
    var actions = el('div', '', 'l108-actions'); actions.appendChild(ok); actions.appendChild(non); p.appendChild(actions);
    carte.parentNode.insertBefore(p, carte.nextSibling);
    ok.focus();
  }

  var STATUTS = { demande: 'Demande transmise à Ollama', manifeste: 'Lecture du manifeste', telechargement: 'Téléchargement', verification: 'Vérification',
    ecriture: 'Écriture', nettoyage: 'Nettoyage', termine: 'Terminé', annule: 'Annulé', erreur: 'Erreur' };
  var ETATS_OP = { en_cours: 'En cours', termine: 'Terminé', annule: 'Annulé', erreur: 'Échec', interrompu: 'Interrompu' };
  function dessinerOperation(op) {
    var box = vider($('l108-telechargement'));
    if (!op) return;
    var c = el('div', '', 'l108-carte');
    var t = el('p'); t.appendChild(el('strong', 'Téléchargement')); t.appendChild(doc.createTextNode(' ')); var n = el('span', op.modele); n.setAttribute('translate', 'no'); t.appendChild(n);
    t.appendChild(doc.createTextNode(' ')); t.appendChild(pastille(ETATS_OP[op.etat] || op.etat, op.etat === 'termine' ? 'ok' : (op.etat === 'en_cours' ? 'alerte' : 'ko')));
    c.appendChild(t);
    if (op.etat === 'en_cours') {
      var barre = el('progress', '', 'l108-progression'); barre.max = 100; if (op.pourcent !== null) barre.value = op.pourcent;
      barre.setAttribute('aria-label', 'Progression du téléchargement'); c.appendChild(barre);
    }
    var dl = el('dl', '', 'l108-dl');
    ligneDl(dl, 'Étape', STATUTS[op.statut] || op.statut);
    if (op.total) { var s = el('span'); s.appendChild(valeurGo(op.fait / 1e9)); s.appendChild(doc.createTextNode(' / ')); s.appendChild(valeurGo(op.total / 1e9)); ligneDl(dl, 'Reçu', s); }
    if (op.pourcent !== null && op.pourcent !== undefined) ligneDl(dl, 'Avancement', nombre(op.pourcent) + ' %');
    if (op.erreur) { var er = el('span', op.erreur); er.setAttribute('translate', 'no'); ligneDl(dl, 'Motif', er); }
    c.appendChild(dl);
    var actions = el('div', '', 'l108-actions');
    if (op.etat === 'en_cours') {
      var b = el('button', 'Annuler le téléchargement', 'ax-btn ghost'); b.type = 'button';
      b.addEventListener('click', function () {
        b.disabled = true;
        appel('annuler', { cle: cle(), id: op.id }).then(function (r) { statut(r.message); }, function (e) { b.disabled = false; statut(e.message, true); });
      });
      actions.appendChild(b);
    } else if (op.etat !== 'termine') {
      var f = (((etatCourant || {}).suggestions || {}).telechargements || []).filter(function (x) { return x.nom === op.modele; })[0];
      if (f && etatCourant.telechargement_permis) {
        var rep = el('button', 'Reprendre le téléchargement', 'ax-btn'); rep.type = 'button';
        rep.addEventListener('click', function () { confirmer(f, c); });
        actions.appendChild(rep);
        c.appendChild(el('p', 'Ollama garde la partie déjà reçue : la reprise repart de là.', 'ax-muted'));
      }
    }
    c.appendChild(actions);
    box.appendChild(c);
  }

  function suivre(id, modele) {
    if (suivi) clearTimeout(suivi);
    bloc.querySelectorAll('.l108-carte button').forEach(function (b) { if (/Télécharger/.test(b.textContent)) b.disabled = true; });
    function tour() {
      appel('operation?id=' + encodeURIComponent(id)).then(function (op) {
        dessinerOperation(op);
        if (op.etat === 'en_cours') { suivi = setTimeout(tour, 1000); return; }
        suivi = null;
        statut(op.etat === 'termine' ? 'Téléchargement terminé : le modèle est installé.' : (op.etat === 'annule' ? 'Téléchargement annulé.' : 'Téléchargement interrompu : vous pouvez le reprendre.'), op.etat !== 'termine' && op.etat !== 'annule');
        if (op.etat === 'termine') lire();
      }, function (e) { suivi = setTimeout(tour, 3000); statut(e.message, true); });
    }
    tour();
  }

  /* ------------------------------------------------------------------ réglages */
  function select(nom, idSel, libelle, valeurs, choisi) {
    var lab = el('label', libelle); lab.htmlFor = idSel;
    var s = el('select'); s.id = idSel; s.name = nom;
    valeurs.forEach(function (v) { var o = el('option', v[1] || v[0]); o.value = v[0]; if (v[0] === choisi) o.selected = true; s.appendChild(o); });
    return [lab, s];
  }
  function dessinerReglages(d) {
    var f = vider($('l108-appliquer')); f.className = 'l108-form';
    var retour = vider($('l108-retour'));
    if (d.derniere_configuration) {
      var b = el('button', 'Revenir aux réglages précédents', 'ax-btn ghost'); b.type = 'button';
      b.addEventListener('click', function () {
        if (!racine.confirm('Rétablir les réglages d’avant la dernière configuration appliquée par l’assistant ?')) return;
        b.disabled = true;
        appel('revenir', { cle: cle(), version: d.version }).then(function (r) { statut(r.message); lire(); }, function (e) { b.disabled = false; statut(e.message, true); });
      });
      retour.appendChild(b);
    }
    if (!d.detection.joignable || !d.suggestions.roles.length) { f.appendChild(el('p', 'Les réglages seront proposés quand Ollama répondra.', 'ax-muted')); return; }
    var discussion = d.installes.filter(function (m) { return m.local && !m.embedding; }).map(function (m) { return [m.nom]; });
    var empreintes = d.installes.filter(function (m) { return m.local && m.embedding; }).map(function (m) { return [m.nom]; });
    d.suggestions.roles.forEach(function (r) {
      var vals = r.role === 'embeddings' ? empreintes : discussion;
      if (!vals.length) return;
      var choix = r.choisi || (r.role === 'embeddings' ? d.reglages.embeddings : '');
      var paire = select(r.role, 'l108-' + r.role, r.libelle, vals, choix);
      f.appendChild(paire[0]); f.appendChild(paire[1]);
    });
    var avert = el('div', '', 'l108-avertissement'); avert.hidden = true;
    avert.appendChild(el('p', 'Changer le modèle d’embeddings fait recalculer l’index de la recherche : les passages déjà indexés le seront de nouveau au fil de l’indexation habituelle ; rien n’est lancé maintenant.'));
    var conf = el('label', '', 'l108-case'); var cc = el('input'); cc.type = 'checkbox'; cc.name = 'confirmer_embeddings'; conf.appendChild(cc);
    conf.appendChild(el('span', 'Je confirme le changement du modèle d’embeddings')); avert.appendChild(conf);
    f.appendChild(avert);
    var se = f.querySelector('select[name=embeddings]');
    function majAvert() { avert.hidden = !se || se.value === d.reglages.embeddings; }
    if (se) se.addEventListener('change', majAvert); majAvert();
    if (d.conseil && d.conseil.prereglages.length) {
      var opts = [['', 'Ne pas changer le conseil de modèles']].concat(d.conseil.prereglages.map(function (p) { return [p.id, p.libelle + ' · ' + p.etat_libelle]; }));
      var actuelConseil = d.conseil.actuel.mode === 'prereglage' ? d.conseil.actuel.prereglage : '';
      var paire = select('conseil', 'l108-conseil', 'Préréglage du conseil de modèles', opts, d.conseil.suggere && d.conseil.suggere !== actuelConseil ? d.conseil.suggere : '');
      f.appendChild(paire[0]); f.appendChild(paire[1]);
      if (d.conseil.motif) f.appendChild(el('p', d.conseil.motif, 'ax-muted'));
    }
    if (d.detection.source === 'saisie' && d.detection.adresse !== d.reglages.adresse) {
      var pa = el('p', 'Adresse d’Ollama enregistrée à l’application :'); var ad = el('strong', d.detection.adresse); ad.setAttribute('translate', 'no');
      pa.appendChild(doc.createTextNode(' ')); pa.appendChild(ad); f.appendChild(pa);
    }
    var es = el('label', '', 'l108-case'); var ce = el('input'); ce.type = 'checkbox'; ce.name = 'essai'; ce.checked = true; es.appendChild(ce);
    es.appendChild(el('span', 'Faire un essai court après l’application (une question fictive à chaque modèle retenu)')); f.appendChild(es);
    var ok = el('button', 'Appliquer cette configuration', 'ax-btn'); ok.type = 'submit'; f.appendChild(ok);
    f.onsubmit = function (ev) {
      ev.preventDefault();
      var corps = { cle: cle(), version: d.version, essai: ce.checked, confirmer_embeddings: cc.checked };
      ['rapide', 'analyse', 'controle', 'embeddings', 'conseil'].forEach(function (k) { var s = f.querySelector('select[name=' + k + ']'); if (s) corps[k] = s.value; });
      if (corps.embeddings === d.reglages.embeddings) delete corps.embeddings;
      if (d.detection.source === 'saisie') corps.adresse = d.detection.adresse;
      ok.disabled = true;
      appel('appliquer', corps).then(function (r) {
        statut(r.message);
        adresseSaisie = ''; var champ = $('l108-adresse-champ'); if (champ) champ.value = '';
        if (r.essai) suivreEssai(r.essai.id);
        lire().then(function () { if (r.reindexation) $('l108-retour').appendChild(el('p', r.reindexation, 'l108-avertissement')); });
      }, function (e) { ok.disabled = false; statut(e.message, true); });
    };
  }

  /* ------------------------------------------------------------------ essai court */
  function dessinerEssai(op) {
    var box = vider($('l108-essai'));
    var b = el('button', 'Lancer un essai court', 'ax-btn ghost'); b.type = 'button';
    b.addEventListener('click', function () {
      b.disabled = true;
      appel('essai', { cle: cle() }).then(function (r) { statut(r.message); suivreEssai(r.operation.id); }, function (e) { b.disabled = false; statut(e.message, true); });
    });
    var actions = el('div', '', 'l108-actions'); actions.appendChild(b);
    if (!op) { box.appendChild(el('p', 'Aucun essai pour l’instant.', 'ax-muted')); box.appendChild(actions); return; }
    var lignes = (op.resultat || {}).lignes || [];
    var t = el('p'); t.appendChild(el('strong', 'Dernier essai')); t.appendChild(doc.createTextNode(' '));
    t.appendChild(pastille(ETATS_OP[op.etat] || op.etat, op.etat === 'termine' ? 'ok' : (op.etat === 'en_cours' ? 'alerte' : 'ko'))); box.appendChild(t);
    if (lignes.length) {
      var cadre = el('div', '', 'l108-cadre'), tab = el('table', '', 'l108-table'), th = el('tr');
      ['Rôle', 'Modèle', 'Résultat', 'Durée (s)', 'Réponse ou motif'].forEach(function (x) { var c = el('th', x); c.scope = 'col'; th.appendChild(c); });
      var thead = el('thead'); thead.appendChild(th); tab.appendChild(thead); var tb = el('tbody');
      lignes.forEach(function (l) {
        var tr = el('tr'); tr.appendChild(el('td', l.libelle));
        var m = el('td', l.modele); m.setAttribute('translate', 'no'); tr.appendChild(m);
        var r = el('td'); r.appendChild(pastille(l.ok ? 'Répond' : 'Échec', l.ok ? 'ok' : 'ko')); tr.appendChild(r);
        tr.appendChild(el('td', nombre(l.secondes)));
        var x = el('td', l.extrait || l.erreur); x.setAttribute('translate', 'no'); tr.appendChild(x);
        tb.appendChild(tr);
      });
      tab.appendChild(tb); cadre.appendChild(tab); box.appendChild(cadre);
    }
    if (op.etat !== 'en_cours') box.appendChild(actions);
  }
  function suivreEssai(id) {
    function tour() {
      appel('operation?id=' + encodeURIComponent(id)).then(function (op) {
        dessinerEssai(op);
        if (op.etat === 'en_cours') { setTimeout(tour, 1500); return; }
        statut(op.etat === 'termine' ? 'Essai court terminé : chaque modèle a répondu.' : 'Essai court terminé avec des échecs : voir le tableau.', op.etat !== 'termine');
      }, function (e) { statut(e.message, true); });
    }
    tour();
  }

  /* ------------------------------------------------------------------ saisies */
  var form = $('l108-adresse');
  if (form) form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    adresseSaisie = ($('l108-adresse-champ').value || '').trim();
    statut('');
    lire();
  });
  var vb = $('l108-vram-ok');
  if (vb) vb.addEventListener('click', function () { vramSaisie = ($('l108-vram').value || '').trim(); lire(); });
  lire();
})(typeof window !== 'undefined' ? window : this);
