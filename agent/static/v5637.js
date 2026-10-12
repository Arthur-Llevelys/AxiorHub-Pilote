/* AxiorHub 5.6.37 : interface affichée dans Nextcloud (application AxiorHub Pilote 0.4.0).
   Premier des scripts différés : la classe ax-integre est posée avant v320.js ; le menu d'AxiorHub est masqué (un seul menu : Nextcloud).
   La page courante (chemin seul, sans paramètres) est signalée au cadre parent pour surligner le menu Nextcloud. */
(function () {
  'use strict';
  var root = document.documentElement;
  var framed = false;
  try { framed = window.top !== window.self; } catch (e) { framed = true; }
  if (!framed) return;
  root.classList.add('ax-integre');
  document.addEventListener('DOMContentLoaded', function () {
    try {
      var meta = document.querySelector('meta[name="axiorhub-prefix"]');
      var prefix = meta ? meta.content : '';
      var path = location.pathname;
      if (prefix && path.indexOf(prefix) === 0) path = path.slice(prefix.length) || '/';
      window.parent.postMessage({ axiorhub: 'page', path: path, title: document.title }, '*');
    } catch (e) { /* cadre fermé */ }
  });
})();
