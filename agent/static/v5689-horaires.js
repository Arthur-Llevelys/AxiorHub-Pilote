/* AxiorHub 5.6.89 — Paramètres › Horaires de fonctionnement : édition des plages, raccourci, aperçu serveur avant enregistrement.
   Aucun HTML venant du serveur n'est injecté ; le modèle d'une plage est dans la page. La logique sans page est exportée pour les essais. */
(function (root) {
  'use strict';
  var HEURE = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/, HEURE_FIN = /^(?:(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00)$/;
  var CHOIX = {tous: [1, 2, 3, 4, 5, 6, 7], ouvres: [1, 2, 3, 4, 5]};

  function minutes(texte) { return parseInt(texte.slice(0, 2), 10) * 60 + parseInt(texte.slice(3, 5), 10); }
  function surLendemain(debut, fin) {
    return HEURE.test(debut) && HEURE_FIN.test(fin) && (fin === '24:00' || minutes(fin) <= minutes(debut));
  }
  function erreurPlage(p) {
    if (!p.days.length) { return 'Choisissez au moins un jour.'; }
    if (p.kind === 'full_day') { return ''; }
    if (!HEURE.test(p.start)) { return 'Début invalide : utilisez le format 24 h, par exemple 18:00.'; }
    if (!HEURE_FIN.test(p.end)) { return 'Fin invalide : utilisez le format 24 h, par exemple 07:00.'; }
    if (p.start === p.end || (p.start === '00:00' && p.end === '24:00')) { return 'Début et fin identiques : choisissez « Journée entière » pour couvrir toute la journée.'; }
    return '';
  }
  function raccourci() {   // 18 h – minuit puis minuit – 7 h : une seule pause, sans reprise à minuit
    return [{id: 'soir', days: CHOIX.tous.slice(), kind: 'period', start: '18:00', end: '00:00'},
            {id: 'nuit', days: CHOIX.tous.slice(), kind: 'period', start: '00:00', end: '07:00'}];
  }
  var logique = {surLendemain: surLendemain, erreurPlage: erreurPlage, raccourci: raccourci, CHOIX: CHOIX};
  if (typeof module !== 'undefined' && module.exports) { module.exports = logique; return; }

  var document = root.document;
  var section = document.getElementById('h89-horaires'), formulaire = document.getElementById('h89-formulaire');
  if (!section || !formulaire || !document.getElementById('h89-modele')) { return; }
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var plages = document.getElementById('h89-plages'), modele = document.getElementById('h89-modele');
  var apercu = document.getElementById('h89-apercu'), erreur = document.getElementById('h89-erreur');
  var confirmation = false;

  function appel(route, corps) {
    return fetch(prefix + '/api440/m5689/' + route, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(corps)})
      .then(function (r) { return r.json().then(function (v) { if (!r.ok) { var x = new Error(v.message || v.error); x.code = v.error; throw x; } return v; }); });
  }
  function joursDe(ligne) {
    var choix = ligne.querySelector('[data-choix]').value;
    if (CHOIX[choix]) { return CHOIX[choix].slice(); }
    return Array.prototype.filter.call(ligne.querySelectorAll('[data-jour]'), function (c) { return c.checked; })
      .map(function (c) { return parseInt(c.getAttribute('data-jour'), 10); });
  }
  function lire(ligne) {
    var genre = ligne.querySelector('[data-genre]').value, p = {id: ligne.getAttribute('data-id') || '', days: joursDe(ligne), kind: genre};
    if (genre === 'period') { p.start = ligne.querySelector('[data-debut]').value.trim(); p.end = ligne.querySelector('[data-fin]').value.trim(); }
    return p;
  }
  function rafraichir(ligne) {
    var p = lire(ligne), texte = erreurPlage(p), bloc = ligne.querySelector('[data-erreur]');
    ligne.querySelector('[data-jours]').hidden = ligne.querySelector('[data-choix]').value !== 'perso';
    ligne.querySelector('[data-heures]').hidden = p.kind === 'full_day';
    ligne.querySelector('[data-lendemain]').hidden = p.kind === 'full_day' || !surLendemain(p.start, p.end);
    bloc.textContent = texte;
    bloc.hidden = !texte;
    ['[data-debut]', '[data-fin]'].forEach(function (s) { ligne.querySelector(s).setAttribute('aria-invalid', texte ? 'true' : 'false'); });
    return !texte;
  }
  function numeroter() {
    var lignes = plages.querySelectorAll('[data-plage]');
    Array.prototype.forEach.call(lignes, function (l, i) {
      l.querySelector('[data-numero]').textContent = String(i + 1);
      var b = l.querySelector('[data-supprimer]');
      if (b) { b.setAttribute('aria-label', 'Supprimer la plage ' + (i + 1)); }
    });
    var vide = document.getElementById('h89-vide');
    if (vide) { vide.hidden = lignes.length > 0; }
  }
  function ajouter(p) {
    var ligne = modele.content.firstElementChild.cloneNode(true);
    ligne.setAttribute('data-id', p.id);
    if (p.days) {
      var cle = p.days.length === 7 ? 'tous' : (p.days.join() === CHOIX.ouvres.join() ? 'ouvres' : 'perso');
      ligne.querySelector('[data-choix]').value = cle;
      Array.prototype.forEach.call(ligne.querySelectorAll('[data-jour]'), function (c) { c.checked = p.days.indexOf(parseInt(c.getAttribute('data-jour'), 10)) >= 0; });
    }
    if (p.start) { ligne.querySelector('[data-debut]').value = p.start; ligne.querySelector('[data-fin]').value = p.end; }
    plages.appendChild(ligne);
    rafraichir(ligne);
    numeroter();
    return ligne;
  }
  function corps() {
    return {revision: parseInt(section.getAttribute('data-revision'), 10), enabled: document.getElementById('h89-actif').checked,
            timezone: document.getElementById('h89-fuseau').value.trim(), windows: Array.prototype.map.call(plages.querySelectorAll('[data-plage]'), lire)};
  }
  function modifie() {
    confirmation = false;
    apercu.textContent = '';
    erreur.hidden = true;
  }
  function montrerErreur(texte) { erreur.textContent = texte; erreur.hidden = false; }

  formulaire.addEventListener('input', function (ev) {
    var ligne = ev.target.closest('[data-plage]');
    if (ligne) { rafraichir(ligne); }
    modifie();
  });
  formulaire.addEventListener('change', function (ev) {
    var ligne = ev.target.closest('[data-plage]');
    if (ligne) { rafraichir(ligne); }
    modifie();
  });
  formulaire.addEventListener('click', function (ev) {
    var retrait = ev.target.closest('[data-supprimer]');
    if (retrait) {
      var ligne = retrait.closest('[data-plage]'), suivante = ligne.nextElementSibling || ligne.previousElementSibling;
      ligne.remove();
      numeroter();
      modifie();
      var cible = suivante && suivante.querySelector('[data-supprimer]');
      (cible || document.getElementById('h89-ajouter')).focus();   // le clavier reste au même endroit de la liste
      return;
    }
    if (ev.target.id === 'h89-ajouter') {
      ajouter({id: ''}).querySelector('[data-choix]').focus();
      modifie();
    } else if (ev.target.id === 'h89-raccourci') {
      Array.prototype.slice.call(plages.querySelectorAll('[data-plage]')).forEach(function (l) { l.remove(); });
      raccourci().forEach(ajouter);
      document.getElementById('h89-actif').checked = true;
      modifie();
      apercu.textContent = 'Pause de 18 h à 7 h le lendemain, tous les jours : vérifiez puis enregistrez. Rien n’est appliqué avant l’enregistrement.';
    }
  });
  formulaire.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var valides = Array.prototype.map.call(plages.querySelectorAll('[data-plage]'), rafraichir).every(Boolean);
    if (!valides) {
      montrerErreur('Corrigez les plages signalées avant d’enregistrer.');
      return;
    }
    var bouton = document.getElementById('h89-enregistrer'), donnees = corps();
    bouton.disabled = true;
    erreur.hidden = true;
    var suite = confirmation ? Promise.resolve({pause_immediate: false}) : appel('horaires/apercu', donnees);
    suite.then(function (a) {
      if (a.pause_immediate) {   // annoncé avant validation : un second clic confirme
        confirmation = true;
        apercu.textContent = a.message + ' Cliquez de nouveau sur Enregistrer pour confirmer.';
        return null;
      }
      donnees.confirmer_pause_immediate = confirmation;
      return appel('horaires', donnees).then(function (etat) {
        section.setAttribute('data-revision', String(etat.revision));
        document.getElementById('h89-synthese').firstElementChild.textContent = etat.synthese;
        apercu.textContent = etat.message;
        confirmation = false;
        setTimeout(function () { root.location.reload(); }, 1500);
      });
    }).catch(function (e) { montrerErreur(e.message); }).then(function () { bouton.disabled = false; });
  });
}(typeof window !== 'undefined' ? window : this));
