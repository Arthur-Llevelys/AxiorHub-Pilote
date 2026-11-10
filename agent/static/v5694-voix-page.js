/* AxiorHub 5.6.94 (V08) : Paramètres › Voix : essai d'écoute avec la vitesse choisie, arrêt immédiat. Le reste de la page est rendu par
   le serveur et enregistré par le traitement commun des formulaires (v500.js). Aucun texte n'est lu avant un clic.
   5.6.96 : V02, l'état affiché suit le résultat typé de la lecture (aucun « terminé » après une erreur, « Reprendre au segment n ») ;
   V05, « Écouter ce profil » essaie le profil Brindille choisi sans l'enregistrer, « Définir par défaut » l'enregistre pour le compte ;
   V06, aperçu du portrait choisi (module v5696-avatar.js, déclaré sur cette page). */
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
  let messages = {};
  try { messages = JSON.parse(document.querySelector('#l5631-msg')?.textContent || '{}'); } catch (e) { messages = {}; }
  const msg = (key, fallback, values) => {
    let t = messages['js.5694.' + key] || fallback;
    Object.entries(values || {}).forEach(([k, v]) => { t = t.split('{' + k + '}').join(String(v)); });
    return t;
  };
  const meta = name => document.querySelector('meta[name="' + name + '"]')?.content || '';
  const reprise = document.createElement('button');
  reprise.type = 'button'; reprise.className = 'ax-btn ghost v96-reprise'; reprise.hidden = true;
  etat?.after(reprise);

  /* Lecture d'essai : la zone d'état reçoit la voix utilisée (data-v94-voix) ou l'erreur (data-v94-erreur), lues par le banc navigateur. */
  async function essayer(bouton, zone, opts) {
    const note = t => { if (zone) zone.textContent = t; };
    if (!window.axiorhubVoix) { note(msg('reload', 'Lecture indisponible : rechargez la page.')); return; }
    bouton.disabled = true; bouton.setAttribute('aria-busy', 'true'); reprise.hidden = true;
    if (zone) { delete zone.dataset.v94Voix; delete zone.dataset.v94Erreur; }
    try {
      const r = await window.axiorhubVoix.speak(texte?.value || '', Object.assign({rate: vitesse ? Number(vitesse.value) : undefined, note}, opts));
      const s = window.axiorhubVoixStats || {};
      if (zone && r && r.statut === 'termine') { zone.dataset.v94Voix = r.provider || ''; zone.dataset.v94Echantillons = String(s.samples || 0); zone.dataset.v94Crete = String(s.peak || 0); }
      else if (zone && r && r.statut !== 'vide') note(msg('stopped', 'Lecture arrêtée.'));
    } catch (e) {
      if (zone) zone.dataset.v94Erreur = e.message;
      const n = e.resultat?.segment || 0;
      if (n) {
        reprise.textContent = msg('reprendre', 'Reprendre au segment {n}', {n});
        reprise.hidden = false;
        reprise.onclick = () => essayer(bouton, zone, Object.assign({}, opts, {depuis: n}));
      }
    } finally {
      bouton.disabled = false; bouton.setAttribute('aria-busy', 'false');
    }
  }
  page.querySelector('[data-v94-ecouter]')?.addEventListener('click', event => essayer(event.currentTarget, etat, {}));
  page.querySelector('[data-v94-arreter]')?.addEventListener('click', () => { window.axiorhubVoix?.stop(); if (etat) etat.textContent = msg('stopped', 'Lecture arrêtée.'); });

  // V05 : profil Brindille (liste fermée côté serveur) ; l'essai n'enregistre rien
  const choixProfil = page.querySelector('select[name="voix_brindille"]'), etatProfil = page.querySelector('[data-v94-profil-etat]');
  page.querySelector('[data-v94-ecouter-profil]')?.addEventListener('click', event => essayer(event.currentTarget, etatProfil, {profil: choixProfil?.value || ''}));
  page.querySelector('[data-v94-defaut]')?.addEventListener('click', async event => {
    const bouton = event.currentTarget;
    bouton.disabled = true;
    try {
      const r = await fetch(meta('axiorhub-prefix') + '/api440/m567/profile', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': meta('axiorhub-csrf')}, body: JSON.stringify({voix_brindille: choixProfil?.value || ''})});
      const v = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(v.message || v.error || 'HTTP ' + r.status);
      if (etatProfil) etatProfil.textContent = msg('profil_enregistre', 'Profil de voix enregistré pour votre compte : discussion, lecture et briefing.');
    } catch (e) {
      if (etatProfil) etatProfil.textContent = msg('profil_refuse', 'Profil non enregistré : {raison}', {raison: e.message});
    } finally {
      bouton.disabled = false;
    }
  });

  // V06 : aperçu du portrait choisi, fixe (aucune animation, aucun son sur cette page)
  const choixAvatar = page.querySelector('select[name="avatar"]'), apercu = page.querySelector('[data-v94-apercu-avatar]');
  const montrer = () => {
    if (!apercu) return;
    apercu.replaceChildren();
    const portrait = window.axiorhubAvatar && choixAvatar ? window.axiorhubAvatar.creer(choixAvatar.value) : null;
    if (portrait) apercu.append(portrait.element);
  };
  choixAvatar?.addEventListener('change', montrer);
  montrer();
  document.addEventListener('DOMContentLoaded', montrer, {once: true});   // le module d'avatar, différé lui aussi, peut s'exécuter après ce script
})();
