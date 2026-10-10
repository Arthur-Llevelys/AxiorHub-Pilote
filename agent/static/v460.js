/* AxiorHub 4.6.0 - cockpit (touches 1 à 5) et décisions sur les faits. Aucun HTML venant du serveur n'est injecté.
   5.6.24 : analyse du dossier suivie jusqu'à son résultat (F25/F26), reprise des seules étapes en erreur, correction structurée
   d'un fait (intitulé, texte, date confirmée, révision attendue : F27). */
(function () {
  'use strict';
  if (window.__axiorhub460) return;
  window.__axiorhub460 = true;
  var prefix = (document.querySelector('meta[name="axiorhub-prefix"]') || {}).content || '';
  var csrf = (document.querySelector('meta[name="axiorhub-csrf"]') || {}).content || '';
  var $ = function (s, r) { return (r || document).querySelector(s); };

  /* Cockpit : touches 1 à 5 ouvrent l'action principale de la carte. */
  var cards = document.querySelectorAll('[data-ck-main]');
  if (cards.length) {
    document.addEventListener('keydown', function (ev) {
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      var t = ev.target;
      if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
      var n = parseInt(ev.key, 10);
      if (n >= 1 && n <= cards.length) { ev.preventDefault(); cards[n - 1].click(); }
    });
  }

  function toast(message, bad) {
    var box = $('#ax-toast');
    if (!box) { if (bad) window.alert(message); return; }
    var el = document.createElement('div');
    el.className = 'ax-toast' + (bad ? ' bad' : '');
    el.textContent = message;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, bad ? 9000 : 4500);
  }
  function post(name, body) {
    return fetch(prefix + '/api440/' + name, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body)
    }).then(function (r) { return r.json().catch(function () { return { error: 'x', message: 'Réponse inattendue.' }; }); })
      .then(function (d) { if (d && d.error) throw new Error(d.message || d.error); return d; });
  }

  /* Analyse suivie : l'état est relu jusqu'à la fin du travail, puis la page est rechargée (aucune hypothèse sur la durée). */
  var analysis = $('#fc-analysis');
  var factsBox = $('#faits');
  var matter = analysis ? analysis.getAttribute('data-matter') : (factsBox ? factsBox.getAttribute('data-matter') : '');
  var result = $('#fc-analyse-result');
  var polling = false;
  function follow(label, key) {
    if (polling || !matter) return;
    polling = true;
    var tries = 0;
    (function tick() {
      post('fiche/etat', { matter: matter }).then(function (st) {
        var busy = key === 'scan_job' ? st.scan_job : st.job;
        if (result) result.textContent = busy ? label + ' en cours…' : '';
        if (busy && tries++ < 360) { setTimeout(tick, 5000); return; }
        polling = false;
        toast(busy ? 'Toujours en cours : rechargez la page plus tard.' : 'Terminé : état « ' + st.overall_label + ' ».');
        if (!busy) setTimeout(function () { location.reload(); }, 700);
      }).catch(function (e) { polling = false; toast(e.message, true); });
    })();
  }
  if (analysis) {
    if (analysis.getAttribute('data-job')) follow('Analyse', 'job');
    analysis.addEventListener('click', function (ev) {
      var btn = ev.target.closest('button');
      if (!btn || btn.disabled) return;
      if (btn.id === 'fc-analyse' || btn.id === 'fc-reprendre') {
        btn.disabled = true;
        var body = { matter: matter };
        if (btn.id === 'fc-reprendre') body.steps = btn.getAttribute('data-steps') || '';
        post('fiche/analyse', body).then(function (d) {
          if (result) result.textContent = d.message;
          follow('Analyse', 'job');
        }).catch(function (e) { toast(e.message, true); btn.disabled = false; });
      }
    });
  }

  if (!factsBox) return;
  var inflight = false;
  factsBox.addEventListener('click', function (ev) {
    var btn = ev.target.closest('button');
    if (!btn || inflight) return;
    if (btn.id === 'fc-scan') {
      btn.disabled = true;
      post('fact/scan', { matter: matter }).then(function (d) {
        $('#fc-scan-result').textContent = d.message || 'Recherche lancée.';
        follow('Recherche des faits', 'scan_job');
      }).catch(function (e) { toast(e.message, true); btn.disabled = false; });
      return;
    }
    var item = btn.closest('.fc-fact');
    if (!item) return;
    var id = item.getAttribute('data-id'), revision = item.getAttribute('data-revision') || '';
    var done = function (msg) { inflight = false; toast(msg); setTimeout(function () { location.reload(); }, 600); };
    var fail = function (e) { inflight = false; toast(e.message, true); };
    if (btn.classList.contains('fc-validate')) {
      inflight = true;
      post('fact/validate', { matter: matter, id: id, revision: revision }).then(function () { done('Fait validé.'); }).catch(fail);
    } else if (btn.classList.contains('fc-correct')) {
      var box = $('.fc-editor', item);
      if (box.hidden) { box.hidden = false; $('.fc-edit', item).focus(); btn.textContent = 'Enregistrer la correction'; return; }
      var text = $('.fc-edit', item).value.trim(), title = $('.fc-edit-title', item).value.trim(), when = $('.fc-edit-date', item).value.trim();
      if (!text) { toast('Le texte corrigé est vide.', true); return; }
      if (when && !/^\d{4}(-\d{2}(-\d{2})?)?$/.test(when)) { toast('Date attendue : AAAA-MM-JJ, AAAA-MM ou AAAA.', true); return; }
      inflight = true;
      post('fact/validate', { matter: matter, id: id, text: text, title: title, event_date: when, revision: revision })
        .then(function () { done('Fait corrigé et validé : la chronologie et la synthèse utiliseront cette version.'); }).catch(fail);
    } else if (btn.classList.contains('fc-reject')) {
      inflight = true;
      post('fact/reject', { matter: matter, id: id, revision: revision }).then(function () { done('Fait refusé : il ne sera plus utilisé.'); }).catch(fail);
    }
  });
})();
