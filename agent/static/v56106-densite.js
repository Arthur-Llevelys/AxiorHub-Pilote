/* AxiorHub 5.6.106 (lot D) — densité d'affichage et logo du cabinet.
   - Titre de page et onglets sur une même bande : en densité compacte, la barre d'onglets qui suit immédiatement le titre de page
     (.page-title + nav.ws-tabs) est rangée dans la bande du titre (ordre de lecture : titre, onglets, Actualiser). Le repli sur
     plusieurs lignes est assuré par la feuille v56106-densite.css quand la place manque.
   - Paramètres › Démarrage du cabinet : dépôt, remplacement et retrait du logo (POST JSON avec jeton CSRF et clé d'idempotence ;
     le serveur contrôle le type par la signature des octets, la taille et les dimensions). Tout texte passe par textContent. */
(function () {
  'use strict';
  var doc = document;
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }

  /* ------------------------------------------------------------ titre et onglets sur une bande */
  if (doc.body && doc.body.dataset.densite === 'compacte') {
    Array.prototype.forEach.call(doc.querySelectorAll('#ws-main .page-title'), function (titre) {
      var suivant = titre.nextElementSibling;
      if (!suivant || !suivant.matches('nav.ws-tabs') || titre.querySelector('nav')) return;
      var tete = titre.firstElementChild;
      titre.insertBefore(suivant, tete ? tete.nextSibling : titre.firstChild);
      titre.classList.add('ax106-bande');
    });
  }

  /* ------------------------------------------------------------ logo du cabinet */
  var zone = doc.getElementById('d106-logo');
  if (!zone) return;
  var statut = zone.querySelector('[data-d106-statut]'), fichier = zone.querySelector('[data-d106-fichier]'), retirer = zone.querySelector('[data-d106-retirer]');
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  function cle() {
    var a = new Uint8Array(16); window.crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function dire(texte, erreur) { if (!statut) return; statut.textContent = texte; statut.dataset.etat = erreur ? 'erreur' : ''; }
  function envoyer(route, corps, occupe) {
    corps.cle = cle();
    corps.revision = zone.dataset.d106Revision || '';
    if (occupe) occupe.setAttribute('aria-busy', 'true');
    return window.fetch(prefix + '/api440/m56106/' + route, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (v) {
        if (!r.ok || v.error) throw new Error(v.message || ('Erreur ' + r.status));
        return v;
      });
    }).then(function (v) {
      dire(v.message || 'Enregistré.', false);
      window.setTimeout(function () { window.location.reload(); }, 600);
    }).catch(function (ex) { dire(ex.message, true); })
      .then(function () { if (occupe) occupe.removeAttribute('aria-busy'); });
  }
  if (fichier) fichier.addEventListener('change', function () {
    var f = fichier.files && fichier.files[0];
    if (!f) return;
    if (f.size > 1000000) { dire('Le logo dépasse 1 Mo : réduisez sa taille, puis recommencez.', true); fichier.value = ''; return; }
    var lecteur = new FileReader();
    lecteur.onload = function () { dire('Envoi du logo…', false); envoyer('identite/logo', { donnees: String(lecteur.result || '') }, fichier.closest('label')); fichier.value = ''; };
    lecteur.onerror = function () { dire('Lecture du fichier impossible.', true); };
    lecteur.readAsDataURL(f);
  });
  if (retirer) retirer.addEventListener('click', function () {
    if (!window.confirm('Retirer le logo du cabinet ? La pastille aux initiales le remplacera.')) return;
    envoyer('identite/logo/retirer', {}, retirer);
  });
})();
