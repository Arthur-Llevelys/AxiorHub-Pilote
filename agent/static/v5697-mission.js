/* AxiorHub 5.6.97 (lot documents-mission) — Nouveau document, document versionné, demandes de mission, suivi des dossiers d'Aujourd'hui.
   Aucun HTML venant du serveur n'est injecté : les messages et les différences sont écrits avec textContent. Chaque formulaire garde sa clé
   d'idempotence tant que la page n'est pas rechargée : un double clic ou un nouvel essai après une coupure rejoue la même commande. */
(function () {
  'use strict';
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var API = prefix + '/api440/m5689/';

  function statut(form, texte, erreur) {
    var s = form.querySelector('[data-m97-statut]');
    if (!s) return;
    s.textContent = texte || '';
    s.classList.toggle('m97-erreur', !!erreur);
  }
  function cle(form) {
    if (!form.getAttribute('data-m97-cle')) form.setAttribute('data-m97-cle', 'u' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12));
    return form.getAttribute('data-m97-cle');
  }
  function suite(form, data) {
    var modele = form.getAttribute('data-m97-suite');
    if (!modele) { window.setTimeout(function () { location.reload(); }, 600); return; }
    var url = modele.replace(/\{(\w+)\}/g, function (_, k) { return encodeURIComponent(data[k] == null ? '' : String(data[k])); });
    window.setTimeout(function () { location.assign(prefix + url); }, 400);
  }
  function corps(form, submitter) {
    var out = {}, valeurs = {};
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.disabled || el.type === 'file' || el.type === 'submit' || el.type === 'button') return;
      if ((el.type === 'radio') && !el.checked) return;
      if (el.type === 'checkbox') {
        if (!el.checked) return;
        (out[el.name] = out[el.name] || []).push(el.value);
        return;
      }
      if (el.name.indexOf('valeurs.') === 0) { valeurs[el.name.slice(8)] = el.value; return; }
      out[el.name] = el.value;
    });
    if (Object.keys(valeurs).length) out.valeurs = valeurs;
    if (submitter && submitter.name) out[submitter.name] = submitter.value;
    out.cle = cle(form);
    return out;
  }
  function envoyer(url, options) {
    options.credentials = 'same-origin';
    options.headers = Object.assign({ 'X-CSRF-Token': csrf }, options.headers || {});
    return fetch(url, options).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, statut: r.status, data: d }; });
    });
  }
  function binaire(form, api, champs) {
    var fichier = form.querySelector('input[type=file]');
    if (!fichier || !fichier.files || !fichier.files[0]) return Promise.resolve({ ok: false, statut: 400, data: { message: 'Choisissez un fichier.' } });
    var f = fichier.files[0];
    champs.nom = champs.nom || f.name;
    if (!champs.mime && f.type) champs.mime = f.type;
    var q = Object.keys(champs).filter(function (k) { return typeof champs[k] === 'string'; }).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(champs[k]); }).join('&');
    return f.arrayBuffer().then(function (buf) {
      return envoyer(API + api + '?' + q, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: buf });
    });
  }

  document.addEventListener('submit', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-m97-api]');
    if (!form) return;
    ev.preventDefault();
    if (form.getAttribute('aria-busy') === 'true') return;
    var api = form.getAttribute('data-m97-api');
    var data = corps(form, ev.submitter);
    var requete;
    if (form.hasAttribute('data-m97-binaire')) {
      requete = binaire(form, api, data);
    } else if (api === 'creation/creer' && data.mode === 'import') {      // gros fichiers : octets bruts, mêmes contrôles côté serveur
      requete = binaire(form, 'creation/importer', { dossier: data.dossier || '', type: data.type || 'libre', titre: data.titre || '', cle: data.cle });
    } else {
      requete = envoyer(API + api, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    }
    form.setAttribute('aria-busy', 'true');
    statut(form, 'Enregistrement…');
    requete.then(function (r) {
      form.removeAttribute('aria-busy');
      if (r.ok) {
        statut(form, (r.data && r.data.message) || 'Enregistré.');
        suite(form, r.data || {});
        return;
      }
      var message = (r.data && (r.data.message || r.data.error)) || ('Erreur ' + r.statut + '.');
      statut(form, message, true);
      if (r.statut === 409 && form.hasAttribute('data-m97-conflit')) {
        var actions = form.querySelector('[data-m97-conflit-actions]');
        if (actions) actions.hidden = false;
      }
    }).catch(function () {
      form.removeAttribute('aria-busy');
      statut(form, 'Réseau indisponible : rien n’est perdu, réessayez (la même demande sera reprise).', true);
    });
  });

  // Nouveau document : sections du mode choisi, champs du type, rappel du type, IA indisponible pour un type préparé à la main.
  var nouveau = document.getElementById('m97-nouveau');
  function majNouveau() {
    if (!nouveau) return;
    var mode = (nouveau.querySelector('input[name=mode]:checked') || {}).value || 'vierge';
    var type = nouveau.querySelector('[data-m97-types]');
    var option = type && type.options[type.selectedIndex];
    Array.prototype.forEach.call(nouveau.querySelectorAll('[data-m97-mode]'), function (s) { s.hidden = s.getAttribute('data-m97-mode') !== mode; });
    Array.prototype.forEach.call(nouveau.querySelectorAll('[data-m97-type]'), function (s) { s.hidden = !option || s.getAttribute('data-m97-type') !== option.value; });
    var rappel = nouveau.querySelector('[data-m97-rappel-texte]');
    if (rappel) rappel.textContent = option ? option.getAttribute('data-m97-rappel') : '';
    var ia = nouveau.querySelector('input[name=mode][value=ia]');
    if (ia && option) {
      ia.disabled = !option.getAttribute('data-m97-ia');
      if (ia.disabled && ia.checked) { nouveau.querySelector('input[name=mode][value=vierge]').checked = true; majNouveau(); }
    }
  }
  if (nouveau) {
    nouveau.addEventListener('change', function (ev) { if (ev.target.name === 'mode' || ev.target.name === 'type') majNouveau(); });
    majNouveau();
  }

  // Éditeur : comparer le texte en cours avec la version enregistrée ; après un conflit, enregistrer comme copie.
  function lignes(form) { return (form.querySelector('textarea[name=lignes]') || {}).value || ''; }
  function afficherDiff(form, d) {
    var zone = form.querySelector('[data-m97-diff]');
    if (!zone) return;
    zone.textContent = '';
    var titre = document.createElement('h2');
    titre.textContent = (d.changements || 0) + ' changement(s) par rapport à la version ' + d.de;
    zone.appendChild(titre);
    var ul = document.createElement('ul');
    ul.className = 'm97-diff';
    (d.lignes || []).forEach(function (x) {
      if (x.op === 'egal') return;
      var li = document.createElement('li');
      li.className = 'm97-' + x.op;
      li.textContent = (x.op === 'ajout' ? 'Ajouté : ' : 'Retiré : ') + x.texte;
      ul.appendChild(li);
    });
    zone.appendChild(ul);
  }
  document.addEventListener('click', function (ev) {
    var bouton = ev.target.closest && ev.target.closest('[data-m97-comparer],[data-m97-copie]');
    if (!bouton) return;
    var form = bouton.closest('form');
    var id = form.querySelector('input[name=id]').value, version = form.querySelector('input[name=version]').value;
    if (bouton.hasAttribute('data-m97-comparer')) {
      envoyer(API + 'creation/comparer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id, de: version, lignes: lignes(form) }) })
        .then(function (r) { if (r.ok) afficherDiff(form, r.data); else statut(form, (r.data && r.data.message) || 'Comparaison impossible.', true); });
      return;
    }
    if (!bouton.getAttribute('data-m97-cle')) bouton.setAttribute('data-m97-cle', 'u' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12));
    envoyer(API + 'creation/copie', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id, lignes: lignes(form), cle: bouton.getAttribute('data-m97-cle') }) })
      .then(function (r) {
        if (r.ok) { statut(form, r.data.message || 'Copie enregistrée.'); window.setTimeout(function () { location.assign(prefix + '/document?id=' + encodeURIComponent(r.data.document_id)); }, 400); }
        else statut(form, (r.data && r.data.message) || 'Copie impossible.', true);
      });
  });
})();
