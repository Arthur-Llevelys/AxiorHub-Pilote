/* AxiorHub 5.6.51 — page Dossier : identifiant de commande de mission (double clic = même mission), fichiers choisis, attente d'une relecture */
(function () {
  'use strict';
  function key() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function init() {
    document.querySelectorAll('input[data-mi51-key]').forEach(function (i) { if (!i.value) { i.value = key(); } });
    document.querySelectorAll('select[data-mi51-files]').forEach(function (sel) {
      var hidden = sel.form && sel.form.querySelector('input[type=hidden][name=fichiers]');
      if (!hidden) { return; }
      var sync = function () {
        hidden.value = Array.prototype.filter.call(sel.options, function (o) { return o.selected; }).map(function (o) { return o.value; }).join('\n');
      };
      sel.addEventListener('change', sync);
      sync();
    });
    if (document.querySelector('[data-d51-waiting]')) {   // propositions en cours de génération : la page se met à jour
      setTimeout(function () { if (!document.querySelector('form.m5-form textarea:focus, form.m5-form input:focus')) { location.reload(); } }, 15000);
    }
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); } else { init(); }
}());
