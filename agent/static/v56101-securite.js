/* AxiorHub 5.6.101 (lot S) — page « Sécurité du serveur » : copier les commandes, « Marquer comme connu » (motif obligatoire),
   fiche d'incident, demande d'activation ou de désactivation de la veille. Aucune commande n'est exécutée : tout passe par
   des routes POST de l'interface (jeton CSRF), qui n'écrivent que dans la base d'AxiorHub ou déposent une demande. */
(function () {
  'use strict';
  function meta(nom) { var m = document.querySelector('meta[name="' + nom + '"]'); return m ? m.getAttribute('content') || '' : ''; }
  function idem() {
    var a = new Uint8Array(12); (window.crypto || window.msCrypto).getRandomValues(a);
    return 's101-' + Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function envoyer(url, donnees) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: JSON.stringify(donnees) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, statut: r.status, j: j }; }); });
  }
  function dire(zone, texte) { if (zone) { zone.textContent = texte; } }

  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-s101-copier]');
    if (!b) { return; }
    var pre = document.getElementById(b.getAttribute('data-s101-copier'));
    if (!pre) { return; }
    var texte = pre.textContent;
    var fini = function () { b.textContent = 'Copié'; setTimeout(function () { b.textContent = 'Copier'; }, 2000); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texte).then(fini, function () { choisir(pre); });
    } else { choisir(pre); }
  });
  function choisir(pre) {   /* sans presse-papiers (page non sécurisée) : le texte est sélectionné, Ctrl+C suffit */
    var r = document.createRange(); r.selectNodeContents(pre);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); pre.focus();
  }

  document.addEventListener('submit', function (ev) {
    var f = ev.target;
    if (f.matches('[data-s101-connu]')) {
      ev.preventDefault();
      var zone = f.querySelector('.s101-statut'), motif = f.querySelector('textarea').value.trim();
      if (motif.length < 3) { dire(zone, 'Indiquez le motif : il est obligatoire et inscrit au journal d’audit.'); f.querySelector('textarea').focus(); return; }
      var bouton = f.querySelector('button'); bouton.disabled = true;
      envoyer(f.getAttribute('data-url'), { cle: f.getAttribute('data-cle'), empreinte: f.getAttribute('data-empreinte'), motif: motif, idem: idem() })
        .then(function (r) {
          bouton.disabled = false;
          dire(zone, (r.j && r.j.message) || (r.ok ? 'Enregistré.' : 'Refusé.'));
          if (r.ok) { var art = f.closest('article'); if (art) { art.classList.add('s101-masque'); } }
        }, function () { bouton.disabled = false; dire(zone, 'Serveur injoignable : rien n’a été enregistré.'); });
    } else if (f.matches('[data-s101-fiche]')) {
      ev.preventDefault();
      var z = f.querySelector('.s101-statut');
      var mesures = Array.prototype.map.call(f.querySelectorAll('input[name="mesures"]:checked'), function (c) { return c.value; });
      envoyer(f.getAttribute('data-url'), { mesures: mesures, idem: idem() }).then(function (r) {
        dire(z, (r.j && r.j.message) || (r.ok ? 'Brouillon préparé.' : 'Refusé.'));
        if (r.ok && r.j.href) { window.location.assign(r.j.href); }
      }, function () { dire(z, 'Serveur injoignable : aucun brouillon préparé.'); });
    }
  });

  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-s101-veille]');
    if (!b) { return; }
    if (!window.confirm(b.getAttribute('data-confirmer') || 'Confirmer ?')) { return; }
    var zone = b.parentNode.querySelector('.s101-statut'); b.disabled = true;
    envoyer(b.getAttribute('data-url'), { action: b.getAttribute('data-s101-veille'), idem: idem() }).then(function (r) {
      dire(zone, (r.j && r.j.message) || (r.ok ? 'Demande déposée.' : 'Refusé.')); if (!r.ok) { b.disabled = false; }
    }, function () { b.disabled = false; dire(zone, 'Serveur injoignable : aucune demande déposée.'); });
  });
}());
