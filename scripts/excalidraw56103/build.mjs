// Construction reproductible du tableau blanc (Excalidraw). Lancé par scripts/build-excalidraw.py dans un dossier de travail hors du dépôt,
// après « npm ci --ignore-scripts » : aucun script d'installation n'est exécuté. Node ne sert qu'à construire, jamais à l'exécution.
//
// Sorties (racine du dépôt passée en argument) :
//   agent/static/excalidraw56103.js et .css  : point d'entrée (module ES, chargé à l'ouverture du volet « Tableau blanc ») ;
//   agent/static/excalidraw56103/*.js        : morceaux chargés à la demande (langue, convertisseur Mermaid, sous-ensembles de police) ;
//   agent/static/excalidraw56103/fonts/…     : polices d'Excalidraw servies par AxiorHub (aucun CDN) ; Xiaolai et Liberation omises (voir OMISES) ;
//   agent/excalidraw_fichiers56103.py        : liste et empreintes des fichiers servis (web440.STATIC, contrôle des tests) ;
//   scripts/excalidraw56103/manifeste.json   : versions, empreintes, inventaire des paquets et licences (refus de toute licence non permissive).
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { brotliDecompressSync } from 'node:zlib';
import { build } from 'esbuild';

const out = resolve(process.argv[2]);                 // racine du dépôt
const here = process.cwd();
const sha = data => createHash('sha256').update(data).digest('hex');
const pkg = name => JSON.parse(readFileSync(join(here, 'node_modules', name, 'package.json'), 'utf8'));
const EXC = join(here, 'node_modules/@excalidraw/excalidraw/dist/prod');
const STATIC = join(out, 'agent/static');
const DOSSIER = join(STATIC, 'excalidraw56103');

// Langues de l'interface d'AxiorHub seulement ; toute autre langue d'Excalidraw retombe sur l'anglais (aucun fichier de plus à servir).
const LANGUES = ['fr-FR-', 'en-', 'de-DE-', 'es-ES-', 'percentages-'];
const anglais = readdirSync(join(EXC, 'locales')).find(n => n.startsWith('en-'));
const langues = {
  name: 'langues-axiorhub',
  setup(b) {
    b.onResolve({ filter: /^\.\/locales\// }, args => {
      if (!args.importer.split(sep).join('/').includes('@excalidraw/excalidraw/dist/prod/')) return undefined;
      const nom = args.path.slice('./locales/'.length);
      return { path: join(dirname(args.importer), 'locales', LANGUES.some(p => nom.startsWith(p)) ? nom : anglais) };
    });
  },
};

// Adresses externes retirées du code d'Excalidraw : polices de secours lues sur esm.sh (remplacées par le dossier local) et scripts de
// widgets (Twitter, Reddit) d'éléments « intégrés », refusés de toute façon par AxiorHub (validateEmbeddable, serveur). Chaque
// remplacement est compté : une version d'Excalidraw qui changerait ce code fait échouer la construction au lieu de laisser passer un CDN.
const remplacements = { esm: 0, widgets: 0, services: 0 };
const local = {
  name: 'sans-cdn',
  setup(b) {
    b.onLoad({ filter: /[\\/]@excalidraw[\\/]excalidraw[\\/]dist[\\/]prod[\\/].*\.js$/ }, args => {
      let text = readFileSync(args.path, 'utf8');
      const debut = text.indexOf('`https://esm.sh/');
      if (debut >= 0) {
        const fin = text.indexOf('/dist/prod/`', debut);
        if (fin < 0) throw new Error('adresse de secours esm.sh non reconnue : ' + args.path);
        text = text.slice(0, debut) + '(typeof window<"u"&&typeof window.EXCALIDRAW_ASSET_PATH=="string"?new URL(window.EXCALIDRAW_ASSET_PATH,globalThis.location.href).href'
          + ':new URL("/static/excalidraw56103/",globalThis.location.href).href)' + text.slice(fin + '/dist/prod/`'.length);
        remplacements.esm += 1;
      }
      if (text.includes('VITE_APP_BACKEND_V2_GET_URL:')) {
        // Réglages de l'application excalidraw.com compilés dans le paquet (partage, bibliothèques publiques, collaboration, IA, Firebase) :
        // vidés, pour qu'aucune action (« Publier la bibliothèque »…) ne puisse joindre un service d'Excalidraw ; l'adresse vide reste locale.
        for (const cle of ['BACKEND_V2_GET_URL', 'BACKEND_V2_POST_URL', 'LIBRARY_URL', 'LIBRARY_BACKEND', 'PLUS_LP', 'PLUS_APP', 'AI_BACKEND', 'WS_SERVER_URL']) {
          const avant = text;
          text = text.replace(new RegExp('VITE_APP_' + cle + ':"[^"]*"'), 'VITE_APP_' + cle + ':""');
          if (text !== avant) remplacements.services += 1;
        }
        const avant = text;
        text = text.replace(/VITE_APP_FIREBASE_CONFIG:'[^']*'/, "VITE_APP_FIREBASE_CONFIG:'{}'");
        if (text !== avant) remplacements.services += 1;
      }
      for (const url of ['https://platform.twitter.com/widgets.js', 'https://embed.reddit.com/widgets.js']) {
        const n = text.split(url).length - 1;
        if (n) { text = text.split(url).join('about:blank'); remplacements.widgets += n; }
      }
      return { contents: text, loader: 'js' };
    });
  },
};

const result = await build({
  entryPoints: { excalidraw56103: 'entry.js' }, bundle: true, splitting: true, format: 'esm', outdir: STATIC, entryNames: '[name]',
  chunkNames: 'excalidraw56103/[name]-[hash]', assetNames: 'excalidraw56103/[name]-[hash]', minify: true, target: ['es2020'], platform: 'browser',
  conditions: ['production'], legalComments: 'none', metafile: true, sourcemap: false, charset: 'utf8', write: false, logLevel: 'warning',
  define: { 'process.env.NODE_ENV': '"production"', 'import.meta.env.DEV': 'false', 'import.meta.env.PROD': 'true', 'import.meta.env.MODE': '"production"' },
  loader: { '.woff2': 'file', '.png': 'file' }, plugins: [langues, local],
});
if (remplacements.esm !== 1) throw new Error('remplacement de l’adresse de secours esm.sh : ' + remplacements.esm + ' au lieu de 1');
if (remplacements.services !== 9) throw new Error('réglages des services d’Excalidraw vidés : ' + remplacements.services + ' au lieu de 9');

// 1. Fichiers assemblés : aucun CDN ni hôte de scripts externe (contrôle refait par tests/test_v56103_tableau.py).
const CDN = /(?:esm\.sh|unpkg\.com|jsdelivr\.net|cdnjs\.cloudflare\.com|fonts\.googleapis\.com|fonts\.gstatic\.com|platform\.twitter\.com|embed\.reddit\.com|skypack\.dev|jspm\.io|json\.excalidraw\.com|oss-(?:ai|collab)\.excalidraw\.com|firebaseio\.com|cloudfunctions\.net|AIza[0-9A-Za-z_-]{30,})/;
if (existsSync(DOSSIER)) rmSync(DOSSIER, { recursive: true, force: true });
mkdirSync(DOSSIER, { recursive: true });
const sorties = {};
for (const f of result.outputFiles) {
  const rel = relative(out, f.path).split(sep).join('/');
  const data = Buffer.from(f.contents);
  if (/\.(js|css)$/.test(rel) && CDN.test(data.toString('utf8'))) throw new Error('adresse de CDN dans ' + rel + ' : ' + CDN.exec(data.toString('utf8'))[0]);
  mkdirSync(dirname(f.path), { recursive: true });
  writeFileSync(f.path, data);
  sorties[rel] = sha(data);
}

// 2. Polices d'Excalidraw, servies par AxiorHub. Licence lue dans la table « name » de chaque fichier (WOFF2 décompressé), jamais supposée.
const TAGS = ['cmap', 'head', 'hhea', 'hmtx', 'maxp', 'name', 'OS/2', 'post', 'cvt ', 'fpgm', 'glyf', 'loca', 'prep', 'CFF ', 'VORG', 'EBDT', 'EBLC', 'gasp',
  'hdmx', 'kern', 'LTSH', 'PCLT', 'VDMX', 'vhea', 'vmtx', 'BASE', 'GDEF', 'GPOS', 'GSUB', 'EBSC', 'JSTF', 'MATH', 'CBDT', 'CBLC', 'COLR', 'CPAL', 'SVG ',
  'sbix', 'acnt', 'avar', 'bdat', 'bloc', 'bsln', 'cvar', 'fdsc', 'feat', 'fmtx', 'fvar', 'gvar', 'hsty', 'just', 'lcar', 'mort', 'morx', 'opbd', 'prop',
  'trak', 'Zapf', 'Silf', 'Glat', 'Gloc', 'Feat', 'Sill'];
function nomsWoff2(buf) {
  if (buf.toString('latin1', 0, 4) !== 'wOF2') throw new Error('pas un fichier WOFF2');
  const n = buf.readUInt16BE(12), comp = buf.readUInt32BE(20);
  let pos = 48;
  const base128 = () => { let v = 0; for (let i = 0; i < 5; i++) { const b = buf[pos++]; v = v * 128 + (b & 0x7f); if (!(b & 0x80)) return v; } throw new Error('UIntBase128'); };
  const tables = [];
  for (let i = 0; i < n; i++) {
    const flags = buf[pos++];
    let tag = TAGS[flags & 0x3f];
    if ((flags & 0x3f) === 63) { tag = buf.toString('latin1', pos, pos + 4); pos += 4; }
    const orig = base128();
    const version = flags >> 6;
    const transforme = (tag === 'glyf' || tag === 'loca') ? version === 0 : version !== 0;
    tables.push({ tag, longueur: transforme ? base128() : orig });
  }
  const data = brotliDecompressSync(buf.subarray(pos, pos + comp));
  let off = 0;
  for (const t of tables) {
    if (t.tag === 'name') {
      const nm = data.subarray(off, off + t.longueur);
      const count = nm.readUInt16BE(2), strings = nm.readUInt16BE(4), noms = {};
      for (let i = 0; i < count; i++) {
        const r = 6 + i * 12, plateforme = nm.readUInt16BE(r), id = nm.readUInt16BE(r + 6), len = nm.readUInt16BE(r + 8), o = nm.readUInt16BE(r + 10);
        const brut = nm.subarray(strings + o, strings + o + len);
        const texte = plateforme === 3 || plateforme === 0 ? Buffer.from(brut).swap16().toString('utf16le') : brut.toString('latin1');
        if (!(id in noms) || plateforme === 3) noms[id] = texte;
      }
      return noms;
    }
    off += t.longueur;
  }
  throw new Error('table « name » absente');
}
const licencePolice = noms => {
  const texte = [noms[13], noms[14], noms[0]].filter(Boolean).join(' ');
  if (/SIL Open Font License|openfontlicense\.org|scripts\.sil\.org\/OFL|\bOFL\b/i.test(texte)) return 'OFL-1.1';
  if (/\bMIT\b/.test(texte)) return 'MIT';
  return null;
};
// Excalifont (police manuscrite par défaut d'Excalidraw 0.18) ne porte aucune licence dans ses métadonnées (« All rights reserved ») :
// licence OFL-1.1 déclarée par l'éditeur (page officielle plus.excalidraw.com/excalifont), consignée ici et dans le manifeste.
// Liberation Sans (2007, Ascender) renvoie à un accord de licence extérieur, non vérifiable ici (ancienne version sous GPL-2.0 avec
// exception) : omise, comme Xiaolai (écritures chinoises, 13 Mo). Excalidraw retombe alors sur une police du système pour ces textes.
const DECLAREES = { Excalifont: { licence: 'OFL-1.1', source: 'déclarée par l’éditeur (plus.excalidraw.com/excalifont) ; absente des métadonnées du fichier' } };
const OMISES = { Xiaolai: 'écritures chinoises, 13 Mo', Liberation: 'licence renvoyant à un accord extérieur (Ascender 2007), non vérifiable : omise' };
const polices = [];
const parcourir = dir => readdirSync(dir).flatMap(n => statSync(join(dir, n)).isDirectory() ? parcourir(join(dir, n)) : [join(dir, n)]);
for (const file of parcourir(join(EXC, 'fonts'))) {
  const rel = relative(join(EXC, 'fonts'), file).split(sep).join('/');
  const famille = rel.split('/')[0];
  if (famille in OMISES || !rel.endsWith('.woff2')) continue;
  const noms = nomsWoff2(readFileSync(file));
  const lue = licencePolice(noms);
  const licence = lue || (DECLAREES[famille] || {}).licence;
  if (!licence) throw new Error('licence de police non reconnue : ' + rel + ' : ' + JSON.stringify([noms[13], noms[14], noms[0]]));
  const dest = join(DOSSIER, 'fonts', rel);
  mkdirSync(dirname(dest), { recursive: true });
  copyFileSync(file, dest);
  sorties['agent/static/excalidraw56103/fonts/' + rel] = sha(readFileSync(file));
  if (!polices.some(p => p.famille === famille)) polices.push({ famille, licence, nom: noms[1] || famille, source: lue ? 'table name du fichier' : DECLAREES[famille].source });
}

// 3. Paquets réellement entrés dans les fichiers assemblés : inventaire pour les licences et le SBOM. Toute licence non permissive arrête.
const PERMISES = new Set(['MIT', 'ISC', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', '0BSD', 'CC0-1.0', 'Zlib', 'Unlicense', 'MIT-0', 'BlueOak-1.0.0']);
const permise = expr => {
  const e = String(expr || '').replace(/[()]/g, '').trim();
  if (!e) return false;
  if (/ OR /.test(e)) return e.split(/ OR /).some(x => permise(x));
  return e.split(/ AND /).every(x => PERMISES.has(x.trim()));
};
const licenceDe = (name, dossier) => {
  const p = JSON.parse(readFileSync(join(dossier, 'package.json'), 'utf8'));
  if (typeof p.license === 'string') return { licence: p.license, source: 'package.json' };
  if (Array.isArray(p.licenses)) return { licence: p.licenses.map(l => l.type || l).join(' OR '), source: 'package.json (licenses)' };
  const fichier = readdirSync(dossier).find(n => /^licen[cs]e/i.test(n));
  const texte = fichier ? readFileSync(join(dossier, fichier), 'utf8') : '';
  if (/Permission is hereby granted, free of charge/.test(texte)) return { licence: 'MIT', source: 'fichier ' + fichier };
  return { licence: '', source: 'aucune' };
};
const vus = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  const m = /^(.*node_modules\/((?:@[^/]+\/)?[^/]+))\//.exec(input);
  if (m && !vus.has(m[2])) vus.set(m[2], m[1]);
}
const inventaire = [...vus.keys()].sort().map(name => {
  const dossier = resolve(here, vus.get(name));
  const p = JSON.parse(readFileSync(join(dossier, 'package.json'), 'utf8'));
  const { licence, source } = licenceDe(name, dossier);
  return { name, version: p.version, license: licence, source };
});
// Dépendances déclarées des paquets embarqués mais absentes de la carte d'esbuild : précompilées dans le paquet parent (Langium,
// Chevrotain, vscode-jsonrpc… dans @mermaid-js/parser) ou non utilisées. Elles sont inventoriées et contrôlées de la même façon.
const trouver = (dep, depuis) => {
  for (let d = depuis; d.length >= here.length; d = dirname(d)) {
    const c = join(d, 'node_modules', dep);
    if (existsSync(join(c, 'package.json'))) return c;
    if (d === dirname(d)) break;
  }
  return null;
};
const declarees = new Map();
const pile = [...vus.keys()].map(name => resolve(here, vus.get(name)));
while (pile.length) {
  const dossier = pile.pop();
  const p = JSON.parse(readFileSync(join(dossier, 'package.json'), 'utf8'));
  for (const dep of Object.keys(p.dependencies || {})) {
    const c = trouver(dep, dossier);
    if (!c || declarees.has(c)) continue;
    const q = JSON.parse(readFileSync(join(c, 'package.json'), 'utf8'));
    declarees.set(c, { name: dep, version: q.version, ...(({ licence, source }) => ({ license: licence, source }))(licenceDe(dep, c)) });
    pile.push(c);
  }
}
const embarques = new Set(inventaire.map(p => p.name + '@' + p.version));
const autres = [...new Map([...declarees.values()].map(p => [p.name + '@' + p.version, p])).values()].filter(p => !embarques.has(p.name + '@' + p.version)).sort((a, b) => (a.name + a.version).localeCompare(b.name + b.version));
const refus = [...inventaire, ...autres].filter(p => !permise(p.license));
if (refus.length) throw new Error('licence non permissive ou inconnue : ' + JSON.stringify(refus));

// 4. Module Python des fichiers servis, puis manifeste (comparé par scripts/build-excalidraw.py --verifier).
const types = rel => rel.endsWith('.js') ? 'text/javascript' : rel.endsWith('.css') ? 'text/css' : rel.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream';
const servis = Object.keys(sorties).sort();
const module_ = '"""Fichiers du tableau blanc (Excalidraw) servis par AxiorHub (fichier généré par scripts/build-excalidraw.py, à ne pas modifier).\n\n'
  + 'Source : @excalidraw/excalidraw ' + pkg('@excalidraw/excalidraw').version + ' (MIT), @excalidraw/mermaid-to-excalidraw '
  + pkg('@excalidraw/mermaid-to-excalidraw').version + ' (MIT), React ' + pkg('react').version + ' (MIT) ; polices : licences lues dans chaque fichier."""\n'
  + 'FICHIERS = {\n' + servis.map(rel => '    ' + JSON.stringify(rel.slice('agent/static/'.length)) + ': (' + JSON.stringify(types(rel)) + ', '
    + JSON.stringify(sorties[rel]) + '),').join('\n') + '\n}\n';
writeFileSync(join(out, 'agent/excalidraw_fichiers56103.py'), module_);
sorties['agent/excalidraw_fichiers56103.py'] = sha(Buffer.from(module_));

const manifest = {
  esbuild: pkg('esbuild').version,
  remplacements,
  sorties: Object.fromEntries(Object.keys(sorties).sort().map(k => [k, sorties[k]])),
  paquets_embarques: inventaire,
  dependances_precompilees_ou_inutilisees: autres,
  polices,
  polices_omises: OMISES,
};
writeFileSync(join(here, 'manifeste.json'), JSON.stringify(manifest, null, 1) + '\n');
const taille = Object.keys(sorties).filter(k => k.endsWith('.js')).reduce((s, k) => s + statSync(join(out, k)).size, 0);
console.log('tableau blanc :', Object.keys(sorties).length, 'fichiers ;', taille, 'octets de JavaScript ;', inventaire.length, 'paquets ;', polices.length, 'familles de polices');
