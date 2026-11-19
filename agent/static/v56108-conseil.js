/* AxiorHub 5.6.108 (lot K) — « Demander l'avis du conseil » dans le volet de l'assistant et à côté d'une demande de consultation
   (formulaire ``prepare_legal_opinion``), chargé sur toutes les pages après v56108-accueil.js.
   - L'option n'existe que si le conseil de modèles est configuré (``GET m56108/conseil/annonce`` : ``pret``), que la personne peut le lancer
     (``peut_lancer``), qu'un dossier est choisi et, dans le volet, que la question relève du jugement (``estJugement``, même motif que
     ``conseil56108.JUGEMENT`` ; le service le revérifie). Sinon elle est absente.
   - Un clic n'envoie rien : il affiche l'annonce (membres, appels, durée, coût) relue sur le serveur, avec « Confirmer » et « Annuler ». Seule
     la confirmation envoie ``POST m56108/conseil/lancer`` (clé d'idempotence, ``conseil56108`` posé par le service). Puis reçu et étape de la
     mission par le mécanisme du lot A (``AX108accueil.recu``), suivi par ``m567/mission``. Aucune réponse : « reçu incertain », jamais un succès.
   Fonctions pures exportées pour Node (tests/conseil56108.cjs). Textes par textContent ; aucun HTML du serveur injecté. */
(function (racine) {
  'use strict';

  /* ------------------------------------------------------------------ fonctions pures */
  var JUGEMENT = /\b(?:strategi|risque|chance|opportun|faut-il|faudrait-il|devrions|doit-on|conseill|recommand|apprecie|apprecia|argument|these|contest|assigner|transiger|transaction|negocier|recours|faire appel|interjeter|pourvoi|consultation|avis sur|position a|interet a|vaut-il|vaudrait-il|exposition|responsabilite encourue)/;
  function sansAccents(t) {
    return String(t || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/’/g, "'");
  }
  function estJugement(t) { return JUGEMENT.test(sansAccents(t)); }
  /* L'option est-elle montrée ? annonce relue (pret, peut_lancer), dossier choisi, question (de jugement si ``exigeJugement``). */
  function visible(annonce, dossier, question, exigeJugement) {
    if (!annonce || annonce.pret !== true || annonce.peut_lancer !== true) return false;
    if (!String(dossier || '').trim() || !String(question || '').trim()) return false;
    return exigeJugement ? estJugement(question) : true;
  }
  function cle() {
    var c = racine.crypto;
    if (c && c.randomUUID) return 'k108-' + c.randomUUID();
    var b = new Uint8Array(16); c.getRandomValues(b);
    return 'k108-' + Array.prototype.map.call(b, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }

  var api = { estJugement: estJugement, visible: visible, sansAccents: sansAccents };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  var doc = document;
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  function el(tag, texte, classe) { var n = doc.createElement(tag); if (texte) n.textContent = texte; if (classe) n.className = classe; return n; }
  function appel(nom, corps) {
    var opts = corps === undefined ? { credentials: 'same-origin' } : { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) };
    return fetch(prefix + '/api440/' + nom, opts).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (d) {
        if (!r.ok || !d) {
          var e = new Error((d && (d.message || d.error)) || 'Le serveur n’a pas confirmé (erreur ' + r.status + ').');
          e.incertain = !d || r.status >= 500; throw e;
        }
        return d;
      });
    }, function () { var e = new Error('Connexion au serveur interrompue.'); e.incertain = true; throw e; });
  }
  var annonceLue = null, enLecture = null;
  function lireAnnonce(frais) {
    if (annonceLue && !frais) return Promise.resolve(annonceLue);
    if (enLecture && !frais) return enLecture;
    enLecture = appel('m56108/conseil/annonce').then(function (a) { annonceLue = a; return a; }, function () { annonceLue = { pret: false }; return annonceLue; });
    return enLecture;
  }

  /* Panneau d'annonce, puis confirmation : seule « Confirmer » envoie quelque chose. */
  function panneau(zone, lire, origine, apres) {
    var ancien = zone.querySelector('.k108-annonce'); if (ancien) ancien.remove();
    var boite = el('div', '', 'k108-annonce'); boite.setAttribute('role', 'group'); boite.setAttribute('aria-label', 'Annonce du conseil de modèles');
    var statut = el('p', 'Lecture de l’annonce…', 'k108-annonce-texte'); statut.setAttribute('aria-live', 'polite');
    boite.appendChild(statut); zone.appendChild(boite);
    lireAnnonce(true).then(function (a) {
      if (!a.pret) { statut.textContent = a.motif || 'Aucun conseil de modèles n’est prêt.'; return; }
      statut.textContent = 'Annonce : ' + a.texte;
      (a.limites || []).forEach(function (x) { boite.appendChild(el('p', x, 'ax-muted')); });
      var ok = el('button', 'Confirmer et lancer le conseil', 'ax-btn'); ok.type = 'button';
      var non = el('button', 'Annuler', 'ax-btn ghost'); non.type = 'button';
      var actions = el('p', '', 'k108-annonce-actions'); actions.appendChild(ok); actions.appendChild(non); boite.appendChild(actions);
      ok.focus();
      non.addEventListener('click', function () { boite.remove(); });
      ok.addEventListener('click', function () {
        if (ok.disabled) return; ok.disabled = true; non.disabled = true;
        var valeurs = lire(), corps = { matter: valeurs.dossier, question: valeurs.question, confirmer: true, cle: boite.dataset.cle || cle(), origine: origine };
        boite.dataset.cle = corps.cle;   // même clé si la personne réessaie : jamais de doublon
        statut.textContent = 'Envoi en cours… (pas encore de reçu)';
        appel('m56108/conseil/lancer', corps).then(function (r) {
          boite.remove(); apres(r, null);
        }, function (e) {
          ok.disabled = false; non.disabled = false;
          statut.textContent = (e.incertain ? 'Aucun reçu du serveur : demande à vérifier dans Missions avant tout nouvel essai. ' : 'Refusée, rien n’a été enregistré : ') + e.message;
          apres(null, e);
        });
      });
    });
  }

  /* Carte de la mission : reçu et étape du lot A, suivi jusqu'à l'état final. */
  var FINAL = { answered: 1, prepared: 1, verified: 1, abstained: 1, error: 1, interrupted: 1, cancelled: 1, decision: 1 };
  function carte(zone, r, e, question) {
    var c = el('article', '', 'mission567-card k108-carte');
    var etat = el('div', r ? (r.carte && r.carte.label) || 'Reçue' : 'Aucun reçu', 'mission567-state');
    c.appendChild(etat); c.appendChild(el('h3', 'Conseil de modèles : ' + String(question || '').slice(0, 200)));
    if (r && r.message) c.appendChild(el('p', r.message, 'ax-muted'));
    var accueil = racine.AX108accueil;
    if (r && r.carte && r.carte.id) {
      c.dataset.missionId = r.carte.id;
      if (accueil) accueil.recu(r.carte, c);
      suivre(c, r.carte.id, etat);
    } else if (accueil) accueil.recuProvisoire(e && e.incertain ? 'incertaine' : 'refusee', c);
    // Volet : carte propre, juste après la carte courante de v567.js (que son suivi remplace) ; ailleurs, sous le formulaire.
    var courante = zone.querySelector('#ws-ai-current');
    if (courante) {
      var place = zone.querySelector('#k108-volet-recu');
      if (!place) { place = el('div', '', 'k108-volet-recu'); place.id = 'k108-volet-recu'; place.setAttribute('aria-live', 'polite'); courante.insertAdjacentElement('afterend', place); }
      place.replaceChildren(c);
    } else zone.appendChild(c);
    return c;
  }
  function suivre(c, id, etat) {
    var essais = 0;
    function tour() {
      if (!c.isConnected || essais++ > 240) return;
      appel('m567/mission?id=' + encodeURIComponent(id)).then(function (m) {
        etat.textContent = (m.panneau && m.panneau.libelle) || m.label || etat.textContent;
        if (racine.AX108accueil) racine.AX108accueil.recu(m, c);
        if (!FINAL[m.state]) setTimeout(tour, 5000);
      }, function () { setTimeout(tour, 10000); });
    }
    setTimeout(tour, 3000);
  }

  /* ------------------------------------------------------------------ volet de l'assistant */
  function volet() {
    var dock = doc.getElementById('ws-ai-dock'), envoyer = doc.getElementById('ws-ai-send');
    var question = doc.getElementById('ws-ai-question'), dossier = doc.getElementById('ws-ai-matter');
    if (!dock || !envoyer || !question || !dossier || doc.getElementById('k108-volet')) return;
    var bouton = el('button', 'Demander l’avis du conseil', 'ghost k108-volet'); bouton.type = 'button'; bouton.id = 'k108-volet'; bouton.hidden = true;
    bouton.title = 'Question de jugement : plusieurs modèles répondent, se relisent de façon anonyme, puis une synthèse expose les désaccords. Annonce avant tout envoi.';
    envoyer.insertAdjacentElement('afterend', bouton);
    function maj() {
      if (!question.value.trim()) { bouton.hidden = true; return; }
      lireAnnonce(false).then(function (a) { bouton.hidden = !visible(a, dossier.value, question.value, true); });
    }
    question.addEventListener('input', maj); dossier.addEventListener('change', maj); maj();
    bouton.addEventListener('click', function () {
      var zone = doc.getElementById('ws-ai-current') ? doc.getElementById('ws-ai-current').parentNode : dock;
      panneau(zone, function () { return { dossier: dossier.value, question: question.value.trim() }; }, 'assistant', function (r, e) {
        var q = question.value.trim();
        if (r) { question.value = ''; dock.dispatchEvent(new CustomEvent('ax108:recu', { detail: { id: (r.carte && r.carte.id) || '' } })); bouton.hidden = true; }
        carte(dock, r, e, q);
      });
    });
  }

  /* ------------------------------------------------------------------ demande de consultation (prepare_legal_opinion) */
  function consultations() {
    if (doc.getElementById('conseil56108-arguments')) return;   // Examiner les arguments : section du serveur, déjà présente
    Array.prototype.forEach.call(doc.querySelectorAll('form'), function (f) {
      var action = f.querySelector('input[name="action"][value="prepare_legal_opinion"]');
      if (!action || f.querySelector('.k108-consultation')) return;
      var m = f.querySelector('[name="matter"]'), q = f.querySelector('[name="question"]');
      if (!m || !q) return;
      var bouton = el('button', 'Demander l’avis du conseil', 'ghost k108-consultation'); bouton.type = 'button'; bouton.hidden = true;
      var soumettre = f.querySelector('button:not([type="button"])');
      if (soumettre) soumettre.insertAdjacentElement('afterend', bouton); else f.appendChild(bouton);
      function maj() { lireAnnonce(false).then(function (a) { bouton.hidden = !visible(a, m.value, q.value, false); }); }
      q.addEventListener('input', maj); m.addEventListener('input', maj); m.addEventListener('change', maj); maj();
      bouton.addEventListener('click', function () {
        var zone = el('div', '', 'k108-zone'); var ancien = f.nextElementSibling;
        if (ancien && ancien.classList.contains('k108-zone')) ancien.remove();
        f.insertAdjacentElement('afterend', zone);
        panneau(zone, function () { return { dossier: m.value.trim(), question: q.value.trim() }; }, 'consultation', function (r, e) { carte(zone, r, e, q.value.trim()); });
      });
    });
  }

  /* ------------------------------------------------------------------ Examiner les arguments : formulaire du serveur (annonce déjà affichée) */
  function formulairesLancer() {
    Array.prototype.forEach.call(doc.querySelectorAll('form.k108-lancer'), function (f) {
      f.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var statut = f.querySelector('[role="status"]'), bouton = f.querySelector('button[type="submit"]');
        var confirmer = f.querySelector('input[name="confirmer"]');
        if (!confirmer || !confirmer.checked) { if (statut) statut.textContent = 'Cochez la case après avoir lu l’annonce.'; return; }
        if (bouton.disabled) return; bouton.disabled = true;
        if (statut) statut.textContent = 'Envoi en cours… (pas encore de reçu)';
        appel('m56108/conseil/lancer', { matter: f.querySelector('[name="matter"]').value, cle: f.querySelector('[name="cle"]').value,
          question: f.querySelector('[name="question"]').value.trim(), confirmer: true, origine: 'arguments' }).then(function (r) {
          if (statut) statut.textContent = r.message || 'Demande reçue.';
          setTimeout(function () { racine.location.reload(); }, 600);
        }, function (e) {
          bouton.disabled = false;
          if (statut) statut.textContent = (e.incertain ? 'Aucun reçu du serveur : vérifiez dans Missions avant tout nouvel essai. ' : 'Refusée, rien n’a été enregistré : ') + e.message;
        });
      });
    });
  }

  function demarrer() { volet(); consultations(); formulairesLancer(); }
  racine.AX108conseil = { estJugement: estJugement, visible: visible };
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', demarrer); else demarrer();
})(typeof window !== 'undefined' ? window : globalThis);
