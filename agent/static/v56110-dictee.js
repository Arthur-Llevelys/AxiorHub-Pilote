/* AxiorHub 5.6.110 (lot D) — dictée sur ce poste : l'audio dicté part en WAV 16 kHz mono PCM 16 bits.
   Chargé avant les autres scripts, seulement quand la dictée du poste est active. Enveloppe window.fetch UNIQUEMENT pour
   POST <préfixe>/dictation dont le corps est un Blob qui n'est pas déjà du WAV : décodage (decodeAudioData), mixage mono et
   rééchantillonnage à 16 kHz (OfflineAudioContext), en-tête RIFF, Content-Type audio/wav. Toute autre requête passe intacte.
   Si la conversion échoue, la requête part telle quelle et le serveur répond par un refus lisible. Aucun appel existant n'est
   réécrit ; rien n'est gardé dans le navigateur. */
(function () {
  'use strict';
  var racine = window;
  if (racine.ax110Dictee || typeof racine.fetch !== 'function') return;
  var meta = document.querySelector('meta[name="axiorhub-prefix"]');
  var prefix = meta ? meta.content : '';
  var origine = racine.fetch;
  var TAUX = 16000;

  function chemin(entree) {
    try { return new URL(String(entree), racine.location.href); } catch (e) { return null; }
  }
  var cible = chemin(prefix + '/dictation');

  function concerne(entree, init) {
    if (!cible || !init || typeof entree !== 'string' && !(entree instanceof URL)) return false;
    if (String(init.method || 'GET').toUpperCase() !== 'POST') return false;
    if (!(init.body instanceof Blob)) return false;
    var u = chemin(entree);
    return !!u && u.origin === cible.origin && u.pathname === cible.pathname;
  }

  function octets(blob) {
    if (blob.arrayBuffer) return blob.arrayBuffer();
    return new Promise(function (ok, ko) {
      var r = new FileReader();
      r.onload = function () { ok(r.result); };
      r.onerror = function () { ko(r.error); };
      r.readAsArrayBuffer(blob);
    });
  }

  function dejaWav(blob, tampon) {
    if (/^audio\/(wav|x-wav|wave)$/i.test(blob.type || '')) return true;
    var v = new Uint8Array(tampon, 0, Math.min(12, tampon.byteLength));
    return v.length >= 12 && String.fromCharCode(v[0], v[1], v[2], v[3]) === 'RIFF' && String.fromCharCode(v[8], v[9], v[10], v[11]) === 'WAVE';
  }

  function decoder(ctx, tampon) {
    return new Promise(function (ok, ko) {
      var p = ctx.decodeAudioData(tampon, ok, ko);   // forme à rappels (anciens navigateurs) et forme à promesse
      if (p && typeof p.then === 'function') p.then(ok, ko);
    });
  }

  function enWav(echantillons) {
    var n = echantillons.length, tampon = new ArrayBuffer(44 + n * 2), v = new DataView(tampon);
    function texte(pos, s) { for (var i = 0; i < s.length; i++) v.setUint8(pos + i, s.charCodeAt(i)); }
    texte(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); texte(8, 'WAVE'); texte(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, TAUX, true);
    v.setUint32(28, TAUX * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); texte(36, 'data'); v.setUint32(40, n * 2, true);
    for (var i = 0; i < n; i++) {
      var x = Math.max(-1, Math.min(1, echantillons[i]));
      v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7FFF, true);
    }
    return new Blob([tampon], { type: 'audio/wav' });
  }

  function convertir(blob) {
    var Hors = racine.OfflineAudioContext || racine.webkitOfflineAudioContext;
    if (!Hors) return Promise.reject(new Error('OfflineAudioContext'));
    return octets(blob).then(function (tampon) {
      if (dejaWav(blob, tampon)) return null;                              // déjà du WAV : rien à faire
      return decoder(new Hors(1, 1, TAUX), tampon).then(function (son) {   // décodé et rééchantillonné à 16 kHz
        var longueur = Math.max(1, Math.ceil(son.duration * TAUX));
        var ctx = new Hors(1, longueur, TAUX);                             // une seule voie : mixage mono par le moteur audio
        var source = ctx.createBufferSource();
        source.buffer = son;
        source.connect(ctx.destination);
        source.start(0);
        return ctx.startRendering();
      }).then(function (rendu) { return enWav(rendu.getChannelData(0)); });
    });
  }

  racine.fetch = function (entree, init) {
    if (!concerne(entree, init)) return origine.apply(racine, arguments);
    return convertir(init.body).then(function (wav) {
      if (!wav) return origine.call(racine, entree, init);
      var entetes = new Headers(init.headers || {});
      entetes.set('Content-Type', 'audio/wav');
      var suite = {};
      for (var k in init) { if (Object.prototype.hasOwnProperty.call(init, k)) suite[k] = init[k]; }
      suite.body = wav;
      suite.headers = entetes;
      return origine.call(racine, entree, suite);
    }, function () {
      return origine.call(racine, entree, init);                             // conversion impossible : refus lisible du serveur
    });
  };
  // Durée d'enregistrement permise aux dictées confidentielles en mode poste (v310.js, v56106-redaction.js) : 9 min 50 s, sous les
  // 10 minutes acceptées par le serveur (marge pour l'arrondi de la conversion). 9 min 50 s en WAV 16 kHz mono : 18,9 Mo < 20 Mo.
  racine.ax110Dictee = { convertir: convertir, dureeMax: 590000 };
})();
