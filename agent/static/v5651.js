/* AxiorHub 5.6.51 — page Dossier : identifiant de commande de mission (double clic = même mission), fichiers choisis, attente d'une relecture */
(function () {
  'use strict';
  function key() {
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function init() {
    document.querySelectorAll('input[data-mi51-key]').forEach(function (i) { if (!i.value) { i.value = key(); } });
    document.querySelectorAll('select[data-mi51-files]').forEach(function (sel) {
      var hidden = sel.form && sel.form.querySelector('input[type=hidden][name=fichiers]');
      if (!hidden) { return; }
      var sync = function () {
        hidden.value = Array.prototype.filter.call(sel.options, function (o) { return o.selected; }).map(function (o) { return o.value; }).join('\n');
      };
      sel.addEventListener('change', sync);
      sync();
    });
    if (document.querySelector('[data-d51-waiting]')) {   // propositions en cours de génération : la page se met à jour
      setTimeout(function () { if (!document.querySelector('form.m5-form textarea:focus, form.m5-form input:focus')) { location.reload(); } }, 15000);
    }
  }
  // 5.6.62 : « Ouvrir dans Nextcloud » partout (page Dossier, point client…) ; Aujourd'hui garde son propre gestionnaire (v530.js)
  document.addEventListener('click', function (ev) {
    var op = ev.target.closest && ev.target.closest('[data-open-path]');
    if (!op || op.closest('#c530')) { return; }
    ev.preventDefault();
    var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute('content') : ''; };
    var w = window.open('about:blank', '_blank');
    var qs = new URLSearchParams({ path: op.getAttribute('data-open-path'), matter: op.getAttribute('data-open-matter') || '' }).toString();
    fetch(meta('axiorhub-prefix') + '/api440/m530/open?' + qs, { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (v) { if (!r.ok) { throw new Error(v.message || v.error || 'Ouverture impossible.'); } return v; }); })
      .then(function (d) { if (w) { w.opener = null; w.location.href = d.url; } else { window.location.href = d.url; } })
      .catch(function (err) { if (w) { w.close(); } window.alert(err.message); });
  });
  // 5.6.62 : « Continuer avec ChatGPT » — onglet ouvert au clic (jamais bloqué), puis page officielle d'OpenAI.
  // 5.6.97 (A04, A05) : suivi sans recharger la page (tentative sur le poste, liaison avec AxiorHub Poste sur un serveur) ; aucun code
  // d'autorisation ni jeton n'est affiché ; le suivi s'arrête si l'on quitte la page ou si l'on annule ; le bouton est rendu ensuite.
  var C97_FIN = ['reussie', 'sans_forfait', 'transmise', 'refusee', 'echec', 'expiree', 'annulee', 'recue'];
  function c97meta(n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.getAttribute('content') : ''; }
  function c97ctrl() {
    if (window.AbortController) { return new AbortController(); }
    var c = { signal: { aborted: false, addEventListener: function () {} } };
    c.abort = function () { c.signal.aborted = true; };
    return c;
  }
  function c97api(route, body, signal) {
    var opts = { credentials: 'same-origin', cache: 'no-store' };
    if (signal && window.AbortController) { opts.signal = signal; }
    if (body) {
      opts.method = 'POST';
      opts.headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': c97meta('axiorhub-csrf') };
      opts.body = JSON.stringify(body);
    }
    return fetch(c97meta('axiorhub-prefix') + '/api440/m5662/' + route, opts).then(function (r) {
      return r.json().then(function (v) {
        if (!r.ok) { var err = new Error(v.message || v.error || 'Suivi de connexion indisponible.'); err.code = v.error; throw err; }
        return v;
      });
    });
  }
  function c97wait(ms, signal) {
    return new Promise(function (ok) {
      var t = setTimeout(ok, ms);
      if (signal && signal.addEventListener) { signal.addEventListener('abort', function () { clearTimeout(t); ok(); }); }
    });
  }
  function c97suivre(route, params, expireLe, zone, signal, onEtat) {
    var limite = Date.parse(expireLe) || (Date.now() + 15 * 60000);
    function tour() {
      if (signal.aborted) { return Promise.resolve('annulee'); }
      if (Date.now() >= limite) { return Promise.resolve('expiree'); }
      return c97api(route + '?' + new URLSearchParams(params).toString(), null, signal).then(function (d) {
        if (zone) { zone.textContent = d.message || ''; }
        if (onEtat) { onEtat(d); }
        if (C97_FIN.indexOf(d.etat) >= 0) { return d.etat; }
        return c97wait(1500, signal).then(tour);
      });
    }
    return tour();
  }
  function c97lien(zone, href, texte) {
    var a = document.createElement('a');
    a.href = href; a.textContent = texte; a.className = 'ax-btn ghost'; a.rel = 'noopener';
    if (href.indexOf('https://') === 0) { a.target = '_blank'; }
    var p = document.createElement('p'); p.appendChild(a); zone.appendChild(p); zone.hidden = false;
    return a;
  }
  function c97code(valeur) {
    var t = document.createElement('p'), n = document.createElement('span');
    t.className = 'c5697-code'; t.appendChild(document.createTextNode('Code à comparer : '));
    n.className = 'c5697-chiffres'; n.textContent = valeur; t.appendChild(n);
    return t;
  }
  function c97etatCarte(form, etat) {
    var card = form.closest('#chatgpt5662, #chatgpt5697-guide') || document;
    var line = card.querySelector('[data-c5697-etat] strong');
    var labels = { reussie: 'Abonnement autorisé', recue: 'Abonnement autorisé', sans_forfait: 'Compte connecté, abonnement non autorisé' };
    if (line && labels[etat]) { line.textContent = 'État : ' + labels[etat]; }
  }
  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-c5662-open]');
    if (!form) { return; }
    ev.preventDefault();
    var mode = form.getAttribute('data-mode') || 'manuel';
    var status = form.querySelector('.m5-status'), zone = form.querySelector('[data-c5697-zone]'), stop = form.querySelector('[data-c5697-annuler]');
    var btn = form.querySelector('button[type=submit]'), ctrl = c97ctrl();
    var w = mode === 'liaison' ? null : window.open('about:blank', '_blank');
    var quitter = function () { ctrl.abort(); };
    window.addEventListener('pagehide', quitter);
    if (zone) { zone.textContent = ''; zone.hidden = true; }
    if (btn) { btn.disabled = true; }
    function finir(etat) {
      window.removeEventListener('pagehide', quitter);
      if (btn) { btn.disabled = false; }
      if (stop) { stop.hidden = true; stop.onclick = null; }
      if (etat === 'expiree' && status) { status.textContent = 'Délai dépassé : recommencez la connexion.'; }
      if ((etat === 'recue' || etat === 'reussie') && zone) { zone.textContent = ''; zone.hidden = true; }
      c97etatCarte(form, etat);
    }
    function annulable(corps) {
      if (!stop) { return; }
      stop.hidden = false;
      stop.onclick = function () {
        ctrl.abort();
        c97api('chatgpt/annuler', corps).then(function (d) { if (status) { status.textContent = d.message; } })
          .catch(function (err) { if (status) { status.textContent = err.message; } });
      };
    }
    function liaison(d) {
      var code = null, aide = null, debut = Date.now();
      c97lien(zone, d.lien, 'Ouvrir AxiorHub Poste');
      annulable({ liaison: d.liaison });
      return c97suivre('chatgpt/statut', { liaison: d.liaison }, d.expire_le, status, ctrl.signal, function (s) {
        if (s.etat === 'attente_poste' && !aide && Date.now() - debut > 30000) {
          aide = document.createElement('p');
          aide.textContent = 'AxiorHub Poste ne répond pas : installez-le sur ce poste, ajoutez l’adresse de ce serveur dans le poste '
            + '(Paramètres › Modèles d’IA › Serveur du cabinet), ou utilisez « Autres méthodes » ci-dessous.';
          zone.appendChild(aide);
        }
        if (s.etat === 'a_approuver' && s.code && !code) {
          code = document.createElement('div');
          code.className = 'c5697-comparer';
          var t = c97code(s.code); code.appendChild(t);
          var lab = null, box = null;
          if (s.compte_connecte) {
            lab = document.createElement('label'); box = document.createElement('input'); box.type = 'checkbox';
            lab.appendChild(box); lab.appendChild(document.createTextNode(' Remplacer le compte ChatGPT connecté si le poste en utilise un autre'));
            code.appendChild(lab);
          }
          var ok = document.createElement('button'); ok.type = 'button'; ok.className = 'ax-btn';
          ok.textContent = 'Les codes sont identiques : autoriser';
          ok.addEventListener('click', function () {
            ok.disabled = true;
            c97api('chatgpt/liaison/approuver', { id: d.liaison, remplacement: box && box.checked ? '1' : '' })
              .then(function (r) { if (status) { status.textContent = r.message; } code.hidden = true; })
              .catch(function (err) { ok.disabled = false; if (status) { status.textContent = err.message; } });
          });
          code.appendChild(ok); zone.appendChild(code); zone.hidden = false; ok.focus();
        }
      });
    }
    c97api('chatgpt/connecter', mode === 'manuel' ? { manuel: true } : {}, ctrl.signal)
      .then(function (d) {
        if (status) { status.textContent = d.message || ''; }
        if (d.mode === 'liaison') { return liaison(d); }
        if (d.url) {
          if (w) { w.opener = null; w.location.href = d.url; } else if (zone) { c97lien(zone, d.url, 'Ouvrir la page d’OpenAI'); }
        }
        if (d.mode === 'serveur') {
          var autres = form.closest('details') || document.querySelector('#chatgpt5662 details.c5697-autres');
          if (autres) { autres.open = true; }
          return 'manuel';
        }
        if (d.tentative) {
          annulable({ tentative: d.tentative });
          return c97suivre('chatgpt/statut', { tentative: d.tentative }, d.expire_le, status, ctrl.signal);
        }
        return 'manuel';
      })
      .then(finir)
      .catch(function (err) {
        if (w) { w.close(); }
        if (status && !(err && err.name === 'AbortError')) { status.textContent = err.message; }
        finir('echec');
      });
  });
  // 5.6.97 (A05, A06) : sur le poste, page ouverte par le lien axiorhub:// (« ?liaison=… ») : association, code à comparer, OpenAI, transfert
  function initPoste97() {
    var panel = document.querySelector('[data-c5697-poste]');
    var id = panel ? new URLSearchParams(window.location.search).get('liaison') : '';
    if (!panel || !id || !/^[A-Za-z0-9_-]{24,64}$/.test(id)) { return; }
    panel.hidden = false;
    var btn = panel.querySelector('[data-c5697-associer]'), suivi = panel.querySelector('[data-c5697-poste-suivi]');
    var zone = panel.querySelector('[data-c5697-poste-zone]'), fin = Date.now() + 25 * 60000, ouvert = false;
    function boucle() {
      if (Date.now() > fin) { suivi.textContent = 'Délai dépassé : recommencez depuis le serveur.'; return; }
      c97api('chatgpt/poste/suivre', { id: id }).then(function (d) {
        suivi.textContent = d.message || '';
        if (d.url && !ouvert) {
          ouvert = true;
          c97lien(zone, d.url, 'Ouvrir la page d’OpenAI');
          window.open(d.url, '_blank', 'noopener');
        }
        if (C97_FIN.indexOf(d.etat) >= 0) { btn.hidden = true; return; }
        setTimeout(boucle, 2000);
      }).catch(function (err) { suivi.textContent = err.message; setTimeout(boucle, 5000); });
    }
    btn.addEventListener('click', function () {
      btn.disabled = true;
      c97api('chatgpt/poste/associer', { id: id }).then(function (d) {
        var t = c97code(d.code);
        zone.textContent = ''; zone.appendChild(t);
        suivi.textContent = d.message || '';
        boucle();
      }).catch(function (err) { btn.disabled = false; suivi.textContent = err.message; });
    });
    panel.scrollIntoView({ block: 'start' });
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); } else { init(); }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', initPoste97); } else { initPoste97(); }
}());
