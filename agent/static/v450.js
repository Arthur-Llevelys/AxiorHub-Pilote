/* AxiorHub 4.5.0 - page Échéances. Aucun HTML venant du serveur n'est injecté : tout passe par textContent. */
(function () {
  'use strict';
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  var csrf = (document.querySelector('meta[name="axiorhub-csrf"]') || {}).content || '';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  if (!$('#ex-list')) return;

  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) return;
    var el = document.createElement('div');
    el.className = 'ax-toast' + (bad ? ' bad' : '');
    el.textContent = message;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, bad ? 9000 : 4500);
  }

  function api(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) {
      if (d && d.error) { var e = new Error(d.message || d.error); e.code = d.error; throw e; }
      return d;
    });
  }
  function fail(err) { toast(err.message || 'Erreur', true); }
  function val(root, sel) { var n = $(sel, root); return n ? n.value : ''; }

  /* ---- actions sur une échéance existante */
  $('#ex-list').addEventListener('click', function (ev) {
    var btn = ev.target.closest('button');
    var item = ev.target.closest('.ex-item');
    if (!btn || !item) return;
    var id = item.getAttribute('data-id');
    var done = function (msg) { return function () { toast(msg); setTimeout(function () { location.reload(); }, 700); }; };
    if (btn.classList.contains('ex-confirm')) {
      btn.disabled = true;
      api('deadline/confirm', {
        id: id, rule_id: val(item, '.ex-rule'), distance: val(item, '.ex-distance'),
        alsace: $('.ex-alsace', item).checked, regime_date: val(item, '.ex-regime')
      }).then(done('Échéance enregistrée et agenda mis à jour.')).catch(function (e) { btn.disabled = false; fail(e); });
    } else if (btn.classList.contains('ex-correct')) {
      var reason = val(item, '.ex-reason').trim();
      if (reason.length < 5) { toast('Indiquez le motif de la correction.', true); return; }
      var payload = { id: id, reason: reason };
      if (val(item, '.ex-newdue')) payload.new_due = val(item, '.ex-newdue');
      else if (val(item, '.ex-newstart')) payload.start_date = val(item, '.ex-newstart');
      else { toast('Indiquez la nouvelle date de départ ou l’échéance imposée.', true); return; }
      btn.disabled = true;
      api('deadline/correct', payload).then(done('Correction enregistrée au journal.')).catch(function (e) { btn.disabled = false; fail(e); });
    } else if (btn.classList.contains('ex-done')) {
      api('deadline/close', { id: id, outcome: 'terminee', reason: val(item, '.ex-reason') }).then(done('Échéance marquée accomplie.')).catch(fail);
    } else if (btn.classList.contains('ex-cancel')) {
      var why = val(item, '.ex-reason').trim();
      if (why.length < 5) { toast('Indiquez le motif d’annulation dans le champ « Motif ».', true); return; }
      if (!confirm('Annuler cette échéance ? L’événement d’agenda sera marqué « ANNULÉE ».')) return;
      api('deadline/close', { id: id, outcome: 'annulee', reason: why }).then(done('Échéance annulée.')).catch(fail);
    } else if (btn.classList.contains('ex-journal')) {
      var list = $('.ex-log', item);
      api('deadline/journal?id=' + encodeURIComponent(id)).then(function (d) {
        list.textContent = '';
        d.journal.forEach(function (j) {
          var li = document.createElement('li');
          li.textContent = j.at.slice(0, 16).replace('T', ' ') + ' · ' + j.action + (j.reason ? ' · ' + j.reason : '');
          list.appendChild(li);
        });
        list.hidden = false;
      }).catch(fail);
    }
  });
  $('#ex-list').addEventListener('change', function (ev) {
    if (!ev.target.classList.contains('ex-act')) return;
    var item = ev.target.closest('.ex-item');
    api('deadline/act', { id: item.getAttribute('data-id'), prepared: ev.target.checked }).then(function () { toast('Mis à jour.'); }).catch(fail);
  });

  /* ---- vérification de l'agenda */
  $('#ex-check').addEventListener('click', function () {
    var out = $('#ex-check-result'); out.textContent = 'Vérification…';
    api('deadline/check', {}).then(function (d) {
      out.textContent = d.count + ' anomalie(s) ; agenda ' + (d.agenda_verifie ? 'vérifié' : 'non vérifiable') + '.';
      if (d.count) setTimeout(function () { location.reload(); }, 1200);
    }).catch(function (e) { out.textContent = ''; fail(e); });
  });

  /* ---- nouvelle échéance / simulation */
  function formData() {
    return {
      matter: $('#ex-matter').value, rule_id: $('#ex-newrule').value, start: $('#ex-start').value,
      regime_date: $('#ex-newregime').value, distance: $('#ex-newdistance').value, alsace: $('#ex-newalsace').checked
    };
  }
  function regimeToggle() {
    var opt = $('#ex-newrule').selectedOptions[0];
    $('#ex-regime-wrap').hidden = !(opt && opt.getAttribute('data-regime') === '1');
  }
  $('#ex-newrule').addEventListener('change', regimeToggle); regimeToggle();
  ['ex-matter', 'ex-newrule', 'ex-start', 'ex-newregime', 'ex-newdistance', 'ex-newalsace'].forEach(function (id) {
    $('#' + id).addEventListener('change', function () { $('#ex-create').disabled = true; });
  });
  $('#ex-preview').addEventListener('click', function () {
    var out = $('#ex-preview-out'); out.textContent = '';
    var data = formData();
    if (!data.start) { toast('Indiquez la date de l’événement de départ.', true); return; }
    api('deadline/preview', data).then(function (r) {
      var res = document.createElement('p'); res.className = 'ex-result'; res.textContent = 'Échéance : ' + r.due_label;
      var ol = document.createElement('ol');
      r.steps.forEach(function (s) { var li = document.createElement('li'); li.textContent = s; ol.appendChild(li); });
      out.appendChild(res); out.appendChild(ol);
      if (r.warnings.length) {
        var ul = document.createElement('ul'); ul.className = 'ex-warn';
        r.warnings.forEach(function (w) { var li = document.createElement('li'); li.textContent = w; ul.appendChild(li); });
        out.appendChild(ul);
      }
      $('#ex-create').disabled = false;
    }).catch(function (e) { $('#ex-create').disabled = true; fail(e); });
  });
  $('#ex-create').addEventListener('click', function () {
    var btn = this; btn.disabled = true;
    api('deadline/create', formData()).then(function () {
      toast('Échéance enregistrée.'); setTimeout(function () { location.reload(); }, 700);
    }).catch(function (e) { btn.disabled = false; fail(e); });
  });

  $('#ex-save').addEventListener('click', function () {
    api('settings/deadlines', { enabled: $('#ex-enabled').checked, calendar: $('#ex-cal').checked })
      .then(function () { toast('Réglages enregistrés.'); }).catch(fail);
  });
})();
