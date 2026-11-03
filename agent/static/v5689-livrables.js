/* AxiorHub 5.6.89 — documents préparés : liste à jour sans rechargement manuel, suivi d'un export, retour à la position quittée. Aucun HTML venant du serveur n'est injecté ici. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var KEY = 'l89-retour';
  function store(value) { try { sessionStorage.setItem(KEY, JSON.stringify(value)); } catch (e) { /* navigation privée : retour sans position */ } }
  function load() { try { return JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch (e) { return null; } }
  function clear() { try { sessionStorage.removeItem(KEY); } catch (e) { /* idem */ } }
  function saisieEnCours() {
    var el = document.activeElement;
    if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return true;
    return Array.prototype.some.call(document.querySelectorAll('form input:not([type=hidden]), form textarea'), function (f) { return f.value && f.value !== f.defaultValue; });
  }

  // Liste : la position et le document ouvert sont gardés pour le bouton Retour de la visionneuse.
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('.l89-ligne a');
    if (!a) return;
    store({ url: location.pathname + location.search, y: window.scrollY, id: a.closest('.l89-ligne').getAttribute('data-livrable') });
  });
  var saved = load();
  if (saved && saved.url === location.pathname + location.search) {
    clear();
    window.scrollTo(0, saved.y || 0);
    var row = document.querySelector('.l89-ligne[data-livrable="' + String(saved.id || '').replace(/[^A-Za-z0-9_-]/g, '') + '"] a');
    if (row) row.focus();
  }

  // Liste, compteurs : un jeton léger est interrogé ; la page ne se recharge que s'il change, et jamais pendant une saisie.
  var zone = document.getElementById('l89-liste') || document.getElementById('l89-section');
  if (zone) {
    var known = zone.getAttribute('data-revision'), scope = zone.getAttribute('data-scope');
    setInterval(function () {
      if (document.hidden) return;
      fetch(prefix + '/api440/m5689/livrables/revision' + (scope ? '?dossier=' + encodeURIComponent(scope) : ''), { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
        if (!d || !d.revision || d.revision === known) return;
        if (saisieEnCours()) return;
        location.reload();
      }).catch(function () { /* hors ligne : nouvel essai au prochain passage */ });
    }, 20000);
  }

  // Export : travail suivi. « Exporté » n'apparaît qu'une fois la preuve reçue du serveur ; une réponse perdue se reprend avec la même clé.
  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-export]');
    if (!form) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    var status = form.querySelector('.m5-status');
    var data = {};
    Array.prototype.forEach.call(form.querySelectorAll('input,select'), function (f) { if (f.name) data[f.name] = f.value; });
    function say(text) { if (status) status.textContent = text; }
    function suivre(id, essais) {
      if (essais <= 0) { say('Export toujours en cours : revenez sur cette page pour en voir le résultat.'); return; }
      fetch(prefix + '/api440/m5689/livrables/exports/' + encodeURIComponent(id), { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.error) { say(d.message || 'Suivi de l’export impossible.'); return; }
        say(d.state_label + (d.fichier ? ' : ' + d.fichier : '') + (d.erreur ? ' (' + d.erreur + ')' : ''));
        if (!d.done && d.state !== 'echec') setTimeout(function () { suivre(id, essais - 1); }, 3000);
      }).catch(function () { setTimeout(function () { suivre(id, essais - 1); }, 5000); });
    }
    say('Demande envoyée…');
    fetch(prefix + '/api440/' + form.getAttribute('data-api'), { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(data) }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) { say(d.message || d.error); return; }
      say(d.message || d.state_label);
      suivre(d.id, 60);
    }).catch(function () { say('Réponse perdue : renvoyez la demande, elle ne créera pas de second fichier.'); });
  }, true);
})();
