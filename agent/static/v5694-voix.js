/* AxiorHub 5.6.94 (lot voix) : lecture à voix haute et micro, communs à toutes les pages.
   - window.axiorhubVoix : lecture. Le serveur prépare le texte (abréviations développées, droits, audit) ; avec Brindille, la voix est
     produite dans un Web Worker (module), au fil des phrases, et jouée par AudioContext : aucun texte ne quitte le navigateur. Avec une
     voix du serveur (Kokoro, eSpeak, Chatterbox, ElevenLabs), même découpage, fichiers audio de la route existante.
     Si Brindille ne démarre pas (navigateur ancien, politique de sécurité d'un hébergeur), message clair puis voix de repli du serveur.
   - window.axiorhubMic : capture du micro. Quand « Isoler ma voix » est actif, Alkove (Focus, Cue) traite le son dans le navigateur ;
     modèles et moteur viennent d'AxiorHub seulement. Si Alkove ne se charge pas, le micro brut est rendu, avec une mention discrète.
   Rien n'est téléchargé avant la première lecture ou la première dictée.
   5.6.96 (lot voix) : file de lecture unique sans troncature (V01), résultat typé et « Reprendre au segment n » (V02), arrêt et
   annulation des requêtes audio (V03), diagnostic local sans texte ni audio et repli du serveur annoncé (V04), profil Brindille transmis au
   worker (V05), niveau sonore local pour l'avatar (V06). */
(() => {
  'use strict';
  const meta = name => document.querySelector('meta[name="' + name + '"]')?.content || '';
  const prefix = meta('axiorhub-prefix'), csrf = meta('axiorhub-csrf');
  const own = document.currentScript?.src || '';
  let version = '';
  try { version = new URL(own, location.href).searchParams.get('v') || ''; } catch (e) { version = ''; }
  const query = version ? '?v=' + encodeURIComponent(version) : '';
  const asset = path => prefix + '/static/' + path + query;          // adresses versionnées : gardées par le navigateur
  let messages = {};
  const msg = (key, fallback, values) => {
    if (!messages.__lu) { try { messages = JSON.parse(document.querySelector('#l5631-msg')?.textContent || '{}'); } catch (e) { messages = {}; } messages.__lu = true; }
    let text = messages['js.5694.' + key] || fallback;
    Object.entries(values || {}).forEach(([k, v]) => { text = text.split('{' + k + '}').join(String(v)); });
    return text;
  };
  async function api(route, data, signal) {
    const r = await fetch(prefix + '/api440/' + route, data === undefined ? {credentials: 'same-origin', signal} : {method: 'POST', credentials: 'same-origin', signal,
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)});
    const v = await r.json().catch(() => ({}));
    if (!r.ok || v.error) throw new Error(v.message || v.error || ('HTTP ' + r.status));
    return v;
  }

  // ------------------------------------------------------------ Brindille dans un Web Worker (module)
  let worker = null, starting = null, failure = '', seq = 0;
  const pending = new Map();
  function startWorker() {
    if (worker) return Promise.resolve(worker);
    if (failure) return Promise.reject(new Error(failure));
    if (starting) return starting;
    starting = new Promise((resolve, reject) => {
      const fail = reason => { failure = reason; starting = null; reject(new Error(reason)); };
      if (typeof WebAssembly !== 'object' || typeof Worker !== 'function') { fail(msg('unsupported', 'ce navigateur ne permet pas WebAssembly ou les Web Workers')); return; }
      let w;
      try { w = new Worker(asset('v5694-brindille.js'), {type: 'module'}); } catch (e) { fail(msg('blocked', 'le module de voix a été bloqué') + ' (' + e.message + ')'); return; }
      const timer = setTimeout(() => { w.terminate(); fail(msg('timeout', 'le chargement a dépassé 30 secondes')); }, 30000);
      w.onmessage = event => {
        const d = event.data || {};
        if (d.type === 'ready') { clearTimeout(timer); worker = w; starting = null; resolve(w); return; }
        if (d.type === 'error' && !worker) { clearTimeout(timer); w.terminate(); fail(d.message || msg('blocked', 'le module de voix a été bloqué')); return; }
        const job = pending.get(d.id);
        if (!job) return;
        pending.delete(d.id);
        if (d.type === 'audio') job.resolve({pcm: d.pcm, rate: d.sampleRate}); else job.reject(new Error(d.message || 'synthèse'));
      };
      w.onerror = event => {
        event.preventDefault?.();
        clearTimeout(timer);
        if (!worker) { w.terminate(); fail(event.message || msg('blocked', 'le module de voix a été bloqué, peut-être par la politique de sécurité de l’hébergeur')); return; }
        pending.forEach(job => job.reject(new Error(event.message || 'synthèse'))); pending.clear();
      };
      w.postMessage({type: 'init', base: prefix + '/static/brindille/', version});
    });
    return starting;
  }
  // 5.6.96 : une synthèse par segment, avec les valeurs du profil (V05) ; arrêt (AbortController) ou délai dépassé : promesse rejetée,
  // réponse tardive du worker ignorée.
  function synth(text, speed, voix, signal) {
    return startWorker().then(w => new Promise((resolve, reject) => {
      const arret = () => Object.assign(new Error('arrêt'), {name: 'AbortError'});
      if (signal && signal.aborted) { reject(arret()); return; }
      const id = ++seq;
      const fin = () => { clearTimeout(timer); pending.delete(id); if (signal) signal.removeEventListener('abort', annuler); };
      const annuler = () => { fin(); reject(arret()); };
      const timer = setTimeout(() => { fin(); reject(new Error(msg('synth_timeout', 'la synthèse du segment a dépassé 30 secondes'))); }, 30000);
      if (signal) signal.addEventListener('abort', annuler, {once: true});
      pending.set(id, {resolve: r => { fin(); resolve(r); }, reject: e => { fin(); reject(e); }});
      w.postMessage({type: 'speak', id, text, speed, voix: voix || null});
    }));
  }

  // ------------------------------------------------------------ lecture (5.6.96 : V01 file unique, V02 états typés, V03 arrêt, V04 diagnostic)
  /* Une lecture = un tour : le texte intégral est préparé une fois par le serveur (normalisé, découpé en segments), puis les segments sont
     joués dans l'ordre, un seul d'avance au plus. Chaque segment attend la fin réelle de sa lecture (« ended »). Résultat typé :
     {statut: 'termine' | 'interrompu_utilisateur' | 'interrompu_voix', segment, segments, tour, provider, …} ; un échec rejette la promesse
     avec err.resultat = {statut: 'echec_segment', segment: n, …} : l'appelant propose « Reprendre au segment n » (opts.depuis = n). */
  const REPLIS_LOCAUX = ['espeak', 'kokoro', 'chatterbox'];
  const PLAFOND_ENVOI = 100000;              // plafond explicite de lecture totale (voix5694.PLAFOND_LECTURE) : annoncé, jamais muet
  let ctx = null, gen = 0, active = false, paused = false, run = null, motifArret = 'utilisateur', moteurCourant = '';
  let source = null, audio = null, finir = null, analyse = null, onde = null, suivi = false;
  let dernier = null;                        // dernière préparation : « Reprendre au segment n » ne reprépare ni ne réaudite le même texte
  const stats = {samples: 0, peak: 0, provider: '', chunks: 0};       // lu par le banc navigateur (P02h) : un son non vide a été produit
  window.axiorhubVoixStats = stats;
  const journal = [];
  function consigner(entree) {               // V04 : tampon local de 50 entrées, sans texte ni audio ; aussi dans la console
    const ligne = Object.assign({heure: new Date().toISOString()}, entree);
    journal.push(ligne); if (journal.length > 50) journal.shift();
    try { console.info('[voix]', JSON.stringify(ligne)); } catch (e) { /* console absente */ }
    return ligne;
  }
  function context() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    if (!ctx || ctx.state === 'closed') ctx = new AC();
    return ctx;
  }
  /* Arrêt immédiat : requêtes audio annulées (AbortController), lecteur coupé, segments d'avance abandonnés. motif : 'utilisateur' ou 'voix'. */
  function stop(motif) {
    motifArret = motif === 'voix' ? 'voix' : 'utilisateur';
    gen++; active = false; paused = false; moteurCourant = '';
    if (run) { run.abort(); run = null; }
    const f = finir; finir = null; if (f) f(null);
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
  }
  function attente(ms, signal) {
    if (!(ms > 0) || signal.aborted) return Promise.resolve();
    return new Promise(resolve => {
      const fin = () => { clearTimeout(t); signal.removeEventListener('abort', fin); resolve(); };
      const t = setTimeout(fin, ms); signal.addEventListener('abort', fin, {once: true});
    });
  }
  /* Segment Brindille : se résout à « ended ». Contexte audio que le navigateur garde suspendu (lecture refusée) : échec annoncé. Horloge
     audio figée alors que le contexte tourne (aucune sortie son) : fin d'après l'heure réelle, consignée au diagnostic. */
  function jouerPCM(pcm, rate, signal) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { resolve(); return; }
      if (!pcm || !pcm.length || !rate) { reject(new Error(msg('audio_vide', 'audio de synthèse vide'))); return; }
      let peak = 0; for (let i = 0; i < pcm.length; i += 64) peak = Math.max(peak, Math.abs(pcm[i]));
      stats.samples += pcm.length; stats.peak = Math.max(stats.peak, peak);
      let node;
      try {
        const buffer = ctx.createBuffer(1, pcm.length, rate);
        buffer.copyToChannel(pcm, 0);
        node = ctx.createBufferSource(); node.buffer = buffer; node.connect(ctx.destination);
        if (suivi) {                         // V06 : niveau sonore local pour l'avatar, seulement s'il est demandé
          if (!analyse) { analyse = ctx.createAnalyser(); analyse.fftSize = 256; onde = new Float32Array(analyse.fftSize); }
          node.connect(analyse);
        }
      } catch (e) { reject(new Error(msg('decodage', 'audio illisible ({raison})', {raison: e.message}))); return; }
      const duree = pcm.length / rate * 1000;
      let veille = null, ecoule = 0, suspendu = 0, pas = performance.now();
      const fin = erreur => {
        clearInterval(veille); node.onended = null;
        try { node.stop(); } catch (e) { /* déjà arrêtée */ }
        try { node.disconnect(); } catch (e) { /* déjà déconnectée */ }
        if (source === node) source = null;
        if (finir === fin) finir = null;
        if (erreur) reject(erreur); else resolve();
      };
      source = node; finir = fin; node.onended = () => fin(null);
      veille = setInterval(() => {
        const maintenant = performance.now(), delta = maintenant - pas; pas = maintenant;
        if (paused) return;
        if (ctx.state !== 'running') {
          suspendu += delta;
          if (suspendu >= 4000) fin(new Error(msg('refus', 'le navigateur a refusé la lecture audio : cliquez dans la page puis reprenez')));
          return;
        }
        suspendu = 0; ecoule += delta;
        if (ecoule > duree + 3000) { consigner({evenement: 'horloge_audio_figee', segment_ms: Math.round(duree)}); fin(null); }
      }, 250);
      try { if (ctx.state !== 'running') ctx.resume().catch(() => {}); node.start(); }
      catch (e) { fin(new Error(msg('refus_debut', 'lecture refusée ({raison})', {raison: e.message}))); }
    });
  }
  /* Segment d'une voix du serveur (fichier audio) : se résout à « ended » ; erreur de décodage ou play() refusé : échec. */
  function jouerBlob(blob, signal) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { resolve(); return; }
      if (!blob || !blob.size) { reject(new Error(msg('audio_vide', 'audio de synthèse vide'))); return; }
      stats.samples += blob.size;
      const url = URL.createObjectURL(blob), el = new Audio(url);
      audio = el;
      let veille = null, position = -1, fige = 0;
      const fin = erreur => {
        clearInterval(veille); el.onended = null; el.onerror = null;
        try { el.pause(); } catch (e) { /* déjà arrêté */ }
        URL.revokeObjectURL(url);
        if (audio === el) audio = null;
        if (finir === fin) finir = null;
        if (erreur) reject(erreur); else resolve();
      };
      finir = fin;
      el.onended = () => fin(null);
      el.onerror = () => fin(new Error(msg('decodage_serveur', 'audio du serveur illisible')));
      veille = setInterval(() => {
        if (paused) { fige = 0; return; }
        const t = Number(el.currentTime) || 0;
        if (t > position) { position = t; fige = 0; } else if ((fige += 500) >= 10000) fin(new Error(msg('bloquee', 'lecture audio bloquée')));
      }, 500);
      let p;
      try { p = el.play(); } catch (e) { fin(new Error(msg('refus_debut', 'lecture refusée ({raison})', {raison: e.message}))); return; }
      if (p && typeof p.catch === 'function') p.catch(e => fin(new Error(msg('refus_debut', 'lecture refusée ({raison})', {raison: (e && e.message) || e}))));
    });
  }
  async function chercher(text, matter, signal) {
    const r = await fetch(prefix + '/api440/m568/speech', {method: 'POST', credentials: 'same-origin', signal,
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify({text, matter})});
    if (!r.ok) { const v = await r.json().catch(() => ({})); throw new Error(v.message || v.error || 'HTTP ' + r.status); }
    return r.blob();
  }
  /* V01 : une phrase plus longue que le plafond du moteur est coupée aux espaces, jamais tronquée. */
  function couper(text, max) {
    const out = []; let reste = String(text || '');
    while (reste.length > max) {
      let fin = reste.lastIndexOf(' ', max);
      if (fin < max / 2) fin = max;
      if (/[\uD800-\uDBFF]/.test(reste.charAt(fin - 1))) fin--;
      out.push(reste.slice(0, fin).trim()); reste = reste.slice(fin);
    }
    if (reste.trim()) out.push(reste.trim());
    return out.filter(Boolean);
  }
  function sequence(prep, plafond) {
    const tous = Array.isArray(prep.paragraphs) ? prep.paragraphs : [], out = [];
    tous.forEach((paragraphe, i) => (paragraphe || []).forEach((phrase, j) => {
      const morceaux = couper(phrase, plafond);
      morceaux.forEach((text, k) => out.push({text, pause: k === morceaux.length - 1 && j === paragraphe.length - 1 && i < tous.length - 1
        ? Math.min(3, Math.max(0, Number(prep.pause_ms) || 0) / 1000) : 0.12}));
    }));
    return out;
  }
  async function secoursServeur(signal) {   // V04 : dernier passage du serveur à sa voix de secours (cause), lu sans texte
    try { return (await api('m5689/voix/etat', undefined, signal)).last_fallback || null; } catch (e) { return null; }
  }
  /* Lit ``text``. Options : matter (dossier, droits et audit), note(texte) pour l'état, rate (vitesse d'essai), profil (essai d'écoute,
     liste fermée côté serveur), depuis (n : reprendre au segment n), tour (identifiant du tour, pour le diagnostic). */
  async function speak(text, opts = {}) {
    stop();
    const g = ++gen, note = typeof opts.note === 'function' ? opts.note : () => {};
    const ctl = new AbortController(); run = ctl; const signal = ctl.signal; motifArret = 'utilisateur';
    text = String(text || '').trim();
    const tour = String(opts.tour || ('lecture-' + Date.now().toString(36) + '-' + g));
    if (!text) { note(msg('empty', 'Aucun texte à lire.')); return {statut: 'vide', stopped: true, segment: 0, segments: 0, tour}; }
    active = true;
    stats.samples = 0; stats.peak = 0; stats.chunks = 0; stats.provider = '';
    const debut = performance.now(), depuis = Math.max(1, Math.floor(Number(opts.depuis) || 1));
    let prep = null, parts = [], n = 0, demande = '', navigateur = false, repli = false, motifRepli = '', secours = null, limite = null;
    const resultat = (statut, plus) => {
      const r = Object.assign({statut, segment: n, segments: parts.length, depuis, tour, provider: stats.provider, demande, browser: navigateur,
        fallback: repli, raison_repli: motifRepli, profil: prep ? String(prep.profil || '') : '', limite, secours_serveur: secours,
        duree_ms: Math.round(performance.now() - debut), stopped: statut !== 'termine'}, plus || {});
      consigner({tour, statut, moteur_demande: demande, moteur_effectif: stats.provider, profil: r.profil, segment: n, segments: parts.length, depuis,
        duree_ms: r.duree_ms, erreur: r.erreur || '', interruption: statut.indexOf('interrompu') === 0 ? motifArret : '', repli: repli ? motifRepli : '',
        delai_premier_son_ms: r.delai_premier_son_ms || null});
      return r;
    };
    const arret = () => resultat(motifArret === 'voix' ? 'interrompu_voix' : 'interrompu_utilisateur');
    let premierSon = 0;
    try {
      const envoi = text.length > PLAFOND_ENVOI ? text.slice(0, PLAFOND_ENVOI + 1) : text;
      const cle = [envoi, opts.matter || '', opts.profil || ''].join('\u0001');
      if (depuis > 1 && dernier && dernier.cle === cle) prep = dernier.prep;
      else {
        const corps = {text: envoi, matter: opts.matter || ''};
        if (opts.profil) corps.profil = String(opts.profil);
        prep = await api('m5689/voix/preparer', corps, signal);
        dernier = {cle, prep};
      }
      if (signal.aborted || g !== gen) return arret();
      limite = prep.limite ? Object.assign({}, prep.limite, {caracteres: Math.max(Number(prep.limite.caracteres) || 0, text.length)}) : null;
      demande = String(prep.provider || '');
      navigateur = prep.browser === true;
      const secoursLocal = REPLIS_LOCAUX.includes(prep.fallback) ? prep.fallback : 'espeak';
      if (navigateur && !context()) { navigateur = false; repli = true; motifRepli = msg('unsupported', 'ce navigateur ne permet pas WebAssembly ou les Web Workers'); }
      stats.provider = navigateur ? 'brindille' : (repli ? secoursLocal : demande);
      moteurCourant = navigateur ? 'brindille' : 'serveur';
      const rate = Number(opts.rate) || Number(prep.rate) || 1;
      const voix = prep.voix || null;        // V05 : profil résolu une fois pour le tour : même timbre pour tous les segments
      parts = sequence(prep, navigateur ? 2000 : 7000);
      if (!parts.length) { note(msg('empty', 'Aucun texte à lire.')); return resultat('vide'); }
      const premier = Math.min(depuis, parts.length);
      const annonce = limite ? ' ' + msg('limite', 'Lecture limitée à {lus} caractères sur {total} : la fin reste affichée sans être lue.',
        {lus: limite.lus || limite.plafond, total: limite.caracteres}) : '';
      if (navigateur && !worker) note(msg('loading', 'Chargement de la voix Brindille (une seule fois, environ 2,6 Mo)…'));
      if (navigateur) await ctx.resume().catch(() => {});
      let base = navigateur ? undefined : await secoursServeur(signal), verifier = !navigateur;
      const produire = i => navigateur ? synth(parts[i].text, rate, voix, signal) : chercher(parts[i].text, opts.matter || '', signal);
      let avance = null;                     // lecture anticipée : un segment au plus
      for (let i = premier - 1; i < parts.length; i++) {
        n = i + 1;
        if (signal.aborted || g !== gen) return arret();
        let son;
        try {
          son = await (avance && avance.i === i ? avance.p : produire(i));
        } catch (e) {
          avance = null;
          if (signal.aborted || g !== gen) return arret();
          if (navigateur) {
            // V02 : Brindille n'a pas produit ce segment : la voix de secours du serveur reprend à ce segment, le motif reste affiché
            navigateur = false; repli = true; motifRepli = (e && e.message) || String(e); stats.provider = secoursLocal; moteurCourant = 'serveur';
            note(msg('fallback_segment', 'La voix du navigateur n’a pas pu lire le segment {n} ({raison}). Suite par la voix de secours du serveur ({voix}).',
              {n, raison: motifRepli, voix: secoursLocal}));
            if (base === undefined) { base = await secoursServeur(signal); verifier = true; }
            i--; continue;
          }
          throw Object.assign(e instanceof Error ? e : new Error(String(e)), {nature: 'synthese'});
        }
        avance = null;
        if (signal.aborted || g !== gen) return arret();
        if (i + 1 < parts.length) { const p = produire(i + 1); p.catch(() => {}); avance = {i: i + 1, p}; }
        if (!premierSon) { premierSon = Math.round(performance.now() - debut); if (!repli) note(msg('playing', 'Lecture en cours. « Arrêter » coupe immédiatement.') + annonce); }
        try { await (navigateur ? jouerPCM(son.pcm, son.rate, signal) : jouerBlob(son, signal)); }
        catch (e) { throw Object.assign(e instanceof Error ? e : new Error(String(e)), {nature: 'lecture'}); }
        if (signal.aborted || g !== gen) return arret();
        stats.chunks++;
        if (verifier) {                      // V04 : le serveur est-il passé à sa voix de secours pour ce tour ?
          verifier = false;
          const apres = await secoursServeur(signal);
          if (apres && (!base || apres.at !== base.at)) {
            secours = apres;
            note(msg('secours_serveur', 'Voix de secours utilisée ({voix}) : {cause}.', {voix: apres.provider, cause: apres.message || apres.reason}));
          }
        }
        await attente(parts[i].pause * 1000, signal);
      }
      if (signal.aborted || g !== gen) return arret();
      if (repli) note(msg('done_fallback', 'Lecture terminée par la voix de secours du serveur ({voix}). Motif : {raison}.', {voix: stats.provider, raison: motifRepli}) + annonce);
      else if (secours) note(msg('done_secours', 'Lecture terminée par la voix de secours ({voix}) : {cause}.', {voix: secours.provider, cause: secours.message || secours.reason}) + annonce);
      else note(msg('done', 'Lecture terminée.') + annonce);
      return resultat('termine', {delai_premier_son_ms: premierSon});
    } catch (e) {
      if (signal.aborted || g !== gen) return arret();
      const raison = (e && e.message) || String(e);
      const message = n ? msg('segment_echec', 'Lecture interrompue au segment {n} sur {total} : {raison}. Le texte reste affiché ; « Reprendre au segment {n} » relance la lecture à cet endroit.',
        {n, total: parts.length, raison}) : msg('error', 'Lecture impossible : {raison}', {raison});
      note(message);
      const erreur = new Error(message);
      erreur.resultat = resultat(n ? 'echec_segment' : 'echec_preparation', {erreur: raison, nature: e.nature || 'preparation', delai_premier_son_ms: premierSon});
      erreur.statut = erreur.resultat.statut; erreur.segment = n;
      throw erreur;
    } finally {
      if (g === gen) { active = false; run = null; moteurCourant = ''; }
    }
  }
  function togglePause() {
    if (!active) return;
    if (audio) { paused = !paused; if (paused) audio.pause(); else audio.play().catch(e => { if (finir) finir(e); }); return; }
    if (!ctx) return;
    paused = !paused;
    (paused ? ctx.suspend() : ctx.resume()).catch(() => {});
  }
  /* V06 : niveau sonore local (0 à 1) de la lecture Brindille en cours, pour l'avatar ; 0 hors lecture, en pause ou avec une voix du serveur. */
  function monitoriser(oui) {
    suivi = oui === true;
    if (!suivi && analyse) { try { analyse.disconnect(); } catch (e) { /* déjà libéré */ } analyse = null; onde = null; }
  }
  function niveau() {
    if (!suivi || !analyse || !source || paused || !ctx || ctx.state !== 'running') return 0;
    analyse.getFloatTimeDomainData(onde);
    let energie = 0; for (let i = 0; i < onde.length; i++) energie += onde[i] * onde[i];
    return Math.min(1, Math.sqrt(energie / onde.length) * 5);
  }
  /* V03 : détecteur d'interruption par la voix pendant la lecture : seulement si actif() (choix de la personne, détection sûre), après
     ``trames`` trames stables au-dessus du seuil ; un pic isolé (bruit, écho du haut-parleur) remet le compte à zéro. */
  function detecteurInterruption(actif, seuil, trames) {
    const s = Number(seuil) || 0.09, t = Math.max(2, Number(trames) || 4);
    let suite = 0;
    return {
      trame(rms) {
        if (typeof actif !== 'function' || !actif()) { suite = 0; return false; }
        suite = Number(rms) > s ? suite + 1 : 0;
        if (suite >= t) { suite = 0; return true; }
        return false;
      },
      reinitialiser() { suite = 0; }
    };
  }
  window.axiorhubVoix = {speak, stop, togglePause, speaking: () => active, failure: () => failure, moteur: () => (active ? moteurCourant : ''),
    diagnostic: () => journal.map(l => Object.assign({}, l)), monitoriser, niveau, detecteurInterruption};

  // ------------------------------------------------------------ micro : Alkove (Focus, Cue) dans le navigateur
  // Chargeur figé sur AxiorHub : aucune adresse externe (Hugging Face n'est jamais contacté) ; empreintes vérifiées par Alkove.
  const ALKOVE = {
    modelBaseUrl: prefix + '/static/alkove/models/', assetBaseUrl: prefix + '/static/alkove/',
    models: {engine: asset('alkove/alkove-engine.wasm'), focus: asset('alkove/models/alkove-focus-1-preview.alk'), cue: asset('alkove/models/alkove-cue-1-preview.alk')}
  };
  let alkove = null, alkoveFailure = '';
  function loadAlkove() {
    if (alkoveFailure) return Promise.reject(new Error(alkoveFailure));
    alkove = alkove || import(asset('alkove/index.js')).then(mod => { mod.Alkove.configure(ALKOVE); return mod; });
    return alkove;
  }
  async function preferences() {
    try { return (await (window.axiorhubProfile567 || api('m567/profile'))) || {}; } catch (e) { return {}; }
  }
  const timeout = (promise, ms, label) => Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error(label)), ms))]);
  function rawCapture(stream, reason) {
    const note = reason ? msg('alkove_off', '(isolation de la voix indisponible : dictée sans traitement)') : '';
    stream.axiorhubNote = note;
    return {stream, mode: 'raw', reason: reason || '', note, stopOnSilence: false, close: () => stream.getTracks().forEach(t => t.stop()), on: () => () => {}};
  }
  /* Ouvre le micro. purpose : 'dictee' ou 'conversation'. Rend {stream, mode, note, close(), on(type, fn), ready, stopOnSilence}.
     Le flux rendu se ferme comme avant (stream.getTracks().forEach(t => t.stop())) : le traitement Alkove et le micro brut sont libérés. */
  async function capture(purpose, constraints) {
    const raw = await navigator.mediaDevices.getUserMedia(constraints || {audio: true});
    const p = await preferences();
    const isolate = p.voice_isolation !== false, cue = purpose === 'conversation' && p.stop_on_silence !== false;
    if (!isolate && !cue) return rawCapture(raw, '');
    let input;
    try {
      const mod = await timeout(loadAlkove(), 15000, msg('timeout', 'le chargement a dépassé 30 secondes'));
      const common = {stream: raw, normalize: true, strength: isolate ? 1 : 0};
      input = await timeout(purpose === 'conversation'
        ? mod.createAgentInput({...common, withTrack: true, turn: cue, interrupt: false, output: {format: 'f32'}})
        : mod.createCallInput({...common, keepAliveWhenHidden: false}), 15000, msg('timeout', 'le chargement a dépassé 30 secondes'));
      const track = input.track;
      if (!track) throw new Error('piste');
      const stream = new MediaStream([track]);
      let closed = false;
      const close = () => { if (closed) return; closed = true; try { input.dispose(); } catch (e) { /* déjà libéré */ } raw.getTracks().forEach(t => t.stop()); };
      const stopTrack = track.stop.bind(track);
      track.stop = () => { stopTrack(); close(); };
      stream.axiorhubNote = isolate ? msg('alkove_on', 'Voix isolée par Alkove.') : '';
      return {stream, mode: input.mode, note: stream.axiorhubNote, stopOnSilence: cue, close, on: (type, fn) => input.on(type, fn), ready: input.ready, input};
    } catch (e) {
      try { input?.dispose(); } catch (x) { /* rien */ }
      if (!String(e.message || '').startsWith('mic.')) alkoveFailure = alkoveFailure || String(e.message || e);
      return rawCapture(raw, String(e.message || e));
    }
  }
  window.axiorhubMic = {capture, stream: (purpose, constraints) => capture(purpose, constraints).then(c => c.stream), config: ALKOVE, failure: () => alkoveFailure};
})();
