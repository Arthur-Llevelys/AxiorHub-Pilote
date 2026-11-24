/* AxiorHub 5.6.109 (lot H, F10) : couleur du menu de gauche, propre au compte — même calcul sRGB que le serveur (apparence56109) et que la
   référence du dossier technique (apparence109.mjs). Les couleurs viennent des attributs data-ax109-* posés par le serveur ; elles sont
   revalidées ici avant toute modification ; les variables sont posées par CSSOM (style.setProperty), seulement sur #ws-sidebar.
   Le contrôleur de thème de v320.js reste l'autorité du mode clair / sombre : ce script suit son attribut data-theme. */
(function () {
  'use strict';
  var HEX = /^#[0-9a-f]{6}$/i;
  function normaliserHex(v) { if (typeof v !== 'string' || !HEX.test(v)) { throw new TypeError('couleur_hex_6_requise'); } return v.toLowerCase(); }
  function rgb(v) { return normaliserHex(v).slice(1).match(/../g).map(function (x) { return parseInt(x, 16); }); }
  function luminance(v) {
    return rgb(v).reduce(function (s, c, i) { c = c / 255; var l = c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); return s + l * [0.2126, 0.7152, 0.0722][i]; }, 0);
  }
  function contraste(a, b) { var x = [luminance(a), luminance(b)].sort(function (p, q) { return p - q; }); return (x[1] + 0.05) / (x[0] + 0.05); }
  function texte(f) { return contraste(f, '#000000') >= contraste(f, '#ffffff') ? '#000000' : '#ffffff'; }
  function melanger(f, a, p) { var x = rgb(f), y = rgb(a); return '#' + x.map(function (n, i) { return ('0' + Math.round(n * (1 - p) + y[i] * p).toString(16)).slice(-2); }).join(''); }
  function palette(c) {
    var fond = normaliserHex(c), pp = texte(fond), survol = melanger(fond, pp, 0.10), actif = melanger(fond, pp, 0.18);
    return { fond: fond, texte: pp, secondaire: pp, survol: survol, texteSurvol: texte(survol), actif: actif, texteActif: texte(actif), marqueur: texte(actif), focusInterieur: '#ffffff', focusExterieur: '#000000' };
  }
  function variables(c) {
    var p = palette(c);
    return { '--ax-nav-bg': p.fond, '--ax-nav-fg': p.texte, '--ax-nav-muted': p.secondaire, '--ax-nav-hover': p.survol, '--ax-nav-hover-fg': p.texteSurvol,
      '--ax-nav-active': p.actif, '--ax-nav-active-fg': p.texteActif, '--ax-nav-marker': p.marqueur, '--ax-nav-focus-inner': p.focusInterieur,
      '--ax-nav-focus-outer': p.focusExterieur, '--ui-nav': p.fond, '--ui-nav-texte': p.texte, '--ui-nav-actif': p.actif, '--ui-nav-accent': p.marqueur,
      '--ui-nav-bord': p.texte, '--ws-sidebar': p.fond, '--ws-sidebar-text': p.texte, '--ws-sidebar-border': p.texte };
  }
  function appliquer(cible, c) {
    var v = variables(c);                                   // validé avant toute modification
    Object.keys(v).forEach(function (k) { cible.style.setProperty(k, v[k]); });
  }
  function retirer(cible) { Object.keys(variables('#000000')).forEach(function (k) { cible.style.removeProperty(k); }); }
  window.AX109 = { contraste: contraste, palette: palette, variables: variables, normaliserHex: normaliserHex };

  var root = document.documentElement;
  var compte = (document.querySelector('meta[name="axiorhub-compte"]') || {}).content || 'c';

  /* ------------------------------------------------------------------ barre latérale */
  function barre() {
    var side = document.getElementById('ws-sidebar');
    if (!side) return;
    var mode = side.dataset.ax109Mode, rev = side.dataset.ax109Rev;
    // mode enregistré sur le serveur (référence) : repris une fois par révision, puis le bouton de thème garde la main
    if (mode && rev) {
      try {
        var vu = localStorage.getItem('ax109-rev:' + compte);
        if (vu !== rev) {
          if (mode === 'system') localStorage.removeItem('axiorhub-theme-v320'); else localStorage.setItem('axiorhub-theme-v320', mode);
          localStorage.setItem('ax109-rev:' + compte, rev);
          var sombre = mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
          root.dataset.theme = sombre ? 'dark' : 'light';
        }
      } catch (e) { /* navigation privée : le thème courant reste */ }
    }
    function maj() {
      var c = root.dataset.theme === 'dark' ? side.dataset.ax109NavDark : side.dataset.ax109NavLight;
      try {
        if (c) { appliquer(side, c); side.classList.add('ax-nav-custom'); } else { retirer(side); side.classList.remove('ax-nav-custom'); }
      } catch (e) { retirer(side); side.classList.remove('ax-nav-custom'); }   // valeur refusée : rendu du produit
    }
    maj();
    new MutationObserver(maj).observe(root, { attributes: true, attributeFilter: ['data-theme'] });
  }

  /* ------------------------------------------------------------------ Paramètres › Apparence */
  function reglages() {
    var s = document.querySelector('[data-ax109-apparence]');
    if (!s) return;
    var meta = function (n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; };
    var API = (meta('axiorhub-prefix') || '') + '/api440/m56109/apparence/', csrf = meta('axiorhub-csrf');
    var etat = s.querySelector('[data-ax109-etat]'), meme = s.querySelector('[data-ax109-meme]'), apercu = s.querySelector('[data-ax109-apercu]');
    var champs = { clair: s.querySelector('[data-ax109-hex=clair]'), sombre: s.querySelector('[data-ax109-hex=sombre]') };
    var choix = { clair: s.querySelector('[data-ax109-choix=clair]'), sombre: s.querySelector('[data-ax109-choix=sombre]') };
    var initial = { clair: champs.clair.value, sombre: champs.sombre.value, meme: meme.checked, mode: s.querySelector('[data-ax109-mode]').value, epingle: s.querySelector('[data-ax109-epingle]').checked };
    s.querySelectorAll('[data-ax109-couleur]').forEach(function (p) { try { p.style.setProperty('background', normaliserHex(p.dataset.ax109Couleur)); } catch (e) { /* */ } });
    var dernier = 'clair';
    function mesure(nom) {
      var sortie = s.querySelector('[data-ax109-mesure=' + nom + ']'), v = champs[nom].value.trim();
      if (!v) { sortie.textContent = 'Couleur du produit (aucune surcharge).'; return true; }
      try {
        var p = palette(v);
        sortie.textContent = 'Contraste du texte ' + contraste(p.fond, p.texte).toFixed(2) + ':1 · survol ' + contraste(p.survol, p.texteSurvol).toFixed(2) + ':1 · page active ' + contraste(p.actif, p.texteActif).toFixed(2) + ':1';
        champs[nom].removeAttribute('aria-invalid'); return true;
      } catch (e) { sortie.textContent = 'Couleur invalide : saisissez un code #RRGGBB.'; champs[nom].setAttribute('aria-invalid', 'true'); return false; }
    }
    function previsualiser() {
      champs.sombre.disabled = choix.sombre.disabled = meme.checked;
      if (meme.checked) { champs.sombre.value = champs.clair.value; }
      var c = (dernier === 'sombre' && !meme.checked ? champs.sombre.value : champs.clair.value).trim();
      try { if (c) { appliquer(apercu, c); apercu.classList.add('ax-nav-custom'); } else { retirer(apercu); apercu.classList.remove('ax-nav-custom'); } } catch (e) { retirer(apercu); }
      mesure('clair'); mesure('sombre');
    }
    ['clair', 'sombre'].forEach(function (nom) {
      champs[nom].addEventListener('input', function () { dernier = nom; if (HEX.test(champs[nom].value)) choix[nom].value = champs[nom].value.toLowerCase(); previsualiser(); });
      choix[nom].addEventListener('input', function () { dernier = nom; champs[nom].value = choix[nom].value; previsualiser(); });
    });
    meme.addEventListener('change', previsualiser);
    s.querySelectorAll('[data-ax109-preset]').forEach(function (b) {
      b.addEventListener('click', function () { var cible = meme.checked ? 'clair' : dernier; champs[cible].value = b.dataset.ax109Preset; choix[cible].value = b.dataset.ax109Preset; previsualiser(); });
    });
    function poster(action, corps) {
      corps.cle = 'ax109-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
      corps.revision = parseInt(s.dataset.ax109Revision, 10);
      return fetch(API + action, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(corps) })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.message || 'Demande refusée.'); return d; }); });
    }
    function apres(p) {
      p.then(function (d) {
        try { localStorage.removeItem('ax109-rev:' + compte); } catch (e) { /* */ }
        etat.textContent = 'Enregistré. La page va se recharger.'; s.dataset.ax109Revision = d.preference.revision; setTimeout(function () { location.reload(); }, 500);
      }).catch(function (e) { etat.textContent = e.message; });
    }
    s.querySelector('[data-ax109-enregistrer]').addEventListener('click', function () {
      if (!mesure('clair') || !mesure('sombre')) { etat.textContent = 'Corrigez la couleur avant d’enregistrer.'; return; }
      apres(poster('enregistrer', { valeur: { mode: s.querySelector('[data-ax109-mode]').value, pin_ai_history: s.querySelector('[data-ax109-epingle]').checked,
        sidebar: { same_for_both: meme.checked, light: champs.clair.value.trim() || null, dark: (meme.checked ? champs.clair.value : champs.sombre.value).trim() || null } } }));
    });
    s.querySelector('[data-ax109-annuler]').addEventListener('click', function () {
      champs.clair.value = initial.clair; champs.sombre.value = initial.sombre; meme.checked = initial.meme;
      s.querySelector('[data-ax109-mode]').value = initial.mode; s.querySelector('[data-ax109-epingle]').checked = initial.epingle;
      previsualiser(); etat.textContent = 'Modifications annulées : rien n’a été enregistré.';
    });
    s.querySelector('[data-ax109-defaut]').addEventListener('click', function () { apres(poster('reinitialiser', {})); });
    previsualiser();
  }

  function demarrer() { barre(); reglages(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', demarrer); else demarrer();
})();
