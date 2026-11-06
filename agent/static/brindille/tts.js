// French TTS in the browser: the wasm module (Alkove engine, model, Lex, dictionary, synthesizer) behind speak(text) -> Float32Array at 22,050 Hz.

const FILES = { wasm: 'tts.wasm', alk: 'model/tts.alk', lex: 'model/lex.alk', dict: 'model/fr.dict.bin', params: 'model/tts.params.bin', meta: 'model/tts.json' }

export class Tts {
  static async load(base = './', read = async (url) => {
    const r = await fetch(url)
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`)
    return r.arrayBuffer()
  }) {
    const at = (k) => new URL(FILES[k], base).href
    const [wasm, alk, lex, dict, params, meta] = await Promise.all(Object.keys(FILES).map((k) => read(at(k))))
    const { instance } = await WebAssembly.instantiate(wasm, {})
    const t = new Tts()
    t.x = instance.exports
    t.meta = JSON.parse(new TextDecoder().decode(meta))
    t.sampleRate = t.meta.sr
    t.sizes = { wasm: wasm.byteLength, model: alk.byteLength, lex: lex.byteLength, dict: dict.byteLength }
    const put = (buf) => {
      const p = t.x.tts_alloc(buf.byteLength)
      new Uint8Array(t.x.memory.buffer, p, buf.byteLength).set(new Uint8Array(buf))
      return [p, buf.byteLength]
    }
    const bufs = [alk, lex, dict, params].map(put)
    t.h = t.x.tts_new(...bufs.flat())
    bufs.forEach(([p, n]) => t.x.tts_free(p, n))  // the module keeps its own copy
    if (!t.h) {
      const p = t.x.tts_alloc(512)
      const msg = new TextDecoder().decode(new Uint8Array(t.x.memory.buffer, p, t.x.tts_error(p, 512)))
      t.x.tts_free(p, 512)
      throw new Error(msg)
    }
    return t
  }

  // speed within 0.5 to 2 (the module clamps it)
  speak(text, speed = 1) {
    const b = new TextEncoder().encode(text)
    const p = this.x.tts_alloc(b.length)
    new Uint8Array(this.x.memory.buffer, p, b.length).set(b)
    const n = this.x.tts_speak(this.h, p, b.length, speed)
    this.x.tts_free(p, b.length)
    return new Float32Array(this.x.memory.buffer, this.x.tts_audio(this.h), n).slice()
  }

  // semitones, intonation range (1 = the model's), treble and breath in dB; kept for the next speak
  voice({ pitch = 0, intonation = 1, treble = 0, breath = 0 } = {}) {
    this.x.tts_voice(this.h, pitch, intonation, treble, breath)
  }

  phonemes() {
    const np = this.x.tts_alloc(4)
    const p = this.x.tts_tokens(this.h, np)
    const n = new DataView(this.x.memory.buffer).getUint32(np, true)
    this.x.tts_free(np, 4)
    const sym = this.meta.symbols
    return Array.from(new Uint32Array(this.x.memory.buffer, p, n), (s) => (sym[s] === '<sil>' ? ' | ' : sym[s])).join('').replace(/ \|  \| /g, ' | ').trim()
  }
}

export function wav(pcm, sr) {
  const v = new DataView(new ArrayBuffer(44 + pcm.length * 2))
  const s = (o, t) => [...t].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)))
  s(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); s(8, 'WAVEfmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true)
  v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); s(36, 'data'); v.setUint32(40, pcm.length * 2, true)
  pcm.forEach((x, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 32767, true))
  return new Blob([v], { type: 'audio/wav' })
}
