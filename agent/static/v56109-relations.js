/* AxiorHub 5.6.109 (lot G2) — relations du dossier. Les formulaires passent par le traitement commun (m5-form) ; ce script ne fait
   qu'une chose : quand le reçu du serveur contient un fichier rendu une seule fois (paquet de partage avec le client, pièce vérifiée
   d'un kit d'audience), il le propose au téléchargement sur ce poste. Aucun HTML venant du serveur n'est injecté ; aucune requête. */
(function () {
  'use strict';
  function telecharger(d) {
    if (!d || typeof d.base64 !== 'string' || typeof d.filename !== 'string') return;
    var octets = Uint8Array.from(atob(d.base64), function (c) { return c.charCodeAt(0); });
    var url = URL.createObjectURL(new Blob([octets], { type: d.mime || 'application/octet-stream' }));
    var a = document.createElement('a');
    a.href = url;
    a.download = d.filename.replace(/[\\/:*?"<>|]/g, '_');
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }
  document.addEventListener('ax:recu', function (ev) {
    var form = ev.target;
    if (!form || !form.classList || !form.classList.contains('rl109-form')) return;
    var res = ev.detail || {};
    if (res.download) telecharger(res.download);
    var phrase = form.querySelector('input[name="phrase"]');
    if (phrase) phrase.value = '';               /* la phrase secrète ne reste pas dans la page */
  });
})();
