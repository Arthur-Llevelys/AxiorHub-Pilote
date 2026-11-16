/* AxiorHub 5.6.106 (lot E) — options d'envoi de la messagerie.
   - Zone « Options d'envoi » de la rédaction (conteneur .bx5628-options-envoi posé par la rédaction) : importance haute, accusé de
     lecture, envoi programmé (date et heure du cabinet), délai pendant lequel « Annuler » reste possible.
   - Confirmation : le message est mis dans la file d'envoi du serveur ; le départ réel est fait par le travailleur, jamais par le
     navigateur. Bandeau « Envoi dans 10 s — Annuler » ; « Annuler » remet le message dans la rédaction (il n'a jamais quitté Brouillons).
   - Liste « Envois programmés » dans Courriels : modifier, envoyer maintenant, annuler ; envois à vérifier ou non partis signalés.
   Tout le texte est posé par textContent : aucun HTML venu du serveur n'est inséré. */
(function () {
  'use strict';
  if (window.axh56106Envoi) return;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var reglage = { delai: 10, delais: [0, 5, 10, 20, 30], fuseau: '', charge: false };
  var liste = null, compteur = null, ouvrir = null;

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
    }).then(function (d) {
      if (d && d.error) { var e = new Error(d.message || d.error); e.code = d.error; throw e; }
      return d;
    });
  }
  function cle() {   // clé d'idempotence de chaque action (un double clic ou un nouvel essai réseau ne refait pas l'effet)
    var a = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return 'e106-' + Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function local(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function dansUneHeure() { var d = new Date(Date.now() + 3600 * 1000); d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0); return local(d); }

  // ------------------------------------------------------------------ zone « Options d'envoi » de la rédaction
  var serie = 0;
  function caseACocher(label, cls) {
    var id = 'e106-' + cls + '-' + (++serie);
    var input = el('input', { type: 'checkbox', id: id, 'class': 'e106-' + cls });
    return { input: input, node: el('div', { 'class': 'e106-ligne' }, [input, el('label', { 'for': id, text: ' ' + label })]) };
  }
  function equiper(zone) {
    if (!zone || zone.getAttribute('data-e106')) return;
    zone.setAttribute('data-e106', '1');
    var fs = el('fieldset', { 'class': 'e106-options' }, [el('legend', { text: 'Options d’envoi' })]);
    var imp = caseACocher('Importance haute', 'importance');
    var acc = caseACocher('Demander un accusé de lecture', 'accuse');
    var prog = caseACocher('Programmer l’envoi', 'programmer');
    var idq = 'e106-quand-' + (++serie);
    var quand = el('input', { type: 'datetime-local', id: idq, 'class': 'e106-quand', step: '60' });
    var aide = el('span', { 'class': 'ax-muted e106-aide', text: 'Heure du cabinet' + (reglage.fuseau ? ' (' + reglage.fuseau + ')' : '') + '. Le message reste dans Brouillons jusqu’à son départ.' });
    var bloc = el('div', { 'class': 'e106-programme', hidden: 'hidden' }, [el('label', { 'for': idq, text: 'Date et heure d’envoi ' }), quand, aide]);
    prog.input.addEventListener('change', function () {
      bloc.hidden = !prog.input.checked;
      if (prog.input.checked) { quand.min = local(new Date(Date.now() + 2 * 60 * 1000)); if (!quand.value) quand.value = dansUneHeure(); quand.focus(); }
    });
    var idd = 'e106-delai-' + (++serie);
    var select = el('select', { id: idd, 'class': 'e106-delai' });
    reglage.delais.forEach(function (v) { select.appendChild(el('option', { value: String(v), text: v ? v + ' s' : 'aucun' })); });
    select.value = String(reglage.delai);
    select.addEventListener('change', function () {
      var v = parseInt(select.value, 10);
      api('m56106/envoi/reglage', { delai: v, cle: cle() }).then(function (r) { reglage.delai = r.delai; toast(r.message); })
        .catch(function (e) { toast(e.message, true); select.value = String(reglage.delai); });
    });
    var delai = el('div', { 'class': 'e106-ligne e106-delai-ligne' }, [el('label', { 'for': idd, text: 'Annulation possible après la confirmation pendant ' }), select]);
    [imp.node, acc.node, prog.node, bloc, delai].forEach(function (n) { fs.appendChild(n); });
    zone.appendChild(fs);
    if (!reglage.charge) charger();   // délai du compte et fuseau du cabinet
  }
  function lire(form) {
    var zone = form && form.querySelector('.bx5628-options-envoi');
    var get = function (s) { return zone ? zone.querySelector(s) : null; };
    var prog = get('.e106-programmer');
    return { importance: !!(get('.e106-importance') || {}).checked, accuse: !!(get('.e106-accuse') || {}).checked,
             quand: prog && prog.checked ? ((get('.e106-quand') || {}).value || '') : '' };
  }
  function joli(texte) {   // « 2026-11-17T09:00 » → « 17/11/2026 à 09 h 00 » (affichage seulement ; le serveur fait foi)
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(texte || '');
    return m ? m[3] + '/' + m[2] + '/' + m[1] + ' à ' + m[4] + ' h ' + m[5] : texte;
  }
  function resume(form) {
    var o = lire(form), lignes = [], opts = [];
    if (o.importance) opts.push('Importance haute');
    if (o.accuse) opts.push('Accusé de lecture demandé');
    if (opts.length) lignes.push(['Options', opts.join(' · ')]);
    lignes.push(['Départ', o.quand ? 'Programmé le ' + joli(o.quand) + ' (heure du cabinet)' :
      (reglage.delai ? 'Dans ' + reglage.delai + ' s après la confirmation (vous pourrez annuler)' : 'Dès la confirmation')]);
    return { lignes: lignes, programme: !!o.quand };
  }

  // ------------------------------------------------------------------ confirmation → file d'envoi
  function confirmer(r, form, hooks) {
    var o = lire(form);
    if (lire(form).quand === '' && form && form.querySelector('.e106-programmer:checked')) {
      return Promise.reject(new Error('Choisissez la date et l’heure d’envoi, ou décochez « Programmer l’envoi ».'));
    }
    return api('m56106/envoi/confirmer', { token: r.token, quand: o.quand, accuse: o.accuse, importance: o.importance, cle: cle() }).then(function (x) {
      if (x.genre === 'differe') { bandeau(x, hooks || {}); x.bandeau = true; }
      charger();
      return x;
    });
  }

  function zoneBandeau() {
    var box = document.getElementById('e106-bandeau');
    if (!box) { box = el('div', { id: 'e106-bandeau', 'class': 'e106-bandeau', role: 'status', 'aria-live': 'polite' }); document.body.appendChild(box); }
    box.textContent = ''; box.className = 'e106-bandeau'; box.hidden = false;
    return box;
  }
  function bandeau(x, hooks) {
    var box = zoneBandeau(), texte = el('span', { 'class': 'e106-texte' }), timer = null, fin = Date.now() + (x.reste || 0) * 1000;
    var annul = el('button', { type: 'button', 'class': 'ax-btn e106-annuler', text: 'Annuler' });
    box.appendChild(texte); box.appendChild(annul);
    var tic = function () {
      var s = Math.ceil((fin - Date.now()) / 1000);
      if (s > 0) { texte.textContent = 'Envoi dans ' + s + ' s — '; timer = setTimeout(tic, 250); return; }
      annul.remove(); texte.textContent = 'Envoi en cours…'; suivre(x.id, box, texte, 0);
    };
    annul.addEventListener('click', function () {
      if (annul.disabled) return; annul.disabled = true; clearTimeout(timer);
      api('m56106/envoi/annuler', { id: x.id, version: x.version, cle: cle() }).then(function (r) {
        annul.remove(); texte.textContent = r.message;
        if (hooks.reprendre) hooks.reprendre(x);
        charger(); setTimeout(function () { box.hidden = true; }, 6000);
      }).catch(function (e) {
        annul.remove(); texte.textContent = e.message;
        if (e.code === 'deja_parti') suivre(x.id, box, texte, 0); else box.className = 'e106-bandeau bad';
      });
    });
    tic();
    annul.focus();
  }
  function fermer(box) {
    var b = el('button', { type: 'button', 'class': 'ax-btn ghost', text: 'Fermer' });
    b.addEventListener('click', function () { box.hidden = true; });
    box.appendChild(b);
  }
  function suivre(id, box, texte, n) {
    api('m56106/envoi/etat?id=' + encodeURIComponent(id)).then(function (s) {
      if (s.etat === 'parti') {
        texte.textContent = 'Message envoyé.' + (s.motif_libelle ? ' ' + s.motif_libelle : '');
        if (s.motif) { box.className = 'e106-bandeau warn'; fermer(box); } else setTimeout(function () { box.hidden = true; }, 6000);
        charger(); return;
      }
      if (s.etat === 'echec' || s.etat === 'a_verifier') {
        texte.textContent = (s.etat === 'echec' ? 'Message NON envoyé : ' : 'À vérifier : ') + (s.motif_libelle || s.etat_libelle) + ' Le message est dans Brouillons.';
        box.className = 'e106-bandeau bad'; fermer(box); charger(); return;
      }
      if (s.etat === 'annule') { texte.textContent = 'Envoi annulé.'; setTimeout(function () { box.hidden = true; }, 4000); return; }
      if (s.motif && s.etat === 'en_attente') { texte.textContent = 'Envoi en attente : ' + s.motif_libelle; box.className = 'e106-bandeau warn'; }
      if (n < 60) setTimeout(function () { suivre(id, box, texte, n + 1); }, n < 20 ? 1500 : 5000);
      else { texte.textContent = 'Le départ prend plus de temps que prévu : suivez-le dans « Envois programmés ».'; fermer(box); }
    }).catch(function (e) {
      if (n < 5) { setTimeout(function () { suivre(id, box, texte, n + 1); }, 3000); return; }
      texte.textContent = 'État de l’envoi indisponible (' + e.message + ') : vérifiez « Envois programmés » et vos éléments envoyés.';
      box.className = 'e106-bandeau warn'; fermer(box);
    });
  }

  // ------------------------------------------------------------------ liste « Envois programmés »
  function bouton(texte, cls, fn) {
    var b = el('button', { type: 'button', 'class': 'ax-btn ' + (cls || 'ghost'), text: texte });
    b.addEventListener('click', function () { if (!b.disabled) fn(b); });
    return b;
  }
  function agir(route, corps, b, question) {
    if (question && !window.confirm(question)) return;
    if (b) { b.disabled = true; b.setAttribute('aria-busy', 'true'); }
    corps.cle = cle();
    api('m56106/envoi/' + route, corps).then(function (r) { toast(r.message); charger(); })
      .catch(function (e) { toast(e.message, true); charger(); })
      .then(function () { if (b) { b.disabled = false; b.setAttribute('aria-busy', 'false'); } });
  }
  function formulaireModifier(it, li) {
    var f = el('form', { 'class': 'e106-modifier' });
    var idq = 'e106-m-' + (++serie);
    var quand = el('input', { type: 'datetime-local', id: idq, step: '60', value: String(it.depart || '').slice(0, 16), required: 'required' });
    quand.min = local(new Date(Date.now() + 2 * 60 * 1000));
    var imp = caseACocher('Importance haute', 'importance'), acc = caseACocher('Demander un accusé de lecture', 'accuse');
    imp.input.checked = !!it.importance; acc.input.checked = !!it.accuse;
    var ok = el('button', { type: 'submit', 'class': 'ax-btn', text: 'Enregistrer' });
    var non = bouton('Abandonner', 'ghost', function () { f.remove(); });
    [el('label', { 'for': idq, text: 'Nouvelle date et heure d’envoi (heure du cabinet) ' }), quand, imp.node, acc.node,
     el('div', { 'class': 'e106-actions' }, [ok, non])].forEach(function (n) { f.appendChild(n); });
    f.addEventListener('submit', function (ev) {
      ev.preventDefault();
      agir('modifier', { id: it.id, version: it.version, quand: quand.value, importance: imp.input.checked, accuse: acc.input.checked }, ok);
    });
    li.appendChild(f); quand.focus();
  }
  function element(it) {
    var li = el('li', { 'class': 'e106-item e106-' + it.etat });
    var titre = el('div', { 'class': 'e106-titre' }, [el('strong', { text: it.objet || '(sans objet)' })]);
    if (it.importance) titre.appendChild(el('span', { 'class': 'e106-badge', text: 'Importance haute' }));
    if (it.accuse) titre.appendChild(el('span', { 'class': 'e106-badge', text: 'Accusé de lecture' }));
    li.appendChild(titre);
    if (it.a) li.appendChild(el('div', { 'class': 'ax-muted', text: 'À : ' + it.a + (it.pieces ? ' · ' + it.pieces + ' pièce(s) jointe(s)' : '') }));
    var quand = it.etat === 'parti' ? 'Envoyé le ' + it.parti_libelle + (it.retard ? ' — envoyé en retard (prévu le ' + it.prevu_libelle + ')' : '')
      : (it.genre === 'programme' ? 'Départ prévu le ' : 'Départ ') + it.depart_libelle;
    li.appendChild(el('div', { 'class': 'e106-quand-texte', text: quand + ' — ' + it.etat_libelle }));
    if (it.motif_libelle) li.appendChild(el('p', { 'class': 'e106-motif', text: it.motif_libelle }));
    var actions = el('div', { 'class': 'e106-actions' });
    (it.actions || []).forEach(function (a) {
      if (a === 'modifier') actions.appendChild(bouton('Modifier', 'ghost', function () { if (!li.querySelector('.e106-modifier')) formulaireModifier(it, li); }));
      if (a === 'maintenant') actions.appendChild(bouton(it.etat === 'echec' ? 'Réessayer maintenant' : 'Envoyer maintenant', '', function (b) {
        agir('maintenant', { id: it.id, version: it.version }, b, 'Envoyer ce message maintenant ?');
      }));
      if (a === 'annuler') actions.appendChild(bouton('Annuler l’envoi', 'danger', function (b) {
        agir('annuler', { id: it.id, version: it.version }, b, 'Annuler cet envoi ? Le message reste dans Brouillons.');
      }));
      if (a === 'retirer') actions.appendChild(bouton('Retirer de la liste', 'ghost', function (b) { agir('retirer', { id: it.id }, b); }));
    });
    li.appendChild(actions);
    return li;
  }
  function charger() {
    return api('m56106/envoi/liste').then(function (d) {
      reglage.delai = d.delai; reglage.delais = d.delais || reglage.delais; reglage.fuseau = d.fuseau || ''; reglage.charge = true;
      document.querySelectorAll('.e106-delai').forEach(function (s) { s.value = String(d.delai); });
      var items = d.elements || [];
      var attente = items.filter(function (x) { return x.etat === 'en_attente' || x.etat === 'en_cours'; }).length;
      var alerte = items.filter(function (x) { return x.etat === 'echec' || x.etat === 'a_verifier'; }).length;
      if (compteur) compteur.textContent = items.length ? ' (' + attente + (alerte ? ' · ' + alerte + ' à voir' : '') + ')' : '';
      if (ouvrir) ouvrir.classList.toggle('e106-alerte', alerte > 0);
      if (!liste || liste.hidden) return;
      liste.textContent = '';
      liste.appendChild(el('h2', { text: 'Envois programmés et en attente' }));
      if (!items.length) { liste.appendChild(el('p', { 'class': 'ax-muted', text: 'Aucun envoi programmé ni en attente.' })); return; }
      var ul = el('ul', { 'class': 'e106-liste-items' });
      items.forEach(function (it) { ul.appendChild(element(it)); });
      liste.appendChild(ul);
    }).catch(function (e) {
      if (compteur) compteur.textContent = ' (?)';
      if (liste && !liste.hidden) { liste.textContent = ''; liste.appendChild(el('p', { 'class': 'ax-alert', role: 'alert', text: 'Liste des envois indisponible : ' + e.message })); }
    });
  }

  window.axh56106Envoi = { equiper: equiper, confirmer: confirmer, resume: resume, charger: charger };

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.bx5628-options-envoi').forEach(equiper);
    if (window.MutationObserver) {   // zones créées plus tard par la rédaction (nouveau message, réponse, transfert)
      new MutationObserver(function (records) {
        records.forEach(function (r) {
          Array.prototype.forEach.call(r.addedNodes, function (n) {
            if (n.nodeType !== 1) return;
            if (n.classList.contains('bx5628-options-envoi')) equiper(n);
            else if (n.querySelectorAll) n.querySelectorAll('.bx5628-options-envoi:not([data-e106])').forEach(equiper);
          });
        });
      }).observe(document.body, { childList: true, subtree: true });
    }
    var head = $('#bx5625 .bx5625-head');
    if (!head || head.querySelector('.e106-ouvrir')) return;
    ouvrir = el('button', { type: 'button', 'class': 'ax-btn ghost e106-ouvrir', 'aria-expanded': 'false', 'aria-controls': 'e106-liste', text: '🕑 Envois programmés' });
    compteur = el('span', { 'class': 'e106-compte' });
    ouvrir.appendChild(compteur);
    liste = el('section', { id: 'e106-liste', 'class': 'e106-liste', hidden: 'hidden', 'aria-label': 'Envois programmés' });
    head.appendChild(ouvrir);
    head.parentNode.insertBefore(liste, head.nextSibling);
    ouvrir.addEventListener('click', function () {
      liste.hidden = !liste.hidden;
      ouvrir.setAttribute('aria-expanded', liste.hidden ? 'false' : 'true');
      if (!liste.hidden) { liste.textContent = ''; liste.appendChild(el('p', { 'class': 'ax-muted', text: 'Chargement…' })); charger(); }
    });
    charger();
  });
})();
