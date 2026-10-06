/* AxiorHub 5.0.0 — fonctions métier. Aucun HTML venant du serveur n'est injecté : tout passe par textContent. */
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
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) {
      if (d && d.error) { var e = new Error(d.message || d.error); e.code = d.error; throw e; }
      return d;
    });
  }
  function busy(btn, on) { if (btn) { btn.disabled = on; btn.setAttribute('aria-busy', on ? 'true' : 'false'); } }

  function collect(form) {
    var data = {};
    $$('input,select,textarea', form).forEach(function (f) {
      if (!f.name || f.type === 'file' || f.closest('.m5-party-row')) return;
      if (f.type === 'checkbox') { data[f.name] = f.checked; return; }
      if (f.type === 'radio' && !f.checked) return;
      data[f.name] = f.value;
    });
    if (form.getAttribute('data-collect') === 'parties') {
      data.parties = $$('.m5-party-row', form).map(function (row) {
        return { name: $('[name=name]', row).value.trim(), role: $('[name=role]', row).value, email: $('[name=email]', row).value.trim() };
      }).filter(function (p) { return p.name; });
    }
    return data;
  }

  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form.m5-form');
    if (!form) return;
    ev.preventDefault();
    var conf = form.getAttribute('data-confirm');
    if (conf && !window.confirm(conf)) return;
    var data = collect(form), btn = ev.submitter || $('button[type=submit]', form);
    var extra = btn && btn.getAttribute('data-extra');
    if (extra) { var kv = extra.split('='); data[kv[0]] = kv.slice(1).join('='); }
    busy(btn, true);
    call(form.getAttribute('data-api'), data).then(function (res) {
      var target = form.getAttribute('data-result');
      if (target && $(target) && typeof res.text === 'string') {
        $(target).value = res.text;
        (res.warnings || []).forEach(function (w) { toast(w, true); });
        toast('Projet établi : relisez-le, corrigez-le puis enregistrez-le.');
      } else { toast(res && typeof res.message === 'string' ? res.message : 'Enregistré.'); }
      if (form.hasAttribute('data-reload')) {
        var go = res && res.id && form.getAttribute('data-api') === 'm500/conflicts/check' ? '?id=' + encodeURIComponent(res.id) : '';
        setTimeout(function () { if (go) { location.href = location.pathname + go; } else { location.reload(); } }, 500);
      }
    }).catch(function (e) { toast(e.message, true); }).then(function () { busy(btn, false); });
  });

  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('.m5-export');
    if (!b) return;
    ev.preventDefault(); busy(b, true);
    call(b.getAttribute('data-api')).then(function (res) {
      var d = res.download; if (!d) throw new Error('Rien à exporter.');
      var url = URL.createObjectURL(new Blob([d.text], { type: d.mime + ';charset=utf-8' }));
      var a = document.createElement('a'); a.href = url; a.download = d.filename; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
    }).catch(function (e) { toast(e.message, true); }).then(function () { busy(b, false); });
  });

  $$('.m5-file').forEach(function (inp) {
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0]; if (!f) return;
      var target = $('[name=' + inp.getAttribute('data-target') + ']', inp.form);
      if (f.size > 2000000) { toast('Fichier trop volumineux (2 Mo maximum).', true); return; }
      var rd = new FileReader();
      rd.onload = function () { if (target) target.value = String(rd.result || ''); };
      rd.readAsText(f, 'utf-8');
    });
  });

  /* Dictée locale (même passerelle que l'éditeur de courriels) */
  $$('.m5-dictate').forEach(function (btn) {
    var recorder = null, stream = null, chunks = [], timer = null, label = btn.textContent;
    function stop() { if (recorder && recorder.state === 'recording') recorder.stop(); }
    btn.addEventListener('click', function () {
      if (recorder && recorder.state === 'recording') { stop(); return; }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) { toast('Micro indisponible : saisissez vos notes au clavier.', true); return; }
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
        stream = s; chunks = [];
        var mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].filter(function (x) { return MediaRecorder.isTypeSupported(x); })[0];
        if (!mime) throw new Error('Format audio non géré par ce navigateur.');
        recorder = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 64000 });
        recorder.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
        recorder.onstop = function () {
          clearTimeout(timer); stream.getTracks().forEach(function (t) { t.stop(); });
          btn.textContent = label; btn.setAttribute('aria-pressed', 'false');
          toast('Transcription locale en cours…');
          fetch(prefix + '/dictation', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': mime.split(';')[0], 'X-CSRF-Token': csrf }, body: new Blob(chunks, { type: mime }) })
            .then(function (r) { return r.json().then(function (j) { if (!r.ok || typeof j.text !== 'string') throw new Error('x'); return j.text; }); })
            .then(function (text) {
              var t = $(btn.getAttribute('data-target'));
              if (!text.trim()) { toast('Rien n’a été entendu.', true); return; }
              if (t) t.value = (t.value ? t.value.replace(/\s+$/, '') + '\n' : '') + text.trim();
            }).catch(function () { toast('Transcription locale non obtenue ; vos notes sont intactes.', true); });
        };
        recorder.start(1000); btn.textContent = '⏹ Arrêter'; btn.setAttribute('aria-pressed', 'true'); timer = setTimeout(stop, 120000);
      }).catch(function (e) { toast(e.message && e.message !== 'Permission denied' ? e.message : 'Micro non démarré : vérifiez l’autorisation du navigateur.', true); });
    });
  });

  /* Prescription : points de départ selon la règle, calcul sans enregistrer */
  var lf = $('#lim-form');
  if (lf) {
    var rules = {}; try { rules = JSON.parse(lf.getAttribute('data-rules') || '{}'); } catch (x) { rules = {}; }
    var rs = $('#lim-rule'), ev = $('#lim-event'), out = $('#lim-result');
    var url = new URLSearchParams(location.search).get('start_event');
    function fill() {
      while (ev.firstChild) ev.removeChild(ev.firstChild);
      (rules[rs.value] || []).forEach(function (s) {
        var o = document.createElement('option'); o.value = s.key; o.textContent = s.label; ev.appendChild(o);
      });
      if (url) { ev.value = url; url = null; }
    }
    rs.addEventListener('change', fill); fill();
    function add(tag, text, cls) { var n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; out.appendChild(n); return n; }
    var pv = $('#lim-preview');
    if (pv) pv.addEventListener('click', function () {
      var d = collect(lf);
      var q = new URLSearchParams({ rule: d.rule || '', start: d.start || '', start_event: d.start_event || '' });
      busy(pv, true);
      call('m500/limitation/preview?' + q.toString()).then(function (c) {
        while (out.firstChild) out.removeChild(out.firstChild);
        add('p', 'Dernier jour utile : ' + (c.due_label || c.due || '—')).classList.add('m5-strong');
        var ol = add('ol', '', 'm5-steps'); (c.steps || []).forEach(function (s) { var li = document.createElement('li'); li.textContent = s; ol.appendChild(li); });
        [['warnings', 'Attention', 'm5-warn'], ['to_confirm', 'Reste à confirmer', 'm5-confirm']].forEach(function (k) {
          if (c[k[0]] && c[k[0]].length) { var box = add('div', '', k[2]); var st = document.createElement('strong'); st.textContent = k[1]; box.appendChild(st);
            var ul = document.createElement('ul'); c[k[0]].forEach(function (s) { var li = document.createElement('li'); li.textContent = s; ul.appendChild(li); }); box.appendChild(ul); }
        });
        if (c.prudence) add('p', c.prudence, 'vf-note');
      }).catch(function (e) { while (out.firstChild) out.removeChild(out.firstChild); add('p', e.message, 'm5-warn'); }).then(function () { busy(pv, false); });
    });
  }
})();
