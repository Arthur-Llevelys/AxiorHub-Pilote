---
license: apache-2.0
language:
- fr
pipeline_tag: text-to-speech
tags:
- text-to-speech
- tts
- on-device
- webassembly
---

# Brindille

A French text-to-speech model with **93,734 parameters** and one voice, Estelle, that runs entirely in the browser: text in, 22.05 kHz audio out, no server, no ONNX Runtime. The whole page, engine included, is 2.7 MB (1.9 MB compressed).

**[Try the demo](https://huggingface.co/spaces/edbrdi/brindille)** · runs on **[Alkove](https://github.com/alkove-ai/alkove)**, my open-source on-device speech engine

| | |
|---|---|
| Parameters | 93,734 (145 of them in the synthesizer) |
| Model file | `tts.alk`, 266 KB (decoder weights in int8, encoder in float32), for the Alkove engine only: no ONNX, no PyTorch weights |
| Output | 22,050 Hz, mono |
| Vocoder | none: the network predicts pitch, voicing and spectral envelopes, a harmonic + noise synthesizer renders the waveform |
| Runtime | the Alkove inference engine, compiled to WebAssembly (342 KB, 100 KB brotli) |
| Speed | ~75× real time in WebAssembly on one thread (Apple M1 Max, Node) |
| Language, voice | French, Estelle |

## Intelligibility and sound

On 49 held-out sentences, transcribed by Whisper large-v3-turbo:

| | WER | CER | DNSMOS OVR |
|---|---|---|---|
| This model | 9.6 % | 3.5 % | 2.73 |
| Reference audio of the same sentences | 7.2 % | 2.7 % | 3.01 |

Whisper's own errors on names and rare words weigh on both rows.

## Use

```js
import { Tts } from './tts.js'

const tts = await Tts.load('./')          // tts.wasm, model/tts.alk, model/lex.alk, model/fr.dict.bin, model/tts.params.bin, model/tts.json
const pcm = tts.speak('Bonjour à tous.')   // Float32Array, tts.sampleRate (22,050 Hz)
```

`speak(text, speed)` takes a speed factor (1 by default). Everything runs on the page's thread; a sentence of a few seconds takes tens of milliseconds.

## Files

| File | |
|---|---|
| `tts.wasm` | the engine, the text frontend and the synthesizer |
| `tts.js` | loads the files above and exposes `speak` |
| `model/tts.alk` | the network, two plans (encoder, decoder) |
| `model/tts.params.bin` | synthesizer and frontend constants |
| `model/tts.json` | sample rate and symbol names (to show phonemes) |
| `model/fr.dict.bin` | pronunciations of 19,662 French words: the most frequent ones and those Lex gets wrong |
| `model/lex.alk` | Lex, which reads every other word |

## Limits

- French only, one voice (Estelle), no voice cloning.
- Numbers are read out (integers, decimals with a comma); abbreviations, acronyms and symbols are not expanded.
- Names and foreign words go through Lex, which can miss.
- Liaisons follow rules for the most common words only.
- The sound is slightly buzzy and muffled compared with natural speech.

## License

Apache-2.0: the model, the WebAssembly module, `tts.js` and the page. `model/fr.dict.bin` is derived from the French dictionary of the Montreal Forced Aligner and stays under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The full notices are in `NOTICE`.

## Credits

- Voice: Estelle, from [Pocket TTS](https://github.com/kyutai-labs/pocket-tts) by Kyutai (CC BY 4.0); the Estelle recording is Kyutai's, released under CC0 ([tts-voices](https://huggingface.co/kyutai/tts-voices)).
- Network and synthesizer architecture: [AuroraTTS-100K](https://huggingface.co/AuroraAI-Research/AuroraTTS-100K) by AuroraAI-Research (Apache-2.0).
- Pronunciations: the French dictionary of the [Montreal Forced Aligner](https://github.com/MontrealCorpusTools/mfa-models) (CC BY 4.0), and [Lex](https://huggingface.co/alkove-ai/alkove-lex-1-preview) (Apache-2.0).
- Inference: the [Alkove](https://github.com/alkove-ai/alkove) engine (Apache-2.0).
