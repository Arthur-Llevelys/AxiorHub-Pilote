/* AxiorHub 5.6.60 : page Paramètres — recherche d'une rubrique, ouverture de la section visée par un lien, « Tout déplier ». */
(function () {
  'use strict';
  function fold(text) {
    return (text || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  }
  function openTarget() {
    var id = decodeURIComponent((location.hash || '').slice(1));
    if (!id) { return; }
    var target = document.getElementById(id);
    var node = target;
    while (node) {
      if (node.tagName === 'DETAILS') { node.open = true; }
      node = node.parentElement;
    }
    if (target && target.scrollIntoView) { target.scrollIntoView({block: 'start'}); }
  }
  function init() {
    var find = document.querySelector('[data-p5660-find]');
    if (find) {
      find.addEventListener('input', function () {
        var words = fold(find.value).split(/\s+/).filter(Boolean);
        document.querySelectorAll('[data-p5660-nav] a').forEach(function (link) {
          var keys = fold(link.getAttribute('data-p5660-keys'));
          var match = words.every(function (w) { return keys.indexOf(w) >= 0; });
          link.hidden = words.length > 0 && !match;
        });
        document.querySelectorAll('.p5660-family').forEach(function (family) {
          family.hidden = !family.querySelector('a:not([hidden])');
        });
      });
    }
    var all = document.querySelector('[data-p5660-all]');
    if (all) {
      all.addEventListener('click', function () {
        var sections = document.querySelectorAll('details.p5660-sec');
        var open = Array.prototype.some.call(sections, function (d) { return !d.open; });
        sections.forEach(function (d) { d.open = open; });
        all.textContent = open ? 'Tout replier' : 'Tout déplier';
      });
    }
    openTarget();
    window.addEventListener('hashchange', openTarget);
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); } else { init(); }
})();
