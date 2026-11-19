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
    if (c.role === 'drafts') mk('✎ Modifier', 'Modifier ce brouillon (le prochain enregistrement le remplace)', function () { compose('brouillon', m, c); }, 'primary');   // 5.6.106 (lot R)
    mk('↩ Répondre', 'Répondre', function () { compose('repondre', m, c); }, c.role === 'drafts' ? '' : 'primary');
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
    if (window.axh56106routine) window.axh56106routine.bouton(bar, m, c);   // 5.6.106 (lot O) : « Créer une routine à partir du message »
    return bar;
  }

  // ------------------------------------------------------------------ rédaction
  var TITLES = { nouveau: 'Nouveau message', repondre: 'Répondre', repondre_tous: 'Répondre à tous', transferer: 'Transférer', reprise: 'Brouillon' };
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
    if (m && m.uidvalidity) q.set('uidvalidity', String(m.uidvalidity));   // 5.6.108 (lot C, B02) : génération du message affiché, jamais complétée par le serveur
    var avant = c.panel.querySelector('form.bx5628-compose');   // 5.6.108 (lot C, B01) : rédaction remplacée : dernières frappes enregistrées d'abord
    if (avant && avant.axhAuto) avant.axhAuto.quitter();
    c.panel.textContent = '';
    c.panel.appendChild(el('p', { 'class': 'ax-muted ax-pad', text: 'Préparation…' }));
    api('m5628/modele?' + q.toString()).then(function (t) { render(t, c); }).catch(function (e) {
      c.panel.textContent = ''; c.panel.appendChild(el('p', { 'class': 'ax-alert', role: 'alert', text: e.message }));
    });
  }

  function render(t, c) {
    var state = { draftUid: t.draft_uid || '', draftValidity: t.draft_uidvalidity || '' };
    var panel = c.panel;
    var R = window.axh56106r || null;              // 5.6.106 (lot R) : dictée, mise en forme étendue, adresses, pièces, modèles (v56106-redaction.js)
    panel.textContent = '';
    var form = el('form', { 'class': 'bx5628-compose' + (R ? ' bx56106r-r' : ''), novalidate: 'novalidate' });
    var field = function (label, name, value, hidden) {
      var input = el('input', { name: name, type: 'text', autocomplete: 'off', value: value || '', 'aria-label': label });
      var row = el('label', { 'class': 'bx5628-field' + (hidden ? ' is-hidden' : '') }, [el('span', { text: label }), input]);
      if (hidden) row.hidden = true;
      return row;
    };
    // 5.6.32 : la copie cachée automatique (Paramètres › Messagerie) est visible et modifiable
    var to = field('À', 'to', t.to), cc = field('Cc', 'cc', t.cc, !t.cc), bcc = field('Cci', 'bcc', t.bcc, !t.bcc), subject = field('Objet', 'subject', t.subject);
    // 5.6.106 (lot R) : Cc et Cci repliés derrière un seul « Cc / Cci » quand ils sont vides (ouverts d'office quand une règle les préremplit)
    var toggles = el('div', { 'class': 'bx5628-toggles' });
    if (cc.hidden || bcc.hidden) {
      var ccb = el('button', { type: 'button', 'class': 'bx5628-link', text: cc.hidden && bcc.hidden ? 'Cc / Cci' : (cc.hidden ? '+ Cc' : '+ Cci'),
                               title: 'Afficher les champs Copie et Copie cachée', 'aria-expanded': 'false' });
      ccb.addEventListener('click', function () {
        var first = cc.hidden ? cc : bcc;
        cc.hidden = false; bcc.hidden = false; ccb.remove(); $('input', first).focus();
        if (R) R.ajuster(form);
      });
      if (R) to.appendChild(ccb); else toggles.appendChild(ccb);
    }
    form.appendChild(el('div', { 'class': 'bx5628-compose-head' }, [el('h2', { text: TITLES[t.mode] || 'Message' }), el('span', { 'class': 'ax-muted', text: 'De : ' + t.from })]));
    form.appendChild(to); form.appendChild(toggles); form.appendChild(cc); form.appendChild(bcc); form.appendChild(subject);
    var editor = el('div', { 'class': 'bx5628-editor', contenteditable: 'true', role: 'textbox', 'aria-multiline': 'true', 'aria-label': 'Texte du message' });
    if (R) R.remplir(editor, t.html || '<p><br></p>');   // 5.6.106 (lot R) : styles gardés posés par CSSOM (politique de sécurité)
    else editor.innerHTML = t.html || '<p><br></p>';
    if (t.font) editor.style.cssText = t.font;   // 5.6.32 : police choisie (posée aussi par le serveur sur le message envoyé)
    // pièces jointes : celles du message transféré, celles d'un brouillon rouvert et celles ajoutées (5.6.106 : liste unique, « ✕ » sur chacune)
    var pj = R ? R.pieces(t, toast) : null;
    var keep = [], files = null, cloud = [];
    var ajouterNextcloud = function () {
      if (!window.axh5670) return;
      window.axh5670.pickNextcloud(function (f) {
        if (pj) { pj.ajouterNextcloud(f); return; }
        if (cloud.some(function (x) { return x.chemin === f.chemin; })) return;
        if (cloud.length >= 10) { toast('10 fichiers au plus.', true); return; }
        cloud.push(f); drawChips();
      });
    };
    var lienVisio = function () {
      if (!window.axh5670) return;
      window.axh5670.visioLink().then(function (u) {
        if (R) R.inserer(editor, '\nVisioconférence : ' + u + '\n');
        else { editor.focus(); document.execCommand('insertText', false, '\nVisioconférence : ' + u + '\n'); }
      }).catch(function (e) { toast(e.message, true); });
    };
    var cloudList = el('ul', { 'class': 'c5670-chips' });
    var drawChips = function () {
      cloudList.textContent = '';
      cloud.forEach(function (f, i) {
        var x = el('button', { type: 'button', 'class': 'ax-btn ghost', text: '✕', 'aria-label': 'Retirer ' + f.nom });
        x.addEventListener('click', function () { cloud.splice(i, 1); drawChips(); });
        cloudList.appendChild(el('li', {}, [el('span', { text: '📄 ' + f.nom }), x]));
      });
    };
    if (R) {
      // 5.6.106 (lot R) : barre compacte (icônes, infobulles, menu « Plus »), dictée au curseur, adresses proposées, pièces jointes
      var barre = R.barre(editor, { jour: t.jour || '', joindre: function () { pj.choisir(); }, nextcloud: ajouterNextcloud, visio: lienVisio });
      [to, cc, bcc].forEach(function (row) { R.adresses($('input', row)); });
      R.micro($('input', subject), subject);
      form.appendChild(barre); form.appendChild(editor); form.appendChild(pj.element);
      R.cases(editor);
      R.images(editor, pj, form);   // 5.6.106 (lot R) : images collées ou déposées dans le corps (intégrées à l'envoi)
      R.deposer(form, pj);
    } else {
      // repli (script 5.6.106 absent) : barre et pièces jointes de la 5.6.28
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
      if (t.attachments && t.attachments.length) {
        var box = el('div', { 'class': 'bx5628-files' }, [el('strong', { text: 'Pièces jointes du message transféré' })]);
        t.attachments.forEach(function (a) {
          var cb = el('input', { type: 'checkbox', value: String(a.index) }); cb.checked = true; keep.push(cb);
          box.appendChild(el('label', { 'class': 'm5-check' }, [cb, el('span', { text: ' ' + a.name + ' · ' + Math.max(1, Math.round(a.size / 1024)) + ' Ko' })]));
        });
        form.appendChild(box);
      }
      files = el('input', { type: 'file', multiple: 'multiple', 'aria-label': 'Joindre des fichiers' });
      form.appendChild(el('label', { 'class': 'bx5628-attach' }, [el('span', { text: '📎 Joindre des fichiers (15 Mo au total)' }), files]));
      // 5.6.70 : documents de l'espace Nextcloud et lien de visioconférence
      var ncBtn = el('button', { type: 'button', 'class': 'ax-btn ghost', text: '📁 Joindre depuis Nextcloud' });
      ncBtn.addEventListener('click', ajouterNextcloud);
      var visioBtn = el('button', { type: 'button', 'class': 'ax-btn ghost', text: '🎥 Lien de visioconférence' });
      visioBtn.addEventListener('click', lienVisio);
      form.appendChild(el('div', { 'class': 'c5670-compose' }, [ncBtn, visioBtn, cloudList]));
    }
    // 5.6.106 (lot R) : zone « Options d'envoi », remplie par le lot E ; repliée sur une ligne dans la rédaction compacte (état résumé)
    form.appendChild(R ? R.optionsEnvoi() : el('div', { 'class': 'bx5628-options-envoi', 'data-lot': 'E' }));
    var sendBtn = el('button', { type: 'submit', 'class': 'ax-btn', text: 'Envoyer' });
    var saveBtn = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Enregistrer le brouillon' });
    var cancel = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Fermer' });
    form.appendChild(el('div', { 'class': 'bx5628-actions' }, [sendBtn, saveBtn, cancel,
      el('span', { 'class': 'ax-muted', text: 'Enregistré d’abord dans Brouillons ; l’envoi suit votre réglage (Paramètres › Messagerie).' })]));
    var confirmBox = el('div', { 'class': 'bx5628-confirm', hidden: 'hidden', role: 'alertdialog', 'aria-label': 'Confirmer l’envoi' });
    form.appendChild(confirmBox);
    panel.appendChild(form);
    // 5.6.106 (lot R) : « Annuler » plutôt que « Fermer », état de l'enregistrement automatique, curseur en tête du message
    var auto = null;
    if (R) {
      cancel.textContent = 'Annuler';
      cancel.title = 'Fermer la rédaction (le dernier brouillon enregistré reste dans Brouillons)';
      auto = R.auto(form, function () { return enregistrer(true); },
                    function () { return !inflight.save && !inflight.send && !inflight.confirm && confirmBox.hidden; });
      form.axhAuto = auto;   // 5.6.108 (lot C, B01) : la rédaction qui la remplace fait d'abord enregistrer les dernières frappes
      R.ajuster(form);
    }
    (t.to ? editor : $('input', to)).focus();
    if (R && t.to) R.curseur(editor);

    function payload() {
      var base = function (extra) {
        return Object.assign({ mode: t.mode, source_uid: t.source_uid || '', source_uidvalidity: t.source_uidvalidity || '',
                 source_folder: t.source_folder || '', to: $('input', to).value, cc: $('input', cc).value,
                 bcc: $('input', bcc).value, subject: $('input', subject).value, html: editor.innerHTML, draft_uid: state.draftUid, draft_uidvalidity: state.draftValidity,
                 reprise_uid: t.reprise_uid || '' }, extra);
      };
      if (pj) return pj.payload().then(function (x) {   // 5.6.106 (lot R) : keep, files, nextcloud et draft_keep (empreintes du brouillon)
        var p = base(x.champs);
        Object.defineProperty(p, 'instantane', { value: x.instantane, enumerable: false });
        return p;
      });
      return Promise.all(Array.prototype.map.call(files.files || [], readFile)).then(function (list) {
        return base({ keep: keep.filter(function (k) { return k.checked; }).map(function (k) { return k.value; }), files: list,
                      nextcloud: cloud.map(function (f) { return f.chemin; }) });
      });
    }
    function enregistrer(silencieux) {   // bouton « Enregistrer le brouillon » et enregistrement automatique (5.6.106)
      if (inflight.save) return Promise.reject(new Error('Enregistrement déjà en cours.'));
      var revisionAuto = auto ? auto.revision() : 0;
      inflight.save = true; busy(saveBtn, true);
      return payload().then(function (p) {
        return api('m5628/brouillon', p).then(function (r) {
          state.draftUid = r.uid;
          state.draftValidity = r.uidvalidity;
          if (pj) pj.apres(r.pieces || [], p.instantane);
          if (auto) auto.enregistre(revisionAuto);
          if (!silencieux) toast(r.message);
          return r;
        });
      }).then(function (r) { delete inflight.save; busy(saveBtn, false); return r; },
              function (e) { delete inflight.save; busy(saveBtn, false); throw e; });
    }
    cancel.addEventListener('click', function () {
      if (auto && auto.sale() && !window.confirm('Fermer sans enregistrer les dernières modifications ? Le dernier brouillon enregistré reste dans Brouillons.')) return;
      if (auto) auto.arreter();   // fermeture voulue : rien n'est enregistré après coup
      c.clear();
    });
    saveBtn.addEventListener('click', function () {
      if (inflight.save) return;
      enregistrer(false).catch(function (e) { toast(e.message, true); });
    });
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (!($('input', to).value + $('input', cc).value + $('input', bcc).value).trim()) { $('input', to).focus(); toast('Indiquez au moins un destinataire.', true); return; }
      if (inflight.send) return; inflight.send = true; busy(sendBtn, true);
      payload().then(function (p) { return api('m5628/envoyer', p); }).then(function (r) {
        state.draftUid = (r.draft || {}).uid || state.draftUid;
        state.draftValidity = (r.draft || {}).uidvalidity || state.draftValidity;
        if (!r.token) { showRefusal(r); return; }
        if (r.confirm_each === false) { sendDirect(r); return; }   // 5.6.36 : réglage « sans seconde confirmation »
        showConfirm(r);
      }).catch(function (e) { toast(e.message, true); }).then(function () { delete inflight.send; busy(sendBtn, false); });
    });

    // 5.6.106 (lot E) : la confirmation met le message dans la file d'envoi du serveur (options de la zone « Options d'envoi ») ; le
    // travailleur l'envoie après le délai d'annulation ou à l'heure programmée. « Annuler » (bandeau) remet ce même formulaire dans la
    // rédaction : le message n'a jamais quitté Brouillons. Sans v56106-envoi.js : route précédente (file d'envoi, sans option).
    var envoi = function () { return window.axh56106Envoi || null; };
    if (envoi()) envoi().equiper(form.querySelector('.bx5628-options-envoi'));
    function confirmer(r) {
      if (envoi()) return envoi().confirmer(r, form, { reprendre: reprendre });
      return api('m5628/envoyer/confirmer', { token: r.token });
    }
    function reprendre() { confirmBox.hidden = true; c.panel.textContent = ''; c.panel.appendChild(form); if (auto) auto.reprendre(); editor.focus(); }
    function fini(x) { if (auto) auto.arreter(); if (!x.bandeau) toast(x.message); c.clear(); c.reload(); }   // 5.6.108 : le brouillon en file n'est plus remplacé
    function sendDirect(r) {
      inflight.confirm = true; busy(sendBtn, true);
      toast('Envoi en cours…');
      setTimeout(function () {
        confirmer(r).then(fini)
          .catch(function (e) { toast(e.message, true); }).then(function () { delete inflight.confirm; busy(sendBtn, false); });
      }, Math.round(((r.min_delay || 1) * 1000) + 250));
    }
    function showRefusal(r) {
      confirmBox.textContent = ''; confirmBox.hidden = false; confirmBox.className = 'bx5628-confirm bad';
      confirmBox.appendChild(el('p', { text: r.message }));
      if (r.reason === 'envoi_desactive' || r.reason === 'smtp_non_configure') {
        confirmBox.appendChild(el('p', {}, [el('a', { href: prefix + '/parametres?rubrique=messagerie#envoi5636', text: 'Activer l’envoi depuis AxiorHub (Paramètres › Messagerie › Envoi depuis AxiorHub)' })]));
      }
    }
    function showConfirm(r) {
      var s = r.summary || {};
      confirmBox.textContent = ''; confirmBox.hidden = false; confirmBox.className = 'bx5628-confirm';
      confirmBox.appendChild(el('h3', { text: 'Confirmer l’envoi' }));
      var dl = el('dl');
      var opts = envoi() ? envoi().resume(form) : { lignes: [], programme: false };   // 5.6.106 (lot E) : options choisies, rappelées ici
      [['À', s.to], ['Cc', s.cc], ['Cci', s.bcc], ['Objet', s.subject], ['Pièces jointes', (s.attachments || []).map(function (a) { return a.filename; }).join(', ')]].concat(opts.lignes).forEach(function (row) {
        if (row[1]) { dl.appendChild(el('dt', { text: row[0] })); dl.appendChild(el('dd', { text: row[1] })); }
      });
      confirmBox.appendChild(dl);
      var go = el('button', { type: 'button', 'class': 'ax-btn', text: opts.programme ? 'Confirmer l’envoi programmé' : 'Confirmer l’envoi', disabled: 'disabled' });
      var back = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Revenir au message' });
      confirmBox.appendChild(el('div', { 'class': 'bx5628-actions' }, [go, back]));
      setTimeout(function () { go.disabled = false; go.focus(); }, Math.round(((r.min_delay || 1) * 1000) + 250));
      back.addEventListener('click', function () { confirmBox.hidden = true; });
      go.addEventListener('click', function () {
        if (inflight.confirm) return; inflight.confirm = true; busy(go, true);
        confirmer(r).then(fini).catch(function (e) { toast(e.message, true); }).then(function () { delete inflight.confirm; busy(go, false); });
      });
    }
  }

  // 5.6.106 (lot R) : compose('brouillon', {uid}, c) rouvre un brouillon du dossier Brouillons (signature de compose inchangée)
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
