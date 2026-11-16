/* 5.6.103 (lot M) : section « Compte Microsoft » de Paramètres › Connexions — connexion par l'application intégrée.
   Poste : ouvre la page Microsoft (PKCE) ; serveur : affiche le code d'appareil et suit la connexion au rythme demandé par Microsoft.
   Aucun jeton ni code d'autorisation ne passe par cette page. */
(function () {
  'use strict';
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute('content') : ''; };
  var prefix = meta('axiorhub-prefix') || '', csrf = meta('axiorhub-csrf');
  var section = document.getElementById('microsoft56103');
  if (!section || section.getAttribute('data-m103-pret')) { return; }
  section.setAttribute('data-m103-pret', '1');
  var retour = section.querySelector('.m103-retour');

  function dire(texte) { if (retour) { retour.textContent = texte; } }

  function post(route, body) {
    return fetch(prefix + '/api440/m56103/microsoft/' + route, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) { throw new Error(j.message || j.error || ('HTTP ' + r.status)); } return j; }); });
  }

  function afficherCode(out) {
    if (!retour) { return; }
    retour.textContent = '';
    var p = document.createElement('p');
    p.className = 'm103-code';
    p.appendChild(document.createTextNode('Code à saisir : '));
    var fort = document.createElement('strong');
    fort.textContent = out.code;
    p.appendChild(fort);
    p.appendChild(document.createTextNode(' sur '));
    var a = document.createElement('a');
    a.href = out.adresse; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = out.adresse;
    p.appendChild(a);
    retour.appendChild(p);
    var attente = document.createElement('p');
    attente.className = 'ax-muted m103-attente';
    attente.textContent = 'En attente de votre saisie sur la page Microsoft…';
    retour.appendChild(attente);
  }

  /* 5.6.106 (audit M2) : un seul suivi à la fois. Une nouvelle liaison ou une déconnexion arrête la minuterie en cours, et la réponse
     tardive d'un code remplacé est ignorée (le serveur, lui, ne l'active de toute façon plus). */
  var minuterie = null, fluxCourant = null;

  function arreter() {
    if (minuterie) { clearTimeout(minuterie); minuterie = null; }
    fluxCourant = null;
  }

  function suivre(flux, intervalle) {
    var delai = Math.max(5, parseInt(intervalle, 10) || 5) * 1000;
    if (minuterie) { clearTimeout(minuterie); }
    fluxCourant = flux;
    minuterie = setTimeout(function () {
      minuterie = null;
      if (flux !== fluxCourant) { return; }
      post('appareil/sonder', { flux: flux }).then(function (out) {
        if (flux !== fluxCourant) { return; }
        if (out.etat === 'en_attente') {
          var a = section.querySelector('.m103-attente');
          if (a) { a.textContent = out.message || 'En attente…'; }
          suivre(flux, out.intervalle || intervalle);
        } else if (out.etat === 'accepte') {
          dire(out.message || 'Compte Microsoft relié.');
          setTimeout(function () { window.location.reload(); }, 1200);
        } else {
          fluxCourant = null;
          dire(out.message || 'Connexion non aboutie.');
        }
      }, function (err) { if (flux === fluxCourant) { dire(err.message); } });
    }, delai);
  }

  var form = section.querySelector('form[data-m103="connexion"]');
  if (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var caps = Array.prototype.map.call(form.querySelectorAll('input[name="capacites"]:checked'), function (i) { return i.value; });
      if (!caps.length) { dire('Cochez au moins une autorisation.'); return; }
      arreter();                                       // 5.6.106 (M2) : la liaison précédente n'est plus suivie
      dire('Préparation de la connexion…');
      // 5.6.106 (M3) : SharePoint n'est demandé que sur choix explicite (case de l'option cochée par la personne)
      post('connexion', { capacites: caps, sharepoint_explicite: caps.indexOf('sharepoint') >= 0 }).then(function (out) {
        if (out.mode === 'pkce' && out.url) {
          dire('La page Microsoft s’ouvre dans un nouvel onglet. Revenez ici une fois la connexion terminée, puis rechargez la page.');
          window.open(out.url, '_blank', 'noopener');
        } else if (out.mode === 'appareil') {
          afficherCode(out);
          suivre(out.flux, out.intervalle);
        }
      }, function (err) { dire(err.message); });
    });
  }

  var deco = section.querySelector('[data-m103="deconnecter"]');
  if (deco) {
    deco.addEventListener('click', function () {
      arreter();
      post('deconnecter', {}).then(function (out) { dire(out.message || 'Déconnecté.'); setTimeout(function () { window.location.reload(); }, 2500); },
        function (err) { dire(err.message); });
    });
  }

  var enCours = section.querySelector('[data-m103-flux]');
  if (enCours) { suivre(enCours.getAttribute('data-m103-flux'), enCours.getAttribute('data-m103-intervalle')); }
}());
