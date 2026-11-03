// Construction reproductible de l'interface Carbon. Lancé par scripts/build-carbon.py dans un dossier de travail, après « npm ci --ignore-scripts » :
// aucun script d'installation n'est exécuté, donc aucune télémétrie de @ibm/telemetry-js. Node ne sert qu'à construire, jamais à l'exécution.
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { build } from 'esbuild';
import * as themes from '@carbon/themes';

const out = resolve(process.argv[2]);                 // racine du dépôt
const here = process.cwd();
const icons = JSON.parse(readFileSync(join(here, 'icones.json'), 'utf8'));
const sha = data => createHash('sha256').update(data).digest('hex');
const pkg = name => JSON.parse(readFileSync(join(here, 'node_modules', name, 'package.json'), 'utf8'));

// 1. Composants : un seul fichier local, sans dépendance à un CDN.
const result = await build({
  entryPoints: ['entry.js'], bundle: true, minify: true, format: 'iife', target: ['es2020'], platform: 'browser', legalComments: 'none', metafile: true,
  sourcemap: false, charset: 'utf8', define: { 'process.env.NODE_ENV': '"production"' }, write: false, logLevel: 'warning',
});
const bundle = Buffer.from(result.outputFiles[0].contents);
writeFileSync(join(out, 'agent/static/carbon5682.js'), bundle);

// 2. Paquets réellement entrés dans le fichier : inventaire pour les licences et le SBOM.
const seen = new Set();
for (const input of Object.keys(result.metafile.inputs)) {
  const m = /node_modules\/((?:@[^/]+\/)?[^/]+)\//.exec(input);
  if (m) seen.add(m[1]);
}
const inventaire = [...seen].sort().map(name => { const p = pkg(name); return { name, version: p.version, license: p.license }; });

// 3. Icônes : chaque nom doit exister dans le paquet verrouillé, sinon la construction échoue.
const lignes = [];
for (const [role, nom] of Object.entries(icons)) {
  const file = join(here, 'node_modules/@carbon/icons/svg/32', nom + '.svg');
  if (!existsSync(file)) throw new Error('icône absente du paquet verrouillé : ' + nom);
  const svg = readFileSync(file, 'utf8').replace(/\r?\n/g, '').replace(/>\s+</g, '><');
  const inner = /<svg[^>]*>(.*)<\/svg>/.exec(svg)[1].replace(/<title>.*?<\/title>/g, '').replace(/<defs>.*?<\/defs>/g, '').replace(/<rect[^>]*(?:fill: *none|fill="none"|Transparent)[^>]*\/>/g, '')
    .replace(/ (?:data-name|class|id)="[^"]*"/g, '').trim()
    .replace(/(\.\d+)(?=\.\d)/g, '$1 ');   // trois nombres collés d'un tracé seraient pris pour une adresse IP par le contrôle de confidentialité : une espace, même tracé
  lignes.push('    ' + JSON.stringify(role) + ': ' + JSON.stringify(inner) + ',');
}
const module_ = '"""Icônes Carbon locales (fichier généré par scripts/build-carbon.py, à ne pas modifier). Source : @carbon/icons ' + pkg('@carbon/icons').version +
  ', Apache-2.0 ; noms vérifiés dans le paquet verrouillé."""\nICONES = {\n' + lignes.join('\n') + '\n}\n';
writeFileSync(join(out, 'agent/ui_icones5689.py'), module_);

// 3 bis. Jetons Carbon des thèmes Gray 10 et Gray 100 : valeurs du paquet verrouillé, jetons de composants compris, aucune palette recopiée à la main.
const kebab = s => s.replace(/([a-z0-9])([A-Z])/g, '$1-$2').replace(/([a-zA-Z])([0-9])/g, '$1-$2').toLowerCase();
const jetons = {};
for (const theme of ['g10', 'g100']) {
  const table = {};
  for (const [k, v] of Object.entries(themes[theme])) if (typeof v === 'string' && /^(#|rgba?\()/.test(v)) table[kebab(k)] = v;
  for (const group of ['buttonTokens', 'notificationTokens', 'tagTokens']) for (const [k, v] of Object.entries(themes[group])) if (v[theme]) table[kebab(k)] = v[theme];
  jetons[theme] = table;
}
const jetons_ = '"""Jetons Carbon (fichier généré par scripts/build-carbon.py, à ne pas modifier). Source : @carbon/themes ' + pkg('@carbon/themes').version + ', Apache-2.0."""\nTHEMES = ' +
  JSON.stringify(jetons, null, 1) + '\n';
writeFileSync(join(out, 'agent/ui_jetons_carbon5689.py'), jetons_);

// 4. Police autorisée (OFL-1.1) : sous-ensemble latin, servi par l'application.
const fonts = {};
for (const [src, dest] of [['Regular', 'plex-sans-400.woff2'], ['SemiBold', 'plex-sans-600.woff2']]) {
  const file = join(here, 'node_modules/@ibm/plex-sans/fonts/split/woff2/IBMPlexSans-' + src + '-Latin1.woff2');
  copyFileSync(file, join(out, 'agent/static', dest));
  fonts['agent/static/' + dest] = sha(readFileSync(file));
}

// 5. Manifeste : versions et empreintes, comparées par scripts/build-carbon.py --verifier.
const manifest = {
  esbuild: pkg('esbuild').version,
  sorties: { 'agent/static/carbon5682.js': sha(bundle), 'agent/ui_icones5689.py': sha(Buffer.from(module_)), 'agent/ui_jetons_carbon5689.py': sha(Buffer.from(jetons_)), ...fonts },
  paquets_embarques: inventaire,
  autres_paquets_embarques: ['@carbon/icons', '@carbon/themes', '@ibm/plex-sans'].map(name => ({ name, version: pkg(name).version, license: pkg(name).license })),
};
writeFileSync(join(here, 'manifeste.json'), JSON.stringify(manifest, null, 1) + '\n');
console.log('carbon5682.js', bundle.length, 'octets ;', inventaire.length, 'paquets ;', Object.keys(icons).length, 'icônes');
