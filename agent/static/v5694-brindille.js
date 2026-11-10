/* AxiorHub 5.6.94 (V05) : voix Brindille dans un Web Worker (module). La synthèse, qui tourne sinon sur le fil principal, ne fige plus
   la page. Les fichiers (moteur, modèle, dictionnaire) viennent d'AxiorHub seulement, à la même version que la page ; le texte reçu n'est
   envoyé nulle part. Messages : {type:'init', base, version} → {type:'ready'} ou {type:'error'} ; {type:'speak', id, text, speed, voix} →
   {type:'audio', id, pcm, sampleRate} (tampon transféré) ou {type:'error', id}.
   5.6.96 : V01, aucun segment coupé en silence (vide ou plus de 2 000 caractères : refusé avec sa raison) ; V05, ``voix`` = {pitch,
   intonation, treble, breath} du profil (« Français posé » ou valeurs neutres de l'originale) appliqué par tts.voice() AVANT la synthèse. */
let tts = null;
const PLAFOND = 2000;
const NEUTRE = { pitch: 0, intonation: 1, treble: 0, breath: 0 };
const BORNES = { pitch: [-12, 12], intonation: [0.5, 1.5], treble: [-12, 12], breath: [-12, 12] };
let voixAppliquee = '';

function appliquerVoix(demande) {
  // Valeurs bornées ; une valeur absente ou invalide revient au neutre. Réappliquées seulement quand elles changent (même timbre entre segments).
  const v = {};
  for (const [cle, [bas, haut]] of Object.entries(BORNES)) {
    const x = demande && demande[cle] !== undefined && demande[cle] !== null ? Number(demande[cle]) : NaN;
    v[cle] = Number.isFinite(x) ? Math.min(haut, Math.max(bas, x)) : NEUTRE[cle];
  }
  const empreinte = JSON.stringify(v);
  if (empreinte === voixAppliquee) return;
  tts.voice(v);
  voixAppliquee = empreinte;
}

async function init(base, version) {
  const root = new URL(base, self.location.href);
  if (root.origin !== self.location.origin) throw new Error('adresse de la voix hors d’AxiorHub refusée');
  const query = version ? '?v=' + encodeURIComponent(version) : '';
  const { Tts } = await import(new URL('tts.js' + query, root).href);
  tts = await Tts.load(root.href, async url => {
    const target = new URL(url);
    if (target.origin !== self.location.origin) throw new Error('adresse de la voix hors d’AxiorHub refusée');
    const response = await fetch(target.href + query, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(target.pathname.split('/').pop() + ' : HTTP ' + response.status);
    return response.arrayBuffer();
  });
}

self.onmessage = async event => {
  const data = event.data || {};
  if (data.type === 'init') {
    try {
      await init(String(data.base || ''), String(data.version || ''));
      self.postMessage({ type: 'ready', sampleRate: tts.sampleRate, sizes: tts.sizes });
    } catch (error) {
      self.postMessage({ type: 'error', message: String((error && error.message) || error) });
    }
    return;
  }
  if (data.type === 'speak') {
    try {
      if (!tts) throw new Error('voix non chargée');
      const text = String(data.text || '');
      if (!text.trim()) throw new Error('segment de lecture vide');
      if (text.length > PLAFOND) throw new Error('segment de lecture trop long (' + text.length + ' caractères, ' + PLAFOND + ' au plus)');
      appliquerVoix(data.voix);
      const pcm = tts.speak(text, Number(data.speed) || 1);
      self.postMessage({ type: 'audio', id: data.id, pcm, sampleRate: tts.sampleRate }, [pcm.buffer]);
    } catch (error) {
      self.postMessage({ type: 'error', id: data.id, message: String((error && error.message) || error) });
    }
  }
};
