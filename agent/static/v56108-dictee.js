/* 5.6.108 (lot Q) : « Dicter la réponse » à côté de chaque question posée à l'avocat — questionnaire de l'acte, questions regroupées d'une
   mission (grille, « Informations à obtenir » du conseil). Réutilise la dictée commune (window.axiorhubDictee de v56106-redaction.js : micro
   par window.axiorhubMic, purpose « dictee », transcription locale par le serveur du cabinet, ou mode « Rapide » du navigateur avec son
   avertissement) : aucune bibliothèque nouvelle, aucun autre envoi d'audio, mêmes réglages de confidentialité.
   Clic : début (pastille et durée) ; second clic ou silence : fin. Le texte s'ajoute au curseur sans effacer la saisie ; date et montant :
   la valeur n'est posée que si elle est valide, sinon elle s'affiche à côté pour correction. Rien n'est envoyé au serveur avant
   « Enregistrer ». Micro indisponible, refusé ou dictée désactivée : bouton inactif (mais focusable) avec une info-bulle claire. */
(function (racine) {
  'use strict';
  var MOIS = { janvier: 1, fevrier: 2, mars: 3, avril: 4, mai: 5, juin: 6, juillet: 7, aout: 8, septembre: 9, octobre: 10, novembre: 11, decembre: 12,
               january: 1, february: 2, march: 3, april: 4, june: 6, july: 7, august: 8, september: 9, october: 10, november: 11, december: 12,
               januar: 1, februar: 2, marz: 3, juni: 6, juli: 7, oktober: 10, dezember: 12,
               enero: 1, febrero: 2, marzo: 3, abril: 4, mayo: 5, junio: 6, julio: 7, agosto: 8, septiembre: 9, octubre: 10, noviembre: 11, diciembre: 12 };
  function plat(t) { return String(t || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function iso(a, m, j) {
    if (!(a >= 1000 && a <= 9999 && m >= 1 && m <= 12 && j >= 1 && j <= 31)) return null;
    var d = new Date(Date.UTC(a, m - 1, j));
    if (d.getUTCFullYear() !== a || d.getUTCMonth() !== m - 1 || d.getUTCDate() !== j) return null;   // 30 février : refusé
    return a + '-' + ('0' + m).slice(-2) + '-' + ('0' + j).slice(-2);
  }
  /* Valeur dictée pour un champ typé : chaîne valide (même forme que le serveur), ou null si elle ne l'est pas. Texte : tel quel. */
  function normaliser(type, texte) {
    var t = String(texte || '').trim().replace(/[.。]+$/, '');
    if (!t) return null;
    if (type === 'date') {
      var m = t.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
      if (m) return iso(+m[1], +m[2], +m[3]);
      m = t.match(/^(\d{1,2})[\/.\- ](\d{1,2})[\/.\- ](\d{4})$/);
      if (m) return iso(+m[3], +m[2], +m[1]);
      m = plat(t).match(/^(?:le\s+)?(\d{1,2}|1er|premier)\s*(?:er)?\.?\s+(?:de\s+)?([a-z]+)\s+(?:de\s+)?(\d{4})$/);
      if (m && MOIS[m[2]]) return iso(+m[3], MOIS[m[2]], /^(1er|premier)$/.test(m[1]) ? 1 : +m[1]);
      return null;
    }
    if (type === 'montant') {
      var b = plat(t), avant = null;
      while (avant !== b) { avant = b; b = b.replace(/\s*(euros?|eur|€|hors taxes|ht|ttc)\s*$/, ''); }
      b = b.replace(/[\s  ]/g, '');
      // points de milliers seulement sans ambiguïté (125.000,50 ou 1.250.000) ; « 1.234 » seul reste refusé, comme au serveur
      b = b.replace(/^(\d{1,3}(?:\.\d{3})+)(,\d{1,2})$|^(\d{1,3}(?:\.\d{3}){2,})$/, function (_, e, d, f) { return (e || f).replace(/\./g, '') + (d || ''); });
      if (!/^\d{1,18}(?:[.,]\d{1,2})?$/.test(b)) return null;
      var p = b.replace(',', '.').split('.');
      return p[0].replace(/^0+(?=\d)/, '') + '.' + ((p[1] || '') + '00').slice(0, 2);
    }
    return t;
  }
  /* Insertion au curseur sans rien effacer (sélection remplacée seulement si l'avocat l'a sélectionnée), longueur bornée. */
  function inserer(valeur, debut, fin, texte, multiligne, max) {
    valeur = String(valeur || '');
    var a = debut == null ? valeur.length : debut, z = fin == null ? valeur.length : fin;
    var avant = valeur.slice(0, a), apres = valeur.slice(z);
    var t = String(texte || '');
    if (!multiligne) t = t.replace(/[\r\n]+/g, ' ');
    t = (avant && !/\s$/.test(avant) ? ' ' : '') + t + (apres && !/^\s/.test(apres) ? ' ' : '');
    var out = avant + t + apres;
    if (max && out.length > max) return null;
    return { valeur: out, curseur: avant.length + t.length };
  }

  if (typeof module !== 'undefined' && module.exports) { module.exports = { normaliser: normaliser, inserer: inserer }; return; }

  var doc = racine.document;
  var messages = null;
  function msg(cle, defaut, valeurs) {
    if (messages === null) { try { messages = JSON.parse((doc.querySelector('#l5631-msg') || {}).textContent || '{}'); } catch (e) { messages = {}; } }
    var t = messages['js.56108q.' + cle] || defaut;
    Object.keys(valeurs || {}).forEach(function (k) { t = t.split('{' + k + '}').join(String(valeurs[k])); });
    return t;
  }
  var MIC = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></svg>';
  function meta(n) { return (doc.querySelector('meta[name="' + n + '"]') || {}).content || ''; }
  function mode() { return racine.axiorhubDictee && racine.axiorhubDictee.mode ? racine.axiorhubDictee.mode() : 'local'; }
  /* Motif d'indisponibilité ('' si la dictée peut démarrer). */
  function motif() {
    if (!racine.axiorhubDictee) return msg('indisponible', 'Dictée indisponible sur cette page.');
    if (mode() === 'browser') return (racine.SpeechRecognition || racine.webkitSpeechRecognition) ? '' : msg('indisponible', 'Micro indisponible sur ce poste ou dans ce navigateur.');
    if (meta('axiorhub-audio') !== 'enabled') return msg('desactivee', 'Dictée désactivée dans Paramètres (Vocal local) : demandez à l’administrateur de l’activer.');
    if (!racine.navigator.mediaDevices || !racine.navigator.mediaDevices.getUserMedia || !racine.MediaRecorder) return msg('indisponible', 'Micro indisponible sur ce poste ou dans ce navigateur.');
    return '';
  }
  function etat(bouton, raison) {
    var titre = raison || msg('dicter', 'Dicter la réponse');
    bouton.setAttribute('aria-disabled', raison ? 'true' : 'false');
    bouton.setAttribute('data-titre', titre);
    if (!bouton.classList.contains('is-rec')) { bouton.title = titre; bouton.setAttribute('aria-label', raison ? msg('dicter', 'Dicter la réponse') + ' — ' + raison : titre); }
  }
  /* monter(champ, {type: 'texte'|'partie'|'date'|'montant', apres: élément après lequel placer le bouton}) : rend le bouton, ou null. */
  function monter(champ, opts) {
    opts = opts || {};
    if (!champ || champ.getAttribute('data-ax108-dictee')) return null;
    champ.setAttribute('data-ax108-dictee', '1');
    var type = opts.type || 'texte';
    var bouton = doc.createElement('button');
    bouton.type = 'button'; bouton.className = 'ax108-dictee'; bouton.innerHTML = MIC;
    var statut = doc.createElement('span');
    statut.className = 'ax108-dictee-statut'; statut.setAttribute('role', 'status'); statut.setAttribute('aria-live', 'polite');
    var place = opts.apres || champ;
    place.parentNode.insertBefore(statut, place.nextSibling);
    place.parentNode.insertBefore(bouton, place.nextSibling);
    etat(bouton, motif());
    bouton.addEventListener('click', function (ev) {   // inactif mais focusable : le clic ne démarre rien, l'info-bulle dit pourquoi
      if (bouton.getAttribute('aria-disabled') === 'true') { ev.preventDefault(); ev.stopImmediatePropagation(); statut.textContent = bouton.getAttribute('data-titre'); }
    }, true);
    if (racine.navigator.permissions && racine.navigator.permissions.query) {
      racine.navigator.permissions.query({ name: 'microphone' }).then(function (p) {
        var suivre = function () { etat(bouton, p.state === 'denied' && mode() !== 'browser' ? msg('refuse', 'Micro refusé : autorisez le micro pour ce site dans le navigateur, puis rechargez la page.') : motif()); };
        suivre(); p.onchange = suivre;
      }).catch(function () { /* permission non interrogeable : l'erreur éventuelle s'affichera au démarrage */ });
    }
    if (!racine.axiorhubDictee) return bouton;
    var pos = null;
    racine.axiorhubDictee.attacher(bouton, {
      statut: statut, silence: true, messageInsere: msg('insere', 'Texte inséré au curseur : relisez-le avant d’enregistrer.'),
      avant: function () { pos = typeof champ.selectionStart === 'number' ? [champ.selectionStart, champ.selectionEnd] : null; },
      inserer: function (texte) {
        if (type === 'date' || type === 'montant') {
          var v = normaliser(type, texte);
          if (v === null) return msg('a_corriger', 'Reconnu : « {texte} » — valeur non valide pour ce champ : corrigez-la à la main.', { texte: texte });
          champ.value = v;
        } else {
          var max = parseInt(champ.getAttribute('maxlength') || '0', 10) || 0;
          var r = inserer(champ.value, pos ? pos[0] : null, pos ? pos[1] : null, texte, champ.tagName === 'TEXTAREA', max);
          if (!r) return msg('trop_long', 'Texte trop long pour ce champ : rien n’a été coupé, raccourcissez la saisie.');
          champ.value = r.valeur;
          try { champ.focus(); champ.setSelectionRange(r.curseur, r.curseur); } catch (e) { /* champ sans sélection */ }
        }
        champ.dispatchEvent(new Event('input', { bubbles: true }));   // brouillon local seulement : rien n'est enregistré avant « Enregistrer »
      }
    });
    return bouton;
  }
  racine.axiorhubDictee108 = { monter: monter, normaliser: normaliser, inserer: inserer };
})(typeof window !== 'undefined' ? window : this);
