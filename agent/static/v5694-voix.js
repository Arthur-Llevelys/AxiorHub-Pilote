/* AxiorHub 5.6.94 (lot voix) : lecture à voix haute et micro, communs à toutes les pages.
   - window.axiorhubVoix : lecture. Le serveur prépare le texte (abréviations développées, droits, audit) ; avec Brindille, la voix est
     produite dans un Web Worker (module), au fil des phrases, et jouée par AudioContext : aucun texte ne quitte le navigateur. Avec une
     voix du serveur (Kokoro, eSpeak, Chatterbox, ElevenLabs), même découpage, fichiers audio de la route existante.
     Si Brindille ne démarre pas (navigateur ancien, politique de sécurité d'un hébergeur), message clair puis voix de repli du serveur.
   - window.axiorhubMic : capture du micro. Quand « Isoler ma voix » est actif, Alkove (Focus, Cue) traite le son dans le navigateur ;
     modèles et moteur viennent d'AxiorHub seulement. Si Alkove ne se charge pas, le micro brut est rendu, avec une mention discrète.
   Rien n'est téléchargé avant la première lecture ou la première dictée. */
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
  async function api(route, data) {
    const r = await fetch(prefix + '/api440/' + route, data === undefined ? {credentials: 'same-origin'} : {method: 'POST', credentials: 'same-origin',
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
  function synth(text, speed) {
    return startWorker().then(w => new Promise((resolve, reject) => {
      const id = ++seq; pending.set(id, {resolve, reject});
      w.postMessage({type: 'speak', id, text, speed});
    }));
  }

  // ------------------------------------------------------------ lecture
  let ctx = null, gen = 0, sources = new Set(), audio = null, audioURL = '', active = false, endWait = null;
  const stats = {samples: 0, peak: 0, provider: '', chunks: 0};       // lu par le banc navigateur (P02h) : un son non vide a été produit
  window.axiorhubVoixStats = stats;
  function context() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    if (!ctx || ctx.state === 'closed') ctx = new AC();
    return ctx;
  }
  function stop() {
    gen++; active = false;
    sources.forEach(s => { try { s.stop(); } catch (e) { /* déjà arrêtée */ } }); sources.clear();
    if (audio) { audio.pause(); audio.src = ''; audio = null; }
    if (audioURL) { URL.revokeObjectURL(audioURL); audioURL = ''; }
    paused = false;
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
    const w = endWait; endWait = null; if (w) w();
  }
  // Attente interrompue par stop() : une lecture coupée rend la main tout de suite. Si l'horloge audio ne progresse pas (aucune sortie son,
  // onglet sans droit de lecture), l'attente se termine d'après l'heure réelle : la lecture ne reste jamais bloquée « en cours ».
  let paused = false;
  function waitUntil(seconds, g) {
    return new Promise(resolve => {
      let limite = performance.now() + Math.max(0, seconds - (ctx ? ctx.currentTime : 0)) * 1000 + 1500;
      const tick = () => {
        if (g !== gen || !ctx) { resolve(); return; }
        const left = seconds - ctx.currentTime;
        if (paused) limite = performance.now() + Math.max(0, left) * 1000 + 1500;   // en pause : l'attente suit la reprise
        if (left <= 0 || performance.now() > limite) { resolve(); return; }
        endWait = resolve; setTimeout(tick, Math.min(250, Math.max(20, left * 1000)));
      };
      tick();
    });
  }
  function play(pcm, rate, at, g) {
    if (g !== gen) return at;
    let peak = 0; for (let i = 0; i < pcm.length; i += 64) peak = Math.max(peak, Math.abs(pcm[i]));
    stats.samples += pcm.length; stats.peak = Math.max(stats.peak, peak); stats.chunks++;
    const buffer = ctx.createBuffer(1, pcm.length, rate);
    buffer.copyToChannel(pcm, 0);
    const source = ctx.createBufferSource();
    source.buffer = buffer; source.connect(ctx.destination);
    source.onended = () => sources.delete(source);
    const start = Math.max(at, ctx.currentTime + 0.02);
    source.start(start); sources.add(source);
    return start + pcm.length / rate;
  }
  function chunksOf(prep) {
    const out = [];
    (prep.paragraphs || []).forEach((paragraph, i, all) => paragraph.forEach((text, j) => out.push({
      text, pause: j === paragraph.length - 1 && i < all.length - 1 ? Math.max(0, Number(prep.pause_ms) || 0) / 1000 : 0.12})));
    return out;
  }
  async function browserRead(prep, rate, g, note) {
    if (!context()) throw new Error(msg('unsupported', 'ce navigateur ne permet pas WebAssembly ou les Web Workers'));
    if (!worker) note(msg('loading', 'Chargement de la voix Brindille (une seule fois, environ 2,6 Mo)…'));
    await ctx.resume().catch(() => {});
    const parts = chunksOf(prep);
    if (!parts.length) return;
    let next = synth(parts[0].text, rate), at = 0;
    for (let i = 0; i < parts.length; i++) {
      const r = await next;
      if (g !== gen) return;
      if (i === 0) note(msg('playing', 'Lecture en cours. « Arrêter » coupe immédiatement.'));
      if (i + 1 < parts.length) next = synth(parts[i + 1].text, rate);
      at = play(r.pcm, r.rate, at, g) + parts[i].pause;
      await waitUntil(at - 1.0, g);              // une phrase d'avance au plus : un arrêt ne laisse rien en file
      if (g !== gen) return;
    }
    await waitUntil(at, g);
  }
  function fetchPart(text, matter) {
    const p = fetch(prefix + '/api440/m568/speech', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify({text, matter})})
      .then(r => r.ok ? r.blob() : r.json().catch(() => ({})).then(v => { throw new Error(v.message || v.error || 'HTTP ' + r.status); }));
    p.catch(() => {});
    return p;
  }
  function playBlob(blob, g) {
    return new Promise(resolve => {
      if (g !== gen) { resolve(); return; }
      if (audioURL) URL.revokeObjectURL(audioURL);
      audioURL = URL.createObjectURL(blob); audio = new Audio(audioURL);
      endWait = resolve;
      audio.onended = () => { endWait = null; resolve(); };
      audio.onerror = () => { endWait = null; resolve(); };
      audio.play().catch(() => { endWait = null; resolve(); });
    });
  }
  async function serverRead(prep, matter, g, note) {
    const parts = chunksOf(prep);
    if (!parts.length) return;
    let next = fetchPart(parts[0].text, matter);
    for (let i = 0; i < parts.length; i++) {
      const blob = await next;
      if (g !== gen) return;
      if (i === 0) note(msg('playing', 'Lecture en cours. « Arrêter » coupe immédiatement.'));
      if (i + 1 < parts.length) next = fetchPart(parts[i + 1].text, matter);
      stats.samples += blob.size; stats.chunks++;
      await playBlob(blob, g);
      if (g !== gen) return;
      if (parts[i].pause > 0.2) await new Promise(r => setTimeout(r, parts[i].pause * 1000));
    }
  }
  /* Lit ``text``. Options : matter (dossier, pour les droits et l'audit), note(texte) pour l'état, rate (vitesse d'essai).
     Rend {provider, browser, stopped} ; une erreur est levée seulement si aucune voix n'a pu lire (le message dit pourquoi). */
  async function speak(text, opts = {}) {
    stop();
    const g = ++gen, note = typeof opts.note === 'function' ? opts.note : () => {};
    text = String(text || '').trim();
    if (!text) { note(msg('empty', 'Aucun texte à lire.')); return {stopped: true}; }
    active = true;
    stats.samples = 0; stats.peak = 0; stats.chunks = 0; stats.provider = '';
    try {
      const prep = await api('m5689/voix/preparer', {text: text.slice(0, 7000), matter: opts.matter || ''});
      if (g !== gen) return {stopped: true};
      const rate = Number(opts.rate) || Number(prep.rate) || 1;
      stats.provider = prep.provider;
      if (prep.browser) {
        try {
          await browserRead(prep, rate, g, note);
          if (g === gen) note(msg('done', 'Lecture terminée.'));
          return {provider: 'brindille', browser: true, stopped: g !== gen};
        } catch (e) {
          if (g !== gen) return {stopped: true};
          // V06 : jamais de silence sans explication ; la voix de repli du serveur prend le relais
          note(msg('fallback', 'La voix du navigateur n’a pas pu démarrer ({raison}). Lecture par la voix de repli du serveur ({voix}).',
            {raison: e.message, voix: prep.fallback || 'espeak'}));
          stats.provider = prep.fallback || 'espeak';
          await serverRead(prep, opts.matter || '', g, () => {});
          return {provider: prep.fallback, browser: false, fallback: true, reason: e.message, stopped: g !== gen};
        }
      }
      await serverRead(prep, opts.matter || '', g, note);
      if (g === gen) note(msg('done', 'Lecture terminée.'));
      return {provider: prep.provider, browser: false, stopped: g !== gen};
    } catch (e) {
      if (g === gen) note(msg('error', 'Lecture impossible : {raison}', {raison: e.message}));
      throw e;
    } finally {
      if (g === gen) active = false;
    }
  }
  function togglePause() {
    if (audio) { audio.paused ? audio.play().catch(() => {}) : audio.pause(); return; }
    if (!ctx) return;
    paused = ctx.state === 'running';
    (paused ? ctx.suspend() : ctx.resume()).catch(() => {});
  }
  window.axiorhubVoix = {speak, stop, togglePause, speaking: () => active, failure: () => failure};

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
