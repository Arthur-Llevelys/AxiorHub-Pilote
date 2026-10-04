/* AxiorHub 5.2.0 — glisser-déposer des tâches sur l'agenda (la recherche Ctrl+K est dans la palette de v420.js). Aucun HTML venant du serveur n'est injecté. */
(function () {
  'use strict';
  var prefix = ((document.querySelector('meta[name="axiorhub-prefix"]') || {}).content) || '/agent-courriel';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ---------- glisser-déposer : tâche → jour de la vue semaine ---------- */
  var panel = $('.ax-dnd'); if (!panel) return;
  var formBox = $('.ax-dnd-form', panel), selected = null;
  function choose(chip, day) {
    var f = $('form', formBox); if (!f) return;
    $('input[name=task]', f).value = chip.getAttribute('data-task');
    $('input[name=start]', f).value = day + 'T09:00';
    $('.ax-dnd-label', f).textContent = '« ' + chip.textContent + ' » le ' + day.split('-').reverse().join('/') + ' : ajustez l’heure et la durée, puis confirmez.';
    formBox.hidden = false; $('input[name=start]', f).focus();
  }
  $$('.ax-dnd-task', panel).forEach(function (chip) {
    chip.addEventListener('dragstart', function (ev) { ev.dataTransfer.setData('text/plain', chip.getAttribute('data-task')); selected = chip; });
    chip.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selected = chip; $$('.ax-dnd-task', panel).forEach(function (c) { c.classList.toggle('on', c === chip); }); }
    });
  });
  $$('[data-day]').forEach(function (day) {
    day.addEventListener('dragover', function (ev) { ev.preventDefault(); day.classList.add('ax-drop'); });
    day.addEventListener('dragleave', function () { day.classList.remove('ax-drop'); });
    day.addEventListener('drop', function (ev) { ev.preventDefault(); day.classList.remove('ax-drop'); if (selected) choose(selected, day.getAttribute('data-day')); });
    var h = $('h3', day); if (h) { h.tabIndex = 0; h.addEventListener('keydown', function (ev) { if ((ev.key === 'Enter' || ev.key === ' ') && selected) { ev.preventDefault(); choose(selected, day.getAttribute('data-day')); } }); }
  });
})();
