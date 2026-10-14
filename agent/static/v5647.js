/* AxiorHub 5.6.47 — case de recherche au-dessus de toute liste de dossiers (<select name="matter">) de plus de 12 choix :
   les dossiers dont le nom ne contient pas le texte saisi (sans accents ni majuscules) sont masqués ; le choix en cours reste visible. */
(function () {
  'use strict';
  function fold(s) { return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }
  function enhance(select) {
    if (select.getAttribute('data-ax5647') || select.options.length <= 12) return;
    select.setAttribute('data-ax5647', '1');
    var input = document.createElement('input');
    input.type = 'search';
    input.className = 'ax5647-filter';
    input.placeholder = 'Rechercher un dossier (nom, client, numéro)';
    input.setAttribute('aria-label', 'Rechercher un dossier');
    input.autocomplete = 'off';
    var count = document.createElement('small');
    count.className = 'ax5647-count';
    count.setAttribute('aria-live', 'polite');
    select.parentNode.insertBefore(input, select);
    select.parentNode.insertBefore(count, select.nextSibling);
    input.addEventListener('input', function () {
      var q = fold(input.value).trim(), shown = 0, first = null;
      Array.prototype.forEach.call(select.options, function (o) {
        var match = fold(o.textContent + ' ' + o.value).indexOf(q) >= 0;
        var keep = !o.value || !q || o.selected || match;
        o.hidden = !keep;
        if (keep && o.value && match) { shown++; if (!first) first = o; }
      });
      count.textContent = q ? shown + ' dossier(s)' : '';
      if (q && first && shown === 1) { first.selected = true; select.dispatchEvent(new Event('change', { bubbles: true })); }
    });
    input.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') ev.preventDefault(); });
  }
  function scan(root) { Array.prototype.forEach.call((root || document).querySelectorAll('select[name="matter"]'), enhance); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { scan(); }); else scan();
  new MutationObserver(function (list) {
    list.forEach(function (m) { Array.prototype.forEach.call(m.addedNodes, function (n) { if (n.querySelectorAll) scan(n); }); });
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
