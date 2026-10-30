/* AxiorHub 5.6.81 — fournisseurs d'IA : bouton « Charger les modèles » ; le tarif connu d'un modèle se remplit quand on le choisit.
   La liste vient du fournisseur (aucune donnée de dossier). Tout le texte passe par textContent. */
(function () {
  'use strict';
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.getAttribute('content') : ''; }
  var lists = {};
  function fill(form, model) {
    var button = form.querySelector('[data-m81-load]');
    var row = button && (lists[button.getAttribute('data-m81-load')] || []).find(function (x) { return x.id === model; });
    if (!row || row.entree == null) return;
    var a = form.querySelector('[name="input_usd_per_million"]'), b = form.querySelector('[name="output_usd_per_million"]');
    if (a) a.value = row.entree;
    if (b) b.value = row.sortie;
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('[data-m81-load]');
    if (!b) return;
    ev.preventDefault();
    var provider = b.getAttribute('data-m81-load'), box = b.parentElement, note = box.querySelector('[data-m81-note]'), list = box.querySelector('datalist');
    var form = b.closest('form');
    b.disabled = true; note.textContent = 'Interrogation du fournisseur…';
    fetch(meta('axiorhub-prefix') + '/api440/m5681/modeles', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') }, body: JSON.stringify({ provider: provider, actualiser: true }) })
      .then(function (r) { return r.json().then(function (v) { if (!r.ok) throw new Error(v.message || v.error || 'Liste indisponible.'); return v; }); })
      .then(function (v) {
        lists[provider] = v.modeles || [];
        list.replaceChildren();
        lists[provider].forEach(function (m) { var o = document.createElement('option'); o.value = m.id; if (m.label !== m.id) o.label = m.label; list.append(o); });
        var input = form && form.querySelector('[name="model"]');
        if (input) { input.setAttribute('list', list.id); fill(form, input.value); }
        var names = lists[provider].slice(0, 6).map(function (m) { return m.id; }).join(', ');
        note.textContent = input ? lists[provider].length + ' modèle(s) disponible(s) : choisissez dans le champ Modèle.'
          : lists[provider].length + ' modèle(s) installé(s)' + (names ? ' : ' + names : '') + (lists[provider].length > 6 ? '…' : '.');
      }).catch(function (e) { note.textContent = e.message; }).finally(function () { b.disabled = false; });
  });
  document.addEventListener('change', function (ev) {
    if (ev.target.name === 'model' && ev.target.form && ev.target.form.querySelector('[data-m81-load]')) fill(ev.target.form, ev.target.value);
  });
})();
