/* 5.6.43 (cahier v9, § 12.1) : « Classement dans le dossier client » sous les en-têtes du courriel ouvert, et « Classement automatique »
   sous « Signature et règles ». Deux actions distinctes (courriel → CORRESPONDANCES, pièces jointes → NOS PIECES), autorisations générales
   ou limitées à ce courriel ; l'état affiché vient du serveur (relecture des fichiers), jamais d'un simple envoi de demande. */
(function () {
  'use strict';
  var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute('content') : ''; };
  var prefix = meta('axiorhub-prefix') || '/agent-courriel', csrf = meta('axiorhub-csrf');
  var T = function (fr) { return window.ax5634Translate ? window.ax5634Translate(fr) : fr; };

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') { n.textContent = T(attrs[k]); } else if (k === 'checked' || k === 'disabled') { n[k] = !!attrs[k]; }
      else { n.setAttribute(k, attrs[k]); }
    });
    (kids || []).forEach(function (c) { if (c) { n.appendChild(c); } });
    return n;
  }
  function call(route, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) };
    return fetch(prefix + '/api440/m5643/' + route, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok || j.error) { var e = new Error(j.message || j.error || ('HTTP ' + r.status)); e.code = j.error; throw e; }
        return j;
      });
    });
  }
  var qs = function (o) { return Object.keys(o).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); };
  var key = function () { return 'cx-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10); };

  var OVERRIDES = [['inherit', 'Suivre la règle générale'], ['allow', 'Autoriser pour ce courriel'], ['deny', 'Exclure ce courriel']];

  function badges(links) {
    var box = el('div', { 'class': 'cx5643-badges', role: 'status' });
    if (!links.length) { box.appendChild(el('span', { 'class': 'cx5643-badge', text: 'À classer' })); return box; }
    links.forEach(function (l) {
      if (l.email_requested) {
        box.appendChild(el('span', { 'class': 'cx5643-badge is-' + l.conservation_state, text: 'Courriel : ' + l.conservation_label }));
      }
      if (l.attachments_requested) {
        box.appendChild(el('span', { 'class': 'cx5643-badge is-' + l.attachments_state,
          text: 'Pièces : ' + l.attachments.verified + '/' + l.attachments.selected + ' vérifiée(s)' }));
      }
      var row = el('span', { 'class': 'cx5643-links' });
      if (l.email_requested && l.conservation_state === 'verified') {
        row.appendChild(el('a', { href: prefix + l.goto_correspondances, text: 'Ouvrir Correspondances' }));
        row.appendChild(el('a', { href: prefix + '/api440/m5643/correspondances/original?' + qs({ lien: l.link_id }), text: 'Télécharger l’original' }));
      }
      if (l.attachments.verified) { row.appendChild(el('a', { href: prefix + l.goto_pieces, text: 'Ouvrir NOS PIECES' })); }
      if (/^(queued|saving|verifying)$/.test(l.conservation_state) || /^(queued|saving|verifying)$/.test(l.attachments_state)) {
        var stop = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Arrêter' });
        stop.addEventListener('click', function () {
          stop.disabled = true;
          call('correspondances/arreter', { lien: l.link_id }).then(function (r) { alert(T(r.message)); }).catch(function (e) { alert(e.message); });
        });
        row.appendChild(stop);
      }
      box.appendChild(row);
    });
    return box;
  }

  function block(m, ctx) {
    var root = el('section', { 'class': 'cx5643', 'aria-labelledby': 'cx5643-title-' + m.uid }, [
      el('h3', { id: 'cx5643-title-' + m.uid, text: 'Classement dans le dossier client' }),
      el('p', { 'class': 'ax-muted', text: 'Chargement…' })]);
    var status = el('p', { role: 'status', 'class': 'cx5643-status' });
    var folder = (ctx && ctx.folder) || m.folder || '';
    call('correspondances/message?' + qs({ uid: m.uid, folder: folder })).then(function (s) {
      root.textContent = '';
      root.appendChild(el('h3', { id: 'cx5643-title-' + m.uid, text: 'Classement dans le dossier client' }));
      var state = { links: s.links };
      var badgeBox = badges(s.links);
      root.appendChild(badgeBox);
      if (s.direction !== 'received') {
        root.appendChild(el('p', { 'class': 'ax-muted', text: s.direction === 'sent' ? 'Message envoyé par le cabinet : il peut être conservé ; aucune réponse automatique.' : 'Sens du message à vérifier.' }));
      }
      var sel = el('select', { id: 'cx5643-matter-' + m.uid }, [el('option', { value: '', text: 'Choisir le dossier client…' })]);
      s.matters.forEach(function (x) {
        var o = el('option', { value: x.id }); o.textContent = x.label + ' (n° ' + x.id + ')';
        if (x.id === (s.rule.matter_id || s.suggested_matter || m.matter || '')) { o.selected = true; }
        sel.appendChild(o);
      });
      root.appendChild(el('label', { 'for': 'cx5643-matter-' + m.uid, text: 'Dossier client' }));
      root.appendChild(sel);
      if (s.suggested_matter) { root.appendChild(el('p', { 'class': 'ax-muted', text: 'Proposé : correspondant confirmé de ce seul dossier.' })); }
      var title = el('input', { id: 'cx5643-title-in-' + m.uid, maxlength: '200', value: s.subject || '' });
      root.appendChild(el('label', { 'for': 'cx5643-title-in-' + m.uid, text: 'Titre de classement' }));
      root.appendChild(title);
      root.appendChild(el('p', { 'class': 'ax-muted', text: 'Destinations : CORRESPONDANCES du dossier (original .eml et lecture .md) ; NOS PIECES / PJ courriels (pièces choisies).' }));

      var pj = el('fieldset', { 'class': 'cx5643-parts' }, [el('legend', { text: 'Pièces jointes' })]);
      var picks = [];
      s.parts.forEach(function (p) {
        var cb = el('input', { type: 'checkbox', id: 'cx5643-p-' + m.uid + '-' + p.part_key, checked: p.default && p.state === 'extraite',
                                disabled: p.state !== 'extraite' });
        var name = el('input', { value: p.name || ('partie-' + p.part_key), maxlength: '80', 'aria-label': T('Nom de classement de ') + (p.name || p.part_key) });
        picks.push({ cb: cb, name: name, key: p.part_key });
        pj.appendChild(el('div', { 'class': 'cx5643-part' }, [cb, el('label', { 'for': cb.id, text: (p.name || 'sans nom') + ' · ' + p.type + ' · ' +
          Math.max(1, Math.round(p.size / 1024)) + ' Ko' + (p.inline_image ? ' · image intégrée' : '') + (p.state !== 'extraite' ? ' · non extractible' : '') }), name]));
      });
      if (!s.parts.length) { pj.appendChild(el('p', { 'class': 'ax-muted', text: 'Ce courriel ne comporte aucune pièce jointe.' })); }
      root.appendChild(pj);

      var rules = el('fieldset', { 'class': 'cx5643-rules' }, [el('legend', { text: 'Classement automatique de ce courriel' })]);
      var mk = function (id, label, value) {
        var s2 = el('select', { id: id });
        OVERRIDES.forEach(function (o) { var opt = el('option', { value: o[0], text: o[1] }); if (o[0] === value) { opt.selected = true; } s2.appendChild(opt); });
        rules.appendChild(el('label', { 'for': id, text: label })); rules.appendChild(s2); return s2;
      };
      var eo = mk('cx5643-eo-' + m.uid, 'Courriel (CORRESPONDANCES)', s.rule.email_override);
      var ao = mk('cx5643-ao-' + m.uid, 'Pièces jointes (NOS PIECES)', s.rule.attachments_override);
      rules.appendChild(el('p', { 'class': 'ax-muted', text: 'Règle générale du compte : courriels ' + (s.policy.email_auto ? 'automatiques' : 'non automatiques') +
        ' ; pièces ' + (s.policy.attachments_auto ? 'automatiques' : 'non automatiques') + '.' + (s.effective.notice ? ' ' + s.effective.notice : '') }));
      var saveRule = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Enregistrer ces autorisations pour ce courriel' });
      rules.appendChild(saveRule);
      root.appendChild(rules);

      var sel_parts = function () {
        return picks.filter(function (p) { return p.cb.checked; }).map(function (p) { return { part_key: p.key, display_name: p.name.value }; });
      };
      var bEmail = el('button', { type: 'button', 'class': 'ax-btn', text: 'Enregistrer le courriel maintenant' });
      var bParts = el('button', { type: 'button', 'class': 'ax-btn', text: 'Enregistrer les pièces jointes maintenant',
                                  disabled: !s.parts.length, title: s.parts.length ? '' : T('Aucune pièce jointe dans ce courriel') });
      var bAgent = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Confier ce classement à l’IA' });
      root.appendChild(el('div', { 'class': 'cx5643-actions' }, [bEmail, bParts, bAgent]));
      root.appendChild(status);

      var source = s.source;
      var refresh = function () {
        return call('correspondances/message?' + qs({ uid: m.uid, folder: folder })).then(function (f) {
          var nb = badges(f.links); root.replaceChild(nb, badgeBox); badgeBox = nb; state.links = f.links; return f;
        });
      };
      var poll = function () {
        var n = 0;
        var t = setInterval(function () {
          refresh().then(function (f) {
            var busy = f.links.some(function (l) { return /^(queued|saving|verifying)$/.test(l.conservation_state) || /^(queued|saving|verifying)$/.test(l.attachments_state); });
            if (!busy || ++n > 40) { clearInterval(t); }
          }).catch(function () { clearInterval(t); });
        }, 3000);
      };
      var now = function (button, actions) {
        if (!sel.value) { status.textContent = T('Choisissez le dossier client.'); sel.focus(); return; }
        button.disabled = true; status.textContent = T('Préparation…');
        call('correspondances/preparer', { source: source, matter_id: sel.value, title: title.value, actions: actions,
          attachments_selection: { mode: 'selected', parts: sel_parts() } }).then(function (p) {
          return call('correspondances/confirmer', { proposal_id: p.proposal_id, expected_version: p.version, request_key: key() });
        }).then(function (r) {
          status.textContent = T('Demande enregistrée ; l’état ci-dessus repose sur la relecture des fichiers.');
          refresh(); poll();
        }).catch(function (e) { status.textContent = e.message; }).then(function () { button.disabled = false; });
      };
      bEmail.addEventListener('click', function () { now(bEmail, ['archive_email']); });
      bParts.addEventListener('click', function () {
        if (!sel_parts().length) { status.textContent = T('Cochez au moins une pièce jointe.'); return; }
        now(bParts, ['file_attachments']);
      });
      saveRule.addEventListener('click', function () {
        saveRule.disabled = true;
        call('correspondances/message-regle', { source: source, uidvalidity: source.uidvalidity, folder: source.folder, uid: source.uid,
          expected_version: s.rule.version, email_override: eo.value, attachments_override: ao.value, matter_id: sel.value }).then(function (r) {
          s.rule.version = r.version; status.textContent = T(r.effective && r.effective.notice ? r.effective.notice : 'Autorisations de ce courriel enregistrées.');
        }).catch(function (e) { status.textContent = e.message; }).then(function () { saveRule.disabled = false; });
      });
      bAgent.addEventListener('click', function () {
        var actions = [];
        if (eo.value === 'allow' || !s.parts.length || window.confirm(T('Confier aussi l’enregistrement du courriel complet ?'))) { actions.push('archive_email'); }
        if (s.parts.length && sel_parts().length) { actions.push('file_attachments'); }
        bAgent.disabled = true;
        call('correspondances/confier', { source: source, uidvalidity: source.uidvalidity, folder: source.folder, uid: source.uid, matter_id: sel.value,
          actions: actions, attachments_selection: { mode: 'selected', parts: sel_parts() } }).then(function (r) {
          status.textContent = T(r.message || 'Mandat enregistré.'); refresh(); poll();
        }).catch(function (e) { status.textContent = e.message; }).then(function () { bAgent.disabled = false; });
      });
    }).catch(function (e) {
      root.textContent = '';
      root.appendChild(el('h3', { text: 'Classement dans le dossier client' }));
      root.appendChild(el('p', { 'class': 'ax-muted', text: 'Classement indisponible : ' + e.message }));
    });
    return root;
  }

  // « Classement automatique » sous « Signature et règles » (règles générales du compte affiché)
  function generalBlock() {
    var head = document.querySelector('#bx5625 .bx5625-head');
    if (!head || head.querySelector('.cx5643-general')) { return; }
    var box = el('details', { 'class': 'cx5643-general' }, [el('summary', { text: 'Classement automatique' })]);
    var status = el('p', { role: 'status', 'class': 'cx5643-status' });
    call('correspondances/politique').then(function (p) {
      var a = p.account;
      var can = p.role === 'administrateur' || p.role === 'avocat';
      var ce = el('input', { type: 'checkbox', id: 'cx5643-ge', checked: a.email_auto, disabled: !can });
      var ca = el('input', { type: 'checkbox', id: 'cx5643-ga', checked: a.attachments_auto, disabled: !can });
      box.appendChild(el('p', { 'class': 'ax-muted', text: 'Compte de messagerie affiché, nouveaux messages seulement, rattachement confirmé à un seul dossier.' +
        (a.effective_from ? ' En vigueur depuis le ' + a.effective_from.slice(0, 10) + '.' : '') }));
      box.appendChild(el('div', {}, [ce, el('label', { 'for': 'cx5643-ge', text: 'Enregistrer automatiquement les courriels dans CORRESPONDANCES' })]));
      box.appendChild(el('div', {}, [ca, el('label', { 'for': 'cx5643-ga', text: 'Enregistrer automatiquement leurs pièces jointes dans NOS PIECES' })]));
      var save = el('button', { type: 'button', 'class': 'ax-btn', text: 'Enregistrer les règles', disabled: !can });
      if (!can) { box.appendChild(el('p', { 'class': 'ax-muted', text: 'Réservé à l’avocat ou à l’administrateur.' })); }
      save.addEventListener('click', function () {
        save.disabled = true;
        call('correspondances/politique', { scope_kind: 'account', expected_version: a.version, email_auto: ce.checked, attachments_auto: ca.checked }).then(function (r) {
          a.version = r.version; ce.checked = r.email_auto; ca.checked = r.attachments_auto; status.textContent = T(r.message);
        }).catch(function (e) { status.textContent = e.message; ce.checked = a.email_auto; ca.checked = a.attachments_auto; })
          .then(function () { save.disabled = false; });
      });
      box.appendChild(save); box.appendChild(status);
    }).catch(function (e) { box.appendChild(el('p', { 'class': 'ax-muted', text: e.message })); });
    var settings = head.querySelector('.bx5632-settings');
    head.insertBefore(box, settings ? settings.nextSibling : null);
  }

  window.axh5643 = { block: block };
  document.addEventListener('DOMContentLoaded', function () { setTimeout(generalBlock, 0); });
})();
