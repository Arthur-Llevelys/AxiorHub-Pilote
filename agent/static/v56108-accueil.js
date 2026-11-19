/* AxiorHub 5.6.108 (lot A) — Aujourd'hui et volet de l'assistant, chargé sur toutes les pages (après v567.js).
   - « Actuellement » : une actualisation ne remplace jamais une dernière valeur connue par un zéro ou un « – » pendant qu'une source ne répond
     pas (état « panne », « retard », ou liste partielle annoncée à zéro). La ligne garde sa valeur, dit « dernière valeur connue à … » et la
     reprise (« nouvel essai à la prochaine mise à jour » ou « Actualiser »). Le HTML vient du serveur (échappé par actuellement5695) ; les
     textes ajoutés ici passent par textContent.
   - Reçu d'une demande à l'assistant : numéro tiré de l'identifiant de la mission enregistrée par le serveur et étape écrite (Reçue,
     En attente, En cours, Résultat, Échec). Une carte posée avant la réponse du serveur (envoi en cours, reçu incertain, refus) ne porte
     jamais de numéro : aucun succès sans reçu. Sur la page Missions, chaque carte porte le même reçu ; « #mission-<id> » la montre.
   Fonctions pures exportées pour Node (tests/accueil56108.cjs). Rien ici n'envoie de demande, n'écrit sur le serveur ni n'appelle un modèle. */
(function (racine) {
  'use strict';

  /* ------------------------------------------------------------------ fonctions pures */
  var SOURCE_MUETTE = { panne: 1, retard: 1 };
  /* Ligne d'« Actuellement » : {etat, nombre (texte affiché), garde (heure de la valeur gardée ou '')}. */
  function aGarder(ancien, nouveau) {
    if (!ancien || !nouveau) return false;
    var muette = SOURCE_MUETTE[nouveau.etat] || (nouveau.etat === 'partiel' && nouveau.nombre === '0');
    if (!muette) return false;
    if (ancien.garde) return true;                                   // déjà gardée : on garde la même valeur et la même heure
    if (SOURCE_MUETTE[ancien.etat] || (ancien.etat === 'partiel' && ancien.nombre === '0')) return false;
    return /^\d+$/.test(String(ancien.nombre || ''));
  }

  var ETAPES = [['recue', 'Reçue'], ['attente', 'En attente'], ['en_cours', 'En cours'], ['resultat', 'Résultat'], ['echec', 'Échec']];
  var PAR_CODE = { recue: 'recue', attente: 'attente', choix_requis: 'attente', suspendue: 'attente', plan_propose: 'attente', en_cours: 'en_cours',
                   reponse_prete: 'resultat', projet_a_relire: 'resultat', sans_production: 'resultat', interrompue: 'echec', annulee: 'echec', refusee: 'echec' };
  var PAR_ETAT = { creating: 'recue', queued: 'attente', decision: 'attente', suspended: 'attente', paused: 'attente', suggested: 'attente',
                   running: 'en_cours', cancel_requested: 'en_cours', answered: 'resultat', prepared: 'resultat', verified: 'resultat', abstained: 'resultat',
                   error: 'echec', interrupted: 'echec', cancelled: 'echec' };
  /* Étape d'une mission (code du panneau d'abord, état ensuite) ; '' si inconnue : rien n'est affirmé. */
  function etape(m) {
    if (!m) return '';
    var code = m.panneau && m.panneau.code;
    return PAR_CODE[code] || PAR_ETAT[m.state] || '';
  }
  function libelleEtape(code) {
    for (var i = 0; i < ETAPES.length; i++) if (ETAPES[i][0] === code) return ETAPES[i][1];
    return '';
  }
  /* Numéro de reçu lisible : huit premiers caractères de l'identifiant de la mission (hexadécimal, 16 caractères au moins), « A1B2-C3D4 ». */
  function numero(id) {
    id = String(id || '');
    if (!/^[0-9a-f]{16,64}$/i.test(id)) return '';
    var x = id.slice(0, 8).toUpperCase();
    return x.slice(0, 4) + '-' + x.slice(4);
  }

  var api = { aGarder: aGarder, etape: etape, libelleEtape: libelleEtape, numero: numero, ETAPES: ETAPES };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  var doc = document;
  function meta(n) { var m = doc.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var prefix = meta('axiorhub-prefix');
  function el(tag, texte, classe) { var n = doc.createElement(tag); if (texte) n.textContent = texte; if (classe) n.className = classe; return n; }

  /* ------------------------------------------------------------------ « Actuellement » : dernière valeur connue */
  function lireLigne(li) {
    if (!li) return null;
    var n = li.querySelector('.ax95-nombre');
    return { etat: li.getAttribute('data-etat') || '', nombre: n ? n.textContent.trim() : '', garde: li.getAttribute('data-ax108-garde') || '' };
  }
  var LIBELLES_ETAT = { panne: 'source indisponible', retard: 'non calculé à temps', partiel: 'liste peut-être incomplète' };
  function fusionnerActuellement(zone, html, heure, auto) {
    var modele = doc.createElement('template');
    modele.innerHTML = html;                                         // HTML échappé par le serveur (actuellement5695.lignes_html)
    var gardees = 0;
    Array.prototype.forEach.call(modele.content.querySelectorAll('li[data-rubrique]'), function (neuve) {
      var nom = neuve.getAttribute('data-rubrique');
      var ancienne = zone.querySelector('li[data-rubrique="' + String(nom).replace(/[^a-z0-9_-]/gi, '') + '"]');
      var a = lireLigne(ancienne), n = lireLigne(neuve);
      if (!aGarder(a, n)) return;
      var copie = ancienne.cloneNode(true);
      var quand = a.garde || heure || '';
      copie.setAttribute('data-ax108-garde', quand);
      copie.setAttribute('data-etat', n.etat);
      var ancienneNote = copie.querySelector('.ax108-garde');
      if (ancienneNote) ancienneNote.remove();
      var note = el('span', (quand ? 'Dernière valeur connue à ' + quand : 'Dernière valeur connue') + ' · ' + (LIBELLES_ETAT[n.etat] || 'source indisponible') +
        ' · ' + (auto ? 'nouvel essai à la prochaine mise à jour' : '« Actualiser » pour réessayer'), 'ax108-garde');
      var texte = copie.querySelector('.ax95-texte') || copie;
      texte.appendChild(note);
      neuve.replaceWith(copie);
      gardees++;
    });
    zone.replaceChildren(modele.content);
    return gardees;
  }

  /* ------------------------------------------------------------------ reçu d'une demande à l'assistant */
  function recuNoeud(id, code, libelleDetaille) {
    var p = el('p', '', 'ax108-recu');
    p.setAttribute('data-etape', code || '');
    var num = numero(id);
    p.appendChild(el('span', 'Reçu n° ' + num, 'ax108-recu-numero'));
    var ol = el('ol', '', 'ax108-etapes');
    ol.setAttribute('aria-label', 'Étape de la demande');
    var suite = code === 'echec' ? ['recue', 'attente', 'en_cours', 'echec'] : ['recue', 'attente', 'en_cours', 'resultat'];
    suite.forEach(function (c) {
      var li = el('li', libelleEtape(c));
      if (c === code) { li.setAttribute('aria-current', 'step'); if (libelleDetaille && libelleDetaille !== libelleEtape(c)) li.title = libelleDetaille; }
      ol.appendChild(li);
    });
    p.appendChild(ol);
    if (!racine.location || !/\/missions$/.test(racine.location.pathname)) {
      var a = el('a', 'Retrouver dans Missions', 'ax108-recu-lien');
      a.href = prefix + '/missions#mission-' + id;
      p.appendChild(a);
    }
    return p;
  }
  /* Carte d'une mission rendue par v567.js : reçu sous la ligne d'état. Réponse immédiate (aucune mission enregistrée) : dit telle quelle. */
  function recu(m, carte) {
    if (!carte || !m) return;
    var ancien = carte.querySelector(':scope > .ax108-recu'); if (ancien) ancien.remove();
    var noeud;
    if (m.id && numero(m.id)) noeud = recuNoeud(m.id, etape(m), (m.panneau && m.panneau.libelle) || m.label || '');
    else { noeud = el('p', 'Réponse immédiate dans le fil · aucune mission enregistrée', 'ax108-recu ax108-recu-absent'); noeud.setAttribute('data-etape', 'immediate'); }
    var etat = carte.querySelector('.mission567-state');
    if (etat && etat.nextSibling) carte.insertBefore(noeud, etat.nextSibling.nextSibling || null); else carte.appendChild(noeud);
  }
  /* Carte posée par le navigateur avant tout reçu : jamais de numéro, l'absence de reçu est écrite. */
  var SANS_RECU = { envoi: 'Pas encore de reçu : transmission au serveur en cours.', incertaine: 'Aucun reçu du serveur : demande à vérifier avant tout nouvel essai.',
                    refusee: 'Aucun reçu : demande refusée, rien n’a été enregistré.' };
  function recuProvisoire(code, carte) {
    if (!carte || !SANS_RECU[code]) return;
    var p = el('p', SANS_RECU[code], 'ax108-recu ax108-recu-absent');
    p.setAttribute('data-etape', code === 'refusee' ? 'echec' : 'sans_recu');
    carte.appendChild(p);
  }
  racine.AX108accueil = { aGarder: aGarder, etape: etape, numero: numero, recu: recu, recuProvisoire: recuProvisoire, fusionnerActuellement: fusionnerActuellement };

  /* ------------------------------------------------------------------ page Missions : même reçu sur chaque carte, carte désignée par l'adresse */
  function missions() {
    var cartes = doc.querySelectorAll('#mission567-list .mission567-card[data-mission-id]');
    Array.prototype.forEach.call(cartes, function (c) {
      if (c.querySelector(':scope > .ax108-recu')) return;
      var id = c.getAttribute('data-mission-id');
      if (!numero(id)) return;
      recu({ id: id, panneau: { code: c.getAttribute('data-etat') || '' }, state: '' }, c);
    });
    var cible = /^#mission-([0-9a-f]{16,64})$/i.exec(racine.location.hash || '');
    if (!cible) return;
    var carte = doc.querySelector('#mission567-list .mission567-card[data-mission-id="' + cible[1].toLowerCase() + '"]');
    if (!carte || carte.classList.contains('ax108-cible')) return;
    carte.classList.add('ax108-cible');
    carte.setAttribute('tabindex', '-1');
    carte.scrollIntoView({ block: 'center' });
    carte.focus({ preventScroll: true });
  }
  function demarrer() {
    var liste = doc.getElementById('mission567-list');
    if (!liste) return;
    missions();
    new MutationObserver(missions).observe(liste, { childList: true });   // la liste est relue par v567.js
  }
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', demarrer); else demarrer();
})(typeof window !== 'undefined' ? window : globalThis);
