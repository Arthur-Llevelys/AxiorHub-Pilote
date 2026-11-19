/* AxiorHub 5.6.108 (lot Z) — page « Faire signer par lien » (SignaturePDF), chargée par cette page seulement.
   - « Copier le lien » : copie le lien de signature dans le presse-papiers (rien n'est envoyé : l'avocat l'adresse lui-même).
   - Nombre de signatures : lecture ``GET m56108/signaturepdf/etat`` ; service injoignable : « indisponible », jamais un faux zéro.
   - « Déposer le PDF signé » : octets du fichier choisi envoyés en POST (jeton CSRF, clé d'idempotence, version attendue) ;
     le serveur contrôle le dossier, le format, la taille et range le PDF à côté de l'original. Aucun reçu : « incertain », jamais un succès.
   Textes par textContent ; aucun HTML du serveur injecté. */
(function () {
  'use strict';
  if (typeof document === 'undefined') return;
  var doc = document, MAX = 24 * 1024 * 1024;
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  function statut(form, texte, erreur) {
    var s = form.querySelector('.m5-status'); if (!s) return;
    s.textContent = texte; s.classList.toggle('z108-erreur', !!erreur);
  }

  Array.prototype.forEach.call(doc.querySelectorAll('[data-z108-copier]'), function (b) {
    b.addEventListener('click', function () {
      var champ = doc.getElementById(b.getAttribute('data-z108-copier')); if (!champ) return;
      var fini = function () { b.textContent = 'Lien copié'; setTimeout(function () { b.textContent = 'Copier le lien'; }, 2500); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(champ.value).then(fini, function () { champ.select(); });
      } else { champ.select(); }
    });
  });

  Array.prototype.forEach.call(doc.querySelectorAll('[data-z108-etat]'), function (p) {
    var url = prefix + '/api440/m56108/signaturepdf/etat?demande=' + encodeURIComponent(p.getAttribute('data-z108-etat'));
    fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d || d.signatures === null || d.signatures === undefined) { p.textContent = 'Nombre de signatures indisponible (service injoignable).'; return; }
      p.textContent = d.signatures === 0 ? 'Aucune signature pour l’instant.' : 'Signatures apposées : ' + d.signatures;
    }, function () { p.textContent = 'Nombre de signatures indisponible (service injoignable).'; });
  });

  Array.prototype.forEach.call(doc.querySelectorAll('form.z108-depot'), function (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var champ = form.querySelector('input[type=file]'), bouton = form.querySelector('button[type=submit]');
      var f = champ && champ.files && champ.files[0];
      if (!f) { statut(form, 'Choisissez le PDF signé.', true); return; }
      if (f.size > MAX) { statut(form, 'PDF trop volumineux pour SignaturePDF (24 Mo au plus).', true); return; }
      if (!/\.pdf$/i.test(f.name) && f.type !== 'application/pdf') { statut(form, 'Seuls les fichiers PDF peuvent être signés par lien.', true); return; }
      if (bouton.disabled) return;
      bouton.disabled = true; statut(form, 'Envoi en cours… (pas encore de reçu)');
      var adresse = prefix + '/api440/m56108/signaturepdf/deposer?' + 'demande=' + encodeURIComponent(form.getAttribute('data-z108-depot')) +
        '&version=' + encodeURIComponent(form.getAttribute('data-version')) + '&cle=' + encodeURIComponent(form.getAttribute('data-cle'));
      fetch(adresse, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/pdf', 'X-CSRF-Token': csrf }, body: f })
        .then(function (r) {
          return r.json().catch(function () { return null; }).then(function (d) {
            if (!d) { statut(form, 'Le serveur n’a pas confirmé : rechargez la page pour voir l’état réel.', true); bouton.disabled = false; return; }
            if (!r.ok) { statut(form, d.message || 'Refusé.', true); bouton.disabled = false; return; }
            statut(form, d.message || 'PDF signé rangé dans le dossier.');
            setTimeout(function () { location.reload(); }, 900);
          });
        }, function () { statut(form, 'Connexion au serveur interrompue : rechargez la page pour voir l’état réel.', true); bouton.disabled = false; });
    });
  });
})();
