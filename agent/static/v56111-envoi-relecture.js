/* AxiorHub 5.6.111 (lot U, U01) — « Envoyer… » raccordé dans le lecteur commun d'À relire (page et fenêtre d'Aujourd'hui).
   Aucune capacité nouvelle : l'envoi existant (send490 : aperçu puis confirmation, routes /api440/send/prepare et send/confirm, garde des
   courriels revue à l'aperçu et à l'effet) est rendu visible là où l'avocat relit. Effets distincts : Retenir n'envoie rien, Déposer dans
   Brouillons n'envoie rien, Envoyer… montre destinataires et objet puis attend un second clic. Une proposition se dépose d'abord dans
   Brouillons ; c'est le brouillon relu chez le fournisseur qui part. Textes en français (traduits par v5634t.js). */
(function () {
  'use strict';
  function meta(nom) { var m = document.querySelector('meta[name="' + nom + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix');
  var statut = null;
  function etatEnvoi() {
    if (!statut) {
      statut = fetch(prefix + '/api440/send/status', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
        .then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; });
    }
    return statut;
  }
  function appel(chemin, corps) {
    var init = { credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { Accept: 'application/json' } };
    if (corps) {
      init.method = 'POST';
      init.headers['Content-Type'] = 'application/json';
      init.headers['X-CSRF-Token'] = meta('axiorhub-csrf');
      init.body = JSON.stringify(corps);
    }
    return fetch(prefix + chemin, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { var ex = new Error(d.message || 'Envoi impossible pour le moment.'); throw ex; }
        return d;
      });
    });
  }
  function el(tag, attrs, texte) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (texte) n.textContent = texte;
    return n;
  }

  function equiper(article) {
    if (article.hasAttribute('data-u111-envoi-pret')) return;
    var pied = article.querySelector('[data-ax100-actions]');
    if (!pied) return;
    article.setAttribute('data-u111-envoi-pret', '1');
    var bloc = el('span', { 'class': 'u111-envoi', 'data-u111-envoi': '' });
    var bouton = el('button', { type: 'button', 'class': 'ax-btn ghost', 'data-u111-envoyer': '', 'aria-expanded': 'false' }, 'Envoyer…');
    var activer = el('a', { 'class': 'ax-btn ghost', 'data-u111-activer': '', href: prefix + '/parametres?rubrique=messagerie#envoi5636' },
      'Envoyer depuis AxiorHub : à activer');
    var note = el('span', { 'class': 'u111-envoi-note', 'data-u111-envoi-note': '' });
    bloc.appendChild(bouton); bloc.appendChild(activer); bloc.appendChild(note);
    bloc.hidden = true; bouton.hidden = true; activer.hidden = true;
    var panneau = el('div', { 'class': 'ax101-panneau u111-confirmer', 'data-u111-confirmer': '', role: 'group', 'aria-label': 'Confirmer l’envoi' });
    panneau.hidden = true;
    var resume = el('p', { 'data-u111-resume': '' });
    var confirmer = el('button', { type: 'button', 'class': 'ax-btn', 'data-u111-confirmer-envoi': '' }, 'Confirmer l’envoi');
    var annuler = el('button', { type: 'button', 'class': 'ax-btn ghost', 'data-u111-annuler': '' }, 'Annuler');
    var annonce = el('p', { 'class': 'u111-envoi-statut', role: 'status', 'aria-live': 'polite', 'data-u111-envoi-statut': '' });
    panneau.appendChild(resume); panneau.appendChild(confirmer); panneau.appendChild(annuler);
    var deposer = pied.querySelector('[data-action="deposer"]');
    pied.insertBefore(bloc, deposer ? deposer.nextSibling : null);
    pied.appendChild(panneau);
    pied.appendChild(annonce);
    var jeton = '';

    function fermer() { panneau.hidden = true; jeton = ''; bouton.setAttribute('aria-expanded', 'false'); }
    function maj() {
      var type = article.getAttribute('data-type') || '', etat = article.getAttribute('data-etat') || '';
      fermer();
      annonce.textContent = '';
      if (!article.getAttribute('data-id')) { bloc.hidden = true; return; }
      if (type === 'proposition') {
        bloc.hidden = false; bouton.hidden = true; activer.hidden = true;
        note.textContent = 'Pour envoyer : « Déposer dans Brouillons », puis « Envoyer… » sur le brouillon déposé.';
        return;
      }
      if (type !== 'imap' || etat === 'remplace' || etat === 'ecarte') { bloc.hidden = true; return; }
      bloc.hidden = false; note.textContent = '';
      etatEnvoi().then(function (s) {
        var actif = Boolean(s && s.enabled);
        bouton.hidden = !actif;
        activer.hidden = actif;
        if (!actif) note.textContent = '';
      });
    }
    new MutationObserver(maj).observe(article, { attributes: true, attributeFilter: ['data-id', 'data-type', 'data-etat'] });
    maj();

    bouton.addEventListener('click', function () {
      var id = article.getAttribute('data-id');
      if (!id) return;
      bouton.disabled = true;
      annonce.textContent = 'Préparation de l’aperçu d’envoi…';
      appel('/api440/m56100/relecture/' + encodeURIComponent(id)).then(function (d) {
        var zone = article.querySelector('[data-ax100-texte]');
        if (zone && typeof d.corps === 'string' && zone.value !== d.corps) {
          throw new Error('Enregistrez d’abord votre texte : c’est la version enregistrée dans Brouillons qui part.');
        }
        var m = /^v(\d+)u(\d+)$/.exec(String(d.revision || ''));
        if (!m) throw new Error('Brouillon introuvable dans la messagerie : rien n’est envoyé.');
        return appel('/api440/send/prepare', { uid: m[2], uidvalidity: m[1] });
      }).then(function (p) {
        var s = p.summary || {};
        jeton = p.token || '';
        resume.textContent = 'À : ' + (s.to || '') + (s.cc ? ' · Cc : ' + s.cc : '') + ' · Objet : ' + (s.subject || '(sans objet)') +
          ' · ' + (s.recipient_count || 0) + ' destinataire(s)' + ((s.attachments || []).length ? ' · ' + s.attachments.length + ' pièce(s) jointe(s)' : '');
        panneau.hidden = false;
        bouton.setAttribute('aria-expanded', 'true');
        annonce.textContent = 'Vérifiez les destinataires et l’objet, puis confirmez. Rien n’est parti.';
        confirmer.disabled = true;
        window.setTimeout(function () { confirmer.disabled = false; confirmer.focus(); }, Math.max(1000, (p.min_delay || 1) * 1000));
      }).catch(function (ex) { annonce.textContent = ex.message; }).then(function () { bouton.disabled = false; });
    });
    confirmer.addEventListener('click', function () {
      if (!jeton) return;
      confirmer.disabled = true;
      var t = jeton; jeton = '';
      appel('/api440/send/confirm', { token: t }).then(function (r) {
        panneau.hidden = true;
        bouton.setAttribute('aria-expanded', 'false');
        annonce.textContent = r.message || 'Envoi demandé.';
      }).catch(function (ex) { annonce.textContent = ex.message; confirmer.disabled = false; });
    });
    annuler.addEventListener('click', function () { fermer(); annonce.textContent = 'Envoi annulé : rien n’est parti.'; bouton.focus(); });
  }

  function tout() { Array.prototype.forEach.call(document.querySelectorAll('[data-ax100-lecteur]'), equiper); }
  tout();
  var prevu = false;   // une passe par image au plus : la page peut changer souvent (liste, fragments)
  new MutationObserver(function () { if (prevu) return; prevu = true; window.requestAnimationFrame(function () { prevu = false; tout(); }); })
    .observe(document.body, { childList: true, subtree: true });
})();
