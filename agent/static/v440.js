/* AxiorHub 4.4.0 - atelier : courriels, documents, réglages. Aucun HTML non maîtrisé n'est injecté. */
(function () {
  'use strict';
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  var csrf = (document.querySelector('meta[name="axiorhub-csrf"]') || {}).content || '';
  var $ = function (s, r) { return (r || document).querySelector(s); };

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

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k]; else n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { n.appendChild(c); });
    return n;
  }

  /* ------------------------------------------------------------ courriels */
  var drafts = $('#ax-drafts');
  if (drafts) {
    var validity = drafts.getAttribute('data-validity');
    var editor = $('#ax-editor');
    var current = null;
    var original = null;

    $('#ax-filter').addEventListener('input', function (ev) {
      var q = ev.target.value.toLowerCase();
      document.querySelectorAll('.ax-row').forEach(function (r) {
        r.hidden = q && r.getAttribute('data-search').indexOf(q) < 0;
      });
    });
    $('#ax-refresh').addEventListener('click', function () { location.reload(); });
    $('#ax-rows').addEventListener('click', function (ev) {
      var row = ev.target.closest('.ax-row');
      if (!row) return;
      if (dirty() && !confirm('Vous avez des modifications non enregistrées. Les abandonner ?')) return;
      document.querySelectorAll('.ax-row').forEach(function (r) { r.classList.remove('on'); });
      row.classList.add('on');
      openDraft(row.getAttribute('data-uid'));
    });
    // 5.6.5 : « Ouvrir dans Courriels à relire » ouvre directement le brouillon demandé (?uid=…)
    var wanted = new URLSearchParams(location.search).get('uid');
    if (wanted && /^\d{1,12}$/.test(wanted)) {
      var target = document.querySelector('.ax-row[data-uid="' + wanted + '"]');
      if (target) target.click();
    }

    function dirty() {
      if (!current) return false;
      return ['to', 'cc', 'bcc', 'subject', 'body'].some(function (k) {
        var f = $('#f-' + k);
        return f && f.value !== original[k];
      });
    }
    window.addEventListener('beforeunload', function (ev) {
      if (dirty()) { ev.preventDefault(); ev.returnValue = ''; }
    });

    function field(label, id, value, tag, hint) {
      var input = tag === 'textarea' ? el('textarea', { id: id, rows: 12 }) : el('input', { id: id, type: 'text' });
      input.value = value || '';
      var l = el('label', { 'for': id }, [el('span', { text: label })]);
      if (hint) l.appendChild(el('small', { text: hint }));
      var wrap = el('div', { 'class': 'ax-field' }, [l, input]);
      return wrap;
    }

    function openDraft(uid) {
      editor.textContent = '';
      editor.appendChild(el('p', { 'class': 'ax-muted ax-pad', text: 'Ouverture du brouillon…' }));
      api('draft?' + new URLSearchParams({ uid: uid, validity: validity }).toString()).then(render).catch(function (err) {
        editor.textContent = '';
        editor.appendChild(el('p', { 'class': 'ax-alert', text: err.message }));
      });
    }

    function render(d) {
      current = d;
      original = { to: d.to, cc: d.cc, bcc: d.bcc, subject: d.subject, body: d.body };
      editor.textContent = '';
      var head = el('div', { 'class': 'ax-ed-head' }, [
        el('h2', { text: d.subject || '(sans objet)' }),
        el('p', { 'class': 'ax-muted', text: (d.agent_key ? 'Préparé par l’agent · ' : '') + 'Dossier « ' + d.folder + ' » · de ' + d.from })
      ]);
      editor.appendChild(head);
      if (!d.editable) {
        editor.appendChild(el('p', { 'class': 'ax-alert', text: 'Ce brouillon contient un élément qui ne peut pas être conservé à l’identique (message joint ou structure particulière). Corrigez-le dans la messagerie.' }));
      }
      if (d.lossy) {
        editor.appendChild(el('p', { 'class': 'ax-note', text: 'Ce brouillon contient une mise en forme (HTML). En l’enregistrant ici, il sera conservé en texte simple.' }));
      }
      var grid = el('div', { 'class': 'ax-ed-grid' }, [
        field('À', 'f-to', d.to, 'input'),
        field('Copie (Cc)', 'f-cc', d.cc, 'input'),
        field('Copie cachée (Cci)', 'f-bcc', d.bcc, 'input')
      ]);
      editor.appendChild(grid);
      editor.appendChild(field('Objet', 'f-subject', d.subject, 'input'));
      editor.appendChild(field('Message', 'f-body', d.body, 'textarea'));

      if (d.attachments && d.attachments.length) {
        editor.appendChild(el('p', { 'class': 'ax-muted', text: 'Pièces jointes conservées : ' + d.attachments.map(function (a) { return a.filename; }).join(', ') }));
      }

      /* aide de l'IA */
      var ai = el('div', { 'class': 'ax-ai' }, [el('strong', { text: 'Demander à l’IA de corriger ce texte' })]);
      var chips = el('div', { 'class': 'ax-chips' });
      ['Plus court', 'Plus ferme', 'Plus courtois', 'Demander une confirmation écrite', 'Ajouter un rappel de la date'].forEach(function (c) {
        var b = el('button', { type: 'button', 'class': 'ax-chip', text: c });
        b.addEventListener('click', function () { $('#f-instr').value = c; runAssist(); });
        chips.appendChild(b);
      });
      ai.appendChild(chips);
      var instr = el('input', { id: 'f-instr', type: 'text', placeholder: 'Ou décrivez la correction : « ajoute que je suis disponible le 12 », « tutoie »…' });
      var go = el('button', { type: 'button', 'class': 'ax-btn ghost', id: 'f-assist', text: 'Reformuler' });
      go.addEventListener('click', runAssist);
      ai.appendChild(el('div', { 'class': 'ax-ai-row' }, [instr, go]));
      var suggestion = el('div', { id: 'f-sugg', 'class': 'ax-sugg', hidden: 'hidden' });
      ai.appendChild(suggestion);
      editor.appendChild(ai);

      /* courriel reçu */
      if (d.source) {
        var det = el('details', { 'class': 'ax-source' }, [
          el('summary', { text: 'Courriel auquel ce brouillon répond — ' + (d.source.sender || '') }),
          el('p', { 'class': 'ax-muted', text: d.source.subject + ' · ' + String(d.source.date).slice(0, 16).replace('T', ' ') }),
          el('pre', { text: d.source.text || '' })
        ]);
        det.open = true;
        editor.appendChild(det);
      }

      var bar = el('div', { 'class': 'ax-bar-actions' });
      var save = el('button', { type: 'button', 'class': 'ax-btn', id: 'f-save', text: 'Enregistrer le brouillon' });
      var undo = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Annuler mes modifications' });
      var drop = el('button', { type: 'button', 'class': 'ax-btn danger', text: 'Écarter ce brouillon' });
      save.addEventListener('click', saveDraft);
      undo.addEventListener('click', function () { render(current); });
      drop.addEventListener('click', discard);
      if (!d.editable) save.disabled = true;
      bar.appendChild(save); bar.appendChild(undo); bar.appendChild(drop);
      // 5.6.25 : relancer une réponse complète (sources du dossier) avec une instruction, si le courriel d'origine est retrouvé
      if (d.source && d.source.key && window.axh5625Relaunch) {
        var again = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Relancer la réponse avec une instruction…' });
        again.addEventListener('click', function () { window.axh5625Relaunch(d.source.key, again); });
        bar.appendChild(again);
      }
      bar.appendChild(el('span', { 'class': 'ax-muted', text: 'L’envoi se fait depuis votre messagerie : le brouillon y sera à jour.' }));
      editor.appendChild(bar);
    }

    function saveDraft() {
      var btn = $('#f-save');
      btn.disabled = true; btn.textContent = 'Enregistrement…';
      api('draft/save', {
        uid: current.uid, uidvalidity: current.uidvalidity, revision: current.revision,
        to: $('#f-to').value, cc: $('#f-cc').value, bcc: $('#f-bcc').value,
        subject: $('#f-subject').value, body: $('#f-body').value
      }).then(function (r) {
        toast('Brouillon enregistré et relu dans la messagerie.');
        current.uid = r.uid;
        return api('draft?' + new URLSearchParams({ uid: r.uid, validity: current.uidvalidity }).toString()).then(function (d) {
          var row = document.querySelector('.ax-row.on');
          if (row) { row.setAttribute('data-uid', d.uid); row.querySelector('strong').textContent = d.subject.slice(0, 110); }
          render(d);
        });
      }).catch(function (err) {
        toast(err.message, true);
        btn.disabled = false; btn.textContent = 'Enregistrer le brouillon';
      });
    }

    function discard() {
      if (!confirm('Écarter ce brouillon ? Il sera déplacé dans la corbeille de la messagerie.')) return;
      api('draft/discard', { uid: current.uid, uidvalidity: current.uidvalidity, confirm: 'yes' }).then(function () {
        toast('Brouillon placé dans la corbeille.');
        var row = document.querySelector('.ax-row.on'); if (row) row.remove();
        current = null; editor.textContent = '';
        editor.appendChild(el('div', { 'class': 'ax-empty' }, [el('h2', { text: 'Brouillon écarté' })]));
      }).catch(function (err) { toast(err.message, true); });
    }

    function runAssist() {
      var instr = $('#f-instr').value.trim();
      if (!instr) { toast('Indiquez la correction souhaitée.', true); return; }
      var box = $('#f-sugg'); box.hidden = false; box.textContent = 'L’IA travaille… (quelques secondes à quelques minutes selon le modèle)';
      var b = $('#f-assist'); b.disabled = true;
      api('draft/assist', { instruction: instr, body: $('#f-body').value, source_key: (current.source || {}).key || '' }).then(function (r) {
        var tries = 0;
        (function poll() {
          api('assist?thread=' + encodeURIComponent(r.thread)).then(function (s) {
            if (s.text) { showSuggestion(s.text); b.disabled = false; return; }
            if (s.error) { box.textContent = 'L’IA n’a pas pu répondre : ' + s.error; b.disabled = false; return; }
            if (++tries > 120) { box.textContent = 'Pas de réponse pour l’instant. Réessayez plus tard.'; b.disabled = false; return; }
            setTimeout(poll, 2500);
          }).catch(function (err) { box.textContent = err.message; b.disabled = false; });
        })();
      }).catch(function (err) { box.textContent = err.message; b.disabled = false; });
    }

    function showSuggestion(text) {
      var box = $('#f-sugg'); box.textContent = '';
      box.appendChild(el('strong', { text: 'Proposition de l’IA' }));
      box.appendChild(el('pre', { text: text }));
      var use = el('button', { type: 'button', 'class': 'ax-btn', text: 'Remplacer mon texte' });
      var drop = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Ignorer' });
      use.addEventListener('click', function () { $('#f-body').value = text; box.hidden = true; toast('Texte remplacé — relisez puis enregistrez.'); });
      drop.addEventListener('click', function () { box.hidden = true; });
      box.appendChild(el('div', { 'class': 'ax-actions' }, [use, drop]));
    }
  }

  /* ------------------------------------------------------------ documents */
  document.querySelectorAll('[data-analyze]').forEach(function (b) {
    b.addEventListener('click', function () {
      b.disabled = true;
      api('notice/analyze', { path: b.getAttribute('data-analyze'), matter: b.getAttribute('data-matter') }).then(function () {
        toast('Analyse lancée : l’agenda et le brouillon d’information seront mis à jour dans quelques instants.');
      }).catch(function (e) { toast(e.message, true); b.disabled = false; });
    });
  });

  /* ------------------------------------------------------------- réglages */
  function diagList(target, checks) {
    target.textContent = '';
    checks.forEach(function (c) {
      var row = el('div', { 'class': 'ax-check-row ' + (c.ok ? 'ok' : 'ko') }, [
        el('strong', { text: (c.ok ? '✔ ' : '✖ ') + c.name }),
        el('span', { text: c.detail || '' })
      ]);
      if (!c.ok && c.fix) row.appendChild(el('em', { text: 'Que faire : ' + c.fix }));
      target.appendChild(row);
    });
  }
  var save = $('#of-save');
  if (save) {
    save.addEventListener('click', function () {
      api('settings/office', {
        server_url: $('#of-server').value, secret: $('#of-secret').value,
        callback_base: $('#of-callback').value, engine: $('#of-engine').value
      }).then(function () { $('#of-secret').value = ''; toast('Réglages enregistrés. Lancez le test de connexion.'); })
        .catch(function (e) { toast(e.message, true); });
    });
    $('#of-diag').addEventListener('click', function () {
      var t = $('#of-result'); t.textContent = 'Test en cours…';
      api('office/diagnostic').then(function (d) { diagList(t, d.checks); }).catch(function (e) { t.textContent = e.message; });
    });
    $('#of-clear').addEventListener('click', function () {
      if (!confirm('Retirer le secret JWT enregistré ?')) return;
      api('settings/office', { server_url: $('#of-server').value, callback_base: $('#of-callback').value, engine: $('#of-engine').value, clear_secret: true })
        .then(function () { toast('Secret retiré.'); }).catch(function (e) { toast(e.message, true); });
    });
  }
  var nt = $('#nt-save');
  if (nt) {
    nt.addEventListener('click', function () {
      api('settings/notices', { enabled: $('#nt-enabled').checked, calendar: $('#nt-cal').checked })
        .then(function () { toast('Réglages enregistrés.'); }).catch(function (e) { toast(e.message, true); });
    });
    document.querySelectorAll('.mp-save').forEach(function (b) {
      b.addEventListener('click', function () {
        var tr = b.closest('tr');
        api('settings/matter', {
          matter: tr.getAttribute('data-matter'), role: $('.mp-role', tr).value,
          partner_name: $('.mp-name', tr).value, partner_email: $('.mp-mail', tr).value
        }).then(function () { toast('Rôle enregistré.'); }).catch(function (e) { toast(e.message, true); });
      });
    });
  }

  window.AxiorHub440 = { api: api, toast: toast, diagList: diagList, el: el };
})();
