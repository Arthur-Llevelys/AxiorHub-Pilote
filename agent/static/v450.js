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
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function newKey() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    var a = new Uint8Array(16); window.crypto.getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  /* 5.6.96 (D03) : l'erreur est liée au champ (aria-describedby, aria-invalid) et le focus y revient */
  function showError(box, field, message) {
    var err = $('.ex-err', box);
    err.textContent = message; err.hidden = false;
    if (field) { field.setAttribute('aria-invalid', 'true'); field.focus(); }
  }
  function clearError(box) {
    var err = $('.ex-err', box);
    if (err) { err.textContent = ''; err.hidden = true; }
    $all('[aria-invalid]', box).forEach(function (n) { n.removeAttribute('aria-invalid'); });
  }
  function sendDecision(item, id, box, btn, done) {
    var kind = box.getAttribute('data-kind');
    var reasonField = $('.ex-reason', box);
    var reason = reasonField ? reasonField.value.trim() : '';
    clearError(box);
    if (kind !== 'agenda' && kind !== 'verifie' && reason.length < 3) { showError(box, reasonField, 'Indiquez le motif (trois caractères au moins) : rien n’a été modifié.'); return; }
    var payload = { id: id, version: item.getAttribute('data-version'), cle: box.getAttribute('data-cle') || newKey() };
    var route, message;
    if (kind === 'corriger') {
      var newDue = val(box, '.ex-newdue'), newStart = val(box, '.ex-newstart');
      if (newDue) payload.new_due = newDue; else if (newStart) payload.start_date = newStart;
      else { showError(box, $('.ex-newstart', box), 'Indiquez la nouvelle date de départ ou l’échéance imposée : rien n’a été modifié.'); return; }
      payload.reason = reason; route = 'deadline/correct'; message = 'Correction enregistrée au journal.';
    } else if (kind === 'ecarter') {
      payload.outcome = 'annulee'; payload.ecart = true; payload.reason = reason; route = 'deadline/close';
      message = 'Détection écartée : elle ne sera pas reproposée.';
    } else if (kind === 'accomplir') {
      payload.outcome = 'terminee'; payload.reason = reason; route = 'deadline/close'; message = 'Échéance marquée accomplie.';
    } else if (kind === 'annuler') {
      payload.outcome = 'annulee'; payload.reason = reason; route = 'deadline/close'; message = 'Échéance annulée.';
    } else if (kind === 'verifie') {
      payload.verifie_main = true; route = 'deadline/agenda'; message = 'Vérification de l’agenda notée au journal.';
    } else {
      route = 'deadline/agenda'; message = 'Agenda repris.';
    }
    btn.disabled = true;
    api(route, payload).then(function (r) {
      var cal = r.calendar === 'retrait_propose' ? ' Son événement d’agenda n’est pas touché : son retrait vous est proposé.' :
                r.calendar === 'retiree' ? ' Événement retiré de l’agenda.' :
                r.calendar === 'synchronisation_a_reprendre' ? ' Agenda injoignable : synchronisation à reprendre.' :
                r.calendar === 'a_reconcilier' ? ' L’événement d’agenda ne correspond plus : rien n’a été effacé, vérifiez-le à la main.' : '';
      done(message + cal)();
    }).catch(function (e) {
      btn.disabled = false;
      showError(box, e.code === 'motif_obligatoire' ? reasonField : null, e.message || 'Erreur');
    });
  }
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
        alsace: $('.ex-alsace', item).checked, regime_date: val(item, '.ex-regime'),
        version: item.getAttribute('data-version')
      }).then(function (r) {
        var inscrite = r.calendar === 'cree' || r.calendar === 'mis_a_jour';
        done(r.status === 'a_completer' ? 'Échéance à compléter : consultez le calcul.' :
             inscrite ? 'Échéance confirmée et inscrite à l’agenda.' :
             r.calendar === 'a_valider' ? 'Échéance confirmée ; son inscription à l’agenda attend votre validation (page Autonomie).' :
             'Échéance confirmée ; agenda : ' + (r.calendar || 'non inscrite') + '.')();
      }).catch(function (e) { btn.disabled = false; fail(e); });
    } else if (btn.classList.contains('ex-open')) {
      /* 5.6.96 (D03) : le formulaire voisin s'ouvre, le focus va au champ Motif (ou à « Confirmer » pour l'agenda) */
      var box = document.getElementById(btn.getAttribute('aria-controls'));
      if (!box) return;
      $all('.ex-box', item).forEach(function (other) {
        if (other !== box && !other.hidden) {
          other.hidden = true;
          var opener = item.querySelector('[aria-controls="' + other.id + '"]');
          if (opener) opener.setAttribute('aria-expanded', 'false');
        }
      });
      box.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      if (!box.getAttribute('data-cle')) box.setAttribute('data-cle', newKey());
      var first = $('.ex-reason', box) || $('.ex-send', box);
      if (first) first.focus();
    } else if (btn.classList.contains('ex-back')) {
      var back = btn.closest('.ex-box');
      back.hidden = true;
      clearError(back);
      var opener2 = item.querySelector('[aria-controls="' + back.id + '"]');
      if (opener2) { opener2.setAttribute('aria-expanded', 'false'); opener2.focus(); }
    } else if (btn.classList.contains('ex-send')) {
      sendDecision(item, id, btn.closest('.ex-box'), btn, done);
    } else if (btn.classList.contains('ex-journal')) {
      var list = $('.ex-log', item);
      api('deadline/journal?id=' + encodeURIComponent(id)).then(function (d) {
        list.textContent = '';
        d.journal.forEach(function (j) {
          var li = document.createElement('li');
          li.textContent = j.at.slice(0, 16).replace('T', ' ') + ' · ' + j.action + (j.actor ? ' · ' + j.actor : '') + (j.reason ? ' · ' + j.reason : '') + (j.calendar ? ' · agenda : ' + j.calendar : '');
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
