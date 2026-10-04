/* AxiorHub 4.9.0 — confort d'usage. Aucun HTML venant du serveur n'est injecté : tout est construit nœud par nœud. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k]; else n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function toast(message, bad) {
    var box = $('#ax-toast'); if (!box) return;
    var t = el('div', { 'class': 'ax-toast' + (bad ? ' bad' : ''), text: message });
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 10000 : 5000);
  }
  function api(name, body) {
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

  /* ------------------------------------------------------------ application installable */
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register(prefix + '/sw.js', { scope: prefix + '/' }).catch(function () { /* facultatif */ });
    });
  }

  /* ------------------------------------------------------------------ raccourcis clavier */
  var NAVKEYS = { a: '/aujourdhui', c: '/courriels', d: '/documents', e: '/echeances', r: '/recherche', p: '/progres' };
  var pendingG = false, gTimer = null;
  function typing(t) { return t && (/^(input|textarea|select)$/i.test(t.tagName) || t.isContentEditable); }
  document.addEventListener('keydown', function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
      var save = $('#f-save');
      if (save && !save.disabled) { ev.preventDefault(); save.click(); }
      return;
    }
    if (ev.altKey && (ev.key === 'd' || ev.key === 'D')) {
      var mic = $('#cf-rec');
      if (mic) { ev.preventDefault(); mic.click(); }
      return;
    }
    if (ev.ctrlKey || ev.metaKey || ev.altKey || typing(ev.target)) return;
    if (ev.key === '/') {
      ev.preventDefault();
      var q = $('#cf-q');
      if (q) q.focus(); else location.href = prefix + '/recherche';
      return;
    }
    if (ev.key === '?') { location.href = prefix + '/confort#clavier'; return; }
    if (pendingG) {
      pendingG = false; clearTimeout(gTimer);
      var target = NAVKEYS[ev.key.toLowerCase()];
      if (target) { ev.preventDefault(); location.href = prefix + target; }
      return;
    }
    if (ev.key === 'g') { pendingG = true; gTimer = setTimeout(function () { pendingG = false; }, 1200); }
  });

  /* --------------------------------------------------------------------------- recherche */
  var form = $('#cf-search');
  if (form) {
    var status = $('#cf-status'), results = $('#cf-results');
    var KIND_LABEL = { courriel: 'Courriel', fichier: 'Fichier', agenda: 'Agenda', note: 'Note' };
    var dirOf = function (p) { var i = String(p).lastIndexOf('/'); return i > 0 ? String(p).slice(0, i) : '/'; };
    var hitLink = function (r) {
      var s = r.source || {}, m = encodeURIComponent(r.matter || '');
      if (r.kind === 'fichier' && r.matter) return prefix + '/documents?matter=' + m + '&dir=' + encodeURIComponent(dirOf(s.path || ''));
      if (r.kind === 'agenda') return prefix + (s.type === 'echeance' ? '/echeances' : '/planning');
      if (r.kind === 'courriel' && s.type === 'imap') return s.draft ? prefix + '/courriels' : (r.matter ? prefix + '/fiche?matter=' + m : '');
      if (r.matter) return prefix + '/fiche?matter=' + m;
      return '';
    };
    var run = function (push) {
      var q = $('#cf-q').value.trim();
      if (!q) { status.textContent = 'Tapez un mot à rechercher.'; return; }
      var kinds = $$('input[name=kind]:checked', form).map(function (i) { return i.value; });
      var params = new URLSearchParams({ q: q, matter: $('#cf-matter').value, from: $('#cf-from').value, to: $('#cf-to').value, kinds: kinds.join(',') });
      if (push) history.replaceState(null, '', '?' + params.toString());
      status.textContent = 'Recherche en cours…';
      results.textContent = '';
      var t0 = performance.now();
      api('search/query?' + params.toString()).then(function (d) {
        var shown = Math.round(performance.now() - t0);
        status.textContent = d.results.length + ' résultat(s) en ' + d.elapsed_ms + ' ms côté serveur (' + shown + ' ms au total).' + (d.note ? ' ' + d.note : '');
        var counts = el('div', { 'class': 'cf-counts' });
        Object.keys(d.counts).forEach(function (k) { if (d.counts[k]) counts.appendChild(el('span', { 'class': 'cf-kind', text: (KIND_LABEL[k] || k) + ' : ' + d.counts[k] })); });
        if (counts.childNodes.length) results.appendChild(counts);
        if (!d.results.length) {
          results.appendChild(el('p', { 'class': 'ax-muted', text: 'Aucun résultat. Essayez moins de mots, ou retirez le filtre de dossier ou de dates. La recherche ignore les accents et la casse et accepte les débuts de mots.' }));
          return;
        }
        d.results.forEach(function (r) {
          var link = hitLink(r);
          var title = link ? el('a', { href: link, text: r.title }) : el('span', { text: r.title });
          var meta = el('div', { 'class': 'cf-meta' }, [
            el('span', { 'class': 'cf-kind', text: KIND_LABEL[r.kind] || r.kind }),
            r.matter_label ? el('span', { text: 'Dossier : ' + r.matter_label }) : null,
            r.date ? el('span', { text: r.date }) : null
          ]);
          var ex = el('p', { 'class': 'cf-excerpt' });
          r.segments.forEach(function (sg) { ex.appendChild(sg.hit ? el('mark', { text: sg.t }) : document.createTextNode(sg.t)); });
          var src = (r.source || {});
          var srcLine = 'Source : ' + (src.label || '') + (src.path ? ' — ' + src.path : '') + (src.folder ? ' — dossier IMAP « ' + src.folder + ' », message ' + (src.uid || '') : '');
          results.appendChild(el('article', { 'class': 'cf-hit' }, [el('h3', {}, [title]), meta, ex, el('div', { 'class': 'cf-source', text: srcLine })]));
        });
      }).catch(function (err) { status.textContent = err.message; });
    };
    form.addEventListener('submit', function (ev) { ev.preventDefault(); run(true); });
    var init = new URLSearchParams(location.search);
    if (init.get('q')) {
      $('#cf-q').value = init.get('q');
      if (init.get('from')) $('#cf-from').value = init.get('from');
      if (init.get('to')) $('#cf-to').value = init.get('to');
      if (init.get('kinds')) { var ks = init.get('kinds').split(','); $$('input[name=kind]', form).forEach(function (i) { i.checked = ks.indexOf(i.value) >= 0; }); }
      run(false);
    }
  }

  /* ------------------------------------------------------------------ page Confort */
  function confirmAnd(msg, fn) { if (window.confirm(msg)) fn(); }
  var sendOn = $('#cf-send-on');
  if (sendOn) {
    sendOn.addEventListener('click', function () {
      if (!$('#cf-send-confirm').checked) { toast('Cochez la case de confirmation.', true); return; }
      busy(sendOn, true);
      api('send/settings', { enabled: true, confirm: 'yes' }).then(function () { location.reload(); })
        .catch(function (e) { toast(e.message, true); busy(sendOn, false); });
    });
    $('#cf-send-off').addEventListener('click', function () {
      api('send/settings', { enabled: false }).then(function () { location.reload(); }).catch(function (e) { toast(e.message, true); });
    });
  }
  $$('.cf-matter-send').forEach(function (box) {
    box.addEventListener('change', function () {
      var on = box.checked;
      var go = function () {
        api('send/matter', { matter: box.getAttribute('data-matter'), enabled: on, confirm: on ? 'yes' : '' })
          .then(function () { toast(on ? 'Envoi autorisé pour ce dossier.' : 'Envoi retiré pour ce dossier.'); })
          .catch(function (e) { box.checked = !on; toast(e.message, true); });
      };
      if (on) { if (window.confirm('Autoriser l’envoi de courriels depuis AxiorHub pour ce dossier ? Chaque envoi demandera une double confirmation.')) go(); else box.checked = false; } else go();
    });
  });
  var mailIdx = $('#cf-mailindex');
  if (mailIdx) {
    mailIdx.addEventListener('change', function () {
      api('search/mail', { enabled: mailIdx.checked }).then(function () { toast('Réglage enregistré.'); }).catch(function (e) { mailIdx.checked = !mailIdx.checked; toast(e.message, true); });
    });
    $('#cf-index-now').addEventListener('click', function () {
      api('search/index', {}).then(function () { toast('Passe d’indexation mise en file ; elle s’exécute en arrière-plan.'); }).catch(function (e) { toast(e.message, true); });
    });
    $('#cf-purge').addEventListener('click', function () {
      confirmAnd('Vider l’index de recherche des courriels, de l’agenda et des notes ? Il sera reconstruit progressivement.', function () {
        api('search/purge', { confirm: 'yes' }).then(function () { toast('Index vidé.'); location.reload(); }).catch(function (e) { toast(e.message, true); });
      });
    });
  }
  var pushOn = $('#cf-push-on');
  if (pushOn) {
    var state = $('#cf-push-state');
    var b64 = function (s) { var p = '='.repeat((4 - s.length % 4) % 4), r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')), o = new Uint8Array(r.length); for (var i = 0; i < r.length; i++) o[i] = r.charCodeAt(i); return o; };
    pushOn.addEventListener('click', function () {
      if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) { state.textContent = 'Ce navigateur ne gère pas les notifications. Sur iPhone, installez d’abord l’application sur l’écran d’accueil.'; return; }
      busy(pushOn, true);
      Notification.requestPermission().then(function (perm) {
        if (perm !== 'granted') throw new Error('Autorisation refusée par le navigateur.');
        return navigator.serviceWorker.ready;
      }).then(function (reg) {
        return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(pushOn.getAttribute('data-key')) });
      }).then(function (sub) {
        return api('mobile/subscribe', { endpoint: sub.endpoint, label: 'Appareil', confirm: 'yes' });
      }).then(function (st) { state.textContent = 'Notifications activées. Appareils enregistrés : ' + st.subscriptions + '.'; $('#cf-push-test').disabled = false; })
        .catch(function (e) { state.textContent = e.message || 'Activation impossible.'; })
        .then(function () { busy(pushOn, false); });
    });
    $('#cf-push-off').addEventListener('click', function () {
      api('mobile/unsubscribe', { all: true }).then(function () {
        if ('serviceWorker' in navigator) navigator.serviceWorker.ready.then(function (r) { return r.pushManager.getSubscription(); }).then(function (s) { if (s) s.unsubscribe(); });
        state.textContent = 'Notifications désactivées sur tous les appareils.';
      }).catch(function (e) { state.textContent = e.message; });
    });
    $('#cf-push-test').addEventListener('click', function () {
      api('mobile/test', {}).then(function (r) { state.textContent = r.sent ? 'Signal envoyé : la notification devrait apparaître dans quelques secondes.' : 'Aucun signal envoyé (' + (r.reason || 'aucun appareil') + ').'; })
        .catch(function (e) { state.textContent = e.message; });
    });
  }

  /* ------------------------------------------------------------ éditeur : dictée, envoi, messagerie */
  var editor = $('#ax-editor');
  if (editor && 'MutationObserver' in window) {
    var sendStatus = null;
    var attach = function () {
      var body = $('#f-body', editor);
      if (!body || $('#cf-tools', editor)) return;
      var snapshot = { body: body.value, to: ($('#f-to') || {}).value, cc: ($('#f-cc') || {}).value, subject: ($('#f-subject') || {}).value };
      var undo = [];
      var tools = el('div', { id: 'cf-tools' });
      var voice = el('div', { 'class': 'cf-voice', role: 'group', 'aria-label': 'Dictée vocale' });
      var rec = el('button', { type: 'button', 'class': 'ax-btn ghost cf-rec', id: 'cf-rec', 'aria-pressed': 'false', text: '🎙 Dicter', title: 'Alt + D' });
      var undoBtn = el('button', { type: 'button', 'class': 'ax-btn ghost', text: '↶ Annuler la dernière dictée', disabled: 'disabled' });
      var note = el('p', { 'class': 'cf-voice-note ax-muted', role: 'status', 'aria-live': 'polite', text: 'Dites par exemple : « ajoute que je suis disponible le 12 », « remplace rendez-vous par entretien », « enregistre ». L’envoi ne se commande pas à la voix.' });
      var warns = el('div', { 'aria-live': 'polite' });
      var manual = el('details', { 'class': 'cf-manual' }, [el('summary', { text: 'Saisir une commande au clavier (sans micro)' })]);
      var manualInput = el('input', { type: 'text', 'aria-label': 'Commande à interpréter', placeholder: 'ajoute que je suis disponible le 12' });
      manual.appendChild(manualInput);
      voice.appendChild(el('div', { 'class': 'cf-voice-row' }, [rec, undoBtn])); voice.appendChild(note); voice.appendChild(warns); voice.appendChild(manual);
      tools.appendChild(voice);

      var say = function (text, bad) { note.textContent = text; note.className = 'cf-voice-note ' + (bad ? 'ax-alert' : 'ax-muted'); };
      var setBody = function (value) {
        undo.push(body.value); body.value = value; body.dispatchEvent(new Event('input', { bubbles: true })); undoBtn.disabled = false;
      };
      var insertAtCaret = function (text) {
        var s = body.selectionStart, e = body.selectionEnd, v = body.value;
        var prev = v.slice(0, s).slice(-1);
        var sep = (prev && !/\s/.test(prev) && !/^[\s,.;:!?)]/.test(text)) ? ' ' : '';
        undo.push(v); body.value = v.slice(0, s) + sep + text + v.slice(e);
        var pos = s + sep.length + text.length; body.setSelectionRange(pos, pos); body.dispatchEvent(new Event('input', { bubbles: true })); undoBtn.disabled = false;
      };
      var handle = function (r) {
        warns.textContent = '';
        (r.warnings || []).forEach(function (w) { warns.appendChild(el('p', { 'class': 'cf-warn', role: 'alert', text: w.message })); });
        if (r.mode === 'refused') { say(r.summary, true); return; }
        if (r.mode === 'insert') { insertAtCaret(r.text); say(r.summary + ' Relisez avant d’enregistrer.'); return; }
        if (r.mode === 'replace_body') { setBody(r.new_body); say(r.summary + ' Relisez avant d’enregistrer.'); return; }
        if (r.mode === 'assist') {
          var instr = $('#f-instr'), go = $('#f-assist');
          if (instr && go) { instr.value = r.instruction; say(r.summary); go.click(); } else say('La reformulation n’est pas disponible ici.', true);
          return;
        }
        if (r.mode === 'action' && r.action === 'undo') { undoBtn.click(); return; }
        if (r.mode === 'action' && r.action === 'save') {
          var sv = $('#f-save'); if (sv && !sv.disabled) { say(r.summary); sv.click(); } else say('Ce brouillon ne peut pas être enregistré ici.', true);
        }
      };
      var interpret = function (text) {
        return api('voice/apply', { transcript: text, body: body.value }).then(handle).catch(function (e) { say(e.message, true); });
      };
      undoBtn.addEventListener('click', function () {
        if (!undo.length) return;
        body.value = undo.pop(); body.dispatchEvent(new Event('input', { bubbles: true }));
        undoBtn.disabled = !undo.length; say('Dernière modification dictée annulée.');
      });
      manualInput.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' && manualInput.value.trim()) { ev.preventDefault(); var t = manualInput.value; manualInput.value = ''; interpret(t); }
      });

      var recorder = null, stream = null, chunks = [], timer = null;
      var stop = function () { if (recorder && recorder.state === 'recording') recorder.stop(); };
      rec.addEventListener('click', function () {
        if (recorder && recorder.state === 'recording') { stop(); return; }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) { say('Micro indisponible : utilisez un navigateur récent en HTTPS, ou la saisie au clavier de la commande.', true); return; }
        navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
          stream = s; chunks = [];
          var mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].filter(function (x) { return MediaRecorder.isTypeSupported(x); })[0];
          if (!mime) throw new Error('Format audio non géré par ce navigateur.');
          recorder = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 64000 });
          recorder.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
          recorder.onstop = function () {
            clearTimeout(timer); stream.getTracks().forEach(function (t) { t.stop(); });
            rec.setAttribute('aria-pressed', 'false'); rec.classList.remove('on'); rec.textContent = '🎙 Dicter';
            say('Transcription locale en cours…');
            fetch(prefix + '/dictation', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': mime.split(';')[0], 'X-CSRF-Token': csrf }, body: new Blob(chunks, { type: mime }) })
              .then(function (r) { return r.json().then(function (j) { if (!r.ok || typeof j.text !== 'string') throw new Error('transcription'); return j.text; }); })
              .then(function (text) { if (!text.trim()) { say('Rien n’a été entendu. Réessayez.', true); return; } return interpret(text); })
              .catch(function () { say('Transcription locale non obtenue (passerelle Vocal non activée ou injoignable). Votre texte est intact.', true); });
          };
          recorder.start(1000); rec.setAttribute('aria-pressed', 'true'); rec.classList.add('on'); rec.textContent = '⏹ Arrêter';
          say('J’écoute… Parlez, puis appuyez sur « Arrêter ».');
          timer = setTimeout(stop, 60000);
        }).catch(function (e) { say(e.message && e.message !== 'Permission denied' ? e.message : 'Micro non démarré : vérifiez l’autorisation du navigateur.', true); });
      });

      /* Messagerie et envoi */
      var send = el('div', { 'class': 'cf-send', role: 'group', 'aria-label': 'Envoi' });
      var row = el('div', { 'class': 'cf-send-row' });
      var open = el('a', { 'class': 'ax-btn ghost', target: '_blank', rel: 'noopener', text: 'Ouvrir dans la messagerie', hidden: 'hidden', title: 'Ouvre ce brouillon exact dans votre messagerie ; rien n’est envoyé' });
      var sendBtn = el('button', { type: 'button', 'class': 'ax-btn', text: 'Envoyer…', hidden: 'hidden' });
      row.appendChild(open); row.appendChild(sendBtn); send.appendChild(row);
      send.appendChild(el('p', { 'class': 'cf-voice-note ax-muted', text: 'Enregistrez le brouillon avant de l’ouvrir dans la messagerie ou de l’envoyer : c’est la version enregistrée qui est utilisée.' }));
      tools.appendChild(send);
      body.closest('.ax-field').insertAdjacentElement('afterend', tools);
      var uidNow = function () { var r = $('.ax-row.on'); return r ? r.getAttribute('data-uid') : ''; };
      var validity = function () { var d = $('#ax-drafts'); return d ? d.getAttribute('data-validity') : ''; };
      api('send/link?' + new URLSearchParams({ uid: uidNow(), validity: validity() }).toString()).then(function (l) {
        if (l.url) { open.href = l.url; open.hidden = false; }
      }).catch(function () { /* lien facultatif */ });
      var known = sendStatus ? Promise.resolve(sendStatus) : api('send/status').then(function (s) { sendStatus = s; return s; });
      known.then(function (s) {
        if (!s.enabled) return;
        sendBtn.hidden = false;
        // 5.6.1 : « Envoyer… » après un enregistrement demandé par ce même bouton (30 s au plus) : la confirmation d'envoi s'ouvre.
        var pend = window.__cfSendAfterSave;
        if (pend && Date.now() - pend.at < 30000 && pend.subject === snapshot.subject && pend.body === snapshot.body) { window.__cfSendAfterSave = null; sendBtn.click(); }
      }).catch(function () { /* envoi désactivé */ });

      sendBtn.addEventListener('click', function () {
        var dirty = body.value !== snapshot.body || ($('#f-to') || {}).value !== snapshot.to || ($('#f-cc') || {}).value !== snapshot.cc || ($('#f-subject') || {}).value !== snapshot.subject;
        if (dirty) {
          // 5.6.1 : au lieu de refuser, enregistrer d'abord (c'est la version enregistrée qui part), puis ouvrir la confirmation d'envoi.
          var saveBtn = $('#f-save');
          if (!saveBtn || saveBtn.disabled) { toast('Ce brouillon ne peut pas être enregistré ici : ouvrez-le dans la messagerie pour l’envoyer.', true); return; }
          if (!confirm('Vos modifications ne sont pas encore enregistrées. Les enregistrer maintenant, puis préparer l’envoi ?')) return;
          window.__cfSendAfterSave = { at: Date.now(), subject: ($('#f-subject') || {}).value, body: body.value };
          saveBtn.click();
          return;
        }
        busy(sendBtn, true);
        api('send/prepare', { uid: uidNow(), uidvalidity: validity() }).then(function (p) { showDialog(p); })
          .catch(function (e) { toast(e.message, true); }).then(function () { busy(sendBtn, false); });
      });

      function showDialog(p) {
        var s = p.summary;
        var dlg = el('dialog', { 'class': 'cf-dialog', 'aria-labelledby': 'cf-dlg-title' });
        var dl = el('dl');
        [['À', s.to], ['Copie', s.cc], ['Copie cachée', s.bcc], ['Objet', s.subject]].forEach(function (row) {
          if (row[1]) { dl.appendChild(el('dt', { text: row[0] })); dl.appendChild(el('dd', { text: row[1] })); }
        });
        dl.appendChild(el('dt', { text: 'Pièces jointes' }));
        dl.appendChild(el('dd', { text: s.attachments.length ? s.attachments.map(function (a) { return a.filename; }).join(', ') : 'aucune' }));
        var cancel = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Annuler' });
        var ok = el('button', { type: 'button', 'class': 'ax-btn cf-danger', text: 'Confirmer l’envoi', disabled: 'disabled' });
        dlg.appendChild(el('h2', { id: 'cf-dlg-title', text: 'Confirmer l’envoi de ce message' }));
        dlg.appendChild(el('p', { 'class': 'ax-alert', role: 'alert', text: s.recipient_count + ' destinataire(s). Vérifiez les adresses et l’objet : un message envoyé ne se rappelle pas.' }));
        dlg.appendChild(dl);
        dlg.appendChild(el('pre', { text: s.preview }));
        dlg.appendChild(el('div', { 'class': 'ax-actions' }, [cancel, ok]));
        document.body.appendChild(dlg);
        var close = function () { try { dlg.close(); } catch (x) { /* déjà fermé */ } dlg.remove(); };
        cancel.addEventListener('click', close);
        dlg.addEventListener('cancel', function () { setTimeout(close, 0); });
        setTimeout(function () { ok.disabled = false; }, Math.ceil((p.min_delay || 1) * 1000) + 200);
        ok.addEventListener('click', function () {
          busy(ok, true);
          api('send/confirm', { token: p.token }).then(function (r) {
            close();
            toast(r.message, !r.copy_saved);
            var rowOn = $('.ax-row.on'); if (rowOn) rowOn.remove();
            editor.textContent = '';
            editor.appendChild(el('div', { 'class': 'ax-empty' }, [el('h2', { text: 'Message envoyé' }), el('p', { text: r.message })]));
          }).catch(function (e) { close(); toast(e.message, true); });
        });
        if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', 'open');
        cancel.focus();
      }
    };
    new MutationObserver(attach).observe(editor, { childList: true, subtree: true });
    attach();
  }
})();
