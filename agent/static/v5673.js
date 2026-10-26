/* AxiorHub 5.6.73 — page « Navigateur supervisé » : lancement, suivi de la séance, décisions (autoriser, refuser, reprendre),
   prise en main, arrêt. Tout est construit ici en texte ; aucun HTML venant du serveur n'est injecté. */
(function () {
  'use strict';
  if (window.__axiorhub5673) return;
  window.__axiorhub5673 = true;
  var root = document.getElementById('n5673');
  if (!root) return;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var live = document.getElementById('n5673-live');
  var timer = null;
  var shown = '';
  var form = root.querySelector('form[data-n5673="lancer"]');
  var STATES = { demarrage: 'Démarrage…', en_cours: 'L’agent travaille', validation: 'Votre accord est demandé', site_a_autoriser: 'Nouveau site à autoriser',
    attente_avocat: 'L’agent vous attend', main_a_l_avocat: 'Vous avez la main', termine: 'Terminé', arrete: 'Arrêté', erreur: 'Interrompu' };

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function toast(message, bad) {
    var box = document.getElementById('ax-toast');
    if (!box) { box = el('div'); box.id = 'ax-toast'; box.setAttribute('role', 'status'); document.body.appendChild(box); }
    var t = el('div', 'ax-toast' + (bad ? ' bad' : ''), message); box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 9000 : 5000);
  }
  function api(name, body) {
    var opts = body === undefined ? { credentials: 'same-origin' } : { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) };
    return fetch(prefix + '/api440/m5673/' + name, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; });
    }).then(function (d) { if (d && d.error) throw new Error(d.message || d.error); return d; });
  }
  function button(label, cls, onClick) { var b = el('button', 'ax-btn' + (cls ? ' ' + cls : ''), label); b.type = 'button'; b.addEventListener('click', onClick); return b; }
  function act(name, body) { return api(name, body).then(function (d) { if (d.message) toast(d.message); refresh(); }).catch(function (x) { toast(x.message, true); }); }

  function render(s) {
    live.textContent = '';
    if (!s) return;
    var head = el('p', 'n5673-state'); head.appendChild(el('strong', '', STATES[s.etat] || s.etat));
    if (s.adresse) head.appendChild(document.createTextNode(' · ' + s.adresse));
    live.appendChild(head);
    live.appendChild(el('p', 'ax-muted', 'Tâche : ' + s.tache));
    var running = ['termine', 'arrete', 'erreur'].indexOf(s.etat) < 0;
    if (form) form.hidden = running;   // une séance à la fois : le formulaire revient à la fin
    if (s.attente) {
      var box = el('div', 'n5673-ask'); box.setAttribute('role', 'alert');
      box.appendChild(el('p', '', s.attente.question)); box.appendChild(el('p', 'ax-muted', s.attente.description || ''));
      var bar = el('div', 'n5673-actions');
      if (s.attente.kind === 'consequence') {
        bar.appendChild(button('Autoriser', '', function () { act('decision', { id: s.id, choix: 'autoriser' }); }));
        bar.appendChild(button('Refuser', 'ghost', function () { act('decision', { id: s.id, choix: 'refuser' }); }));
      } else if (s.attente.kind === 'site') {
        bar.appendChild(button('Autoriser une fois', '', function () { act('decision', { id: s.id, choix: 'autoriser' }); }));
        bar.appendChild(button('Toujours autoriser', 'ghost', function () { act('decision', { id: s.id, choix: 'toujours' }); }));
        bar.appendChild(button('Refuser', 'ghost', function () { act('decision', { id: s.id, choix: 'refuser' }); }));
      } else {
        var input = el('input'); input.type = 'text'; input.placeholder = 'Réponse (facultatif)'; input.setAttribute('aria-label', 'Réponse à l’agent');
        bar.appendChild(input);
        bar.appendChild(button('Reprendre', '', function () { act('decision', { id: s.id, choix: 'reprendre', reponse: input.value }); }));
      }
      box.appendChild(bar); live.appendChild(box);
      var key = s.id + '|' + s.attente.question;
      if (key !== shown) { shown = key; box.scrollIntoView({ block: 'center', behavior: 'smooth' }); }   // la question ne passe pas inaperçue
    }
    if (running) {
      var tools = el('div', 'n5673-actions');
      tools.appendChild(s.main ? button('Rendre la main', '', function () { act('main', { id: s.id, prendre: '0' }); })
        : button('Prendre la main', 'ghost', function () { act('main', { id: s.id, prendre: '1' }); }));
      tools.appendChild(button('Arrêter', 'ghost', function () { act('arreter', { id: s.id }); }));
      live.appendChild(tools);
    } else {
      live.appendChild(button('Fermer le navigateur', 'ghost', function () { act('fermer', {}); }));
    }
    if (s.resume) { live.appendChild(el('h3', '', 'Résultat')); live.appendChild(el('p', 'n5673-summary', s.resume)); }
    if (s.erreur) live.appendChild(el('p', 'notice', 'Motif : ' + s.erreur));
    if (s.fichiers && s.fichiers.length) {
      live.appendChild(el('h3', '', 'Fichiers récupérés'));
      var ul = el('ul'); s.fichiers.forEach(function (f) { ul.appendChild(el('li', '', f.nom + (f.dossier ? ' (dossier ' + f.dossier + ')' : ' (sur ce poste)'))); });
      live.appendChild(ul);
    }
    if (s.pas && s.pas.length) {
      var d = el('details', 'n5673-steps'); d.appendChild(el('summary', '', 'Pas de l’agent (' + s.pas.length + ')'));
      var ol = el('ol'); s.pas.forEach(function (p) { ol.appendChild(el('li', '', p.t + ' · ' + p.a + (p.d ? ' — ' + p.d : '') + (p.u ? ' · ' + p.u : ''))); });
      d.appendChild(ol); live.appendChild(d);
    }
    if (!running && timer) { clearInterval(timer); timer = null; }
  }
  function refresh() {
    api('etat').then(function (d) { render(d.seance); if (d.seance && ['termine', 'arrete', 'erreur'].indexOf(d.seance.etat) < 0 && !timer) timer = setInterval(refresh, 1500); })
      .catch(function () {});
  }
  if (form) form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var b = form.querySelector('button[type="submit"]'); b.disabled = true;
    api('lancer', { tache: form.elements.tache.value, dossier: form.elements.dossier.value }).then(function (d) {
      toast(d.message || 'Lancé.'); refresh();
    }).catch(function (x) { toast(x.message, true); }).then(function () { b.disabled = false; });
  });
  refresh();
})();
