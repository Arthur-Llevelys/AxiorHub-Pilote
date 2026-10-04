/* AxiorHub 5.4.0 — page « IA externe sûre » : aperçu de la pseudonymisation et journal. Tout est affiché par textContent. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s) { return document.querySelector(s); };
  function call(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.error) throw new Error(d.message || d.error); return d;
    });
  }
  var form = $('#p540-form');
  if (form) {
    form.removeAttribute('data-api');           // géré ici (affichage du tableau), pas par le script générique
    form.classList.remove('m5-form');
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var btn = form.querySelector('button'); btn.disabled = true;
      call('m540/preview', { text: form.elements.text.value, matter: form.elements.matter.value }).then(function (res) {
        $('#p540-result').hidden = false;
        $('#p540-sent').textContent = res.sent;
        var tbody = $('#p540-table'); tbody.textContent = '';
        res.replacements.forEach(function (r) {
          var tr = document.createElement('tr');
          [r.token, r.original, r.category].forEach(function (v) { var td = document.createElement('td'); td.textContent = v; tr.appendChild(td); });
          tbody.appendChild(tr);
        });
        $('#p540-check').textContent = res.restored_ok ? 'Contrôle : la réponse peut être rétablie à l’identique sur le serveur.' : 'Attention : rétablissement incomplet.';
      }).catch(function (err) { alert(err.message); }).then(function () { btn.disabled = false; });
    });
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-p540-sample]'); if (!b) return;
    call('m540/sample?id=' + encodeURIComponent(b.getAttribute('data-p540-sample'))).then(function (res) {
      var pre = $('#p540-sample'); pre.hidden = false; pre.textContent = res.text || '(vide)'; pre.scrollIntoView({ block: 'nearest' });
    }).catch(function (err) { alert(err.message); });
  });
})();
