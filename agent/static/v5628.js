/* AxiorHub 5.6.28 — messagerie de « Courriels › Boîte de réception » : répondre, répondre à tous, transférer, nouveau message
   (À, Cc, Cci, mise en forme, pièces jointes), archiver, déplacer, indésirable, supprimer (corbeille), lu / non lu, suivi.
   L'envoi passe par l'aperçu puis la confirmation du circuit d'envoi existant. Le seul HTML inséré dans l'éditeur est celui
   renvoyé par le serveur pour la citation, déjà nettoyé et échappé. */
(function () {
  'use strict';
  if (window.axh5628) return;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) { box = el('div', { id: 'ax-toast', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(box); }
    var t = el('div', { 'class': 'ax-toast' + (bad ? ' bad' : ''), text: message });
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 5000);
  }
  function api(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    };
    return fetch(prefix + '/api440/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) { if (d && d.error) throw new Error(d.message || d.error); return d; });
  }
  function busy(b, on) { if (b) { b.disabled = on; b.setAttribute('aria-busy', on ? 'true' : 'false'); } }
  var inflight = {};

  // ------------------------------------------------------------------ actions sur un courriel
  function act(c, m, action, cible, button) {
    var key = action + ':' + m.uid;
    if (inflight[key]) return;
    if (action === 'trash' && !window.confirm('Déplacer ce courriel dans la corbeille ? Il reste récupérable dans la messagerie.')) return;
    inflight[key] = true; busy(button, true);
    api('m5628/action', { action: action, uid: m.uid, dossier: m.folder || c.folder, cible: cible || '' }).then(function (r) {
      toast(r.message);
      if (['lu', 'non_lu', 'suivi', 'non_suivi'].indexOf(action) < 0) { c.clear(); c.reload(); }
    }).catch(function (e) { toast(e.message, true); }).then(function () { delete inflight[key]; busy(button, false); });
  }

  function toolbar(m, c) {
    var bar = el('div', { 'class': 'bx5628-bar', role: 'toolbar', 'aria-label': 'Actions sur le courriel' });
    var mk = function (label, title, fn, cls) {
      var b = el('button', { type: 'button', 'class': 'bx5628-btn' + (cls ? ' ' + cls : ''), title: title, 'aria-label': title, text: label });
      b.addEventListener('click', function () { fn(b); });
      bar.appendChild(b); return b;
    };
    mk('↩ Répondre', 'Répondre', function () { compose('repondre', m, c); }, 'primary');
    mk('↩↩ Répondre à tous', 'Répondre à tous', function () { compose('repondre_tous', m, c); });
    mk('↪ Transférer', 'Transférer', function () { compose('transferer', m, c); });
    mk('✨ Répondre avec l’IA', 'Répondre avec l’IA (brouillon préparé par l’agent, même pour un courriel ignoré)', function () {
      var ask = document.getElementById('bx5625-ask');
      if (ask) { ask.scrollIntoView({ behavior: 'smooth', block: 'start' }); var s = ask.querySelector('select'); if (s) s.focus(); }
    }, 'ai');
    bar.appendChild(el('span', { 'class': 'bx5628-sep', 'aria-hidden': 'true' }));
    if (c.role !== 'archive') mk('🗄 Archiver', 'Archiver', function (b) { act(c, m, 'archive', '', b); });
    var move = el('select', { 'class': 'bx5628-move', 'aria-label': 'Déplacer vers un dossier' }, [el('option', { value: '', text: '📁 Déplacer…' })]);
    (c.folders || []).forEach(function (f) { if (f.name !== (m.folder || c.folder)) move.appendChild(el('option', { value: f.name, text: f.label })); });
    move.addEventListener('change', function () { if (move.value) act(c, m, 'move', move.value, move); move.value = ''; });
    bar.appendChild(move);
    if (c.role !== 'junk') mk('⚠ Indésirable', 'Message indésirable', function (b) { act(c, m, 'junk', '', b); });
    if (c.role !== 'trash') mk('🗑 Supprimer', 'Supprimer (corbeille)', function (b) { act(c, m, 'trash', '', b); }, 'danger');
    bar.appendChild(el('span', { 'class': 'bx5628-sep', 'aria-hidden': 'true' }));
    mk('✉ Non lu', 'Marquer comme non lu', function (b) { act(c, m, 'non_lu', '', b); });
    mk('⚑ Suivi', 'Marquer pour suivi', function (b) { act(c, m, 'suivi', '', b); });
    return bar;
  }

  // ------------------------------------------------------------------ rédaction
  var TITLES = { nouveau: 'Nouveau message', repondre: 'Répondre', repondre_tous: 'Répondre à tous', transferer: 'Transférer' };
  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var r = new FileReader();
      r.onload = function () { resolve({ name: file.name, type: file.type || 'application/octet-stream', data: String(r.result).split(',')[1] || '' }); };
      r.onerror = function () { reject(new Error('Pièce jointe illisible : ' + file.name)); };
      r.readAsDataURL(file);
    });
  }

  function compose(mode, m, c) {
    var q = new URLSearchParams({ mode: mode });
    if (m) { q.set('uid', m.uid); if (m.folder || c.folder) q.set('dossier', m.folder || c.folder); }
    c.panel.textContent = '';
    c.panel.appendChild(el('p', { 'class': 'ax-muted ax-pad', text: 'Préparation…' }));
    api('m5628/modele?' + q.toString()).then(function (t) { render(t, c); }).catch(function (e) {
      c.panel.textContent = ''; c.panel.appendChild(el('p', { 'class': 'ax-alert', role: 'alert', text: e.message }));
    });
  }

  function render(t, c) {
    var state = { draftUid: '' };
    var panel = c.panel;
    panel.textContent = '';
    var form = el('form', { 'class': 'bx5628-compose', novalidate: 'novalidate' });
    var field = function (label, name, value, hidden) {
      var input = el('input', { name: name, type: 'text', autocomplete: 'off', value: value || '', 'aria-label': label });
      var row = el('label', { 'class': 'bx5628-field' + (hidden ? ' is-hidden' : '') }, [el('span', { text: label }), input]);
      if (hidden) row.hidden = true;
      return row;
    };
    // 5.6.32 : la copie cachée automatique (Paramètres › Messagerie) est visible et modifiable
    var to = field('À', 'to', t.to), cc = field('Cc', 'cc', t.cc, !t.cc), bcc = field('Cci', 'bcc', t.bcc, !t.bcc), subject = field('Objet', 'subject', t.subject);
    var toggles = el('div', { 'class': 'bx5628-toggles' });
    [['Cc', cc], ['Cci', bcc]].forEach(function (p) {
      if (!p[1].hidden) return;
      var b = el('button', { type: 'button', 'class': 'bx5628-link', text: '+ ' + p[0] });
      b.addEventListener('click', function () { p[1].hidden = false; b.remove(); $('input', p[1]).focus(); });
      toggles.appendChild(b);
    });
    form.appendChild(el('div', { 'class': 'bx5628-compose-head' }, [el('h2', { text: TITLES[t.mode] || 'Message' }), el('span', { 'class': 'ax-muted', text: 'De : ' + t.from })]));
    form.appendChild(to); form.appendChild(toggles); form.appendChild(cc); form.appendChild(bcc); form.appendChild(subject);
    // barre de mise en forme
    var editor = el('div', { 'class': 'bx5628-editor', contenteditable: 'true', role: 'textbox', 'aria-multiline': 'true', 'aria-label': 'Texte du message' });
    editor.innerHTML = t.html || '<p><br></p>';
    if (t.font) editor.style.cssText = t.font;   // 5.6.32 : police choisie (posée aussi par le serveur sur le message envoyé)
    var fmt = el('div', { 'class': 'bx5628-fmt', role: 'toolbar', 'aria-label': 'Mise en forme' });
    [['B', 'bold', 'Gras'], ['I', 'italic', 'Italique'], ['U', 'underline', 'Souligné'], ['S', 'strikeThrough', 'Barré'], ['• Liste', 'insertUnorderedList', 'Liste à puces'],
     ['1. Liste', 'insertOrderedList', 'Liste numérotée'], ['❝ Citation', 'formatBlock:blockquote', 'Citation'], ['🔗 Lien', 'link', 'Insérer un lien'],
     ['⌫ Format', 'removeFormat', 'Effacer la mise en forme']].forEach(function (b) {
      var btn = el('button', { type: 'button', title: b[2], 'aria-label': b[2], text: b[0], 'class': 'bx5628-fbtn' });
      btn.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
      btn.addEventListener('click', function () {
        editor.focus();
        if (b[1] === 'link') {
          var href = window.prompt('Adresse du lien (https://… ou mailto:…)', 'https://');
          if (href && /^(https?:|mailto:)/i.test(href.trim())) document.execCommand('createLink', false, href.trim());
        } else if (b[1].indexOf('formatBlock:') === 0) document.execCommand('formatBlock', false, b[1].split(':')[1]);
        else document.execCommand(b[1], false, null);
      });
      fmt.appendChild(btn);
    });
    form.appendChild(fmt); form.appendChild(editor);
    // pièces jointes : celles du message transféré et celles ajoutées
    var keep = [];
    if (t.attachments && t.attachments.length) {
      var box = el('div', { 'class': 'bx5628-files' }, [el('strong', { text: 'Pièces jointes du message transféré' })]);
      t.attachments.forEach(function (a) {
        var cb = el('input', { type: 'checkbox', value: String(a.index) }); cb.checked = true; keep.push(cb);
        box.appendChild(el('label', { 'class': 'm5-check' }, [cb, el('span', { text: ' ' + a.name + ' · ' + Math.max(1, Math.round(a.size / 1024)) + ' Ko' })]));
      });
      form.appendChild(box);
    }
    var files = el('input', { type: 'file', multiple: 'multiple', 'aria-label': 'Joindre des fichiers' });
    form.appendChild(el('label', { 'class': 'bx5628-attach' }, [el('span', { text: '📎 Joindre des fichiers (15 Mo au total)' }), files]));
    var sendBtn = el('button', { type: 'submit', 'class': 'ax-btn', text: 'Envoyer' });
    var saveBtn = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Enregistrer le brouillon' });
    var cancel = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Fermer' });
    form.appendChild(el('div', { 'class': 'bx5628-actions' }, [sendBtn, saveBtn, cancel,
      el('span', { 'class': 'ax-muted', text: 'Enregistré d’abord dans Brouillons ; l’envoi demande une confirmation.' })]));
    var confirmBox = el('div', { 'class': 'bx5628-confirm', hidden: 'hidden', role: 'alertdialog', 'aria-label': 'Confirmer l’envoi' });
    form.appendChild(confirmBox);
    panel.appendChild(form);
    (t.to ? editor : $('input', to)).focus();

    function payload() {
      return Promise.all(Array.prototype.map.call(files.files || [], readFile)).then(function (list) {
        return { mode: t.mode, source_uid: t.source_uid || '', source_folder: t.source_folder || '', to: $('input', to).value, cc: $('input', cc).value,
                 bcc: $('input', bcc).value, subject: $('input', subject).value, html: editor.innerHTML, draft_uid: state.draftUid,
                 keep: keep.filter(function (k) { return k.checked; }).map(function (k) { return k.value; }), files: list };
      });
    }
    cancel.addEventListener('click', function () { c.clear(); });
    saveBtn.addEventListener('click', function () {
      if (inflight.save) return; inflight.save = true; busy(saveBtn, true);
      payload().then(function (p) { return api('m5628/brouillon', p); }).then(function (r) { state.draftUid = r.uid; toast(r.message); })
        .catch(function (e) { toast(e.message, true); }).then(function () { delete inflight.save; busy(saveBtn, false); });
    });
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (!($('input', to).value + $('input', cc).value + $('input', bcc).value).trim()) { $('input', to).focus(); toast('Indiquez au moins un destinataire.', true); return; }
      if (inflight.send) return; inflight.send = true; busy(sendBtn, true);
      payload().then(function (p) { return api('m5628/envoyer', p); }).then(function (r) {
        state.draftUid = (r.draft || {}).uid || state.draftUid;
        if (!r.token) { showRefusal(r); return; }
        showConfirm(r);
      }).catch(function (e) { toast(e.message, true); }).then(function () { delete inflight.send; busy(sendBtn, false); });
    });

    function showRefusal(r) {
      confirmBox.textContent = ''; confirmBox.hidden = false; confirmBox.className = 'bx5628-confirm bad';
      confirmBox.appendChild(el('p', { text: r.message }));
      if (r.reason === 'envoi_desactive' || r.reason === 'smtp_non_configure') {
        confirmBox.appendChild(el('p', {}, [el('a', { href: prefix + '/confort#envoi', text: 'Activer l’envoi depuis AxiorHub (Paramètres › Cabinet › Confort › Envoi depuis l’interface)' })]));
      }
    }
    function showConfirm(r) {
      var s = r.summary || {};
      confirmBox.textContent = ''; confirmBox.hidden = false; confirmBox.className = 'bx5628-confirm';
      confirmBox.appendChild(el('h3', { text: 'Confirmer l’envoi' }));
      var dl = el('dl');
      [['À', s.to], ['Cc', s.cc], ['Cci', s.bcc], ['Objet', s.subject], ['Pièces jointes', (s.attachments || []).map(function (a) { return a.filename; }).join(', ')]].forEach(function (row) {
        if (row[1]) { dl.appendChild(el('dt', { text: row[0] })); dl.appendChild(el('dd', { text: row[1] })); }
      });
      confirmBox.appendChild(dl);
      var go = el('button', { type: 'button', 'class': 'ax-btn', text: 'Confirmer l’envoi', disabled: 'disabled' });
      var back = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Revenir au message' });
      confirmBox.appendChild(el('div', { 'class': 'bx5628-actions' }, [go, back]));
      setTimeout(function () { go.disabled = false; go.focus(); }, Math.round(((r.min_delay || 1) * 1000) + 250));
      back.addEventListener('click', function () { confirmBox.hidden = true; });
      go.addEventListener('click', function () {
        if (inflight.confirm) return; inflight.confirm = true; busy(go, true);
        api('m5628/envoyer/confirmer', { token: r.token }).then(function (x) {
          toast(x.message); c.clear(); c.reload();
        }).catch(function (e) { toast(e.message, true); }).then(function () { delete inflight.confirm; busy(go, false); });
      });
    }
  }

  window.axh5628 = { toolbar: toolbar, compose: compose };

  // bouton « Nouveau message » dans la boîte de réception
  document.addEventListener('DOMContentLoaded', function () {
    var head = $('#bx5625 .bx5625-head');
    if (!head || head.querySelector('.bx5628-new')) return;
    var b = el('button', { type: 'button', 'class': 'ax-btn bx5628-new', text: '✉ Nouveau message' });
    b.addEventListener('click', function () { if (window.axh5625Ctx) compose('nouveau', null, window.axh5625Ctx()); });
    head.insertBefore(b, head.firstChild.nextSibling);
    // 5.6.32 : signature, police et règles de la messagerie
    var s = el('a', { 'class': 'ax-btn ghost bx5632-settings', href: prefix + '/parametres?rubrique=messagerie', text: '⚙ Signature et règles' });
    head.insertBefore(s, b.nextSibling);
  });
})();
