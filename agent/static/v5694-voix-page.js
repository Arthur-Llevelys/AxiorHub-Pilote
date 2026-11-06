/* AxiorHub 5.6.94 (V08) : Paramètres › Voix : essai d'écoute avec la vitesse choisie, arrêt immédiat. Le reste de la page est rendu par
   le serveur et enregistré par le traitement commun des formulaires (v500.js). Aucun texte n'est lu avant un clic. */
(() => {
  'use strict';
  const page = document.querySelector('[data-v94-page]');
  if (!page) return;
  const etat = page.querySelector('[data-v94-etat]'), texte = page.querySelector('#v94-essai-texte');
  const vitesse = page.querySelector('#v94-vitesse'), valeur = page.querySelector('#v94-vitesse-valeur');
  const lang = document.documentElement.lang || 'fr';
  const affiche = () => { if (vitesse && valeur) valeur.textContent = Number(vitesse.value).toLocaleString(lang, {minimumFractionDigits: 1, maximumFractionDigits: 1}); };
  vitesse?.addEventListener('input', affiche);
  affiche();
  const note = t => { if (etat) etat.textContent = t; };
  let messages = {};
  try { messages = JSON.parse(document.querySelector('#l5631-msg')?.textContent || '{}'); } catch (e) { messages = {}; }
  const msg = (key, fallback) => messages['js.5694.' + key] || fallback;
  page.querySelector('[data-v94-ecouter]')?.addEventListener('click', async event => {
    const bouton = event.currentTarget;
    if (!window.axiorhubVoix) { note(msg('reload', 'Lecture indisponible : rechargez la page.')); return; }
    bouton.disabled = true; bouton.setAttribute('aria-busy', 'true');
    try {
      const r = await window.axiorhubVoix.speak(texte?.value || '', {rate: vitesse ? Number(vitesse.value) : undefined, note});
      const s = window.axiorhubVoixStats || {};
      if (etat) { etat.dataset.v94Voix = r?.provider || ''; etat.dataset.v94Echantillons = String(s.samples || 0); etat.dataset.v94Crete = String(s.peak || 0); }
    } catch (e) {
      if (etat) etat.dataset.v94Erreur = e.message;
    } finally {
      bouton.disabled = false; bouton.setAttribute('aria-busy', 'false');
    }
  });
  page.querySelector('[data-v94-arreter]')?.addEventListener('click', () => { window.axiorhubVoix?.stop(); note(msg('stopped', 'Lecture arrêtée.')); });
})();
