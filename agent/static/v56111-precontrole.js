/* AxiorHub 5.6.111 (lot U, B6) — carte « Précontrôle du poste » (Paramètres › Voix) : autorisation du micro lue sans l'ouvrir.
   navigator.permissions.query ne déclenche aucune demande ; aucun son n'est capté, rien n'est envoyé. */
(function () {
  'use strict';
  var zone = document.querySelector('[data-u111-micro-etat]');
  if (!zone) return;
  var ligne = zone.closest('[data-u111-pc]');
  // Textes en français : traduits dans la page par v5634t.js avec le dictionnaire des écrans (ecrans_*.json), comme les autres scripts.
  var T = { securise: 'Adresse non sécurisée : le navigateur refuse le micro. Ouvrez l’application par son adresse https ou sur ce poste (127.0.0.1).',
            absent: 'Ce navigateur ne donne pas accès au micro.', accorde: 'Micro autorisé par le navigateur.',
            refuse: 'Micro refusé par le navigateur : cliquez sur le cadenas de la barre d’adresse, autorisez le micro, puis rechargez la page.',
            demande: 'Le navigateur demandera l’autorisation au premier clic sur le micro.',
            inconnu: 'Autorisation du micro non lisible ici : elle sera demandée au premier clic.' };
  function dire(cle, etat) {
    zone.textContent = T[cle];
    if (ligne && etat) {
      ligne.classList.remove('u111-pc-navigateur', 'u111-pc-ok', 'u111-pc-manque', 'u111-pc-a_verifier');
      ligne.classList.add('u111-pc-' + etat);
      ligne.setAttribute('data-u111-micro', cle);
    }
  }
  if (!window.isSecureContext) { dire('securise', 'manque'); return; }
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') { dire('absent', 'manque'); return; }
  if (!navigator.permissions || typeof navigator.permissions.query !== 'function') { dire('inconnu', 'a_verifier'); return; }
  navigator.permissions.query({ name: 'microphone' }).then(function (s) {
    var suivre = function () { dire(s.state === 'granted' ? 'accorde' : (s.state === 'denied' ? 'refuse' : 'demande'),
                                     s.state === 'granted' ? 'ok' : (s.state === 'denied' ? 'manque' : 'a_verifier')); };
    suivre();
    s.onchange = suivre;
  }).catch(function () { dire('inconnu', 'a_verifier'); });
})();
