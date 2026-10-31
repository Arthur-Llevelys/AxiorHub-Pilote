/* 5.6.84 : panneau à côté de l'éditeur. Trois états séparés : observé, proposé, enregistré. Texte échappé par textContent ; rien de ce qui vient d'un modèle n'est interprété comme du HTML. */
(function () {
  'use strict';
  var panel = document.getElementById('ax-office-panel');
  if (!panel) return;
  var boot = JSON.parse(document.getElementById('ax-office-boot').textContent);
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  var documentId = '', csrf = (document.querySelector('meta[name="axiorhub-csrf"]') || {}).content || '';
  var fingerprint = '';

  function api(route, body) {
    var options = {credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}};
    if (body) { options.method = 'POST'; options.body = JSON.stringify(body); }
    return fetch(prefix + '/api440/m5684/' + route, options).then(function (r) { return r.json(); });
  }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function button(label, fn) { var b = el('button', 'ax-btn ghost', label); b.type = 'button'; b.addEventListener('click', fn); return b; }
  function act(route, extra) { api(route, Object.assign({document: documentId}, extra)).then(function (r) { if (r && r.error) alert(r.message || r.error); refresh(); }); }

  function render(s) {
    panel.hidden = false;
    panel.textContent = '';
    panel.appendChild(el('h2', '', 'État du document'));
    var dl = el('dl', 'o84-state');
    function row(a, b) { dl.appendChild(el('dt', '', a)); dl.appendChild(el('dd', '', b)); }
    var obs = s.observed;
    row('Observé', obs ? (obs.recent ? 'révision ' + obs.revision : 'pas d’observation récente') : 'observation inactive');
    var a = s.analysis;
    row('Analyse', !a ? 'aucune' : ({queued: 'en attente', running: 'en cours', ready: 'prête', stale: 'périmée', partial: 'partielle', cancelled: 'annulée', failed: 'échec'})[a.status] || a.status);
    var save = s.save || {};
    row('Enregistré', save.state === 'verified' ? 'à ' + String(save.at || '').slice(11, 16) + ', vérifié' : (save.label || 'aucun enregistrement'));
    panel.appendChild(dl);
    if (save.state === 'failed') panel.appendChild(el('p', 'c5637-alert', 'Échec de l’enregistrement : ' + (save.error || '') + '. Utilisez « Télécharger comme » si le problème persiste.'));
    if (save.conflict_path) panel.appendChild(el('p', 'c5637-alert', 'Conflit : une copie a été enregistrée à côté du fichier.'));
    if (s.participants > 1) panel.appendChild(el('p', 'ax-muted', s.participants + ' personnes dans le document : aucune écriture automatique.'));
    if (!s.flags.observation) { panel.appendChild(el('p', 'ax-muted', 'Observation désactivée dans Paramètres.')); return; }
    var mode = el('label', '', 'Mode ');
    var select = el('select');
    [['observe', 'Observer'], ['propose', 'Proposer'], ['draft_authorized', 'Rédiger en autonomie']].forEach(function (o) { var op = el('option', '', o[1]); op.value = o[0]; op.selected = s.mode === o[0]; select.appendChild(op); });
    select.addEventListener('change', function () { act('office/mode', {mode: select.value}); });
    mode.appendChild(select);
    panel.appendChild(mode);
    var row2 = el('p');
    row2.appendChild(button(s.paused ? 'Reprendre' : 'Pause', function () { act('office/pause', {on: s.paused ? '' : '1'}); }));
    row2.appendChild(button(s.cancelled ? 'Réactiver' : 'Annuler', function () { act(s.cancelled ? 'office/reprendre' : 'office/annuler', {}); }));
    panel.appendChild(row2);
    if (a && a.result && (a.status === 'ready' || a.status === 'partial')) {
      panel.appendChild(el('h2', '', 'Ce que cela change'));
      if (a.result.summary) panel.appendChild(el('p', '', a.result.summary));
      (a.result.impacts || []).forEach(function (i) { panel.appendChild(el('p', '', (i.needs_decision ? 'À décider : ' : '') + i.summary)); });
    }
    panel.appendChild(el('h2', '', 'Propositions'));
    if (!s.proposals.length) panel.appendChild(el('p', 'ax-muted', 'Aucune proposition en attente.'));
    s.proposals.forEach(function (p) {
      var box = el('div', 'o84-diff');
      p.operations.forEach(function (o) { var line = el('p'); line.appendChild(el('del', '', o.avant)); line.appendChild(document.createTextNode(' → ')); line.appendChild(el('ins', '', o.apres)); box.appendChild(line); });
      if (p.reason) box.appendChild(el('p', 'ax-muted', p.reason));
      if (p.state === 'proposed') {
        box.appendChild(button('Appliquer', function () { act('office/decision', {commande: p.command_id, decision: 'appliquer'}); }));
        box.appendChild(button('Rejeter', function () { act('office/decision', {commande: p.command_id, decision: 'rejeter'}); }));
      } else box.appendChild(el('p', 'ax-muted', 'Application en cours…'));
      panel.appendChild(box);
    });
    var done = s.history.filter(function (h) { return h.state === 'applied' && h.origin === 'ia'; });
    if (done.length) {
      panel.appendChild(el('h2', '', 'Appliquées'));
      done.slice(0, 5).forEach(function (h) {
        var box = el('div', 'o84-diff');
        box.appendChild(el('p', '', h.operations.map(function (o) { return o.apres; }).join(' ; ')));
        box.appendChild(button('Revenir en arrière', function () { act('office/retour', {commande: h.command_id}); }));
        panel.appendChild(box);
      });
    }
  }

  function refresh() {
    if (!documentId) return;
    api('office/panel?document=' + encodeURIComponent(documentId)).then(function (s) {
      if (!s || s.error) return;
      var mark = JSON.stringify(s);
      if (mark !== fingerprint) { fingerprint = mark; render(s); }
    }).catch(function () {});
  }
  window.addEventListener('ax-office-opened', function (ev) { documentId = ev.detail.document_id; refresh(); setInterval(refresh, 3000); });
  if (window.axOfficeOpened) { documentId = window.axOfficeOpened; refresh(); setInterval(refresh, 3000); }
  void boot;
}());
