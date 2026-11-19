/* AxiorHub 5.6.108 (lot G) — Agenda local : éditeur prérempli lisible et commandes visibles pendant le défilement de l'amplitude horaire.
   - Durée : liste synchronisée avec Début et Fin (arithmétique sur l'heure saisie, sans fuseau du poste : le serveur convertit et refuse
     une heure sautée ou répétée) ; « Autre durée » quand la fin ne correspond à aucune durée proposée.
   - Rappel : choix explicite envoyé à la route existante (``rappels``) ; un événement existant montre ses rappels réels.
   - Récurrence et responsable : lus dans le détail de l'événement ouvert ; un événement créé ici est unique.
   - Barre de l'agenda : hauteur mesurée (CSSOM) pour que la ligne des jours reste visible sous elle quand la page défile.
   Aucun HTML n'est inséré depuis une réponse : textContent seulement ; aucun style en ligne hors CSSOM (CSP). */
(function () {
  'use strict';
  if (window.AX108agenda) return;
  function $(s, r) { return (r || document).querySelector(s); }
  var RECURRENCE_UNIQUE = 'Aucune (événement unique)';
  var RECURRENCE_SERIE = 'Occurrence d’une série : modifiable dans son calendrier d’origine';
  var RAPPELS_ACTUELS = 'Rappels actuels conservés';

  function minutes(v) {   // « AAAA-MM-JJTHH:MM » → minutes (UTC arithmétique : aucune dépendance au fuseau du poste)
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(v || '');
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 60000 : null;
  }
  function local(min) { return new Date(min * 60000).toISOString().slice(0, 16); }

  function syncDuree(form) {
    var sel = form.elements.duree;
    if (!sel) return;
    var a = minutes(form.elements.debut.value), b = minutes(form.elements.fin.value), d = (a !== null && b !== null) ? b - a : null, ok = false;
    Array.prototype.forEach.call(sel.options, function (o) { if (!ok && o.value && Number(o.value) === d) { sel.value = o.value; ok = true; } });
    if (!ok) sel.value = '';
  }
  function appliquerDuree(form) {
    var sel = form.elements.duree, a = minutes(form.elements.debut.value);
    if (sel && sel.value && a !== null) form.elements.fin.value = local(a + Number(sel.value));
  }
  function journee(form) {
    var sel = form.elements.duree, lab = sel && sel.closest('label');
    if (lab) lab.hidden = !!form.elements.journee_entiere.checked;
  }
  function brancher(form) {
    if (form.getAttribute('data-ag108-pret')) return;
    form.setAttribute('data-ag108-pret', '1');
    if (form.elements.duree) form.elements.duree.addEventListener('change', function () { appliquerDuree(form); });
    form.elements.debut.addEventListener('change', function () {    // nouveau début : la durée choisie est gardée
      if (form.elements.duree && form.elements.duree.value) appliquerDuree(form); else syncDuree(form);
    });
    form.elements.fin.addEventListener('change', function () { syncDuree(form); });
    form.elements.journee_entiere.addEventListener('change', function () { journee(form); syncDuree(form); });
  }

  function ouvert(form, d) {
    if (!form.elements.duree) return;
    brancher(form);
    syncDuree(form);
    journee(form);
    var rappel = form.elements.rappel, ancien = rappel && $('option[data-ag108-actuel]', rappel), labRappel = rappel && rappel.closest('label');
    if (ancien) ancien.remove();
    if (labRappel) labRappel.hidden = false;
    if (rappel && d) {
      if (Array.isArray(d.reminders)) {
        var v = d.reminders.length ? d.reminders.slice().sort(function (x, y) { return y - x; }).join(',') : 'aucun';
        if (!$('option[value="' + v + '"]', rappel)) {
          var o = document.createElement('option'); o.value = v; o.textContent = RAPPELS_ACTUELS; o.setAttribute('data-ag108-actuel', '1');
          rappel.appendChild(o);
        }
        rappel.value = v;
      } else if (labRappel) {
        labRappel.hidden = true;            // calendrier en ligne : ses rappels ne sont pas lus ici, rien n'est affiché à leur place
      }
    }
    var rec = $('[data-ag108-recurrence]', form);
    if (rec) rec.textContent = d && d.series ? RECURRENCE_SERIE : RECURRENCE_UNIQUE;
    var resp = $('[data-ag108-resp-ev] dd', form);
    if (resp) {
      if (!resp.hasAttribute('data-defaut')) resp.setAttribute('data-defaut', resp.textContent);
      resp.textContent = d && d.responsable_nom ? d.responsable_nom : resp.getAttribute('data-defaut');
    }
  }
  function rappels(form) {
    var r = form.elements.rappel;
    if (!r || r.disabled || (r.closest('label') || {}).hidden) return undefined;
    if (r.value === 'aucun') return [];
    var out = r.value.split(',').map(Number).filter(function (x) { return !isNaN(x); });
    return out.length ? out : undefined;
  }
  window.AX108agenda = { ouvert: ouvert, rappels: rappels };

  // ------------------------------------------------------------------ barre visible pendant le défilement : hauteur mesurée
  function mesurer() {
    var racine = $('[data-ag97].ag108'), barre = racine && $('.ag97-barre', racine);
    if (barre) racine.style.setProperty('--ag108-barre', Math.ceil(barre.getBoundingClientRect().height) + 'px');
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mesurer); else mesurer();
  window.addEventListener('resize', mesurer);
})();
