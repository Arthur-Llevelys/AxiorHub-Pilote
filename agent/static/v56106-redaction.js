/* AxiorHub 5.6.106 (lot R) — rédaction d'un courriel dans « Courriels » (Nouveau message, Répondre, Répondre à tous, Transférer, brouillon
   rouvert) : barre compacte (icônes, infobulles, menu « Plus »), dictée vocale au curseur, cases ☐ / ☑, titres, alignement, retrait, ligne,
   couleurs et surlignage (palette fixe), tailles, annuler et rétablir, date du jour, adresses proposées depuis les contacts, pièces jointes
   (glisser-déposer, « ✕ », total et plafond), enregistrement automatique, modèles de réponse du cabinet.
   Exposé : window.axiorhubDictee = {attacher, mode, choisirMode} (composant réutilisable) et window.axh56106r (utilisé par v5628.js).
   Rien n'est envoyé ici : « Envoyer » reste l'aperçu puis la confirmation de v5628.js. Le seul HTML inséré dans l'éditeur est celui d'un
   modèle renvoyé par le serveur, déjà nettoyé (liste fermée). Le texte dicté est inséré comme du texte. */
(function () {
  'use strict';
  if (window.axh56106r) return;
  var meta = function (n) { return (document.querySelector('meta[name="' + n + '"]') || {}).content || ''; };
  var prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

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
  // 5.6.107 (lot T) : texte traduit par le dictionnaire des écrans (v5634t.js) ; en français, le texte est rendu tel quel
  function tr(texte) { return window.ax5634Translate ? window.ax5634Translate(texte) : texte; }
  function parTitre(racine, titre) {   // bouton repéré par son infobulle française OU traduite
    var traduit = tr(titre);
    return $$('[title]', racine).filter(function (b) { return b.title === titre || b.title === traduit; })[0] || null;
  }
  function toast(message, bad) {
    if (window.axh5670 && window.axh5670.toast) { window.axh5670.toast(message, bad); return; }
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
    }).then(function (d) { if (d && d.error) throw new Error(d.message || d.error); return d; });
  }
  function cle() {   // clé d'idempotence (32 caractères hexadécimaux)
    var a = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  }
  function taille(n) {
    if (n >= 1048576) return (n / 1048576).toFixed(1).replace('.', ',') + ' Mo';
    return Math.max(1, Math.round(n / 1024)) + ' Ko';
  }

  // =================================================================== dictée (composant réutilisable)
  var MODE_KEY = 'axiorhub-dictation-mode-v310', WARNING_KEY = 'axiorhub-browser-speech-warning-v310';   // mêmes clés que v310.js
  var DUREE_MAX = 60000, OCTETS_MAX = 8000000;
  var ERREURS = {
    dictee_locale_non_configuree: 'Dictée désactivée dans Paramètres (Vocal local) : demandez à l’administrateur de l’activer.',
    pont_vocal_injoignable: 'Le pont vocal local ne répond pas : vérifiez que Vocal local (Whisper) est démarré sur le serveur.',
    transcription_locale_en_echec: 'La transcription locale a échoué. Réessayez.',
    transcription_trop_longue: 'Transcription trop longue : dictez des passages plus courts.',
    transcription_en_cours: 'Une transcription est déjà en cours : réessayez dans un instant.',
    dictee_trop_volumineuse: 'Enregistrement trop long (60 secondes au plus).',
    format_audio_refuse: 'Format audio refusé par le serveur.',
    origine_ou_csrf_refuse: 'Session expirée : rechargez la page.'
  };
  var actif = null;   // une seule dictée à la fois dans la page
  function lireMode() { try { return localStorage.getItem(MODE_KEY) === 'browser' ? 'browser' : 'local'; } catch (e) { return 'local'; } }
  function choisirMode(v) { try { localStorage.setItem(MODE_KEY, v === 'browser' ? 'browser' : 'local'); } catch (e) { /* mode du poste non retenu */ } }
  function duree(ms) { var s = Math.floor(ms / 1000); return Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2); }

  /* attacher(bouton, {inserer(texte), statut: élément role=status (facultatif), avant(): appelé au démarrage (mémoriser le curseur)})
     Rend {arreter}. États : enregistrement (durée), transcription, erreur lisible ; texte jamais envoyé automatiquement. */
  function attacher(bouton, opts) {
    opts = opts || {};
    var dire = function (texte, mauvais) {
      if (opts.statut) { opts.statut.textContent = texte; opts.statut.classList.toggle('is-bad', !!mauvais); }
      else if (texte && mauvais) toast(texte, true);
    };
    var repos = function () {
      bouton.classList.remove('is-rec', 'is-busy'); bouton.setAttribute('aria-pressed', 'false'); bouton.disabled = false;
      bouton.title = bouton.getAttribute('data-titre') || 'Dicter'; bouton.setAttribute('aria-label', bouton.title);
    };
    if (!bouton.getAttribute('data-titre')) bouton.setAttribute('data-titre', bouton.title || 'Dicter');
    bouton.setAttribute('aria-pressed', 'false');
    var inserer = function (texte) {
      texte = String(texte || '').trim();
      if (!texte) { dire('Aucun texte reconnu.', true); return; }
      try {   // 5.6.108 (lot Q) : un texte rendu par inserer() est un avertissement (valeur reconnue non valide, laissée à corriger)
        var r = opts.inserer(texte);
        if (typeof r === 'string') dire(r, true); else dire(opts.messageInsere || 'Texte inséré au curseur : relisez-le.');
      } catch (e) { dire('Le texte n’a pas pu être inséré.', true); }
    };
    function arreter() {
      if (!actif || actif.bouton !== bouton) return;
      if (actif.kind === 'browser') actif.recognition.stop();
      else if (actif.recorder && actif.recorder.state === 'recording') actif.recorder.stop();
    }
    function navigateur() {
      var Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!Speech) { dire('Reconnaissance « Rapide » indisponible dans ce navigateur. Choisissez « Confidentiel — vocal local ».', true); return; }
      var accepte = false;
      try { accepte = localStorage.getItem(WARNING_KEY) === 'accepted'; } catch (e) { accepte = false; }
      if (!accepte) {
        if (!window.confirm('Mode Rapide : selon votre navigateur, l’audio peut être traité par le fournisseur du navigateur. N’utilisez pas ce mode pour une donnée client ou confidentielle. Continuer ?')) {
          dire('Mode rapide annulé. Choisissez « Confidentiel — vocal local ».'); return;
        }
        try { localStorage.setItem(WARNING_KEY, 'accepted'); } catch (e) { /* avertissement redemandé la prochaine fois */ }
      }
      var rec = new Speech(), texte = '', debut = Date.now();
      rec.lang = 'fr-FR'; rec.interimResults = false; rec.continuous = !opts.silence;   // 5.6.108 (lot Q) : opts.silence, le silence termine la dictée
      var etat = { bouton: bouton, kind: 'browser', recognition: rec };
      actif = etat;
      bouton.classList.add('is-rec'); bouton.setAttribute('aria-pressed', 'true'); bouton.title = 'Arrêter la dictée'; bouton.setAttribute('aria-label', 'Arrêter la dictée');
      var horloge = setInterval(function () { dire('● Dictée rapide ' + duree(Date.now() - debut) + ' — aucune donnée confidentielle. Cliquez pour arrêter.'); }, 1000);
      dire('● Dictée rapide 0:00 — aucune donnée confidentielle. Cliquez pour arrêter.');
      rec.onresult = function (ev) { for (var i = ev.resultIndex; i < ev.results.length; i++) if (ev.results[i].isFinal) texte += (texte ? ' ' : '') + ev.results[i][0].transcript; };
      rec.onerror = function (ev) { dire(ev && ev.error === 'not-allowed' ? 'Micro refusé par le navigateur : autorisez-le dans la barre d’adresse.' : 'Dictée rapide interrompue par le navigateur.', true); };
      rec.onend = function () { clearInterval(horloge); actif = null; repos(); if (texte) inserer(texte); };
      try { rec.start(); } catch (e) { clearInterval(horloge); actif = null; repos(); dire('Le micro n’a pas démarré.', true); }
    }
    function local() {
      if (meta('axiorhub-audio') !== 'enabled') { dire(ERREURS.dictee_locale_non_configuree, true); return; }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
        dire('Micro indisponible : utilisez un navigateur récent, en HTTPS.', true); return;
      }
      var etat = { bouton: bouton, kind: 'local', chunks: [], octets: 0, annule: false, trop: false, stream: null, recorder: null };
      actif = etat; bouton.disabled = true; bouton.classList.add('is-busy'); dire('Ouverture du micro…');
      var ouvrir = window.axiorhubMic ? window.axiorhubMic.stream('dictee', { audio: true }) : navigator.mediaDevices.getUserMedia({ audio: true });
      Promise.resolve(ouvrir).then(function (stream) {
        etat.stream = stream;
        if (actif !== etat) { stream.getTracks().forEach(function (tr) { tr.stop(); }); return; }
        var mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].filter(function (x) { return MediaRecorder.isTypeSupported(x); })[0];
        if (!mime) throw Object.assign(new Error('format'), { name: 'FormatError' });
        var rec = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 64000 });
        etat.recorder = rec;
        var debut = Date.now(), note = stream.axiorhubNote ? ' ' + stream.axiorhubNote : '';
        var horloge = setInterval(function () { dire('● Enregistrement ' + duree(Date.now() - debut) + ' (confidentiel, vocal local). Cliquez pour arrêter.' + note); }, 1000);
        var limite = setTimeout(function () { if (rec.state === 'recording') rec.stop(); }, DUREE_MAX);
        rec.ondataavailable = function (ev) {
          etat.octets += ev.data.size;
          if (etat.octets > OCTETS_MAX) { etat.trop = true; if (rec.state === 'recording') rec.stop(); } else etat.chunks.push(ev.data);
        };
        rec.onerror = function () { etat.annule = true; if (rec.state === 'recording') rec.stop(); };
        rec.onstop = function () {
          clearInterval(horloge); clearTimeout(limite); if (etat.silence) etat.silence();
          stream.getTracks().forEach(function (tr) { tr.stop(); });
          if (etat.annule || etat.trop) {
            actif = null; repos(); dire(etat.trop ? ERREURS.dictee_trop_volumineuse : 'Enregistrement interrompu. Votre texte est conservé.', true); return;
          }
          bouton.disabled = true; bouton.classList.remove('is-rec'); bouton.classList.add('is-busy'); bouton.setAttribute('aria-pressed', 'false');
          dire('Transcription locale en cours…');
          fetch(prefix + '/dictation', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': mime.split(';')[0], 'X-CSRF-Token': csrf },
                                         body: new Blob(etat.chunks, { type: mime }) }).then(function (r) {
            return r.json().catch(function () { return { error: 'reponse_invalide' }; }).then(function (d) { return { ok: r.ok, d: d || {} }; });
          }).then(function (x) {
            if (!x.ok || typeof x.d.text !== 'string') throw new Error(ERREURS[x.d.error] || x.d.message || 'Transcription locale non obtenue. Votre texte est conservé.');
            inserer(x.d.text);
          }).catch(function (e) {
            dire(e && e.message && e.message !== 'Failed to fetch' ? e.message : 'Serveur injoignable : transcription non obtenue. Votre texte est conservé.', true);
          }).then(function () { etat.chunks.length = 0; actif = null; repos(); });
        };
        rec.start(1000);
        if (opts.silence) etat.silence = surveillerSilence(stream, rec);
        bouton.disabled = false; bouton.classList.remove('is-busy'); bouton.classList.add('is-rec');
        bouton.setAttribute('aria-pressed', 'true'); bouton.title = 'Arrêter la dictée'; bouton.setAttribute('aria-label', 'Arrêter la dictée');
        dire('● Enregistrement 0:00 (confidentiel, vocal local). Cliquez pour arrêter.' + note);
      }).catch(function (e) {
        if (etat.stream) etat.stream.getTracks().forEach(function (tr) { tr.stop(); });
        actif = null; repos();
        var n = e && e.name;
        dire(n === 'NotAllowedError' || n === 'SecurityError' ? 'Micro refusé : autorisez le micro pour ce site dans le navigateur, puis recommencez.'
          : n === 'NotFoundError' ? 'Aucun micro détecté sur ce poste.'
          : n === 'FormatError' ? 'Ce navigateur ne sait pas enregistrer un format accepté (WebM, Ogg ou MP4).'
          : 'Micro non démarré. Vérifiez l’autorisation du navigateur.', true);
      });
    }
    bouton.addEventListener('mousedown', function (ev) { ev.preventDefault(); });   // le curseur reste dans le champ
    bouton.addEventListener('click', function () {
      if (actif) {
        if (actif.bouton === bouton) arreter(); else dire('Une dictée est déjà en cours dans un autre champ.', true);
        return;
      }
      if (opts.avant) opts.avant();
      if (lireMode() === 'browser') navigateur(); else local();
    });
    return { arreter: arreter };
  }
  /* 5.6.108 (lot Q) : fin de dictée sur silence (après une première parole, 1,8 s sous le seuil), mesurée dans le navigateur ; rend l'arrêt. */
  function surveillerSilence(stream, rec) {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return function () {};
    var ctx, timer;
    try {
      ctx = new AC();
      var an = ctx.createAnalyser(); an.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(an);
      var buf = new Float32Array(an.fftSize), parle = false, calme = 0;
      timer = setInterval(function () {
        an.getFloatTimeDomainData(buf);
        var s = 0; for (var i = 0; i < buf.length; i++) s += buf[i] * buf[i];
        var rms = Math.sqrt(s / buf.length);
        if (rms > 0.02) { parle = true; calme = 0; } else if (parle) { calme += 200; }
        if (parle && calme >= 1800 && rec.state === 'recording') rec.stop();
      }, 200);
    } catch (e) { return function () {}; }
    return function () { clearInterval(timer); try { ctx.close(); } catch (e) { /* déjà fermé */ } };
  }
  window.addEventListener('pagehide', function () {
    if (!actif) return;
    if (actif.kind === 'browser') { try { actif.recognition.abort(); } catch (e) { /* déjà arrêtée */ } }
    else { if (actif.recorder) actif.recorder.onstop = null; if (actif.stream) actif.stream.getTracks().forEach(function (t) { t.stop(); }); }
    actif = null;
  });
  window.axiorhubDictee = { attacher: attacher, mode: lireMode, choisirMode: choisirMode };

  // =================================================================== éditeur : curseur mémorisé
  var MIC = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></svg>';
  var curseurs = new WeakMap();   // éditeur → dernier Range connu dans l'éditeur
  function suivre(editor) {
    if (curseurs.has(editor)) return;
    curseurs.set(editor, null);
    document.addEventListener('selectionchange', function () {
      var s = window.getSelection();
      if (s && s.rangeCount && editor.contains(s.getRangeAt(0).commonAncestorContainer)) curseurs.set(editor, s.getRangeAt(0).cloneRange());
    });
  }
  function memoriser(editor) {
    var s = window.getSelection();
    if (s && s.rangeCount && editor.contains(s.getRangeAt(0).commonAncestorContainer)) curseurs.set(editor, s.getRangeAt(0).cloneRange());
  }
  function debut(editor) {
    var r = document.createRange(), first = editor.firstChild;
    while (first && first.nodeType === 1 && /^(P|DIV|H1|H2|H3|LI)$/.test(first.nodeName) && first.firstChild && first.firstChild.nodeType === 1 && first.firstChild.nodeName !== 'BR') first = first.firstChild;
    if (first && first.nodeType === 1 && first.nodeName !== 'BR' && first.nodeName !== 'BLOCKQUOTE') r.setStart(first, 0); else r.setStart(editor, 0);
    r.collapse(true);
    return r;
  }
  function restaurer(editor) {
    editor.focus();
    var r = curseurs.get(editor);
    if (!r || !editor.contains(r.commonAncestorContainer)) r = debut(editor);
    var s = window.getSelection();
    s.removeAllRanges(); s.addRange(r);
  }
  /* Curseur en tête du message, au-dessus de la signature (première ligne vide) */
  function curseur(editor) {
    suivre(editor);
    var r = debut(editor), s = window.getSelection();
    editor.focus(); s.removeAllRanges(); s.addRange(r); curseurs.set(editor, r.cloneRange());
  }
  function inserer(editor, texte) {
    restaurer(editor);
    if (!document.execCommand('insertText', false, texte)) {   // repli : nœud texte au curseur
      var s = window.getSelection(), r = s.getRangeAt(0), n = document.createTextNode(texte);
      r.deleteContents(); r.insertNode(n); r.setStartAfter(n); r.collapse(true); s.removeAllRanges(); s.addRange(r);
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    }
    memoriser(editor);
  }
  function selectionHTML(editor) {
    var r = curseurs.get(editor);
    if (!r || r.collapsed || !editor.contains(r.commonAncestorContainer)) return '';
    var d = document.createElement('div'); d.appendChild(r.cloneContents());
    return d.textContent.trim() ? d.innerHTML : '';
  }

  /* HTML du serveur (déjà nettoyé) → fragment du document : analysé hors de la page (DOMParser, document inerte), attributs style retirés
     puis rendus par CSSOM. La politique de sécurité de la page (style-src 'self') refuse les attributs style écrits dans du HTML ; les
     couleurs, alignements et retraits gardés par le serveur restent ainsi visibles dans l'éditeur. */
  function fragment(html) {
    // le document analysé hérite de la politique de la page : les attributs style sont renommés AVANT l'analyse (HTML du serveur, toujours
    // écrit style="…" entre guillemets doubles), puis rendus par CSSOM
    var source = String(html || '').replace(/ style="([^"]*)"/g, ' data-bx56106r-s="$1"');
    var doc = new DOMParser().parseFromString('<!doctype html><body>' + source + '</body>', 'text/html');
    $$('script,style,iframe,object,embed', doc.body).forEach(function (n) { n.remove(); });
    var corps = document.importNode(doc.body, true), f = document.createDocumentFragment();
    $$('[data-bx56106r-s]', corps).forEach(function (n) { n.style.cssText = n.getAttribute('data-bx56106r-s') || ''; n.removeAttribute('data-bx56106r-s'); });
    while (corps.firstChild) f.appendChild(corps.firstChild);
    return f;
  }
  function remplir(editor, html) { editor.textContent = ''; editor.appendChild(fragment(html)); }

  // =================================================================== mise en forme
  var PALETTE_TEXTE = [['#000000', 'Noir'], ['#5b6676', 'Gris'], ['#b42318', 'Rouge'], ['#1f4e9c', 'Bleu'], ['#1a7f37', 'Vert'], ['#b54708', 'Orange']];
  var PALETTE_SURLIGNAGE = [['#fff3a3', 'Jaune'], ['#c9f2d0', 'Vert clair'], ['#cfe3ff', 'Bleu clair'], ['#ffd6e0', 'Rose']];
  var BLOCS = /^(P|DIV|H1|H2|H3|LI|BLOCKQUOTE|PRE)$/;

  function commande(editor, nom, valeur) {   // commandes d'édition sans style écrit (politique de sécurité : style-src 'self')
    restaurer(editor);
    var ok = document.execCommand(nom, false, valeur === undefined ? null : valeur);
    normaliser(editor);
    memoriser(editor);
    return ok;
  }
  /* Ramène ce que produisent les navigateurs à ce que le serveur garde : <font> → <span style>, taille « medium » retirée (= normale) */
  /* Couleur, surlignage, taille : la politique de sécurité (style-src 'self') refuse les attributs style que poseraient les commandes
     d'édition (styleWithCSS). La sélection est donc marquée par la commande « fontName » (élément <font face>, sans style), puis chaque
     marque devient un <span> dont le style est posé par CSSOM. valeur '' : propriété retirée (taille normale, aucun surlignage). */
  var MARQUE = 'bx56106r-marque';
  function appliquerStyle(editor, prop, valeur) {
    restaurer(editor);
    var s = window.getSelection();
    if (!s.rangeCount || s.getRangeAt(0).collapsed) { toast('Sélectionnez d’abord le texte à mettre en forme.', true); return; }
    document.execCommand('styleWithCSS', false, false);
    document.execCommand('fontName', false, MARQUE);
    var premier = null, dernier = null;
    $$('font[face="' + MARQUE + '"]', editor).forEach(function (f) {
      var span = document.createElement('span');
      while (f.firstChild) span.appendChild(f.firstChild);
      $$('span', span).forEach(function (x) { x.style.removeProperty(prop); if (!x.getAttribute('style')) x.removeAttribute('style'); });
      if (valeur) span.style.setProperty(prop, valeur);
      f.parentNode.replaceChild(span, f);
      if (!span.getAttribute('style')) { while (span.firstChild) span.parentNode.insertBefore(span.firstChild, span); premier = premier || span.previousSibling; dernier = span.previousSibling; span.remove(); }
      else { premier = premier || span; dernier = span; }
    });
    if (premier && dernier) { var r = document.createRange(); r.setStartBefore(premier); r.setEndAfter(dernier); s.removeAllRanges(); s.addRange(r); }
    normaliser(editor); memoriser(editor);
    editor.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function aligner(editor, valeur) {   // text-align posé par CSSOM sur les paragraphes de la sélection
    blocs(editor).forEach(function (b) { b.style.textAlign = valeur === 'left' ? '' : valeur; if (!b.getAttribute('style')) b.removeAttribute('style'); });
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    restaurer(editor);
  }
  function normaliser(editor) {
    $$('font', editor).forEach(function (f) {
      var span = document.createElement('span'), size = f.getAttribute('size'), color = f.getAttribute('color');
      if (size === '2' || size === '1') span.style.fontSize = 'small'; else if (size && +size >= 4) span.style.fontSize = 'large';
      if (color) span.style.color = color;
      while (f.firstChild) span.appendChild(f.firstChild);
      f.parentNode.replaceChild(span, f);
    });
    $$('[style]', editor).forEach(function (n) {
      if (n.style.fontSize && ['small', 'large'].indexOf(n.style.fontSize) < 0) n.style.fontSize = n.style.fontSize === 'x-large' || n.style.fontSize === 'xx-large' ? 'large' : (n.style.fontSize === 'x-small' ? 'small' : '');
      if (!n.getAttribute('style').trim()) n.removeAttribute('style');
    });
  }
  function blocs(editor) {   // blocs « feuilles » touchés par la sélection mémorisée
    var feuilles = function () {
      var r = curseurs.get(editor);
      if (!r) return [];
      return $$('p,div,h1,h2,h3,li,blockquote,pre', editor).filter(function (n) {
        return r.intersectsNode(n) && !n.querySelector('p,div,h1,h2,h3,li,blockquote,pre');
      }).slice(0, 200);
    };
    var out = feuilles();
    if (!out.length) { commande(editor, 'formatBlock', 'p'); out = feuilles(); }
    return out;
  }
  function retrait(editor, sens) {   // pas de 40 px, de 0 à 240 px (valeurs acceptées par le serveur)
    var r = curseurs.get(editor), li = r && r.startContainer && (r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentNode).closest('li');
    if (li && editor.contains(li)) { commande(editor, sens > 0 ? 'indent' : 'outdent'); return; }
    blocs(editor).forEach(function (b) {
      var v = Math.max(0, Math.min(240, (parseInt(b.style.marginLeft, 10) || 0) + 40 * sens));
      b.style.marginLeft = v ? v + 'px' : '';
      if (!b.getAttribute('style')) b.removeAttribute('style');
    });
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    restaurer(editor);
  }
  /* Bouton « Case » : ☐ en début de ligne ; si la ligne commence déjà par ☐ ou ☑, la case est basculée (chemin clavier) */
  function caseACocher(editor) {
    restaurer(editor);
    var s = window.getSelection();
    if (s.modify) {
      s.collapseToStart();
      s.modify('move', 'backward', 'lineboundary');
      s.modify('extend', 'forward', 'character');
      var c = s.toString();
      if (c === '☐' || c === '☑') { document.execCommand('insertText', false, c === '☐' ? '☑' : '☐'); memoriser(editor); return; }
      s.collapseToStart();
    }
    document.execCommand('insertText', false, '☐ ');
    memoriser(editor);
  }
  /* Clic sur une case dans l'éditeur : ☐ ↔ ☑ (remplacement par la commande d'édition, donc annulable) */
  function cases(editor) {
    suivre(editor);
    editor.addEventListener('click', function (ev) {
      var node, off;
      if (document.caretPositionFromPoint) { var p = document.caretPositionFromPoint(ev.clientX, ev.clientY); if (p) { node = p.offsetNode; off = p.offset; } }
      else if (document.caretRangeFromPoint) { var q = document.caretRangeFromPoint(ev.clientX, ev.clientY); if (q) { node = q.startContainer; off = q.startOffset; } }
      if (!node || node.nodeType !== 3) return;
      [off, off - 1].some(function (i) {
        if (i < 0 || i >= node.data.length) return false;
        var ch = node.data.charAt(i);
        if (ch !== '☐' && ch !== '☑') return false;
        var r = document.createRange(); r.setStart(node, i); r.setEnd(node, i + 1);
        var b = r.getBoundingClientRect();
        if (ev.clientX < b.left - 3 || ev.clientX > b.right + 3 || ev.clientY < b.top - 3 || ev.clientY > b.bottom + 3) return false;
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
        document.execCommand('insertText', false, ch === '☐' ? '☑' : '☐');
        memoriser(editor);
        return true;
      });
    });
  }

  /* Menu déroulant accessible (bouton + liste), au clavier : flèches, Début, Fin, Échap */
  function menu(bouton, construire, libelle) {
    var boite = el('div', { 'class': 'bx56106r-menu', role: 'menu', 'aria-label': libelle, hidden: 'hidden' });
    bouton.setAttribute('aria-haspopup', 'menu'); bouton.setAttribute('aria-expanded', 'false');
    var items = function () { return $$('[role^="menuitem"]', boite).filter(function (x) { return !x.disabled; }); };
    var fermer = function (rendre) {
      if (boite.hidden) return;
      boite.hidden = true; bouton.setAttribute('aria-expanded', 'false');
      document.removeEventListener('mousedown', dehors, true);
      if (rendre) bouton.focus();
    };
    var dehors = function (ev) { if (!boite.contains(ev.target) && ev.target !== bouton) fermer(false); };
    var ouvrir = function () {
      boite.textContent = '';
      construire(boite, fermer);
      boite.hidden = false; bouton.setAttribute('aria-expanded', 'true');
      document.addEventListener('mousedown', dehors, true);
      var it = items(); if (it[0]) it[0].focus();
    };
    bouton.addEventListener('click', function () { if (boite.hidden) ouvrir(); else fermer(true); });
    boite.addEventListener('keydown', function (ev) {
      var it = items(), i = it.indexOf(document.activeElement);
      if (ev.key === 'Escape') { ev.preventDefault(); fermer(true); }
      else if (ev.key === 'ArrowDown' || ev.key === 'ArrowRight') { ev.preventDefault(); (it[(i + 1) % it.length] || it[0]).focus(); }
      else if (ev.key === 'ArrowUp' || ev.key === 'ArrowLeft') { ev.preventDefault(); (it[(i - 1 + it.length) % it.length] || it[0]).focus(); }
      else if (ev.key === 'Home') { ev.preventDefault(); if (it[0]) it[0].focus(); }
      else if (ev.key === 'End') { ev.preventDefault(); if (it.length) it[it.length - 1].focus(); }
      else if (ev.key === 'Tab') fermer(false);
    });
    return { element: boite, fermer: fermer };
  }
  function item(texte, titre, action, fermer, extra) {
    var b = el('button', Object.assign({ type: 'button', role: 'menuitem', 'class': 'bx56106r-mi', title: titre || texte, text: texte, tabindex: '-1' }, extra || {}));
    b.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
    b.addEventListener('click', function () { fermer(false); action(); });
    return b;
  }
  function groupe(titre, enfants) {
    return el('div', { 'class': 'bx56106r-mg', role: 'group', 'aria-label': titre }, [el('span', { 'class': 'bx56106r-mt', text: titre, 'aria-hidden': 'true' })].concat(enfants));
  }

  /* barre(editor, {jour, joindre(), nextcloud(), visio()}) : barre d'outils compacte en une ligne */
  function barre(editor, ctx) {
    suivre(editor);
    // une seule ligne vide (celle du curseur) au-dessus de la signature : la seconde ligne vide du gabarit est retirée à l'ouverture
    var b0 = editor.children[0], b1 = editor.children[1], vide = function (b) { return b && /^(P|DIV)$/.test(b.nodeName) && !b.textContent.trim() && !b.querySelector('img,hr'); };
    if (vide(b0) && vide(b1) && editor.children[2]) b1.remove();
    var bar = el('div', { 'class': 'bx5628-fmt bx56106r-barre', role: 'toolbar', 'aria-label': 'Mise en forme' });
    var statut = el('span', { 'class': 'bx56106r-statut', role: 'status', 'aria-live': 'polite' });
    var bouton = function (icone, titre, action, cls) {
      var b = el('button', { type: 'button', 'class': 'bx5628-fbtn bx56106r-ib' + (cls ? ' ' + cls : ''), title: titre, 'aria-label': titre });
      if (icone.charAt(0) === '<') b.innerHTML = icone; else b.textContent = icone;   // icône SVG fixe de ce fichier, jamais une donnée
      b.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
      if (action) b.addEventListener('click', action);
      bar.appendChild(b); return b;
    };
    var sep = function () { bar.appendChild(el('span', { 'class': 'bx56106r-sep', 'aria-hidden': 'true' })); };
    var cmd = function (nom, val) { return function () { commande(editor, nom, val); }; };
    bouton('↶', 'Annuler la dernière modification (Ctrl+Z)', cmd('undo'), 'bx56106r-large');
    bouton('↷', 'Rétablir (Ctrl+Y)', cmd('redo'), 'bx56106r-large');
    bar.appendChild(el('span', { 'class': 'bx56106r-sep bx56106r-large', 'aria-hidden': 'true' }));
    bouton('B', 'Gras (Ctrl+B)', cmd('bold'), 'is-b');
    bouton('I', 'Italique (Ctrl+I)', cmd('italic'), 'is-i');
    bouton('U', 'Souligné (Ctrl+U)', cmd('underline'), 'is-u');
    sep();
    bouton('•≡', 'Liste à puces', cmd('insertUnorderedList'));
    bouton('1≡', 'Liste numérotée', cmd('insertOrderedList'));
    bouton('☐', 'Case à cocher en début de ligne (cliquez sur une case pour la cocher ou la décocher)', function () { caseACocher(editor); });
    bouton('🔗', 'Insérer un lien', function () {
      memoriser(editor);
      var href = window.prompt('Adresse du lien (https://… ou mailto:…)', 'https://');
      if (href && /^(https?:|mailto:)/i.test(href.trim())) commande(editor, 'createLink', href.trim());
    });
    sep();
    var mic = bouton(MIC, 'Dicter au curseur', null, 'bx56106r-mic');
    attacher(mic, { statut: statut, avant: function () { memoriser(editor); }, inserer: function (texte) { inserer(editor, texte); } });
    bouton('📎', 'Joindre des fichiers (ou glissez-les sur le message)', function () { ctx.joindre(); });
    var mod = bouton('☰', 'Modèles de réponse du cabinet', null, 'bx56106r-txt');
    mod.appendChild(el('span', { 'class': 'bx56106r-lib', text: ' Modèles', 'aria-hidden': 'true' })); mod.appendChild(document.createTextNode(' ▾'));
    var mm = menu(mod, function (boite, fermer) { modeles(boite, fermer, editor); }, 'Modèles de réponse');
    bar.appendChild(mm.element);
    var plus = bouton('⋯', 'Plus de mise en forme et d’outils', null, 'bx56106r-txt');
    plus.appendChild(el('span', { 'class': 'bx56106r-lib', text: ' Plus', 'aria-hidden': 'true' })); plus.appendChild(document.createTextNode(' ▾'));
    var pm = menu(plus, function (boite, fermer) {
      var it = function (t, titre, f) { return item(t, titre, f, fermer); };
      boite.appendChild(groupe('Modification', [it('↶ Annuler', 'Annuler la dernière modification (Ctrl+Z)', cmd('undo')),
        it('↷ Rétablir', 'Rétablir (Ctrl+Y)', cmd('redo'))]));
      boite.appendChild(groupe('Texte', [
        it('S̶ Barré', 'Barré', cmd('strikeThrough')), it('❝ Citation', 'Citation', cmd('formatBlock', 'blockquote')),
        it('Titre 1', 'Titre de premier niveau', cmd('formatBlock', 'h2')), it('Titre 2', 'Titre de second niveau', cmd('formatBlock', 'h3')),
        it('Texte normal', 'Paragraphe normal', cmd('formatBlock', 'p'))]));
      boite.appendChild(groupe('Paragraphe', [
        it('⇤ Gauche', 'Aligner à gauche', function () { aligner(editor, 'left'); }), it('↔ Centrer', 'Centrer', function () { aligner(editor, 'center'); }),
        it('⇥ Droite', 'Aligner à droite', function () { aligner(editor, 'right'); }),
        it('→ Retrait', 'Augmenter le retrait', function () { retrait(editor, 1); }), it('← Désindenter', 'Diminuer le retrait', function () { retrait(editor, -1); }),
        it('― Ligne', 'Ligne horizontale', cmd('insertHorizontalRule'))]));
      boite.appendChild(groupe('Couleur du texte', PALETTE_TEXTE.map(function (p) {
        var b = item('A', 'Couleur : ' + p[1], function () { appliquerStyle(editor, 'color', p[0]); }, fermer, { 'class': 'bx56106r-mi bx56106r-sw', 'aria-label': 'Couleur du texte : ' + p[1] });
        b.style.color = p[0];   // CSSOM : permis par la politique de sécurité (aucun attribut style)
        return b;
      })));
      boite.appendChild(groupe('Surlignage', PALETTE_SURLIGNAGE.map(function (p) {
        var b = item('ab', 'Surlignage : ' + p[1], function () { appliquerStyle(editor, 'background-color', p[0]); }, fermer, { 'class': 'bx56106r-mi bx56106r-sw', 'aria-label': 'Surlignage : ' + p[1] });
        b.style.background = p[0]; b.style.color = '#1f2a3a';
        return b;
      }).concat([it('Aucun', 'Retirer le surlignage', function () { appliquerStyle(editor, 'background-color', ''); })])));
      boite.appendChild(groupe('Taille', [it('Petit', 'Petit texte', function () { appliquerStyle(editor, 'font-size', 'small'); }),
        it('Normal', 'Taille normale', function () { appliquerStyle(editor, 'font-size', ''); }),
        it('Grand', 'Grand texte', function () { appliquerStyle(editor, 'font-size', 'large'); })]));
      boite.appendChild(groupe('Insérer', [
        it('📅 Date du jour', 'Insérer la date du jour (' + (ctx.jour || 'jour du cabinet') + ')', function () { if (ctx.jour) inserer(editor, ctx.jour); else toast('Date du jour indisponible.', true); }),
        it('📁 Depuis Nextcloud', 'Joindre un document de l’espace Nextcloud', function () { ctx.nextcloud(); }),
        it('🎥 Visioconférence', 'Insérer un lien de visioconférence', function () { ctx.visio(); }),
        it('⌫ Effacer la mise en forme', 'Effacer la mise en forme de la sélection', cmd('removeFormat'))]));
      var m = lireMode();
      boite.appendChild(groupe('Dictée', [['local', 'Confidentiel — vocal local'], ['browser', 'Rapide — texte non confidentiel']].map(function (x) {
        var b = item(x[1], x[0] === 'local' ? 'Audio envoyé uniquement à Vocal local (pont du cabinet)' : 'Le navigateur peut utiliser un service distant : jamais pour une donnée client',
                     function () { choisirMode(x[0]); statut.textContent = 'Dictée : ' + x[1] + '.'; }, fermer, { role: 'menuitemradio', 'aria-checked': m === x[0] ? 'true' : 'false' });
        return b;
      })));
    }, 'Plus de mise en forme');
    bar.appendChild(pm.element);
    bar.appendChild(statut);
    editor.addEventListener('keydown', function (ev) {   // Ctrl+K : lien ; le reste (Ctrl+B, I, U, Z, Y) est natif
      if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && ev.key && ev.key.toLowerCase() === 'k') { ev.preventDefault(); ev.stopPropagation(); memoriser(editor); var lien = parTitre(bar, 'Insérer un lien'); if (lien) lien.click(); }
    });
    return bar;
  }

  /* Micro du champ Objet : insertion au curseur du champ */
  function micro(input, ligne) {
    var b = el('button', { type: 'button', 'class': 'bx56106r-ib bx56106r-mic bx56106r-mic-champ', title: 'Dicter l’objet', 'aria-label': 'Dicter l’objet' });
    b.innerHTML = MIC;
    var statut = el('span', { 'class': 'bx56106r-statut', role: 'status', 'aria-live': 'polite' });
    var pos = null;
    attacher(b, { statut: statut, avant: function () { pos = [input.selectionStart, input.selectionEnd]; }, inserer: function (texte) {
      var v = input.value, a = pos ? pos[0] : v.length, z = pos ? pos[1] : v.length;
      var avant = v.slice(0, a), apres = v.slice(z);
      var t = (avant && !/\s$/.test(avant) ? ' ' : '') + texte.replace(/[\r\n]+/g, ' ');
      input.value = (avant + t + apres).slice(0, 300);
      input.focus(); input.setSelectionRange(avant.length + t.length, avant.length + t.length);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    } });
    ligne.appendChild(b); ligne.appendChild(statut);
  }

  // =================================================================== modèles de réponse du cabinet
  function modeles(boite, fermer, editor) {
    var liste = el('div', { 'class': 'bx56106r-modeles', role: 'none' }, [el('p', { 'class': 'ax-muted', text: 'Chargement…' })]);
    boite.appendChild(liste);
    var sel = selectionHTML(editor);
    var enregistrerComme = function (html, quoi) {
      var titre = window.prompt('Titre du modèle (' + quoi + ')', '');
      if (!titre || !titre.trim()) return;
      api('m56106/redaction/modeles/creer', { titre: titre.trim(), html: html, cle: cle() }).then(function (r) { toast(r.message); })
        .catch(function (e) { toast(e.message, true); });
    };
    var message = function () {   // le message sans la citation du courriel d'origine
      var d = document.createElement('div'); Array.prototype.forEach.call(editor.childNodes, function (n) { d.appendChild(n.cloneNode(true)); });   // copie de nœuds (aucune analyse de HTML : politique de sécurité)
      $$('blockquote', d).forEach(function (q) { q.remove(); });
      return d.innerHTML;
    };
    boite.appendChild(groupe('Enregistrer', [
      item('Enregistrer la sélection comme modèle', 'Le texte sélectionné devient un modèle du cabinet', function () { enregistrerComme(sel, 'texte sélectionné'); }, fermer,
           sel ? {} : { disabled: 'disabled', title: 'Sélectionnez d’abord du texte dans le message' }),
      item('Enregistrer le message comme modèle', 'Le message (sans la citation) devient un modèle du cabinet', function () { enregistrerComme(message(), 'message'); }, fermer)]));
    api('m56106/redaction/modeles').then(function (d) {
      liste.textContent = '';
      if (!d.modeles.length) { liste.appendChild(el('p', { 'class': 'ax-muted', text: 'Aucun modèle pour l’instant : enregistrez un message ou une sélection.' })); return; }
      d.modeles.forEach(function (m) {
        var ligne = el('div', { 'class': 'bx56106r-ml', role: 'none' });
        ligne.appendChild(item(m.titre, 'Insérer « ' + m.titre + ' » au curseur', function () {
          restaurer(editor);   // HTML nettoyé par le serveur (liste fermée de balises et de styles), styles posés par CSSOM
          var sel = window.getSelection(), r = sel.getRangeAt(0), f = fragment(m.html), dernier = f.lastChild;
          r.deleteContents(); r.insertNode(f);
          if (dernier) { r.setStartAfter(dernier); r.collapse(true); sel.removeAllRanges(); sel.addRange(r); }
          editor.dispatchEvent(new Event('input', { bubbles: true }));
          normaliser(editor); memoriser(editor);
        }, fermer, { 'class': 'bx56106r-mi bx56106r-mtitre' }));
        if (m.modifiable) {
          ligne.appendChild(item('✎', 'Modifier « ' + m.titre + ' »', function () {
            var titre = window.prompt('Titre du modèle', m.titre);
            if (titre === null || !titre.trim()) return;
            var html = m.html;
            if (sel && window.confirm('Remplacer aussi le texte du modèle par le texte sélectionné ?')) html = sel;
            api('m56106/redaction/modeles/modifier', { id: m.id, titre: titre.trim(), html: html, version: m.version, cle: cle() })
              .then(function (r) { toast(r.message); }).catch(function (e) { toast(e.message, true); });
          }, fermer, { 'aria-label': 'Modifier le modèle ' + m.titre }));
          ligne.appendChild(item('🗑', 'Supprimer « ' + m.titre + ' »', function () {
            if (!window.confirm('Supprimer le modèle « ' + m.titre + ' » pour tout le cabinet ?')) return;
            api('m56106/redaction/modeles/supprimer', { id: m.id, version: m.version, cle: cle() })
              .then(function (r) { toast(r.message); }).catch(function (e) { toast(e.message, true); });
          }, fermer, { 'aria-label': 'Supprimer le modèle ' + m.titre }));
        }
        liste.appendChild(ligne);
      });
      var premier = $('[role^="menuitem"]', liste); if (premier) premier.focus();
    }).catch(function (e) { liste.textContent = ''; liste.appendChild(el('p', { 'class': 'ax-alert', role: 'alert', text: 'Modèles indisponibles : ' + e.message })); });
  }

  // =================================================================== adresses proposées (À, Cc, Cci)
  var cache = {};
  function affichage(c) {
    var nom = [c.first_name, c.name].filter(Boolean).join(' ').replace(/["\r\n]+/g, ' ').trim() || String(c.label || '').replace(/["\r\n]+/g, ' ').trim();
    if (!nom) return c.email;
    return (/[,;:<>@()\[\]]/.test(nom) ? '"' + nom + '"' : nom) + ' <' + c.email + '>';
  }
  var nAdr = 0;
  function adresses(input) {
    var id = 'bx56106r-adr-' + (++nAdr);
    var liste = el('ul', { 'class': 'bx56106r-adr', role: 'listbox', id: id, hidden: 'hidden', 'aria-label': 'Adresses proposées' });
    input.parentNode.appendChild(liste);
    input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list'); input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', id);
    var choix = [], actifI = -1, minuteur = null, ticket = 0;
    var jeton = function () {
      var avant = input.value.slice(0, input.selectionStart == null ? input.value.length : input.selectionStart);
      var debutJ = Math.max(avant.lastIndexOf(','), avant.lastIndexOf(';')) + 1;
      return { debut: debutJ, texte: avant.slice(debutJ).trim() };
    };
    var fermer = function () { liste.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); actifI = -1; };
    var marquer = function (i) {
      actifI = i;
      $$('[role="option"]', liste).forEach(function (o, k) { o.setAttribute('aria-selected', k === i ? 'true' : 'false'); });
      if (i >= 0) { input.setAttribute('aria-activedescendant', id + '-' + i); var o = document.getElementById(id + '-' + i); if (o && o.scrollIntoView) o.scrollIntoView({ block: 'nearest' }); }
    };
    var retenir = function (i) {
      var c = choix[i]; if (!c) return;
      var j = jeton(), fin = input.selectionStart == null ? input.value.length : input.selectionStart;
      var apres = input.value.slice(fin).replace(/^[^,;]*/, '');
      var avant = input.value.slice(0, j.debut);
      var ins = (avant && !/\s$/.test(avant) ? ' ' : '') + affichage(c) + ', ';
      input.value = avant + ins + apres.replace(/^[,;]\s*/, '');
      var p = (avant + ins).length; input.setSelectionRange(p, p);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      fermer();
    };
    var montrer = function (list, q) {
      var deja = input.value.toLowerCase();
      choix = list.filter(function (c) { return c.email && deja.indexOf(String(c.email).toLowerCase() + '>') < 0; }).slice(0, 8);
      liste.textContent = '';
      if (!choix.length) { fermer(); return; }
      choix.forEach(function (c, i) {
        var o = el('li', { role: 'option', id: id + '-' + i, 'aria-selected': 'false', 'class': 'bx56106r-opt' },
                   [el('span', { text: affichage(c).replace(/ <.*$/, '') }), el('span', { 'class': 'bx56106r-mail', text: c.email })]);
        o.addEventListener('mousedown', function (ev) { ev.preventDefault(); retenir(i); });
        liste.appendChild(o);
      });
      liste.hidden = false; input.setAttribute('aria-expanded', 'true'); marquer(0);
    };
    input.addEventListener('input', function () {
      clearTimeout(minuteur);
      var j = jeton();
      if (j.texte.length < 2) { fermer(); return; }   // aucune requête avant deux caractères
      minuteur = setTimeout(function () {             // anti-rebond
        var q = j.texte.toLowerCase(), n = ++ticket;
        if (cache[q]) { montrer(cache[q], q); return; }
        api('m5636/contacts?q=' + encodeURIComponent(j.texte)).then(function (d) {
          cache[q] = (d.contacts || []).filter(function (c) { return c.email; });
          if (n === ticket && document.activeElement === input) montrer(cache[q], q);
        }).catch(function () { if (n === ticket) fermer(); });
      }, 250);
    });
    input.addEventListener('keydown', function (ev) {
      if (liste.hidden) return;
      if (ev.key === 'ArrowDown') { ev.preventDefault(); marquer((actifI + 1) % choix.length); }
      else if (ev.key === 'ArrowUp') { ev.preventDefault(); marquer((actifI - 1 + choix.length) % choix.length); }
      else if (ev.key === 'Enter' || (ev.key === 'Tab' && actifI >= 0 && !ev.shiftKey)) { if (actifI >= 0) { ev.preventDefault(); retenir(actifI); } }
      else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); fermer(); }
    });
    input.addEventListener('blur', function () { setTimeout(fermer, 120); });
  }

  // =================================================================== pièces jointes
  var MAX_PIECES = 10, MAX_OCTETS = 15 * 1024 * 1024, nPiece = 0;
  function Pieces(t, avertir) {
    var self = this;
    this.items = [];
    var id = function () { return 'p' + (++nPiece) + Math.random().toString(36).slice(2, 8); };
    (t.attachments || []).forEach(function (a) { self.items.push({ id: id(), kind: 'source', index: a.index, name: a.name, size: a.size || 0 }); });
    (t.pieces_brouillon || []).forEach(function (a) { self.items.push({ id: id(), kind: 'brouillon', empreinte: a.empreinte, name: a.name, size: a.size || 0 }); });
    this.nouvelId = id;
    this.avertir = avertir;
    this.liste = el('ul', { 'class': 'bx56106r-pj-liste', 'aria-label': 'Pièces jointes' });
    this.total = el('span', { 'class': 'bx56106r-pj-total', role: 'status', 'aria-live': 'polite' });
    this.input = el('input', { type: 'file', multiple: 'multiple', 'class': 'bx56106r-pj-input', 'aria-label': 'Joindre des fichiers', tabindex: '-1' });
    this.input.addEventListener('change', function () { self.ajouter(self.input.files); self.input.value = ''; });
    this.element = el('div', { 'class': 'bx5628-files bx56106r-pj' }, [this.liste, this.total, this.input]);
    this.dessiner();
  }
  Pieces.prototype.octets = function () {   // pièces jointes et images du corps (même plafond de 15 Mo)
    return this.items.reduce(function (s, x) { return s + (x.size || 0); }, 0) + (this.editor ? octetsImages(this.editor) : 0);
  };
  Pieces.prototype.changer = function () { this.dessiner(); this.element.dispatchEvent(new Event('change', { bubbles: true })); };
  Pieces.prototype.dessiner = function () {
    var self = this;
    this.liste.textContent = '';
    this.items.forEach(function (x) {
      var retirer = el('button', { type: 'button', 'class': 'bx56106r-pj-x', text: '✕', title: 'Retirer ' + x.name, 'aria-label': 'Retirer la pièce ' + x.name });
      retirer.addEventListener('click', function () {
        self.items = self.items.filter(function (y) { return y.id !== x.id; });
        self.changer();
      });
      var info = x.kind === 'nextcloud' ? 'Nextcloud' : taille(x.size || 0);
      self.liste.appendChild(el('li', { 'class': 'bx56106r-pj-item' }, [el('span', { 'class': 'bx56106r-pj-nom', text: '📄 ' + x.name, title: x.name }),
        el('span', { 'class': 'bx56106r-pj-taille', text: info }), retirer]));
    });
    var n = this.items.length, o = this.octets(), ni = this.editor ? $$('img', this.editor).length : 0;
    this.element.hidden = !n && !ni;
    this.total.textContent = (n || ni) ? (n + (n > 1 ? ' pièces' : ' pièce') + (ni ? ' et ' + ni + (ni > 1 ? ' images' : ' image') : '') + ' · ' + taille(o) + ' sur 15 Mo · '
      + MAX_PIECES + ' fichiers au plus') : '';
    this.total.classList.toggle('is-bad', n > MAX_PIECES || o > MAX_OCTETS);
  };
  Pieces.prototype.choisir = function () { this.input.click(); };
  Pieces.prototype.ajouter = function (liste) {
    var self = this, refus = [];
    Array.prototype.forEach.call(liste || [], function (f) {
      if (self.items.some(function (x) { return x.kind === 'local' && x.file && x.file.name === f.name && x.file.size === f.size && x.file.lastModified === f.lastModified; })) return;
      if (self.items.length >= MAX_PIECES) { refus.push(f.name + ' (10 fichiers au plus)'); return; }
      if (self.octets() + f.size > MAX_OCTETS) { refus.push(f.name + ' (15 Mo au total dépassés)'); return; }
      self.items.push({ id: self.nouvelId(), kind: 'local', file: f, name: f.name, size: f.size });
    });
    if (refus.length) this.avertir('Non joint : ' + refus.join(', ') + '.', true);
    this.changer();
  };
  Pieces.prototype.ajouterNextcloud = function (f) {
    if (this.items.some(function (x) { return x.kind === 'nextcloud' && x.chemin === f.chemin; })) return;
    if (this.items.length >= MAX_PIECES) { this.avertir('10 fichiers au plus.', true); return; }
    this.items.push({ id: this.nouvelId(), kind: 'nextcloud', chemin: f.chemin, name: f.nom, size: 0 });
    this.changer();
  };
  /* Champs du corps envoyé : keep (pièces du message transféré), files (fichiers locaux, avec leur clé), nextcloud (chemins), draft_keep
     (empreintes des pièces déjà dans le brouillon) ; l'instantané sert à remplacer, après l'enregistrement, les pièces locales par celles
     du brouillon (elles ne sont plus renvoyées). */
  Pieces.prototype.payload = function () {
    var items = this.items.slice();
    var lire = function (x) {
      return new Promise(function (resolve, reject) {
        var r = new FileReader();
        r.onload = function () { resolve({ name: x.file.name, type: x.file.type || 'application/octet-stream', data: String(r.result).split(',')[1] || '', cle: x.id }); };
        r.onerror = function () { reject(new Error('Pièce jointe illisible : ' + x.file.name)); };
        r.readAsDataURL(x.file);
      });
    };
    return Promise.all(items.filter(function (x) { return x.kind === 'local'; }).map(lire)).then(function (files) {
      var instantane = {};
      items.forEach(function (x) { instantane[x.id] = true; });
      return { instantane: instantane, champs: {
        keep: items.filter(function (x) { return x.kind === 'source'; }).map(function (x) { return String(x.index); }),
        files: files,
        nextcloud: items.filter(function (x) { return x.kind === 'nextcloud'; }).map(function (x) { return x.chemin; }),
        draft_keep: items.filter(function (x) { return x.kind === 'brouillon'; }).map(function (x) { return x.empreinte; }) } };
    });
  };
  Pieces.prototype.apres = function (pieces, instantane) {
    if (!instantane) return;
    var origine = {};
    (pieces || []).forEach(function (p) {
      (p.origines || []).forEach(function (o) {
        var k = o.type + ':' + String(o.type === 'local' ? o.cle : o.type === 'source' ? o.index : o.type === 'nextcloud' ? o.chemin : o.empreinte);
        origine[k] = p;
      });
    });
    var vues = {}, out = [];
    this.items.forEach(function (x) {
      if (!instantane[x.id]) { out.push(x); return; }   // ajoutée pendant l'enregistrement : gardée telle quelle
      var k = x.kind + ':' + String(x.kind === 'local' ? x.id : x.kind === 'source' ? x.index : x.kind === 'nextcloud' ? x.chemin : x.empreinte);
      var p = origine[k];
      if (!p) { out.push(x); return; }                  // non retrouvée : rien n'est perdu, elle sera renvoyée
      if (vues[p.empreinte]) return;
      vues[p.empreinte] = true;
      out.push({ id: x.id, kind: 'brouillon', empreinte: p.empreinte, name: p.name, size: p.size });
    });
    this.items = out;
    this.dessiner();
  };
  function pieces(t, avertir) { return new Pieces(t, avertir || toast); }

  /* Glisser-déposer de fichiers sur toute la rédaction (l'éditeur n'insère jamais le fichier lui-même) */
  function deposer(form, pj) {
    var n = 0;
    var fichiers = function (ev) { return ev.dataTransfer && Array.prototype.indexOf.call(ev.dataTransfer.types || [], 'Files') >= 0; };
    form.addEventListener('dragenter', function (ev) { if (!fichiers(ev)) return; ev.preventDefault(); n++; form.classList.add('is-drop'); }, true);
    form.addEventListener('dragover', function (ev) { if (!fichiers(ev)) return; ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; }, true);
    form.addEventListener('dragleave', function (ev) { if (!fichiers(ev)) return; n = Math.max(0, n - 1); if (!n) form.classList.remove('is-drop'); }, true);
    form.addEventListener('drop', function (ev) {
      if (!fichiers(ev)) return;
      ev.preventDefault(); ev.stopPropagation(); n = 0; form.classList.remove('is-drop');
      var editor = $('.bx5628-editor', form), liste = Array.prototype.slice.call(ev.dataTransfer.files || []);
      // 5.6.106 : des images déposées dans le corps du message y sont insérées en ligne ; tout le reste devient pièce jointe
      if (editor && editor.contains(ev.target) && liste.length && liste.every(function (f) { return IMG_TYPES[f.type]; })) {
        var r = document.caretRangeFromPoint ? document.caretRangeFromPoint(ev.clientX, ev.clientY) : null;
        if (!r && document.caretPositionFromPoint) { var q = document.caretPositionFromPoint(ev.clientX, ev.clientY); if (q) { r = document.createRange(); r.setStart(q.offsetNode, q.offset); } }
        if (r && editor.contains(r.startContainer)) curseurs.set(editor, r);
        insererImages(editor, liste, pj);
        return;
      }
      pj.ajouter(liste);
    }, true);
    form.setAttribute('data-drop-label', tr('Déposez les fichiers pour les joindre (une image déposée dans le texte y est insérée)'));   // attribut hors de la traduction automatique
  }

  // =================================================================== images collées ou déposées dans le corps (pièces intégrées à l'envoi)
  var IMG_TYPES = { 'image/png': 1, 'image/jpeg': 1, 'image/gif': 1, 'image/webp': 1 };
  var IMG_MAX = 2 * 1024 * 1024, IMG_NB = 10, IMG_COTE = 1600;
  var LARGEURS = [['200', 'Petite'], ['400', 'Moyenne'], ['640', 'Pleine largeur']];
  var IMG_DATA = /^data:image\/(png|jpeg|gif|webp);base64,/i;
  /* Type réel d'après les premiers octets (le type annoncé par le presse-papiers ne compte pas) ; SVG et le reste : '' */
  function typeOctets(u) {
    if (u.length > 8 && u[0] === 0x89 && u[1] === 0x50 && u[2] === 0x4E && u[3] === 0x47) return 'image/png';
    if (u.length > 3 && u[0] === 0xFF && u[1] === 0xD8 && u[2] === 0xFF) return 'image/jpeg';
    if (u.length > 6 && u[0] === 0x47 && u[1] === 0x49 && u[2] === 0x46 && u[3] === 0x38) return 'image/gif';
    if (u.length > 12 && u[0] === 0x52 && u[1] === 0x49 && u[2] === 0x46 && u[3] === 0x46 && u[8] === 0x57 && u[9] === 0x45 && u[10] === 0x42 && u[11] === 0x50) return 'image/webp';
    return '';
  }
  function octetsDe(blob) {
    if (blob.arrayBuffer) return blob.arrayBuffer().then(function (b) { return new Uint8Array(b); });
    return new Promise(function (res, rej) { var r = new FileReader(); r.onload = function () { res(new Uint8Array(r.result)); }; r.onerror = rej; r.readAsArrayBuffer(blob); });
  }
  function dataURL(blob) {
    return new Promise(function (res, rej) { var r = new FileReader(); r.onload = function () { res(String(r.result)); }; r.onerror = rej; r.readAsDataURL(blob); });
  }
  /* Côté long ramené à 1600 px (GIF agrandi au-delà : converti en PNG, animation perdue) ; JPEG et WebP recompressés si besoin */
  function redimensionner(blob, type) {
    if (!window.createImageBitmap) return Promise.resolve(blob);
    return createImageBitmap(blob).then(function (bm) {
      var cote = Math.max(bm.width, bm.height);
      if (cote <= IMG_COTE && blob.size <= IMG_MAX) { if (bm.close) bm.close(); return blob; }
      var k = Math.min(1, IMG_COTE / cote), c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(bm.width * k)); c.height = Math.max(1, Math.round(bm.height * k));
      c.getContext('2d').drawImage(bm, 0, 0, c.width, c.height);
      if (bm.close) bm.close();
      var sortie = type === 'image/gif' ? 'image/png' : type;
      var essai = function (qualite) { return new Promise(function (res) { c.toBlob(function (b) { res(b || blob); }, sortie, qualite); }); };
      return essai(0.85).then(function (b) { return b.size > IMG_MAX && sortie !== 'image/png' ? essai(0.7) : b; });
    }).catch(function () { return blob; });
  }
  function octetsImages(editor) {
    return $$('img', editor).reduce(function (t, i) { var src = i.getAttribute('src') || ''; return t + (IMG_DATA.test(src) ? Math.floor((src.length - src.indexOf(',') - 1) * 3 / 4) : 0); }, 0);
  }
  function insererImages(editor, fichiers, pj) {
    var suite = Promise.resolve(), refus = [];
    Array.prototype.forEach.call(fichiers, function (f) {
      suite = suite.then(function () {
        if ($$('img', editor).length >= IMG_NB) { refus.push((f.name || 'image') + ' : 10 images au plus'); return; }
        return octetsDe(f).then(function (u) {
          var type = typeOctets(u);
          if (!type) { refus.push((f.name || 'image') + ' : seuls PNG, JPEG, GIF et WebP sont acceptés (SVG refusé)'); return; }
          return redimensionner(f, type).then(function (b) {
            if (b.size > IMG_MAX) { refus.push((f.name || 'image') + ' : 2 Mo au plus par image'); return; }
            if ((pj ? pj.octets() : 0) + octetsImages(editor) + b.size > MAX_OCTETS) { refus.push((f.name || 'image') + ' : 15 Mo au total dépassés'); return; }
            return dataURL(b).then(function (url) {
              if (!IMG_DATA.test(url)) url = url.replace(/^data:[^;,]*/, 'data:' + (b.type || type));
              restaurer(editor);
              // insertion par le DOM : la commande « insertImage » écrit un style en ligne, refusé par la politique de sécurité de la page
              var img = document.createElement('img'), sel = window.getSelection(), r = sel.getRangeAt(0);
              img.setAttribute('src', url); img.setAttribute('alt', 'Image collée');
              r.deleteContents(); r.insertNode(img); r.setStartAfter(img); r.collapse(true); sel.removeAllRanges(); sel.addRange(r);
              memoriser(editor);
              if (pj) pj.dessiner();
            });
          });
        });
      });
    });
    return suite.then(function () {
      if (refus.length) toast('Image non insérée — ' + refus.join(' ; ') + '.', true);
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    }, function () { toast('Image illisible : non insérée.', true); });
  }
  /* Images arrivées par un collage HTML (page web, autre logiciel) : seules les images data: PNG, JPEG, GIF ou WebP restent. Une image
     distante (http, https) n'est jamais téléchargée : elle est remplacée par un message ; SVG, data:text/html et le reste sont retirés. */
  function nettoyerImages(editor) {
    var distantes = 0, refusees = 0;
    $$('img', editor).forEach(function (img) {
      var src = (img.getAttribute('src') || '').trim();
      if (IMG_DATA.test(src)) {
        Array.prototype.slice.call(img.attributes).forEach(function (a) { if (['src', 'alt', 'width'].indexOf(a.name) < 0) img.removeAttribute(a.name); });
        if (img.hasAttribute('width') && !LARGEURS.some(function (l) { return l[0] === img.getAttribute('width'); })) img.removeAttribute('width');
        return;
      }
      var remote = /^(https?:)?\/\//i.test(src);
      if (remote) distantes++; else refusees++;
      img.replaceWith(document.createTextNode(remote ? '[Image distante non insérée : copiez l’image elle-même]' : '[Image refusée]'));
    });
    if (distantes) toast('Image distante non insérée : copiez l’image elle-même (clic droit › Copier l’image), puis collez-la.', true);
    else if (refusees) toast('Image refusée : seuls PNG, JPEG, GIF et WebP sont acceptés.', true);
  }
  /* Collage HTML contenant des images (page web, autre logiciel) : analysé hors de la page (document inerte) ; scripts, styles, attributs
     d'événement et liens javascript: retirés ; images distantes remplacées par un message AVANT insertion (aucune requête vers un tiers ;
     la politique de sécurité de la page les bloquerait de toute façon). */
  var ATTR_INTERDIT = /\s(on[a-z0-9_-]*|style)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi;
  /* Assainissement du HTML collé ou déposé, entièrement hors du document vivant : attributs d'événement (on…) et style retirés du texte
     AVANT toute analyse, puis analyse dans un document inerte (DOMParser) où l'on retire encore tout attribut on…, style, class, id et tout
     lien qui n'est ni http(s) ni mailto. Rien de ce HTML n'atteint la page avant ce nettoyage. */
  function assainir(html) {
    var source = String(html || '').replace(ATTR_INTERDIT, '');
    var doc = new DOMParser().parseFromString(source, 'text/html');
    $$('script,style,meta,link,iframe,object,embed,svg,math,template,form,input,button,select,textarea,base', doc.body).forEach(function (n) { n.remove(); });
    $$('*', doc.body).forEach(function (n) {
      Array.prototype.slice.call(n.attributes).forEach(function (a) {
        if (/^on/i.test(a.name) || a.name === 'style' || a.name === 'class' || a.name === 'id' || /^(href|src|action|formaction|xlink:href)$/i.test(a.name) && !/^(https?:|mailto:|data:image\/(png|jpeg|gif|webp);base64,)/i.test(a.value.trim())) n.removeAttribute(a.name);
      });
    });
    return doc;
  }
  function collerHTML(editor, html) {
    var doc = assainir(html), distantes = 0;
    $$('img', doc.body).forEach(function (img) {
      var src = (img.getAttribute('src') || '').trim();
      if (IMG_DATA.test(src)) return;
      if (/^(https?:)?\/\//i.test(src)) distantes++;
      img.replaceWith(doc.createTextNode(/^(https?:)?\/\//i.test(src) ? '[Image distante non insérée : copiez l’image elle-même]' : '[Image refusée]'));
    });
    var f = document.createDocumentFragment(), corps = document.importNode(doc.body, true), dernier;
    while (corps.firstChild) f.appendChild(corps.firstChild);
    dernier = f.lastChild;
    restaurer(editor);
    var sel = window.getSelection(), r = sel.getRangeAt(0);
    r.deleteContents(); r.insertNode(f);
    if (dernier) { r.setStartAfter(dernier); r.collapse(true); sel.removeAllRanges(); sel.addRange(r); }
    memoriser(editor);
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    if (distantes) toast('Image distante non insérée : copiez l’image elle-même (clic droit › Copier l’image), puis collez-la.', true);
  }
  function images(editor, pj, form) {
    if (pj) pj.editor = editor;
    editor.addEventListener('paste', function (ev) {   // phase de capture : avant tout autre écouteur
      var dt = ev.clipboardData;
      if (!dt) return;
      var fichiers = Array.prototype.slice.call(dt.items || []).filter(function (i) { return i.kind === 'file' && /^image\//.test(i.type); })
        .map(function (i) { return i.getAsFile(); }).filter(Boolean);
      if (fichiers.length) { ev.preventDefault(); memoriser(editor); insererImages(editor, fichiers, pj); return; }
      // tout HTML collé est assaini dans un document inerte avant insertion (aucun attribut on… ni image distante dans la page)
      var html = dt.getData && dt.getData('text/html');
      if (html) { ev.preventDefault(); ev.stopImmediatePropagation(); memoriser(editor); collerHTML(editor, html); return; }
      setTimeout(function () { nettoyerImages(editor); }, 0);
    }, true);
    editor.addEventListener('drop', function (ev) {   // HTML glissé depuis une page : même assainissement, au point de dépôt
      var dt = ev.dataTransfer, html = dt && dt.getData && dt.getData('text/html');
      if (html && !(dt.files && dt.files.length)) {
        ev.preventDefault(); ev.stopImmediatePropagation();
        var r = document.caretRangeFromPoint ? document.caretRangeFromPoint(ev.clientX, ev.clientY) : null;
        if (r && editor.contains(r.startContainer)) curseurs.set(editor, r);
        collerHTML(editor, html);
        return;
      }
      setTimeout(function () { nettoyerImages(editor); }, 0);
    }, true);
    nettoyerImages(editor);
    // outils de l'image choisie : trois largeurs fixes, taille d'origine, suppression
    var barreImg = el('div', { 'class': 'bx56106r-imgbar', role: 'toolbar', 'aria-label': 'Image choisie', hidden: 'hidden' });
    var cible = null;
    var cacher = function () { barreImg.hidden = true; if (cible) cible.classList.remove('is-choisie'); cible = null; };
    var outil = function (texte, titre, f) {
      var b = el('button', { type: 'button', 'class': 'bx56106r-ib bx56106r-txt', text: texte, title: titre, 'aria-label': titre });
      b.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
      b.addEventListener('click', function () { if (!cible) return; f(cible); editor.dispatchEvent(new Event('input', { bubbles: true })); });
      barreImg.appendChild(b);
    };
    LARGEURS.forEach(function (l) { outil(l[1], 'Largeur : ' + l[1].toLowerCase() + ' (' + l[0] + ' px)', function (i) { i.setAttribute('width', l[0]); }); });
    outil('Origine', 'Taille d’origine (au plus la largeur du message)', function (i) { i.removeAttribute('width'); });
    outil('🗑 Retirer', 'Retirer l’image', function (i) { i.remove(); cacher(); if (pj) pj.dessiner(); });
    form.appendChild(barreImg);
    editor.addEventListener('click', function (ev) {
      if (ev.target && ev.target.tagName === 'IMG' && editor.contains(ev.target)) {
        if (cible) cible.classList.remove('is-choisie');
        cible = ev.target; cible.classList.add('is-choisie');
        var r = cible.getBoundingClientRect(), f = form.getBoundingClientRect();
        barreImg.hidden = false;
        barreImg.style.top = Math.max(0, Math.round(r.top - f.top - barreImg.offsetHeight - 4)) + 'px';
        barreImg.style.left = Math.max(0, Math.round(r.left - f.left)) + 'px';
      } else cacher();
    });
    editor.addEventListener('scroll', cacher);
    editor.addEventListener('keydown', function (ev) { if (ev.key !== 'Tab') cacher(); });
  }

  // =================================================================== enregistrement automatique (toutes les 30 s s'il y a un changement)
  var PERIODE = 30000;
  /* 5.6.108 (lot C, B01) : révisions locales comptées ; une réponse n'acquitte que l'instantané qu'elle a enregistré. États affichés :
     « Enregistrement du brouillon… » pendant la requête, « Modifications à enregistrer… » dès qu'une frappe suit l'instantané,
     « Enregistré à … » seulement pour la version courante ; une erreur garde le texte et donne l'heure du prochain essai. Fermer l'onglet
     avec une saisie non acquittée demande confirmation ; une rédaction remplacée (autre message, changement de vue) enregistre d'abord ses
     dernières frappes (« quitter »), sauf après un envoi confirmé ou une fermeture voulue sans enregistrer (« arreter »). */
  function auto(form, enregistrer, peut) {
    var revision = 0, enregistree = 0, en = false, detache = 0, arrete = false, enErreur = false;
    var etat = el('span', { 'class': 'bx56106r-auto', role: 'status', 'aria-live': 'polite' });
    var actions = $('.bx5628-actions', form);
    if (actions) { var note = $('.ax-muted', actions); if (note) { etat.title = note.textContent; note.replaceWith(etat); } else actions.appendChild(etat); }
    var sale = function () { return revision > enregistree; };
    var marquer = function () {
      revision += 1;
      if (!en && !enErreur) etat.textContent = 'Modifications à enregistrer…';
    };
    form.addEventListener('input', marquer, true);
    form.addEventListener('change', marquer, true);
    var heure = function (d) { d = d || new Date(); return d.getHours() + ' h ' + ('0' + d.getMinutes()).slice(-2); };
    var minuteur = null;
    var essai = function () {
      if (arrete || !sale() || en || !peut()) return;
      en = true; enErreur = false; etat.classList.remove('is-bad');
      etat.textContent = 'Enregistrement du brouillon…';
      enregistrer().then(function () { /* état posé par enregistre() */ }, function (e) {
        clearInterval(minuteur); minuteur = setInterval(tour, PERIODE);   // prochain essai exactement dans PERIODE
        enErreur = true; etat.classList.add('is-bad');
        etat.textContent = 'Brouillon non enregistré (' + (e && e.message ? e.message : 'erreur') + '). Votre texte reste ici ; nouvel essai à ' +
          heure(new Date(Date.now() + PERIODE)) + '.';
      }).then(function () { en = false; });
    };
    var tour = function () {
      if (!document.contains(form)) {   // rédaction fermée (ou reprise ailleurs) : dernières frappes enregistrées une fois, puis arrêt
        if (!arrete && sale() && !en) essai();
        if (++detache > 20 || (!sale() && !en)) { clearInterval(minuteur); }
        return;
      }
      detache = 0;
      essai();
    };
    minuteur = setInterval(tour, PERIODE);
    var avantFermeture = function (ev) {   // onglet ou fenêtre fermés : avertissement tant que la saisie courante n'est pas acquittée
      if (arrete || !sale() || !document.contains(form)) return;
      ev.preventDefault(); ev.returnValue = '';
      return '';
    };
    if (typeof window !== 'undefined' && window.addEventListener) window.addEventListener('beforeunload', avantFermeture);
    var surveiller = function () {   // rédaction retirée de la page (changement de message) : dernières frappes enregistrées
      if (typeof MutationObserver === 'undefined' || !form.parentNode) return;
      new MutationObserver(function (records, obs) {
        if (document.contains(form)) return;
        obs.disconnect(); tour();
      }).observe(form.parentNode, { childList: true });
    };
    surveiller();
    return {
      revision: function () { return revision; },
      enregistre: function (acquittee) {
        // Ne jamais acquitter les frappes postérieures à l'instantané envoyé.
        if (typeof acquittee !== 'number' || acquittee < 0 || acquittee > revision) return;
        enregistree = Math.max(enregistree, acquittee);
        enErreur = false; etat.classList.remove('is-bad');
        etat.textContent = enregistree < revision ? 'Modifications à enregistrer…' : 'Enregistré à ' + heure();
      },
      sale: sale,
      marquer: marquer,
      tour: tour,
      quitter: function () { if (!arrete && sale() && !en) essai(); },   // avant de remplacer la rédaction
      arreter: function () {   // envoi confirmé ou fermeture voulue sans enregistrer : plus aucun enregistrement automatique
        arrete = true; clearInterval(minuteur);
        if (typeof window !== 'undefined' && window.removeEventListener) window.removeEventListener('beforeunload', avantFermeture);
      },
      reprendre: function () { if (!arrete) return; arrete = false; detache = 0; minuteur = setInterval(tour, PERIODE); surveiller();
        if (typeof window !== 'undefined' && window.addEventListener) window.addEventListener('beforeunload', avantFermeture); }
    };
  }

  // =================================================================== zone « Options d'envoi » (contenu : lot E)
  /* Conteneur <details class="bx5628-options-envoi" data-lot="E"> replié : une ligne « Options d'envoi » qui résume les cases cochées ;
     le lot E y ajoute ses réglages (importance, accusé de lecture, envoi programmé, délai d'annulation). */
  function optionsEnvoi() {
    var resume = el('span', { 'class': 'bx56106r-opt-etat' });
    var zone = el('details', { 'class': 'bx5628-options-envoi bx56106r-options', 'data-lot': 'E' },
                  [el('summary', {}, [el('span', { text: 'Options d’envoi' }), resume])]);
    var maj = function () {
      var choix = $$('input[type=checkbox]', zone).filter(function (c) { return c.checked; }).map(function (c) {
        var l = c.closest('label'); return (l ? l.textContent : c.name || '').trim().toLowerCase();
      }).filter(Boolean);
      resume.textContent = choix.length ? ' : ' + choix.join(', ') : '';
    };
    zone.addEventListener('change', maj);
    zone.addEventListener('toggle', function () { var f = zone.closest('form'); if (f) ajuster(f); });
    return zone;
  }

  // =================================================================== mise en page : une seule zone de défilement (l'éditeur)
  function ajuster(form) {
    var fit = function () {   // hauteur = place restante dans la vue ET dans la colonne (seule l'éditeur défile)
      if (!document.contains(form)) { window.removeEventListener('resize', fit); return; }
      var vh = window.innerHeight, haut = Math.max(0, form.getBoundingClientRect().top), h = vh - haut - 12;
      var panel = form.parentElement;
      if (panel) {
        var cs = getComputedStyle(panel), bas = (parseFloat(cs.paddingBottom) || 0) + (parseFloat(cs.borderBottomWidth) || 0) + 1;
        // hauteur de la colonne indépendante du formulaire (grille de la page) : mesurée formulaire réduit, puis rétabli
        var ancien = form.style.cssText;
        form.style.height = '1px'; form.style.minHeight = '0';
        var colonne = panel.getBoundingClientRect();
        form.style.cssText = ancien;
        if (colonne.height > 240) h = Math.min(h, colonne.bottom - bas - haut);
        var max = parseFloat(cs.maxHeight);
        if (isFinite(max)) h = Math.min(h, colonne.top + max - bas - haut);
      }
      form.style.setProperty('--bx56106r-h', Math.max(300, Math.floor(h)) + 'px');
    };
    if (!form.__bx56106fit) { form.__bx56106fit = fit; window.addEventListener('resize', fit); }
    requestAnimationFrame(form.__bx56106fit);
  }

  window.axh56106r = { barre: barre, adresses: adresses, micro: micro, pieces: pieces, deposer: deposer, auto: auto, cases: cases, images: images, optionsEnvoi: optionsEnvoi, remplir: remplir, fragment: fragment,
                       curseur: curseur, inserer: inserer, ajuster: ajuster, normaliser: normaliser };
})();
