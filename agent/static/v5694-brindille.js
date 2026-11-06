/* AxiorHub 5.6.94 (V05) : voix Brindille dans un Web Worker (module). La synthèse, qui tourne sinon sur le fil principal, ne fige plus
   la page. Les fichiers (moteur, modèle, dictionnaire) viennent d'AxiorHub seulement, à la même version que la page ; le texte reçu n'est
   envoyé nulle part. Messages : {type:'init', base, version} → {type:'ready'} ou {type:'error'} ; {type:'speak', id, text, speed} →
   {type:'audio', id, pcm, sampleRate} (tampon transféré) ou {type:'error', id}. */
let tts = null;

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
      const pcm = tts.speak(String(data.text || '').slice(0, 2000), Number(data.speed) || 1);
      self.postMessage({ type: 'audio', id: data.id, pcm, sampleRate: tts.sampleRate }, [pcm.buffer]);
    } catch (error) {
      self.postMessage({ type: 'error', id: data.id, message: String((error && error.message) || error) });
    }
  }
};
