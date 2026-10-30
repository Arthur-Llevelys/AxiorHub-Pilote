/* AxiorHub 5.6.80 — réaction immédiate des boutons de l'assistant.
   Ce petit script est chargé avant tous les autres. Si l'avocat clique sur le robot ou sur la vague pendant que les scripts de la page se
   chargent, le panneau s'ouvre tout de suite et la demande est reprise dès qu'ils sont prêts (window.__axPending). Il fournit aussi
   l'affichage des passages trouvés sans IA pendant que le modèle répond. Tout le texte passe par textContent. */
(function () {
  'use strict';
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.getAttribute('content') : ''; }
  document.addEventListener('click', function (ev) {
    if (window.axiorhubPilotReady || !ev.target.closest) return;
    var b = ev.target.closest('#ws-ai-launcher,#ws-ai-live');
    if (!b) return;
    var dock = document.getElementById('ws-ai-dock');
    if (!dock) return;
    ev.stopImmediatePropagation();
    dock.hidden = false;
    b.classList.add('is-waiting');
    window.__axPending = b.id;
    var status = document.getElementById('ws-ai-status');
    if (status) status.textContent = 'Ouverture de l’assistant…';
  }, true);
  /* Passages trouvés sans IA : quelques millisecondes, même si le modèle est occupé. */
  window.axiorhubPassages = function (question, matter, box, title) {
    return fetch(meta('axiorhub-prefix') + '/api440/m5680/passages', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf') },
      body: JSON.stringify({ question: question, matter: matter || '' }) }).then(function (r) { return r.ok ? r.json() : { passages: [] }; }).then(function (data) {
      var list = data.passages || [];
      if (!list.length || !box || !box.isConnected) return 0;
      var old = box.querySelector('.ax5680-passages');
      if (old) old.remove();
      var wrap = document.createElement('section'); wrap.className = 'ax5680-passages';
      var head = document.createElement('p'); head.className = 'ax5680-head';
      head.textContent = title || 'L’IA rédige sa réponse. Premiers passages trouvés dans les pièces (sans analyse) :';
      wrap.append(head);
      list.forEach(function (p) {
        var item = document.createElement('article');
        var name = document.createElement('strong'); name.textContent = p.nom;
        var text = document.createElement('p'); text.textContent = String(p.extrait || '').replace(/\s+/g, ' ').slice(0, 320) + '…';
        item.append(name, text); wrap.append(item);
      });
      box.prepend(wrap);
      return list.length;
    }).catch(function () { return 0; });
  };
})();
