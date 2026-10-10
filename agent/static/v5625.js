/* AxiorHub 5.6.25 — Courriels : boîte de réception, réponse demandée à l'agent, réponses préparées (modifier, supprimer, relancer).
   Agenda : ajout d'un événement dans les agendas configurés. Aucun HTML venant du serveur n'est injecté : tout est construit ici. */
(function () {
  'use strict';
  if (window.__axiorhub5625) return;
  window.__axiorhub5625 = true;
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
  function when(iso) { return String(iso || '').slice(0, 16).replace('T', ' '); }
  var inflight = {};

  // ------------------------------------------------------------------ relance depuis l'éditeur de brouillon (« À relire »)
  window.axh5625Relaunch = function (key, button) {
    var instruction = window.prompt('Instruction pour la nouvelle réponse (elle sera préparée avec les sources du dossier ; l’ancienne reste dans Brouillons) :', '');
    if (instruction === null) return;
    if (inflight[key]) return;
    inflight[key] = true; busy(button, true);
    api('m5625/boite/repondre', { key: key, instruction: instruction }).then(function (r) { toast(r.message); })
      .catch(function (e) { toast(e.message, true); }).then(function () { delete inflight[key]; busy(button, false); });
  };

  // ------------------------------------------------------------------ boîte de réception
  var box = $('#bx5625');
  if (box && !box.hidden) {
    var rows = $('#bx5625-rows'), panel = $('#bx5625-panel'), pages = $('#bx5625-pages');
    var state = { page: 1, q: '', matters: null, current: null, poll: null, folder: '', role: 'inbox' };
    var qs = function (o) { if (state.folder) o.dossier = state.folder; return new URLSearchParams(o).toString(); };
    var outgoing = function () { return ['sent', 'drafts', 'outbox'].indexOf(state.role) >= 0; };
    var loadMatters = function () {
      if (state.matters) return Promise.resolve(state.matters);
      return api('m567/matters').then(function (d) { state.matters = d.matters || []; return state.matters; }).catch(function () { state.matters = []; return []; });
    };
    var list = function () {
      rows.textContent = ''; pages.textContent = ''; rows.appendChild(el('p', { 'class': 'ax-muted ax-pad', text: 'Lecture du dossier…' }));
      api('m5625/boite/liste?' + qs({ page: state.page, q: state.q })).then(function (d) {
        rows.textContent = '';
        if (!d.items.length) rows.appendChild(el('p', { 'class': 'ax-muted ax-pad', text: state.q ? 'Aucun courriel ne correspond.' : 'Ce dossier est vide.' }));
        d.items.forEach(function (m) {
          var top = el('span', { 'class': 'ax-row-top' }, [el('strong', { text: m.subject })]);
          if (m.attachments) top.appendChild(el('em', { 'class': 'ax-badge', title: 'Pièces jointes probables', text: 'PJ' }));
          if (m.status_label) top.appendChild(el('em', { 'class': 'ax-badge bx5625-st-' + m.status, text: m.status_label }));
          var row = el('button', { type: 'button', 'class': 'ax-row' + (m.seen ? '' : ' bx5625-unseen'), 'data-uid': m.uid },
            [top, el('span', { 'class': 'ax-row-sub', text: outgoing() ? 'À : ' + (m.to || '—') : m.from }), el('span', { 'class': 'ax-row-date', text: when(m.date) })]);
          row.addEventListener('click', function () {
            Array.prototype.forEach.call(rows.querySelectorAll('.ax-row'), function (r) { r.classList.remove('on'); });
            row.classList.add('on'); open(m.uid);
          });
          rows.appendChild(row);
        });
        pages.textContent = '';
        if (d.pages > 1) {
          var prev = el('button', { type: 'button', 'class': 'ax-btn ghost', text: '← Plus récents' }), next = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Plus anciens →' });
          prev.disabled = state.page <= 1; next.disabled = state.page >= d.pages;
          prev.addEventListener('click', function () { state.page -= 1; list(); });
          next.addEventListener('click', function () { state.page += 1; list(); });
          pages.appendChild(prev); pages.appendChild(el('span', { 'class': 'ax-muted', text: 'Page ' + d.page + ' / ' + d.pages + ' · ' + d.total + ' courriel(s)' })); pages.appendChild(next);
        }
      }).catch(function (e) { rows.textContent = ''; rows.appendChild(el('p', { 'class': 'ax-alert', role: 'alert', text: e.message })); });
    };
    $('.bx5625-search', box).addEventListener('submit', function (ev) {
      ev.preventDefault(); state.q = ev.target.q.value.trim(); state.page = 1; list();
    });

    var replyList = function (m) {
      var wrap = el('div', { 'class': 'bx5625-replies' }, [el('h3', { text: 'Réponses préparées (' + m.replies.length + ')' })]);
      if (!m.replies.length) wrap.appendChild(el('p', { 'class': 'ax-muted', text: 'Aucune réponse dans Brouillons pour ce courriel.' }));
      m.replies.forEach(function (r) {
        var edit = el('a', { 'class': 'ax-btn ghost', href: prefix + '/courriels?uid=' + encodeURIComponent(r.uid), text: 'Modifier' });
        var drop = el('button', { type: 'button', 'class': 'ax-btn danger', text: 'Supprimer' });
        var again = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Relancer avec mon instruction' });
        drop.addEventListener('click', function () {
          if (!window.confirm('Supprimer cette réponse ? Elle sera déplacée dans la corbeille de votre messagerie (récupérable).')) return;
          busy(drop, true);
          api('m5625/boite/supprimer', { uid: r.uid, uidvalidity: r.uidvalidity }).then(function (x) { toast(x.message); open(m.uid); })
            .catch(function (e) { toast(e.message, true); busy(drop, false); });
        });
        again.addEventListener('click', function () {
          var field = $('#bx5625-instruction');
          if (!field.value.trim()) { field.focus(); toast('Écrivez votre commentaire ou votre instruction dans « Instruction à l’agent », puis relancez.', true); return; }
          ask(again);
        });
        wrap.appendChild(el('div', { 'class': 'bx5625-reply' }, [
          el('div', {}, [el('strong', { text: r.subject }), el('span', { 'class': 'ax-muted', text: ' · ' + when(r.date) + (r.agent ? ' · préparée par l’agent' : '') })]),
          el('div', { 'class': 'ax-actions' }, [edit, again, drop])]));
      });
      return wrap;
    };

    var ask = function (button) {
      var m = state.current; if (!m) return;
      var matter = $('#bx5625-matter').value, instruction = $('#bx5625-instruction').value.trim(), pj = $('#bx5625-pj').checked;
      if (!matter) { $('#bx5625-matter').focus(); toast('Choisissez un dossier, « Sans dossier », ou créez un nouveau dossier.', true); return; }
      if (matter === '__nouveau__') { toast('Remplissez puis validez « Créer le dossier » d’abord.', true); return; }
      var none = matter === '__aucun__';
      if (inflight[m.uid]) return;
      inflight[m.uid] = true; busy(button, true);
      api('m5625/boite/repondre', { uid: m.uid, dossier: m.folder || state.folder, matter: none ? '' : matter, sans_dossier: none ? 'oui' : '',
                                    instruction: instruction, pieces_jointes: pj }).then(function (r) {
        toast(r.message);
        var count = m.replies.length, tries = 0;
        clearInterval(state.poll);
        state.poll = setInterval(function () {   // la réponse apparaît dans « Réponses préparées » dès qu'elle est déposée
          if (++tries > 40 || !state.current || state.current.uid !== m.uid) { clearInterval(state.poll); return; }
          api('m5625/boite/courriel?' + qs({ uid: m.uid })).then(function (fresh) {
            if (fresh.replies.length > count) { clearInterval(state.poll); toast('Réponse préparée : elle est dans « À relire ».'); render(fresh); }
          }).catch(function () {});
        }, 10000);
      }).catch(function (e) { toast(e.message, true); }).then(function () { delete inflight[m.uid]; busy(button, false); });
    };

    var render = function (m) {
      state.current = m;
      panel.textContent = '';
      var head = el('div', { 'class': 'bx5625-mhead' }, [el('h2', { text: m.subject || '(sans objet)' }),
        el('p', { 'class': 'ax-muted', text: 'De : ' + m.from + ' · ' + when(m.date) }),
        el('p', { 'class': 'ax-muted', text: 'À : ' + m.to + (m.cc ? ' · Cc : ' + m.cc : '') }),
        el('p', {}, [el('em', { 'class': 'ax-badge', text: m.status_label })])]);
      panel.appendChild(head);
      var body = el('pre', { 'class': 'bx5625-text', text: m.text + (m.truncated ? '\n\n[… texte tronqué à l’affichage]' : '') });
      panel.appendChild(body);
      if (m.attachments.length) {
        var pj = el('div', { 'class': 'bx5625-pj' }, [el('h3', { text: 'Pièces jointes (' + m.attachments.length + ')' })]);
        m.attachments.forEach(function (a) {
          var read = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Lire le texte' });
          var out = el('pre', { 'class': 'bx5625-text', hidden: 'hidden' });
          read.addEventListener('click', function () {
            if (!out.hidden) { out.hidden = true; return; }
            busy(read, true);
            api('m5625/boite/piece?' + qs({ uid: m.uid, index: a.index })).then(function (d) {
              out.textContent = d.text + (d.truncated ? '\n[…]' : ''); out.hidden = false;
            }).catch(function (e) { toast(e.message, true); }).then(function () { busy(read, false); });
          });
          pj.appendChild(el('div', { 'class': 'bx5625-pjrow' }, [el('span', { text: a.name + ' · ' + Math.max(1, Math.round(a.size / 1024)) + ' Ko' }), read]));
          pj.appendChild(out);
        });
        panel.appendChild(pj);
      }
      panel.appendChild(replyList(m));
      if (m.own) {
        panel.appendChild(el('p', { 'class': 'ax-muted', text: 'Courriel envoyé par le cabinet : pas de réponse à préparer.' }));
        return;
      }
      var select = el('select', { id: 'bx5625-matter' }, [el('option', { value: '', text: 'Choisir le dossier…' }),
        el('option', { value: '__aucun__', text: 'Sans dossier (réponse à partir du courriel et de ses pièces jointes)' }),
        el('option', { value: '__nouveau__', text: '＋ Créer un nouveau dossier…' })]);
      var creator = el('div', { 'class': 'bx5625-new', hidden: 'hidden' });
      loadMatters().then(function (all) {
        var group = el('optgroup', { label: 'Dossiers du cabinet' });
        all.forEach(function (x) { var o = el('option', { value: x.id, text: x.label }); if (x.id === m.matter) o.selected = true; group.appendChild(o); });
        select.appendChild(group);
      });
      var senderName = String(m.from || '').replace(/<[^>]*>/, '').replace(/"/g, '').trim();
      var showCreator = function () {
        creator.textContent = ''; creator.hidden = false;
        var client = el('input', { maxlength: '120', value: senderName.indexOf('@') < 0 ? senderName : '', placeholder: 'Nom du client' });
        var title = el('input', { maxlength: '120', value: String(m.subject || '').replace(/^(re|tr|fwd?|réf)\s*:\s*/ig, '').slice(0, 80), placeholder: 'Affaire (facultatif)' });
        var ref = el('input', { maxlength: '80', pattern: '[A-Za-z0-9_-]{1,80}' }), parent = el('input', { maxlength: '1000' });
        var link = el('input', { type: 'checkbox' }); link.checked = true;
        var make = el('button', { type: 'button', 'class': 'ax-btn', text: 'Créer le dossier' });
        var cancel = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Annuler' });
        api('m5625/boite/nouveau-dossier').then(function (p) { ref.value = p.reference; parent.value = p.parent; }).catch(function () {});
        cancel.addEventListener('click', function () { creator.hidden = true; select.value = ''; });
        make.addEventListener('click', function () {
          if (inflight.creer) return;
          inflight.creer = true; busy(make, true);
          api('m5625/boite/creer-dossier', { client_name: client.value, title: title.value, reference: ref.value, parent: parent.value,
                                              correspondent: link.checked ? String((String(m.from).match(/<([^>]+)>/) || [0, m.from])[1]).trim() : '' })
            .then(function (r) {
              toast(r.message); state.matters = null;
              var o = el('option', { value: r.id, text: r.label }); select.appendChild(o); select.value = r.id; creator.hidden = true;
            }).catch(function (e) { toast(e.message, true); }).then(function () { delete inflight.creer; busy(make, false); });
        });
        creator.appendChild(el('h4', { text: 'Nouveau dossier' }));
        creator.appendChild(el('label', {}, [el('span', { text: 'Client' }), client]));
        creator.appendChild(el('label', {}, [el('span', { text: 'Affaire' }), title]));
        creator.appendChild(el('div', { 'class': 'bx5625-new-row' }, [el('label', {}, [el('span', { text: 'Référence' }), ref]),
          el('label', {}, [el('span', { text: 'Emplacement (Nextcloud)' }), parent])]));
        creator.appendChild(el('label', { 'class': 'm5-check' }, [link, el('span', { text: ' Rattacher l’expéditeur comme client de ce dossier' })]));
        creator.appendChild(el('p', { 'class': 'ax-muted', text: 'Le répertoire « Client - Affaire - Référence » est créé à cet emplacement puis enregistré ; rien n’est déplacé.' }));
        creator.appendChild(el('div', { 'class': 'ax-actions' }, [make, cancel]));
        client.focus();
      };
      select.addEventListener('change', function () { if (select.value === '__nouveau__') showCreator(); else creator.hidden = true; });
      var instr = el('textarea', { id: 'bx5625-instruction', rows: '3', maxlength: '4000', placeholder: 'Exemple : réponds que nous acceptons le report au 15 et demande la pièce 4 ; ton cordial.' });
      var pjBox = el('input', { type: 'checkbox', id: 'bx5625-pj' }); pjBox.checked = true;
      var go = el('button', { type: 'button', 'class': 'ax-btn', text: 'Demander une réponse à l’agent' });
      go.addEventListener('click', function () { ask(go); });
      panel.appendChild(el('div', { 'class': 'bx5625-ask' }, [el('h3', { text: 'Demander une réponse' }),
        el('label', {}, [el('span', { text: 'Dossier' + (m.matter_reason ? ' (' + m.matter_reason + ')' : '') }), select]), creator,
        el('label', {}, [el('span', { text: 'Instruction à l’agent (facultative)' }), instr]),
        el('label', { 'class': 'm5-check' }, [pjBox, el('span', { text: ' Utiliser le texte des pièces jointes' })]),
        el('div', { 'class': 'ax-actions' }, [go, el('span', { 'class': 'ax-muted', text: 'La réponse est déposée dans Brouillons et contrôlée ; rien n’est envoyé.' })])]));
    };
    var open = function (uid) {
      clearInterval(state.poll);
      panel.textContent = ''; panel.appendChild(el('p', { 'class': 'ax-muted ax-pad', text: 'Ouverture du courriel…' }));
      api('m5625/boite/courriel?' + qs({ uid: uid })).then(render)
        .catch(function (e) { panel.textContent = ''; panel.appendChild(el('p', { 'class': 'ax-alert', role: 'alert', text: e.message })); });
    };
    // dossiers de la messagerie : Boîte de réception, Brouillons, Envoyés, Corbeille, sous-dossiers…
    var flist = $('#bx5625-flist'), title = $('#bx5625-title');
    var ICONS = { inbox: '📥', drafts: '📝', sent: '📤', outbox: '📮', archive: '🗄', junk: '⚠', trash: '🗑' };
    var choose = function (f, button) {
      Array.prototype.forEach.call(flist.querySelectorAll('.bx5625-folder'), function (b) { b.removeAttribute('aria-current'); });
      if (button) button.setAttribute('aria-current', 'true');
      state.folder = f.role === 'inbox' ? '' : f.name; state.role = f.role || ''; state.page = 1; state.q = ''; state.current = null;
      clearInterval(state.poll);
      $('.bx5625-search', box).q.value = '';
      title.textContent = f.label;
      panel.textContent = '';
      panel.appendChild(el('div', { 'class': 'ax-empty' }, [el('h2', { text: 'Choisissez un courriel' }),
        el('p', { text: 'Texte, pièces jointes, réponses déjà préparées ; pour un courriel reçu, vous pouvez demander une réponse à l’agent. Rien n’est envoyé.' })]));
      list();
    };
    api('m5625/boite/dossiers').then(function (d) {
      flist.textContent = '';
      d.folders.forEach(function (f) {
        var b = el('button', { type: 'button', 'class': 'bx5625-folder', title: f.name },
          [el('span', { 'aria-hidden': 'true', text: ICONS[f.role] || '📁' }), el('span', { text: f.label })]);
        if (f.role === 'inbox') b.setAttribute('aria-current', 'true');
        b.addEventListener('click', function () { choose(f, b); });
        flist.appendChild(b);
      });
    }).catch(function (e) { flist.textContent = ''; flist.appendChild(el('p', { 'class': 'ax-muted', text: 'Dossiers indisponibles : ' + e.message })); });
    list();
  }

  // ------------------------------------------------------------------ agenda : agendas cibles du formulaire d'ajout
  var agenda = $('#ag5625');
  if (agenda) {
    var holder = $('[data-ag5625-targets]', agenda), hidden = $('input[name=targets]', agenda), submit = $('button[type=submit]', agenda);
    var sync = function () {
      hidden.value = Array.prototype.map.call(agenda.querySelectorAll('.ag5625-target:checked'), function (c) { return c.value; }).join(',');
      submit.disabled = !hidden.value;
    };
    api('m5625/agenda/cibles').then(function (d) {
      holder.textContent = '';
      if (!d.targets.length) {
        holder.appendChild(el('span', { text: 'Aucun agenda configuré : ajoutez-en un dans Paramètres › Agendas (Nextcloud, CalDAV ou Google).' }));
        submit.disabled = true; return;
      }
      d.targets.forEach(function (t, i) {
        var box = el('input', { type: 'checkbox', 'class': 'ag5625-target', value: t.id });
        box.checked = i === 0;
        box.addEventListener('change', sync);
        holder.appendChild(el('label', { 'class': 'm5-check' }, [box, el('span', { text: ' ' + t.label + (t.provider === 'google' && !t.details ? ' (titre neutre : détails non transmis à Google)' : '') })]));
      });
      sync();
    }).catch(function (e) { holder.textContent = e.message; submit.disabled = true; });
    var form = $('.ag5625-form', agenda);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (!form.elements.title.value.trim()) { form.elements.title.focus(); toast('Indiquez l’intitulé de l’événement.', true); return; }
      sync();
      if (!hidden.value) { toast('Cochez au moins un agenda.', true); return; }
      var data = { title: form.elements.title.value, date: form.elements.date.value, start: form.elements.start.value, end: form.elements.end.value,
                   all_day: form.elements.all_day.checked, location: form.elements.location.value, matter: form.elements.matter.value,
                   description: form.elements.description.value, targets: hidden.value };
      if (inflight.agenda) return;
      inflight.agenda = true; busy(submit, true);
      api('m5625/agenda/creer', data).then(function (r) { toast(r.message); setTimeout(function () { location.reload(); }, 1200); })
        .catch(function (e) { toast(e.message, true); }).then(function () { delete inflight.agenda; busy(submit, false); });
    });
    var allDay = $('input[name=all_day]', agenda);
    allDay.addEventListener('change', function () {
      ['start', 'end'].forEach(function (n) { $('input[name=' + n + ']', agenda).disabled = allDay.checked; });
    });
  }
})();
