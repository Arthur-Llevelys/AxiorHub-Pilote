/* AxiorHub 5.6.96 (V06) : avatar 2D facultatif de l'assistant vocal. Deux portraits vectoriels originaux, dessinés ici en SVG : aucun
   média, aucune photographie, aucune personne réelle, aucune requête. window.axiorhubAvatar.creer(cle) rend {element, cle, bouche(niveau)} :
   bouche(0 à 1) ouvre la bouche selon le niveau sonore local que l'appelant lit sur la lecture Brindille (window.axiorhubVoix.niveau()) ;
   bouche(0) remet le portrait au repos. Ce module ne touche ni au micro, ni au réseau, ni à la saisie. Il n'est déclaré dans une page que si
   le compte a choisi un portrait (agent/avatar5696.py), sur la page Paramètres › Voix (aperçu), ou chargé par v568.js au moment du choix. */
(() => {
  'use strict';
  if (window.axiorhubAvatar) return;
  const NS = 'http://www.w3.org/2000/svg';
  // Teintes fixes (un visage garde ses couleurs quel que soit le thème) ; le fond du médaillon suit le thème par sa classe (v5694-voix.css).
  const PORTRAITS = {
    portrait_a: {nom: 'Portrait A', peau: '#e2b493', cheveux: '#4b3428', haut: '#2f4d6c', bouche: '#7b3c45', coiffure: 'carre'},
    portrait_b: {nom: 'Portrait B', peau: '#b07658', cheveux: '#211c1a', haut: '#3a5a51', bouche: '#5e2f36', coiffure: 'court'},
  };
  const svg = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, String(v)));
    return n;
  };
  function cheveuxArriere(c) {
    if (c.coiffure === 'carre') return svg('path', {d: 'M30 52 Q27 20 60 17 Q93 20 90 52 L88 84 L32 84 Z', fill: c.cheveux});   // carré mi-long
    return svg('path', {d: 'M33 50 Q32 24 60 20 Q88 24 87 50 L86 60 L34 60 Z', fill: c.cheveux});                              // coupe courte
  }
  function cheveuxAvant(c) {
    if (c.coiffure === 'carre') return svg('path', {d: 'M34 50 Q36 22 60 21 Q84 22 86 50 Q78 38 66 36 Q56 44 34 50 Z', fill: c.cheveux});   // frange de côté
    const groupe = svg('g', {fill: c.cheveux});
    [[40, 34, 9], [52, 28, 10], [66, 27, 10], [78, 33, 9], [85, 44, 7], [35, 44, 7]].forEach(([cx, cy, r]) => groupe.append(svg('circle', {cx, cy, r})));
    return groupe;
  }
  function dessiner(c) {
    const image = svg('svg', {viewBox: '0 0 120 120', class: 'v96-portrait', role: 'img', 'aria-label': c.nom + ' · avatar illustré', focusable: 'false'});
    image.append(
      svg('circle', {cx: 60, cy: 60, r: 60, class: 'v96-fond'}),
      svg('path', {d: 'M14 120 Q18 95 46 92 L74 92 Q102 95 106 120 Z', fill: c.haut}),                                   // épaules
      svg('path', {d: 'M50 78 L70 78 L73 96 Q60 106 47 96 Z', fill: c.peau}),                                            // cou
      cheveuxArriere(c),
      svg('ellipse', {cx: 35, cy: 60, rx: 5.5, ry: 8, fill: c.peau}),                                                     // oreilles
      svg('ellipse', {cx: 85, cy: 60, rx: 5.5, ry: 8, fill: c.peau}),
      svg('path', {d: 'M36 48 Q37 24 60 23 Q83 24 84 48 L83 64 Q79 86 60 90 Q41 86 37 64 Z', fill: c.peau}),               // visage
      cheveuxAvant(c),
      svg('path', {d: 'M44 50 Q49 47 55 49 M65 49 Q71 47 76 50', fill: 'none', stroke: c.cheveux, 'stroke-width': 2, 'stroke-linecap': 'round'}),   // sourcils
      svg('ellipse', {cx: 50, cy: 56, rx: 3, ry: 2.6, fill: '#2b2a2a'}),                                                  // yeux
      svg('ellipse', {cx: 70, cy: 56, rx: 3, ry: 2.6, fill: '#2b2a2a'}),
      svg('path', {d: 'M60 57 L57.5 67 Q60 69 63 67', fill: 'none', stroke: c.bouche, 'stroke-width': 1.3, 'stroke-linecap': 'round', opacity: .8})   // nez
    );
    const bouche = svg('ellipse', {cx: 60, cy: 76, rx: 7, ry: 1.4, fill: c.bouche, class: 'v96-bouche'});
    image.append(bouche);
    return {image, bouche};
  }
  /* Portrait prêt à insérer : <figure> avec l'image et sa légende. */
  function creer(cle) {
    const c = PORTRAITS[cle];
    if (!c) return null;
    const {image, bouche} = dessiner(c);
    const figure = document.createElement('figure'), legende = document.createElement('figcaption');
    figure.className = 'v96-figure'; legende.textContent = c.nom + ' · avatar illustré';
    figure.append(image, legende);
    return {
      element: figure,
      cle,
      bouche(niveau) {
        const n = Math.min(1, Math.max(0, Number(niveau) || 0));
        bouche.setAttribute('ry', (1.4 + n * 5).toFixed(2));
        bouche.setAttribute('rx', (7 - n * 1.5).toFixed(2));
      }
    };
  }
  window.axiorhubAvatar = {creer, portraits: Object.keys(PORTRAITS), libelle: cle => (PORTRAITS[cle] ? PORTRAITS[cle].nom : '')};
})();
