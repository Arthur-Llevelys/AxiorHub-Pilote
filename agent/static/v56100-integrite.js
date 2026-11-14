/* AxiorHub 5.6.100 (lot C, R05) — volet « Portefeuille détaillé » de la page Dossiers chargé au dépli.
   Replié, le volet n'est ni calculé ni lu par le serveur. À l'ouverture, la route de lecture /dossiers?volet=portefeuille (droits relus
   par le serveur, aucune écriture) est demandée une fois ; seul le contenu marqué [data-c100-volet-contenu] de cette page du serveur est
   repris (scripts jamais exécutés). Échec, session expirée ou réponse inattendue : message clair, bouton « Réessayer » et lien vers la
   page du volet (repli sans script). Aucun appel au modèle. */
(function () {
  'use strict';
  if (window.axh56100Integrite) return;
  window.axh56100Integrite = true;

  function etat(hote, texte, reessayer, adresse) {
    var p = document.createElement('p');
    p.className = 'ax-muted';
    p.setAttribute('role', 'status');
    p.setAttribute('data-c100-volet-etat', '');
    p.textContent = texte + ' ';
    if (reessayer) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'ax-btn ghost';
      b.textContent = 'Réessayer';
      b.addEventListener('click', reessayer);
      p.appendChild(b);
      p.appendChild(document.createTextNode(' '));
    }
    if (adresse) {
      var a = document.createElement('a');
      a.href = adresse;
      a.textContent = 'Ouvrir le portefeuille détaillé';
      p.appendChild(a);
    }
    hote.replaceChildren(p);
  }

  function monter(pli) {
    var hote = pli.querySelector('[data-c100-volet-hote]');
    var adresse = pli.getAttribute('data-c100-volet');
    if (!hote || !adresse) return;
    var reussi = false, enCours = false;
    function charger() {
      if (!pli.open || reussi || enCours) return;
      enCours = true;
      pli.setAttribute('aria-busy', 'true');
      etat(hote, 'Chargement du portefeuille détaillé…');
      fetch(adresse, { credentials: 'same-origin', headers: { 'Accept': 'text/html' } }).then(function (r) {
        var type = r.headers.get('Content-Type') || '';
        if (!r.ok || type.indexOf('text/html') !== 0) throw new Error('reponse');
        return r.text();
      }).then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var contenu = doc.querySelector('[data-c100-volet-contenu]');
        if (!contenu) throw new Error('contenu');   // page de connexion ou d'erreur : rien n'est repris
        doc.querySelectorAll('script').forEach(function (s) { s.remove(); });
        var fragment = document.createDocumentFragment();
        Array.prototype.slice.call(contenu.childNodes).forEach(function (n) { fragment.appendChild(document.importNode(n, true)); });
        hote.replaceChildren(fragment);
        reussi = true;
      }).catch(function () {
        etat(hote, 'Portefeuille détaillé indisponible pour le moment.', function () { enCours = false; charger(); }, adresse);
      }).then(function () {
        enCours = false;
        pli.removeAttribute('aria-busy');
      });
    }
    pli.addEventListener('toggle', charger);
    if (pli.open) charger();
  }

  function demarrer() {
    document.querySelectorAll('details[data-c100-volet]').forEach(monter);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', demarrer);
  else demarrer();
})();
