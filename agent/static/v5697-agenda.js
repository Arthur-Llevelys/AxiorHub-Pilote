/* AxiorHub 5.6.97 (lot agenda, § 14 et § 14.1) — grille Jour et Semaine de /planning et éditeur compact.
   - Hauteur : la grille prend la zone utile de la fenêtre sous elle-même (mesurée), ses lignes se partagent cette hauteur ; en dessous d'un
     minimum lisible (petits écrans, zoom fort), elle garde ce minimum et la page défile, sans défilement horizontal.
   - Créneaux : clic, toucher ou Entrée ouvrent l'éditeur ; glisser (souris) ou Maj + flèches étendent la plage ; flèches pour se déplacer.
   - Éditeur : dialogue de la même page ; la clé d'idempotence est tirée à l'ouverture et réutilisée telle quelle si l'envoi est rejoué
     (délai réseau) ; une nouvelle clé n'est tirée que pour une nouvelle opération explicite. Échap ferme et rend le focus au créneau.
   - Aucun HTML n'est inséré depuis une réponse : textContent seulement ; aucun style en ligne hors CSSOM (CSP style-src 'self'). */
(function () {
  'use strict';
  if (window.__axiorhub5697agenda) return;
  window.__axiorhub5697agenda = true;
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.getAttribute('content') : ''; }
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  // ------------------------------------------------------------------ Paramètres › Connexions : Talk, Google Agenda, Speakr (aucun jeton saisi)
  $$('form[data-ag97-reglage]').forEach(function (f) {
    var nature = f.getAttribute('data-ag97-reglage'), sortie = $('.ag97-statut', f);
    function poster(corps) {
      return fetch((meta('axiorhub-prefix') || '') + '/api440/m5689/agenda/reglages/' + nature, { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: JSON.stringify(corps) })
        .then(function (r) { return r.json().then(function (d) { if (!r.ok || d.error) throw new Error(d.message || d.error || 'Demande refusée.'); return d; }); })
        .then(function (d) { sortie.textContent = d.message || 'Enregistré.'; }, function (e) { sortie.textContent = e.message; });
    }
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      poster(nature === 'speakr' ? { portee: f.getAttribute('data-portee'), url: f.elements.url.value } : { actif: f.elements.actif.checked });
    });
    var retirer = $('[data-ag97-retirer]', f);
    if (retirer) retirer.addEventListener('click', function () { f.elements.url.value = ''; poster({ portee: f.getAttribute('data-portee'), url: '' }); });
  });

  var racine = $('[data-ag97]');
  if (!racine) return;
  var donnees = {};
  try { donnees = JSON.parse(($('#ag97-donnees') || {}).textContent || '{}'); } catch (err) { donnees = {}; }
  var csrf = meta('axiorhub-csrf');
  var grille = $('[data-ag97-grille]', racine), dialogue = $('#ag97-editeur'), form = $('[data-ag97-form]');
  var statut = form ? $('[data-ag97-statut]', form) : null;
  var pas = Number(donnees.pas) || 30, ouvreur = null, envoiEnCours = false;

  function cle() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID().replace(/-/g, '');
    var a = new Uint8Array(16); window.crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function appel(route, corps) {
    var opts = corps === undefined ? { credentials: 'same-origin' } : { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) };
    return fetch(donnees.api + route, opts).then(function (r) {
      if (r.status === 401) { var x = new Error('Votre session a expiré : reconnectez-vous.'); x.status = 401; throw x; }
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; }).then(function (d) {
        if (!r.ok || (d && d.error)) { var err = new Error((d && (d.message || d.error)) || 'Demande refusée.'); err.status = r.status; err.donnees = d; throw err; }
        d.__status = r.status; return d;
      });
    });
  }
  function dire(texte, mauvais) { if (statut) { statut.textContent = texte || ''; statut.classList.toggle('ag97-mauvais', !!mauvais); } }

  // ------------------------------------------------------------------ hauteur : la grille tient dans la zone utile
  function ajuster() {
    if (!grille) return;
    var lignes = Number((grille.className.match(/ag97-rows(\d+)/) || [])[1]) || 22;
    var haut = grille.getBoundingClientRect().top + window.scrollY;
    var dispo = window.innerHeight - haut - 6;
    var minimum = 56 + lignes * 13;            // en-tête + journée entière + créneaux lisibles et cliquables
    grille.style.setProperty('height', Math.max(dispo, minimum) + 'px');
    grille.classList.toggle('ag97-serre', dispo < 56 + lignes * 20);
  }
  ajuster();
  window.addEventListener('resize', ajuster);
  $$('details', racine).forEach(function (d) { d.addEventListener('toggle', ajuster); });

  // ------------------------------------------------------------------ volets, filtres, source, préférences
  var boutonVolets = $('[data-ag97-volets]', racine), volets = $('#ag97-volets');
  function montrerVolets(ouvert) {
    if (!volets) return;
    volets.hidden = !ouvert; boutonVolets.setAttribute('aria-expanded', ouvert ? 'true' : 'false');
    racine.classList.toggle('ag97-volets-ouverts', ouvert); ajuster();
  }
  if (boutonVolets) {
    montrerVolets(racine.classList.contains('ag97-volets-ouverts'));
    boutonVolets.addEventListener('click', function () {
      var ouvert = volets.hidden; montrerVolets(ouvert);
      appel('/preferences', { volets: ouvert }).catch(function () { /* préférence seulement : l'affichage reste correct */ });
    });
  }
  $$('[data-ag97-filtre]', racine).forEach(function (c) {
    c.addEventListener('change', function () { racine.classList.toggle('ag97-sans-' + c.getAttribute('data-ag97-filtre'), !c.checked); });
  });
  var choixSource = $('[data-ag97-source]', racine);
  if (choixSource) choixSource.addEventListener('change', function () { window.location.href = choixSource.value; });
  var prefs = $('[data-ag97-prefs]', racine);
  if (prefs) prefs.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var c = {}; $$('select, input', prefs).forEach(function (f) { c[f.name] = f.value; });
    var s = $('.ag97-statut', prefs);
    appel('/preferences', c).then(function (r) { s.textContent = r.message; window.location.reload(); })
      .catch(function (e) { s.textContent = e.message; });
  });

  // ------------------------------------------------------------------ créneaux : sélection, clavier, glisser
  var cases = $$('.ag97-case:not(.ag97-jcase)', racine);
  var jours = []; cases.forEach(function (b) { if (jours.indexOf(b.getAttribute('data-jour')) < 0) jours.push(b.getAttribute('data-jour')); });
  var minutes = []; cases.forEach(function (b) { var m = Number(b.getAttribute('data-minute')); if (minutes.indexOf(m) < 0) minutes.push(m); });
  minutes.sort(function (a, b) { return a - b; });
  function caseA(j, m) { return $('.ag97-case[data-jour="' + j + '"][data-minute="' + m + '"]', racine); }
  var selection = null;   // {jour, debut, fin} en minutes
  function marquer() {
    cases.forEach(function (b) {
      var dans = selection && b.getAttribute('data-jour') === selection.jour && Number(b.getAttribute('data-minute')) >= selection.debut && Number(b.getAttribute('data-minute')) < selection.fin;
      b.classList.toggle('ag97-choisi', !!dans);
    });
  }
  var courant = cases[0] || null;
  cases.forEach(function (b, i) { b.tabIndex = i === 0 ? 0 : -1; });
  function focaliser(b) { if (!b) return; if (courant) courant.tabIndex = -1; courant = b; b.tabIndex = 0; b.focus(); }
  function hhmm(m) { return ('0' + Math.floor(m / 60)).slice(-2) + ':' + ('0' + (m % 60)).slice(-2); }
  function jourSuivant(jour) {   // arithmétique en UTC : aucune dépendance au fuseau du poste
    var p = jour.split('-').map(Number), d = new Date(Date.UTC(p[0], p[1] - 1, p[2] + 1));
    return d.toISOString().slice(0, 10);
  }
  function local(jour, m) { return m >= 1440 ? jourSuivant(jour) + 'T00:00' : jour + 'T' + hhmm(m); }
  cases.forEach(function (b) {
    b.addEventListener('keydown', function (ev) {
      var j = jours.indexOf(b.getAttribute('data-jour')), m = minutes.indexOf(Number(b.getAttribute('data-minute'))), cible = null;
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        var s = selection && selection.jour === b.getAttribute('data-jour') ? selection : { jour: b.getAttribute('data-jour'), debut: minutes[m], fin: minutes[m] + pas };
        ouvrirCreation(s, b); return;
      }
      if (ev.key === 'Escape') { selection = null; marquer(); return; }
      if (ev.key === 'ArrowDown') cible = caseA(jours[j], minutes[Math.min(m + 1, minutes.length - 1)]);
      else if (ev.key === 'ArrowUp') cible = caseA(jours[j], minutes[Math.max(m - 1, 0)]);
      else if (ev.key === 'ArrowRight') cible = caseA(jours[Math.min(j + 1, jours.length - 1)], minutes[m]);
      else if (ev.key === 'ArrowLeft') cible = caseA(jours[Math.max(j - 1, 0)], minutes[m]);
      else if (ev.key === 'Home') cible = caseA(jours[j], minutes[0]);
      else if (ev.key === 'End') cible = caseA(jours[j], minutes[minutes.length - 1]);
      if (!cible) return;
      ev.preventDefault();
      if (ev.shiftKey && (ev.key === 'ArrowDown' || ev.key === 'ArrowUp')) {   // équivalent clavier du glisser
        var ancre = selection && selection.jour === jours[j] ? selection : { jour: jours[j], debut: minutes[m], fin: minutes[m] + pas };
        var mc = Number(cible.getAttribute('data-minute'));
        selection = { jour: jours[j], debut: Math.min(ancre.debut, mc), fin: Math.max(ancre.fin, mc + pas) };
        if (ev.key === 'ArrowUp' && mc < ancre.debut) selection.debut = mc;
        marquer();
      } else if (!ev.shiftKey) { selection = null; marquer(); }
      focaliser(cible);
    });
  });
  var glisse = null;
  cases.forEach(function (b) {
    b.addEventListener('pointerdown', function (ev) {
      if (ev.pointerType !== 'mouse' || ev.button !== 0) return;
      glisse = { jour: b.getAttribute('data-jour'), depart: Number(b.getAttribute('data-minute')), bouge: false };
      selection = { jour: glisse.jour, debut: glisse.depart, fin: glisse.depart + pas }; marquer();
    });
    b.addEventListener('pointerenter', function () {
      if (!glisse || b.getAttribute('data-jour') !== glisse.jour) return;
      var m = Number(b.getAttribute('data-minute'));
      glisse.bouge = glisse.bouge || m !== glisse.depart;
      selection = { jour: glisse.jour, debut: Math.min(glisse.depart, m), fin: Math.max(glisse.depart, m) + pas }; marquer();
    });
    b.addEventListener('click', function () {
      if (glisse) return;   // la fin du glisser ouvre déjà l'éditeur
      focaliser(b);
      var m = Number(b.getAttribute('data-minute'));
      ouvrirCreation({ jour: b.getAttribute('data-jour'), debut: m, fin: m + pas }, b);
    });
  });
  document.addEventListener('pointerup', function (ev) {
    if (!glisse) return;
    var s = selection, b = ev.target && ev.target.closest ? ev.target.closest('.ag97-case') : null;
    glisse = null;
    if (s) ouvrirCreation(s, b || courant);
  });
  $$('.ag97-jcase', racine).forEach(function (b) {
    b.addEventListener('click', function () { ouvrirCreation({ jour: b.getAttribute('data-jour'), journee: true }, b); });
  });
  var nouveau = $('[data-ag97-nouveau]', racine);
  if (nouveau) nouveau.addEventListener('click', function () {
    var maintenant = new Date(), jour = jours.indexOf(donnees.aujourdhui) >= 0 ? donnees.aujourdhui : jours[0];
    var m = Math.ceil((maintenant.getHours() * 60 + maintenant.getMinutes()) / pas) * pas;
    if (jour !== donnees.aujourdhui || m < donnees.heure_debut * 60 || m >= donnees.heure_fin * 60) m = donnees.heure_debut * 60;
    ouvrirCreation({ jour: jour, debut: m, fin: m + 30 }, nouveau, true);
  });
  $$('[data-ag97-programmer]', racine).forEach(function (b) {
    b.addEventListener('click', function () {
      var m = donnees.heure_debut * 60, jour = jours.indexOf(donnees.aujourdhui) >= 0 ? donnees.aujourdhui : jours[0];
      ouvrirCreation({ jour: jour, debut: m, fin: m + 60 }, b, true);
      form.elements.titre.value = b.getAttribute('data-titre') || '';
      form.setAttribute('data-tache-existante', b.getAttribute('data-ag97-programmer'));
      choisirNature('tache');
      dire('Choisissez le créneau : seule la tâche est reliée au bloc ; son échéance ne change pas.');
    });
  });

  // ------------------------------------------------------------------ éditeur compact
  function objet() { var r = $('input[name=objet]:checked', form); return r ? r.value : 'evenement'; }
  function majSections() {
    var o = objet(), nature = form.elements.nature.value, journee = form.elements.journee_entiere.checked;
    var reserver = form.elements.reserver.checked;
    $$('[data-ag97-pour="evenement"]', form).forEach(function (n) { n.hidden = o === 'tache' && !reserver; });
    $$('[data-ag97-pour="tache"]', form).forEach(function (n) { n.hidden = o === 'evenement'; });
    $('[data-ag97-reserver]', form).hidden = o !== 'tache';
    $('[data-ag97-visio]', form).hidden = o === 'tache' || nature !== 'rendez_vous';
    form.elements.lien.hidden = !(($('input[name=visio]:checked', form) || {}).value === 'lien');
    ['debut', 'fin'].forEach(function (n) {
      var f = form.elements[n], v = f.value;
      if (journee && f.type !== 'date') { f.type = 'date'; f.value = v.slice(0, 10); }
      if (!journee && f.type !== 'datetime-local') { f.type = 'datetime-local'; f.value = v ? v.slice(0, 10) + 'T09:00' : ''; }
    });
  }
  function choisirNature(n) {
    if (n === 'tache') { $('input[name=objet][value=tache]', form).checked = true; form.elements.reserver.checked = true; }
    majSections();
  }
  function reinitialiser(titre) {
    form.reset(); form.removeAttribute('data-tache-existante'); form.removeAttribute('data-mode');
    form.elements.cle.value = cle(); form.elements.id.value = ''; form.elements.version.value = '';
    $('#ag97-editeur-titre').textContent = titre || 'Nouvel événement';
    var choix = $('[data-ag97-choix]', form); choix.hidden = true; choix.textContent = '';
    var detail = $('[data-ag97-detail]', form); detail.hidden = true; detail.textContent = '';
    $$('fieldset.ag97-objet, [data-ag97-enregistrer]', form).forEach(function (n) { n.hidden = false; });
    $$('input, select, textarea', form).forEach(function (f) { f.disabled = f.hasAttribute('data-ag97-toujours-inactif'); });   // Talk indisponible : reste inactif
    if (donnees.source === 'online') {
      var opt = $$('option', form.elements.calendrier).filter(function (o) { return o.value !== 'local' && !o.disabled; })[0];
      if (opt) form.elements.calendrier.value = opt.value;
    }
    dire('');
  }
  function ouvrir(depuis) {
    ouvreur = depuis || document.activeElement;
    if (dialogue.showModal) { if (!dialogue.open) dialogue.showModal(); } else dialogue.setAttribute('open', '');
    majSections();
    window.setTimeout(function () { form.elements.titre.focus(); }, 0);
  }
  function fermer() { if (dialogue.open) { if (dialogue.close) dialogue.close(); else dialogue.removeAttribute('open'); } }
  function ouvrirCreation(s, depuis, nouveau) {
    if (!form) return;
    reinitialiser('Nouvel événement');
    if (s.journee) {
      form.elements.journee_entiere.checked = true; majSections();
      form.elements.debut.value = s.jour; form.elements.fin.value = jourSuivant(s.jour);
    } else {
      form.elements.debut.value = local(s.jour, s.debut); form.elements.fin.value = local(s.jour, s.fin);
    }
    form.elements.reserver.checked = !nouveau;
    ouvrir(depuis);
  }
  if (dialogue) {
    dialogue.addEventListener('close', function () {
      selection = null; marquer();
      if (ouvreur && document.contains(ouvreur)) ouvreur.focus();
    });
    dialogue.addEventListener('cancel', function () { /* Échap : fermeture native, focus rendu à l'ouvreur (close) */ });
  }
  if (form) {
    $$('input[name=objet], select[name=nature], input[name=journee_entiere], input[name=visio], input[name=reserver]', form).forEach(function (f) {
      f.addEventListener('change', majSections);
    });
    $('[data-ag97-annuler]', form).addEventListener('click', fermer);
    form.addEventListener('submit', function (ev) { ev.preventDefault(); envoyer(); });
  }

  function corps() {
    var f = form.elements, o = objet(), journee = f.journee_entiere.checked, c = { cle: f.cle.value, objet: o, titre: f.titre.value.trim(),
      nature: f.nature.value, calendrier: f.calendrier.value, lieu: f.lieu.value, dossier: f.dossier.value, notes: f.notes.value, visibilite: f.visibilite.value,
      visio: ($('input[name=visio]:checked', form) || {}).value || 'sans', lien: f.lien.value, speakr: !!(f.speakr && f.speakr.checked), zone: donnees.zone };
    if (journee) { c.journee_entiere = true; c.debut = f.debut.value.slice(0, 10); c.jour_fin = f.fin.value.slice(0, 10); }
    else { c.debut = f.debut.value; c.fin = f.fin.value; }
    var choixD = $('input[name=decalage_debut]:checked', form), choixF = $('input[name=decalage_fin]:checked', form);
    if (choixD) c.decalage_debut = choixD.value;
    if (choixF) c.decalage_fin = choixF.value;
    if (o !== 'evenement') c.tache = { titre: c.titre, responsable: f.responsable.value, priorite: f.priorite.value, echeance: f.echeance.value, reserver: f.reserver.checked };
    if (form.getAttribute('data-tache-existante')) c.tache_existante = form.getAttribute('data-tache-existante');
    return c;
  }
  function horaire(err) {   // B05 : heure sautée (proposition) ou heure répétée (deux décalages à choisir)
    var d = err.donnees && err.donnees.detail, choix = $('[data-ag97-choix]', form), champ = null;
    if (!d) return false;
    choix.textContent = '';
    var saisi = d.saisi || '';
    ['debut', 'fin'].forEach(function (n) { if (form.elements[n].value === saisi) champ = n; });
    champ = champ || 'debut';
    var p = document.createElement('p'); p.textContent = err.message; choix.appendChild(p);
    if (err.donnees.error === 'horaire_inexistant' && d.proposition) {
      var b = document.createElement('button'); b.type = 'button'; b.className = 'ax-btn ghost';
      b.textContent = 'Utiliser ' + d.proposition.slice(11, 16);
      b.addEventListener('click', function () { form.elements[champ].value = d.proposition; choix.hidden = true; choix.textContent = ''; form.elements[champ].focus(); });
      choix.appendChild(b);
    } else if (d.choix) {
      d.choix.forEach(function (x, i) {
        var l = document.createElement('label'), r = document.createElement('input');
        r.type = 'radio'; r.name = 'decalage_' + champ; r.value = x.decalage; r.checked = i === 0;
        l.appendChild(r); l.appendChild(document.createTextNode(' ' + saisi.slice(11, 16) + ' (UTC' + x.decalage + ')' + (i === 0 ? ' : avant le changement d’heure' : ' : après le changement d’heure')));
        choix.appendChild(l);
      });
    }
    choix.hidden = false;
    var premier = $('input, button', choix); if (premier) premier.focus();
    return true;
  }
  function envoyer() {
    if (envoiEnCours) return;           // double clic : un seul envoi à la fois (et la clé protège le rejeu)
    if (!form.elements.titre.value.trim()) { dire('Indiquez un titre.', true); form.elements.titre.focus(); return; }
    var modif = form.getAttribute('data-mode') === 'modifier';
    var c = corps(), route = '/evenements';
    if (modif) {
      route = '/evenements/modifier';
      c = { cle: c.cle, id: form.elements.id.value, version: form.elements.version.value, titre: c.titre, lieu: c.lieu, notes: c.notes, dossier: c.dossier,
            debut: c.debut, fin: c.fin, journee_entiere: c.journee_entiere, jour_fin: c.jour_fin, zone: c.zone, decalage_debut: c.decalage_debut, decalage_fin: c.decalage_fin };
    }
    var bouton = $('[data-ag97-enregistrer]', form);
    envoiEnCours = true; bouton.disabled = true; dire('Enregistrement…');
    appel(route, c).then(function (r) {
      var texte = r.message || 'Enregistré.';
      if (r.salon) texte += ' Visio : ' + r.salon.libelle + (r.salon.motif ? ' (' + r.salon.motif + ')' : '') + '.';
      if (r.synchronisation) texte += ' Calendrier en ligne : ' + r.synchronisation.libelle + '.';
      dire(texte, r.__status === 202 && !!(r.synchronisation && r.synchronisation.etat !== 'confirme'));
      try { window.sessionStorage.setItem('ag97-focus', r.evenement_id || ''); } catch (err) { /* sans stockage : pas de mise en évidence */ }
      window.setTimeout(function () { window.location.reload(); }, r.__status === 202 ? 1800 : 500);
    }).catch(function (err) {
      if (err.status === 422 && horaire(err)) { dire('Corrigez l’horaire puis enregistrez.', true); return; }
      dire(err.message, true);   // la saisie est conservée ; même clé au prochain essai
      if (err.donnees && err.donnees.error === 'cle_idempotence_deja_utilisee') {
        var b = document.createElement('button'); b.type = 'button'; b.className = 'ax-btn ghost'; b.textContent = 'Enregistrer comme nouvelle opération';
        b.addEventListener('click', function () { form.elements.cle.value = cle(); b.remove(); envoyer(); });
        statut.appendChild(document.createTextNode(' ')); statut.appendChild(b);
      }
    }).then(function () { envoiEnCours = false; bouton.disabled = false; });
  }

  // ------------------------------------------------------------------ événements existants : détail, édition, Talk, Speakr
  function lienExterne(texte, href) {
    var a = document.createElement('a'); a.className = 'ax-btn ghost'; a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.referrerPolicy = 'no-referrer'; a.textContent = texte; return a;
  }
  function ligne(parent, texte) { var p = document.createElement('p'); p.textContent = texte; parent.appendChild(p); return p; }
  function detail(ev, d) {
    var zone = $('[data-ag97-detail]', form); zone.textContent = ''; zone.hidden = false;
    ligne(zone, 'Source : ' + (d.source_label || 'Agenda') + (d.modifiable ? '' : ' · lecture seule ici') + (d.serie ? ' · occurrence d’une série' : ''));
    if (d.salon) {
      var s = ligne(zone, 'Visio Talk : ' + d.salon.libelle + (d.salon.motif ? ' — ' + d.salon.motif : '') + ' · aucune invitation envoyée.');
      if (d.salon.lien) {
        zone.appendChild(lienExterne('Rejoindre la visio', d.salon.lien));
        var copie = document.createElement('button'); copie.type = 'button'; copie.className = 'ax-btn ghost'; copie.textContent = 'Copier le lien';
        copie.addEventListener('click', function () {
          if (navigator.clipboard) navigator.clipboard.writeText(d.salon.lien).then(function () { dire('Lien copié.'); }, function () { dire('Copie refusée par le navigateur.', true); });
        });
        zone.appendChild(copie);
        var acces = document.createElement('div'); acces.className = 'ag97-acces';
        var champ = document.createElement('input'); champ.placeholder = 'Comptes Nextcloud du cabinet (séparés par des virgules)'; champ.setAttribute('aria-label', 'Comptes à ajouter au salon');
        var conf = document.createElement('label'), cc = document.createElement('input'); cc.type = 'checkbox';
        conf.appendChild(cc); conf.appendChild(document.createTextNode(' Je donne l’accès au salon (Talk peut les notifier)'));
        var ajout = document.createElement('button'); ajout.type = 'button'; ajout.className = 'ax-btn ghost'; ajout.textContent = 'Donner l’accès';
        ajout.addEventListener('click', function () {
          appel('/salon/participants', { evenement: d.id, comptes: champ.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean), confirme: cc.checked })
            .then(function (r) { dire(r.message); }).catch(function (e) { dire(e.message, true); });
        });
        acces.appendChild(champ); acces.appendChild(conf); acces.appendChild(ajout); zone.appendChild(acces);
        if (d.salon.participants && d.salon.participants.length) ligne(zone, 'Accès donné à : ' + d.salon.participants.join(', '));
      } else if (d.salon.etat !== 'pret') {
        var reprise = document.createElement('button'); reprise.type = 'button'; reprise.className = 'ax-btn ghost'; reprise.textContent = 'Vérifier et reprendre';
        reprise.addEventListener('click', function () { appel('/salon/relancer', { evenement: d.id }).then(function (r) { dire('Visio : ' + r.libelle); }).catch(function (e) { dire(e.message, true); }); });
        zone.appendChild(reprise);
      }
      s.className = 'ag97-etat-' + d.salon.etat;
    } else if (d.lien_fourni) {
      ligne(zone, 'Visio : lien fourni (non vérifié par AxiorHub).'); zone.appendChild(lienExterne('Rejoindre la visio', d.lien_fourni));
    }
    if (d.speakr_url) zone.appendChild(lienExterne('Ouvrir Speakr', d.speakr_url));
    if (d.nouvelle_demande && d.nouvelle_demande.href) {   // § 16 : même formulaire de demande que Courriels et Dossiers
      var nd = document.createElement('a'); nd.className = 'ax-btn ghost'; nd.href = d.nouvelle_demande.href; nd.textContent = d.nouvelle_demande.libelle;
      zone.appendChild(nd);
    }
    if (d.tache) ligne(zone, 'Tâche liée : ' + d.tache.title + (d.tache.due ? ' · échéance ' + d.tache.due : ' · sans échéance') + ' (déplacer le bloc ne change pas l’échéance).');
    if (d.recu) ligne(zone, 'Calendrier en ligne : ' + d.recu.libelle + (d.recu.erreur ? ' — ' + d.recu.erreur : ''));
  }
  function ouvrirEvenement(b) {
    var id = b.getAttribute('data-ag97-ev');
    appel('/evenement?id=' + encodeURIComponent(id)).then(function (d) {
      reinitialiser(d.modifiable ? 'Modifier l’événement' : 'Événement');
      var f = form.elements;
      $('fieldset.ag97-objet', form).hidden = true;
      f.titre.value = d.title || (d.recu ? '' : '');
      f.lieu.value = d.location || ''; f.notes.value = d.description || '';
      if (d.matter) f.dossier.value = d.matter;
      if (d.all_day) { f.journee_entiere.checked = true; majSections(); f.debut.value = (d.debut_local || '').slice(0, 10); f.fin.value = (d.fin_local || '').slice(0, 10); }
      else { f.debut.value = d.debut_local || ''; f.fin.value = d.fin_local || ''; }
      if (d.nature && f.nature.querySelector('option[value="' + d.nature + '"]')) f.nature.value = d.nature;
      if (d.modifiable) {
        form.setAttribute('data-mode', 'modifier'); f.id.value = d.id; f.version.value = d.version || '';
        $$('[data-ag97-visio], select[name=calendrier], select[name=nature]', form).forEach(function (n) { n.disabled = true; });
      } else {
        $$('input, select, textarea', form).forEach(function (n) { if (n.type !== 'hidden') n.disabled = true; });
        $('[data-ag97-enregistrer]', form).hidden = true;
      }
      ouvrir(b);
      detail(b, d);
      $('[data-ag97-visio]', form).hidden = true;
    }).catch(function (e) { window.alert(e.message); });
  }
  $$('[data-ag97-ev]', racine).forEach(function (b) { b.addEventListener('click', function (ev) { ev.stopPropagation(); ouvrirEvenement(b); }); });

  racine.setAttribute('data-ag97-pret', '1');   // grille prête : les parcours attendent ce repère avant de jouer le clavier

  // après un enregistrement : l'objet est mis en évidence une fois
  try {
    var focus = window.sessionStorage.getItem('ag97-focus');
    if (focus) {
      window.sessionStorage.removeItem('ag97-focus');
      var cible = $('[data-ag97-ev="' + focus.replace(/[^A-Za-z0-9:_-]/g, '') + '"]', racine);
      if (cible) { cible.classList.add('ag97-nouveau-ev'); cible.focus(); }
    }
  } catch (err) { /* stockage indisponible */ }
})();
