/* AxiorHub 5.6.96 (lot affichage, U05 / N01) — mesure du retour à une liste, à coller dans la console du navigateur sur la page de liste
   (cabinet fictif de préférence ; sur le serveur du cabinet, c'est le cabinet qui la lance). Rien n'est envoyé ailleurs, rien n'est écrit :
   le résultat ne contient que des durées et des nombres (aucune adresse, aucun titre, aucun filtre, aucune donnée de dossier).

   Trois mesures distinctes :
   1. la navigation qui vient d'afficher cette page : attente du premier octet (TTFB), DOM interactif, premier affichage, page chargée ;
   2. les ressources statiques de cette navigation (scripts, styles, polices, images de /static/) : nombre, octets transférés (0 = cache),
      durée la plus longue ;
   3. trente passages de la même adresse par fetch (même origine, même session, sans cache) : TTFB et durée complète (médiane, 95e
      centile), et l'en-tête Server-Timing du serveur (authentification, SQLite et nombre de requêtes, construction, total).
   Usage : coller, puis attendre « mesure terminée » ; await window.__axMesureRetour donne l'objet. */
(async () => {
  'use strict';
  const PASSAGES = 30;
  const ms = n => (Number.isFinite(n) ? Math.round(n * 10) / 10 : null);
  const centile = (valeurs, c) => { const v = [...valeurs].sort((a, b) => a - b); return v.length ? ms(v[Math.min(v.length - 1, Math.ceil(v.length * c) - 1)]) : null; };
  const nav = performance.getEntriesByType('navigation')[0];
  const fcp = performance.getEntriesByName('first-contentful-paint')[0];
  const statiques = performance.getEntriesByType('resource').filter(r => { try { const u = new URL(r.name); return u.origin === location.origin && u.pathname.includes('/static/'); } catch (e) { return false; } });
  const navigation = {
    type: nav ? nav.type : 'inconnu',
    ttfb_ms: nav ? ms(nav.responseStart - nav.requestStart) : null,
    dom_interactif_ms: nav ? ms(nav.domInteractive) : null,
    premier_affichage_ms: fcp ? ms(fcp.startTime) : null,
    page_chargee_ms: nav ? ms(nav.loadEventEnd) : null,
  };
  const ressources = {
    nombre: statiques.length,
    depuis_le_cache: statiques.filter(r => r.transferSize === 0).length,
    octets_transferes: statiques.reduce((n, r) => n + (r.transferSize || 0), 0),
    duree_max_ms: statiques.length ? ms(Math.max(...statiques.map(r => r.duration))) : null,
  };
  const ttfb = [], complet = [], serveur = {};
  const adresse = location.pathname + location.search;      // la page courante elle-même ; jamais écrite dans le résultat
  for (let i = 0; i < PASSAGES; i++) {
    const t0 = performance.now();
    const r = await fetch(adresse, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'text/html' } });
    ttfb.push(performance.now() - t0);
    await r.arrayBuffer();
    complet.push(performance.now() - t0);
    (r.headers.get('Server-Timing') || '').split(',').forEach(part => {
      const nom = part.trim().split(';')[0], dur = /dur=([\d.]+)/.exec(part), desc = /desc="?(\d+)/.exec(part);
      if (!nom || !dur) return;
      (serveur[nom] = serveur[nom] || []).push(Number(dur[1]));
      if (nom === 'sqlite' && desc) (serveur.requetes_sqlite = serveur.requetes_sqlite || []).push(Number(desc[1]));
    });
  }
  const resume = v => ({ mediane: centile(v, 0.5), p95: centile(v, 0.95), max: ms(Math.max(...v)) });
  const resultat = {
    navigation, ressources_statiques: ressources,
    passages: { nombre: PASSAGES, ttfb_ms: resume(ttfb), complet_ms: resume(complet) },
    serveur: Object.fromEntries(Object.entries(serveur).map(([k, v]) => [k, resume(v)])),
  };
  window.__axMesureRetour = resultat;
  console.table(navigation); console.table(ressources); console.table(resultat.passages); console.table(resultat.serveur);
  console.log('mesure terminée');
  return resultat;
})();
