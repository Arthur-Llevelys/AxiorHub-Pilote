/* AxiorHub 5.2.0 — Pièces et bordereaux. Aucun HTML venant du serveur n'est injecté : tout passe par textContent. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  function toast(message, bad) {
    var box = $('#ax-toast'); if (!box) return;
    var t = document.createElement('div'); t.className = 'ax-toast' + (bad ? ' bad' : ''); t.textContent = message;
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 10000 : 5000);
  }
  function call(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) { if (d && d.error) { throw new Error(d.message || d.error); } return d; });
  }
  function renumber(table) {
    var holder = table.closest('[data-first]'), n = holder ? (parseInt(holder.getAttribute('data-first'), 10) || 1) - 1 : 0;
    $$('tbody tr[data-key]', table).forEach(function (tr) {
      var on = $('.p5-inc', tr).checked; if (on) n += 1;
      $('.p5-num', tr).textContent = on ? String(n) : '–';
      tr.classList.toggle('p5-off', !on);
    });
  }
  var table = $('.p5-table');
  if (table) {
    renumber(table);
    table.addEventListener('click', function (ev) {
      var b = ev.target.closest('.p5-up,.p5-down'); if (!b) return;
      var tr = b.closest('tr');
      if (b.classList.contains('p5-up') && tr.previousElementSibling) tr.parentNode.insertBefore(tr, tr.previousElementSibling);
      if (b.classList.contains('p5-down') && tr.nextElementSibling) tr.parentNode.insertBefore(tr.nextElementSibling, tr);
      renumber(table); b.focus();
    });
    table.addEventListener('change', function (ev) { if (ev.target.classList.contains('p5-inc')) renumber(table); });
  }
  $$('.p5-save').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var rows = $$('tbody tr[data-key]', table).map(function (tr) {
        return { key: tr.getAttribute('data-key'), include: $('.p5-inc', tr).checked, title: $('.p5-title', tr).value, date: $('.p5-date', tr).value };
      });
      var order = $('.p5-order'), body = { draft: btn.getAttribute('data-draft'), rows: rows, order: order ? order.value : '' };
      btn.disabled = true;
      call('m510/table', body).then(function () { toast('Tableau enregistré.'); setTimeout(function () { location.reload(); }, 400); })
        .catch(function (e) { toast(e.message, true); }).then(function () { btn.disabled = false; });
    });
  });
  $$('.p5-upload').forEach(function (inp) {
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0]; if (!f) return;
      if (f.size > 180000) { toast('Modèle trop volumineux pour l’import (180 Ko au plus).', true); return; }
      var rd = new FileReader();
      rd.onload = function () {
        var data = String(rd.result || '').split(',').pop();
        call('m510/template/upload', { name: f.name, data: data }).then(function (r) {
          toast('Modèle importé : ' + r.fields.length + ' balise(s).'); setTimeout(function () { location.reload(); }, 600);
        }).catch(function (e) { toast(e.message, true); });
      };
      rd.readAsDataURL(f);
    });
  });
  $$('.p5-upload-image').forEach(function (inp) {
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0]; if (!f) return;
      if (f.size > 190000) { toast('Image trop lourde pour l’import (190 Ko au plus) : réduisez sa taille.', true); return; }
      var rd = new FileReader();
      rd.onload = function () {
        call('m510/stamp-image/upload', { name: f.name, data: String(rd.result || '').split(',').pop() }).then(function () {
          toast('Image du tampon importée.'); setTimeout(function () { location.reload(); }, 600);
        }).catch(function (e) { toast(e.message, true); });
      };
      rd.readAsDataURL(f);
    });
  });
  $$('.p5-request').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var out = $('#p5-code'); out.textContent = ''; btn.disabled = true;
      call('m510/create/request', { draft: btn.getAttribute('data-draft') }).then(function (r) {
        if (r.ready) { out.textContent = 'Code de confirmation : ' + r.code + ' (valable ' + r.expires_minutes + ' minutes). Recopiez-le ci-dessous pour créer les fichiers.'; }
        else { out.textContent = 'Création impossible pour l’instant :\n• ' + r.reasons.join('\n• '); }
      }).catch(function (e) { toast(e.message, true); }).then(function () { btn.disabled = false; });
    });
  });
  $$('.p5-export').forEach(function (b) {
    b.addEventListener('click', function () {
      b.disabled = true;
      call(b.getAttribute('data-api')).then(function (res) {
        var d = res.download; if (!d) throw new Error('Rien à télécharger.');
        var blob;
        if (d.base64) { var bin = atob(d.base64), arr = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i); blob = new Blob([arr], { type: d.mime }); }
        else { blob = new Blob([d.text], { type: d.mime + ';charset=utf-8' }); }
        var url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = d.filename; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
      }).catch(function (e) { toast(e.message, true); }).then(function () { b.disabled = false; });
    });
  });
})();
