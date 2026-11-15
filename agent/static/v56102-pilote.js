/* AxiorHub 5.6.102 (lot M) — Pilote dans la messagerie instantanée.
   - En-tête de la discussion : « Résumé de la discussion » (résumé chronologique tenu à jour par Pilote, ouvert dans le volet de droite,
     jamais posté dans la discussion ; « Arrêter le suivi » / « Reprendre le suivi ») et, dans le menu « … », « Retirer Pilote » / « Ajouter Pilote » (routes m56102/pilote/*, clé
     d'idempotence tirée ici et gardée pour une nouvelle tentative du même geste).
   - Indicateur « Pilote prépare une réponse… » tant qu'une demande est en file ou en cours (lecture seule de m56102/pilote/etat).
   - Sous chaque réponse de Pilote : ce qui a été fait (tâche, rappel, événement : « Annuler » ; projet de courriel ou de document : état et
     « Ouvrir à droite »).
   - Projets : volet de relecture 5.6.101 (window.AX101volet, v56101-volet.js) monté dans un <dialog> : ancré à droite sur grand écran
     (≥ 1200 px), fenêtre flottante sinon. Le détail vient de /api440/m5689/accueil/action (HTML construit et échappé par le serveur, mêmes
     droits que la page complète) ; tout le reste passe par textContent. Aucun envoi, aucun dépôt : seules ces routes et les décisions du volet. */
(function () {
  'use strict';
  var doc = document, racine = window;
  var outils = doc.getElementById('p102-outils');
  if (!outils) return;

  function meta(n) { var x = doc.querySelector('meta[name="' + n + '"]'); return x ? x.content : ''; }
  var prefix = meta('axiorhub-prefix') || '', csrf = meta('axiorhub-csrf');
  var fil = outils.getAttribute('data-fil');
  var attente = doc.getElementById('p102-attente'), boite = doc.getElementById('m50-messages'), statut = doc.getElementById('m50-status');
  var M = {};
  Array.prototype.forEach.call(doc.querySelectorAll('script[data-p102-messages]'), function (s) {
    try { var d = JSON.parse(s.textContent || '{}'); for (var k in d) if (Object.prototype.hasOwnProperty.call(d, k)) M[k] = d[k]; } catch (e) { /* libellés par défaut */ }
  });
  function remplir(t, v) { return String(t || '').replace(/\{(\w+)\}/g, function (_, k) { return v && v[k] !== undefined ? String(v[k]) : ''; }); }
  function m(k, v) { return remplir(M[k] || '', v || {}); }
  function el(tag, cls, text) { var n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function cle() {
    var t = new Uint8Array(16), c = racine.crypto;
    if (c && c.getRandomValues) c.getRandomValues(t); else for (var i = 0; i < 16; i++) t[i] = Math.floor(Math.random() * 256);
    return 'p102-' + Array.prototype.map.call(t, function (o) { return ('0' + o.toString(16)).slice(-2); }).join('');
  }
  function lire(r) {
    return r.json().catch(function () { return {}; }).then(function (d) {
      if (!r.ok || (d && d.error)) { var x = new Error((d && d.message) || ('HTTP ' + r.status)); x.code = d && d.error; x.statut = r.status; throw x; }
      return d;
    });
  }
  function get(route) { return racine.fetch(prefix + '/api440/m56102/pilote/' + route, { credentials: 'same-origin', cache: 'no-store' }).then(lire); }
  var cles = {};   /* même geste après un échec réseau : même clé, donc jamais un second effet */
  function post(route, corps, geste) {
    corps.cle = cles[geste] || (cles[geste] = cle());
    return racine.fetch(prefix + '/api440/m56102/pilote/' + route, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) }).then(lire)
      .then(function (d) { delete cles[geste]; return d; });
  }
  function dire(texte) { if (statut) statut.textContent = texte || ''; }

  /* ------------------------------------------------------------------ outils de l'en-tête */
  var etat = { present: outils.getAttribute('data-present') === 'oui', actions: {} };
  var resumer = outils.querySelector('[data-p102-resume]'), presence = outils.querySelector('[data-p102-presence]');
  function peindreOutils() {
    resumer.hidden = !etat.present;
    presence.textContent = etat.present ? 'Retirer Pilote' : 'Ajouter Pilote';
    outils.setAttribute('data-present', etat.present ? 'oui' : 'non');
  }
  presence.addEventListener('click', function () {
    var voulu = !etat.present;
    presence.disabled = true;
    post('presence', { fil: fil, present: voulu }, 'presence:' + voulu).then(function (d) {
      etat.present = !!d.present; peindreOutils(); dire(d.message || '');
      var menu = presence.closest('details'); if (menu) menu.open = false;
      if (racine.AX5650) racine.AX5650.recharger();
    }).catch(function (e) { dire(e.message); }).then(function () { presence.disabled = false; });
  });
  resumer.addEventListener('click', function () {
    resumer.disabled = true;
    post('resume/demarrer', { fil: fil }, 'resume:demarrer').then(function () { ouvrirResume(); })
      .catch(function (e) { dire(e.message); ouvrirResume(); }).then(function () { resumer.disabled = false; });
  });

  /* ------------------------------------------------------------------ indicateur et actions des réponses */
  var enAttente = false, minuterie = null;
  function montrerAttente(texte) { attente.textContent = texte; attente.hidden = !texte; enAttente = !!texte; planifier(2500); }
  function planifier(ms) {
    racine.clearTimeout(minuterie);
    minuterie = racine.setTimeout(function () { if (doc.hidden) planifier(ms); else rafraichir(); }, ms);
  }
  function rafraichir() {
    return get('etat?fil=' + encodeURIComponent(fil)).then(function (d) {
      etat.present = !!d.present; etat.actions = d.actions || {};
      peindreOutils();
      var textes = (d.attente || []).map(function (x) { return x.texte; });
      var avant = enAttente;
      attente.textContent = textes[0] || ''; attente.hidden = !textes.length; enAttente = !!textes.length;
      if (avant && !enAttente && racine.AX5650) racine.AX5650.recharger();   /* la réponse vient d'arriver */
      decorer();
      planifier(enAttente ? 3000 : 12000);
    }).catch(function () { planifier(20000); });
  }
  doc.addEventListener('m50:envoye', function (ev) {
    var p = ev.detail && ev.detail.pilote;
    if (p && p.etat === 'en_file') montrerAttente('Pilote prépare une réponse…');
    else if (p) rafraichir();
  });

  var LIBELLES = { tache: 'Tâche', rappel: 'Rappel', evenement: 'Événement', courriel: 'Projet de courriel', document: 'Projet de document' };
  function decorer() {
    if (!boite) return;
    Array.prototype.forEach.call(boite.querySelectorAll('li.m50-msg[data-id]'), function (li) {
      var liste = etat.actions[li.getAttribute('data-id')], ancien = li.querySelector('.p102-actions');
      var sig = liste ? JSON.stringify(liste.map(function (a) { return [a.id, a.etat, a.etat_libelle, a.annulable]; })) : '';
      if (ancien && ancien.getAttribute('data-sig') === sig) return;
      if (ancien) ancien.remove();
      if (!liste) return;
      var ul = el('ul', 'p102-actions'); ul.setAttribute('data-sig', sig);
      liste.forEach(function (a) {
        var item = el('li', 'p102-action' + (a.etat === 'annule' ? ' p102-annule' : ''));
        item.appendChild(el('span', 'p102-action-titre', (LIBELLES[a.type] || a.type) + ' : « ' + a.titre + ' »'));
        item.appendChild(el('span', 'p102-action-etat', ' — ' + (a.etat_libelle || '')));
        if (a.annulable) { var b = el('button', 'p102-lien', 'Annuler'); b.type = 'button'; b.setAttribute('data-p102-annuler', a.id); item.appendChild(b); }
        if (a.ouvrable) { var o = el('button', 'p102-lien', 'Ouvrir à droite'); o.type = 'button'; o.setAttribute('data-p102-ouvrir', a.id); item.appendChild(o); }
        ul.appendChild(item);
      });
      li.appendChild(ul);
    });
  }
  if (boite && racine.MutationObserver) new racine.MutationObserver(function () { decorer(); }).observe(boite, { childList: true });
  if (boite) boite.addEventListener('click', function (ev) {
    var annuler = ev.target.closest && ev.target.closest('[data-p102-annuler]');
    var ouvrir = ev.target.closest && ev.target.closest('[data-p102-ouvrir]');
    if (annuler) {
      annuler.disabled = true;
      var id = annuler.getAttribute('data-p102-annuler');
      post('annuler', { action: id }, 'annuler:' + id).then(function (d) { dire(d.message || ''); rafraichir(); })
        .catch(function (e) { dire(e.message); annuler.disabled = false; });
    } else if (ouvrir) {
      get('projet?action=' + encodeURIComponent(ouvrir.getAttribute('data-p102-ouvrir'))).then(function (p) {
        if (p.ouvrable) ouvrirVolet(p);
        else dire('« ' + p.titre + ' » : ' + p.etat_libelle + '.');
      }).catch(function (e) { dire(e.message); });
    }
  });

  /* ------------------------------------------------------------------ volet de relecture (5.6.101) : ancré à droite ou fenêtre flottante */
  var LARGE = 1200, fenetre = null, ouvert = null, demande = null, bascule = false;
  function icone(cls, texte, libelle) { var b = el('button', 'f96-icone-btn ' + cls, texte); b.type = 'button'; b.setAttribute('aria-label', libelle); b.title = libelle; return b; }
  function construire() {
    var d = el('dialog', 'f96-fenetre p102-fenetre');
    d.setAttribute('aria-labelledby', 'p102-fenetre-titre');
    var barre = el('div', 'f96-fenetre-barre'), tete = el('div', 'f96-fenetre-tete');
    var titre = el('h2', 'f96-fenetre-titre'); titre.id = 'p102-fenetre-titre';
    tete.appendChild(titre); tete.appendChild(el('p', 'f96-fenetre-dossier'));
    var cmd = el('div', 'f96-fenetre-commandes');
    var page = el('a', 'f96-icone-btn f96-fenetre-page', '↗'); page.setAttribute('aria-label', m('fenetre.page_complete') || 'Page complète'); page.title = page.getAttribute('aria-label');
    var fermer = icone('f96-fenetre-fermer', '✕', m('fenetre.fermer') || 'Fermer');
    cmd.appendChild(page); cmd.appendChild(fermer);
    barre.appendChild(tete); barre.appendChild(cmd);
    var corps = el('div', 'f96-fenetre-corps'); corps.tabIndex = -1;
    var etatVolet = el('p', 'f96-fenetre-etat'); etatVolet.setAttribute('role', 'status');
    corps.appendChild(etatVolet); corps.appendChild(el('div', 'f96-fenetre-contenu'));
    d.appendChild(barre); d.appendChild(corps);
    (doc.getElementById('ws-main') || doc.body).appendChild(d);
    fermer.addEventListener('click', function () { d.close(); });
    d.addEventListener('close', function () {
      if (bascule) { bascule = false; return; }   /* passage ancré ↔ flottant : fermer puis rouvrir le même <dialog>, contenu gardé */
      if (racine.AX101volet) racine.AX101volet.demonter();
      doc.body.classList.remove('v101-volet-ouvert');
      ouvert = null;
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
  /* Résumé suivi : cadre simple dans le même volet (texte par textContent, aucune barre de décision : rien n'est à valider) */
  var minuterieResume = null;
  function peindreResume(r) {
    if (!fenetre || !fenetre.open || !ouvert || ouvert.nature !== 'resume') return;
    var contenu = fenetre.querySelector('.f96-fenetre-contenu');
    fenetre.querySelector('.f96-fenetre-dossier').textContent = r.etat === 'aucun' ? '' : (r.libelle || '');
    while (contenu.firstChild) contenu.removeChild(contenu.firstChild);
    var bloc = el('section', 'p102-resume');
    bloc.appendChild(el('p', 'p102-resume-etat', r.etat === 'aucun' ? r.libelle : (r.etat_libelle || '') + (r.en_cours ? ' — mise à jour en cours…' : '')));
    if (r.motif) { var avis = el('p', 'v101-avis', r.motif); avis.setAttribute('role', 'status'); bloc.appendChild(avis); }
    bloc.appendChild(r.texte ? el('div', 'p102-resume-texte v101-texte', r.texte) : el('p', 'v101-vide', 'Le premier résumé est en préparation ; il s’affichera ici.'));
    var actions = el('p', 'p102-resume-actions');
    var b = el('button', 'ax-btn ghost', r.etat === 'actif' ? 'Arrêter le suivi' : 'Reprendre le suivi'); b.type = 'button';
    b.addEventListener('click', function () {
      b.disabled = true;
      var route = r.etat === 'actif' ? 'resume/arreter' : 'resume/demarrer';
      post(route, { fil: fil }, 'resume:' + route).then(function (d) { dire(d.message || ''); return chargerResume(); })
        .catch(function (e) { dire(e.message); b.disabled = false; });
    });
    if (r.etat !== 'aucun' || r.present) actions.appendChild(b);
    bloc.appendChild(actions);
    contenu.appendChild(bloc);
  }
  function chargerResume() {
    racine.clearTimeout(minuterieResume);
    return get('resume?fil=' + encodeURIComponent(fil)).then(function (r) {
      var etatVolet = fenetre.querySelector('.f96-fenetre-etat'); etatVolet.textContent = ''; etatVolet.removeAttribute('data-erreur');
      fenetre.removeAttribute('aria-busy');
      peindreResume(r);
    }).catch(function (e) {
      var etatVolet = fenetre.querySelector('.f96-fenetre-etat'); etatVolet.setAttribute('data-erreur', '1'); etatVolet.textContent = e.message;
      fenetre.removeAttribute('aria-busy');
    }).then(function () {
      if (fenetre.open && ouvert && ouvert.nature === 'resume') minuterieResume = racine.setTimeout(chargerResume, 30000);
    });
  }
  function ouvrirResume() {
    if (!fenetre) fenetre = construire();
    if (demande) demande.abort();
    if (racine.AX101volet) racine.AX101volet.demonter();
    ouvert = { nature: 'resume', titre: 'Résumé de la discussion' };
    fenetre.querySelector('.f96-fenetre-titre').textContent = 'Résumé de la discussion';
    fenetre.querySelector('.f96-fenetre-dossier').textContent = '';
    var page = fenetre.querySelector('.f96-fenetre-page'); page.hidden = true;
    var contenu = fenetre.querySelector('.f96-fenetre-contenu');
    while (contenu.firstChild) contenu.removeChild(contenu.firstChild);
    fenetre.querySelector('.f96-fenetre-etat').textContent = m('fenetre.chargement') || 'Chargement…';
    fenetre.setAttribute('aria-busy', 'true');
    if (!placer()) fenetre.querySelector('.f96-fenetre-fermer').focus();
    chargerResume();
  }

  function ouvrirVolet(p) {
    if (!fenetre) fenetre = construire();
    fenetre.querySelector('.f96-fenetre-page').hidden = false;
    if (demande) demande.abort();
    demande = racine.AbortController ? new racine.AbortController() : null;
    ouvert = p;
    var contenu = fenetre.querySelector('.f96-fenetre-contenu'), etatVolet = fenetre.querySelector('.f96-fenetre-etat');
    fenetre.querySelector('.f96-fenetre-titre').textContent = p.titre || '';
    fenetre.querySelector('.f96-fenetre-dossier').textContent = '';
    fenetre.querySelector('.f96-fenetre-page').href = prefix + (p.page || '');
    if (racine.AX101volet) racine.AX101volet.demonter();
    while (contenu.firstChild) contenu.removeChild(contenu.firstChild);
    etatVolet.textContent = m('fenetre.chargement') || 'Chargement…'; etatVolet.removeAttribute('data-erreur');
    fenetre.setAttribute('aria-busy', 'true');
    var ancre = placer();
    if (!ancre) fenetre.querySelector('.f96-fenetre-fermer').focus();
    var url = prefix + '/api440/m5689/accueil/action?nature=' + encodeURIComponent(p.nature) + '&cle=' + encodeURIComponent(p.cle);
    racine.fetch(url, { credentials: 'same-origin', cache: 'no-store', signal: demande ? demande.signal : undefined }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok || d.error) throw new Error(d.message || ('HTTP ' + r.status)); return d; });
    }).then(function (d) {
      fenetre.querySelector('.f96-fenetre-titre').textContent = d.titre || p.titre || '';
      if (d.meta || d.dossier) fenetre.querySelector('.f96-fenetre-dossier').textContent = [d.dossier || '', d.meta || ''].filter(Boolean).join(' · ');
      if (racine.AX101volet) {
        racine.AX101volet.monter({ fenetre: fenetre, contenu: contenu, donnees: d, ctx: {
          m: function (k, v) { return m(k, v); },
          apres: function (nom, texte) { if (racine.AX101volet.courant()) racine.AX101volet.annoncer(texte || m('volet.fait') || 'Fait.'); rafraichir(); },
          recharger: function (msg) { if (ouvert) { ouvrirVolet(ouvert); if (msg) racine.setTimeout(function () { if (racine.AX101volet.courant()) racine.AX101volet.annoncer(msg); }, 400); } }
        } });
      } else {
        contenu.innerHTML = d.html;      /* HTML construit et échappé par le serveur (mêmes générateurs que la page complète) */
      }
      etatVolet.textContent = '';
      fenetre.removeAttribute('aria-busy');
    }).catch(function (e) {
      if (e && e.name === 'AbortError') return;
      fenetre.removeAttribute('aria-busy');
      etatVolet.setAttribute('data-erreur', '1');
      etatVolet.textContent = e.message;
    });
  }
  racine.addEventListener('resize', function () { if (fenetre && fenetre.open && ouvert) placer(); });

  peindreOutils();
  rafraichir();
})();
