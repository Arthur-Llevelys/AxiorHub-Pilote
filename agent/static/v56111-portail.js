/* AxiorHub 5.6.111 (lot P) — « Envois sur le portail » de l'onglet Juridiction du dossier.
   Préparer : le formulaire est lu ici (identifiants des pièces et rangs seulement), le code de confirmation est affiché UNE fois dans une zone
   d'état persistante avec son champ de saisie (aucune conservation dans le navigateur). « Nouveau code », « Copier le texte », présélection
   d'un brouillon de conclusions venu d'« À relire ». Aucun HTML venant du serveur n'est injecté : tout passe par textContent. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var bloc = document.getElementById('p111-portail');
  if (!bloc || bloc.getAttribute('data-p111-pret') === '1') return;
  bloc.setAttribute('data-p111-pret', '1');
  var zone = bloc.querySelector('[data-p111-code-zone]');

  function el(tag, attrs, texte) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (texte) n.textContent = texte;
    return n;
  }
  function appel(route, corps) {
    return fetch(prefix + '/api440/m56111/portail/' + route, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps)
    }).then(function (r) {
      return r.json().catch(function () { return { error: 'reponse_invalide', message: 'Réponse inattendue du serveur.' }; }).then(function (d) {
        if (!r.ok && !(d && d.error)) d = { error: 'http_' + r.status, message: 'Le serveur a refusé la demande : rien n’a été enregistré.' };
        if (d && d.error) { var e = new Error(d.message || d.error); e.code = d.error; throw e; }
        return d;
      });
    });
  }
  function heure(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? '' : ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }
  function cle() {
    var a = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }

  /* Code affiché une fois, avec son champ : la confirmation recharge la page (liste à jour). */
  function montrerCode(envoi, code, expire) {
    if (!zone) return;
    zone.textContent = '';
    zone.appendChild(el('p', {}, 'Code de confirmation à recopier :'));
    zone.appendChild(el('p', { 'class': 'p111-code', 'aria-label': 'Code de confirmation' }, code));
    var h = heure(expire);
    if (h) zone.appendChild(el('p', {}, 'Valable jusqu’à ' + h + '.'));
    var form = el('form', { 'class': 'p111-code-form' });
    var label = el('label', {}, 'Code de confirmation (six chiffres)');
    var champ = el('input', { name: 'code', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: '6', required: 'required' });
    label.appendChild(champ);
    form.appendChild(label);
    form.appendChild(el('button', { 'class': 'ax-btn', type: 'submit' }, 'Confirmer l’envoi préparé'));
    var etat = el('p', { role: 'status', 'class': 'm5-status' });
    form.appendChild(etat);
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var b = form.querySelector('button'); b.disabled = true;
      appel('confirmer', { id: envoi, code: champ.value.trim() }).then(function (d) {
        etat.textContent = d.message || 'Envoi confirmé.';
        setTimeout(function () { location.reload(); }, 600);
      }).catch(function (e) { etat.textContent = e.message; champ.focus(); }).then(function () { b.disabled = false; });
    });
    zone.appendChild(form);
    zone.hidden = false;
    champ.focus();
  }

  var prep = bloc.querySelector('form[data-p111-preparer]');
  if (prep) {
    prep.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var statut = prep.querySelector('[data-p111-statut]');
      var data = window.ax89Form ? window.ax89Form.collect(prep) : {};
      if (!window.ax89Form) Array.prototype.forEach.call(prep.querySelectorAll('input,select,textarea'), function (f) { if (f.name) data[f.name] = f.value; });
      var b = prep.querySelector('button[type=submit]'); b.disabled = true;
      if (statut) statut.textContent = 'Préparation en cours…';
      appel('preparer', data).then(function (d) {
        if (statut) statut.textContent = d.message || '';
        var champCle = prep.querySelector('input[name=cle]');
        if (champCle) champCle.value = cle();
        if (d.code) montrerCode(d.id, d.code, d.expire);
      }).catch(function (e) { if (statut) statut.textContent = e.message; }).then(function () { b.disabled = false; });
    });
  }

  bloc.addEventListener('click', function (ev) {
    var nouveau = ev.target.closest && ev.target.closest('[data-p111-nouveau]');
    if (nouveau) {
      nouveau.disabled = true;
      appel('nouveau-code', { id: nouveau.getAttribute('data-p111-nouveau') }).then(function (d) {
        montrerCode(d.id, d.code, d.expire);
      }).catch(function (e) {
        if (zone) { zone.textContent = ''; zone.appendChild(el('p', {}, e.message)); zone.hidden = false; }
      }).then(function () { nouveau.disabled = false; });
      return;
    }
    var copier = ev.target.closest && ev.target.closest('[data-p111-copier]');
    if (copier) {
      var texte = copier.parentNode.querySelector('[data-p111-texte]');
      if (!texte) return;
      var fini = function () { copier.textContent = 'Texte copié'; setTimeout(function () { copier.textContent = 'Copier le texte'; }, 2000); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(texte.value).then(fini, function () { texte.select(); });
      } else { texte.select(); }
    }
  });

  /* Venu d'« À relire » : la version proposée du brouillon de conclusions reçoit le rang 1 (l'avocat peut tout changer). */
  try {
    var q = new URLSearchParams(location.search), doc = q.get('portail_doc'), ver = q.get('portail_version');
    if (doc && prep) {
      var details = document.getElementById('p111-preparer');
      if (details) details.open = true;
      var trouve = null;
      Array.prototype.forEach.call(prep.querySelectorAll('[data-p111-doc]'), function (inp) { if (inp.getAttribute('data-p111-doc') === doc) trouve = inp; });
      var statutP = prep.querySelector('[data-p111-statut]');
      if (trouve) {
        var ref = prep.querySelector('input[name="' + trouve.name.replace('p_', 'r_') + '"]');
        trouve.value = '1';
        var origine = prep.querySelector('[data-p111-origine]');
        if (origine) origine.value = 'a_relire';
        if (ver && ref && ref.value.slice(-ver.length - 1) !== '|' + ver && statutP) statutP.textContent = 'Une version plus récente de ce document existe : vérifiez la pièce choisie.';
        trouve.focus();
      } else if (statutP) {
        statutP.textContent = 'Ce document n’est pas proposé pour ce dossier : choisissez les pièces vous-même.';
      }
      bloc.scrollIntoView({ block: 'start' });
    }
  } catch (e) { /* navigateur ancien : choix manuel */ }
})();
