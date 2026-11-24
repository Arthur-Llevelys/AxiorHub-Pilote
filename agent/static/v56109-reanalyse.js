/* AxiorHub 5.6.109 (lot A, F03) — bloc « Analyse du dossier » de la fiche.
   Le bloc est rendu par le serveur et fonctionne sans ce script (formulaires POST communs, CSRF et clé d'idempotence rendus par le serveur).
   Ce script ajoute seulement : le suivi pendant qu'un travail est vivant (relecture de l'état réel toutes les cinq secondes, jamais une
   durée supposée), la fermeture du dialogue de lancement par Échap, et le retour au même endroit de la page après un rechargement.
   Tout texte reçu passe par textContent ; aucun HTML du serveur n'est injecté. */
(function () {
  'use strict';
  var bloc = document.querySelector('[data-a109]');
  if (!bloc) { return; }
  var api = bloc.getAttribute('data-a109-api') || '';
  var dossier = bloc.getAttribute('data-a109-dossier') || '';
  var CLE = 'a109:retour';

  // ---- retour au même endroit après un rechargement demandé par une action du bloc
  function memoriser() {
    try { sessionStorage.setItem(CLE, JSON.stringify({ u: location.pathname + location.search, y: window.scrollY || 0 })); } catch (err) { /* stockage refusé : rien */ }
  }
  try {
    var garde = JSON.parse(sessionStorage.getItem(CLE) || 'null');
    sessionStorage.removeItem(CLE);
    if (garde && garde.u === location.pathname + location.search && typeof garde.y === 'number') { window.scrollTo(0, garde.y); }
  } catch (err) { /* rien à restaurer */ }
  bloc.addEventListener('ax:recu', memoriser);

  // ---- dialogue de lancement : Échap le referme et rend le focus au bouton
  var lancer = bloc.querySelector('[data-a109-lancer]');
  if (lancer) {
    lancer.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && lancer.open) {
        lancer.open = false;
        var s = lancer.querySelector('summary'); if (s) { s.focus(); }
      }
    });
  }

  // ---- suivi : l'état est relu tant qu'un travail est vivant, puis la page est rechargée une fois
  if (!bloc.getAttribute('data-a109-actif') || !api || !dossier) { return; }
  var resume = bloc.querySelector('[data-a109-resume]');
  var etat = bloc.querySelector('[data-a109-etat]');
  var pannes = 0;
  function dire(texte) { if (resume) { resume.textContent = texte; } }
  function appliquer(j) {
    if (etat && typeof j.libelle === 'string') { etat.textContent = j.libelle; }
    (j.etapes || []).forEach(function (s) {
      var li = bloc.querySelector('[data-a109-etape="' + String(s.etape).replace(/[^a-z_]/g, '') + '"]');
      var b = li && li.querySelector('[data-a109-etat-etape]');
      if (b && typeof s.etat_libelle === 'string') { b.textContent = s.etat_libelle; }
    });
  }
  function suivre() {
    fetch(api + 'etat?d=' + encodeURIComponent(dossier), { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (r) {
      if (!r.ok) { dire((r.j && r.j.message) || 'Suivi indisponible : rechargez la page.'); return; }
      pannes = 0;
      appliquer(r.j);
      if (r.j.travail) {
        dire('Travail n° ' + r.j.travail + (r.j.arret_demande ? ' : arrêt demandé, confirmation attendue.' : ' : analyse en cours…'));
        setTimeout(suivre, 5000);
      } else if (r.j.attente) {
        dire('Demande conservée (travail n° ' + r.j.attente.job + ') : ' + r.j.attente.libelle + '.');
        setTimeout(suivre, 20000);
      } else {
        dire('Analyse terminée : état « ' + r.j.libelle + ' ». Mise à jour de la page…');
        memoriser();
        setTimeout(function () { location.reload(); }, 800);
      }
    }).catch(function () {
      pannes += 1;                                 // panne de lecture : l'état affiché reste, on le dit, on réessaie moins souvent
      dire('Suivi momentanément indisponible : l’analyse continue sur le serveur.');
      if (pannes < 20) { setTimeout(suivre, 15000); }
    });
  }
  setTimeout(suivre, 2000);
})();
