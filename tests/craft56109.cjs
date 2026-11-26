/* 5.6.109 (lot V, F16) : contrats du pont hôte, portés du dossier technique (craft-host109.test.mjs, 23 contrôles) sur le portage produit
   agent/static/v56109-craft-host.js (sans module ES), plus les contrôles du plan validé. Sous Node, sans navigateur ni binaire Craft. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { CraftHost, commandeIA, schemaDuPlan } = require('../agent/static/v56109-craft-host.js');

function differe() { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return { promise, resolve, reject }; }
function banc(options = {}) {
  const calls = [], frame = { contentWindow: { postMessage: (...args) => calls.push(args) } };
  let n=0;
  const host = new CraftHost({ app: 'gridcraft', frame, origin: 'http://127.0.0.1:8080',
    session: 'session-test', revision: 0, autoriser: async () => {},
    sauvegarder: async p => ({ durable: true, revision: p.expectedRevision+1 }),
    exporter: async () => ({ durable: true }), idOperation: () => `operation-${++n}`, ...options });
  const event = data => ({ origin: host.origin, source: frame.contentWindow, data });
  const send = data => host.recevoir(event(data));
  const ready = () => send({ type: `${host.app}:ready` });
  const dirty = value => send({ type: `${host.app}:dirty`, dirty: value });
  const save = (name='test.xlsx') => send({ type: `${host.app}:save`, name, bytes: new Uint8Array([1,2,3]) });
  return { host, frame, calls, event, send, ready, dirty, save };
}

test('autre origine et autre fenêtre refusées', async () => {
  const b=banc();
  assert.equal((await b.host.recevoir({ ...b.event({type:'gridcraft:ready'}), origin:'https://evil.test' })).accepted,false);
  assert.equal((await b.host.recevoir({ ...b.event({type:'gridcraft:ready'}), source:{} })).accepted,false);
  assert.equal(b.host.ready,false);
});
test('autre éditeur et type inconnu ignorés', async () => {
  const b=banc();
  assert.equal((await b.send({type:'pdfcraft:ready'})).accepted,false);
  assert.equal((await b.send({type:'gridcraft:execute', command:'file.open'})).accepted,false);
});
test('origine et services doivent être explicites', () => {
  assert.throws(()=>banc({origin:'null'}));
  assert.throws(()=>banc({origin:'https://app.test/path'}));
  assert.throws(()=>banc({autoriser:null}));
});
test('sauvegarde avant démarrage refusée', async () => {
  const b=banc(); assert.equal((await b.save()).accepted,false);
});
test('dirty false ne masque pas une requête encore active', async () => {
  const d=differe(),b=banc({sauvegarder:()=>d.promise}); await b.ready(); await b.dirty(true);
  const p=b.save(); await b.dirty(false);
  assert.equal(b.host.etat().dirty,true); assert.equal(b.host.fermer(),false);
  d.resolve({durable:true,revision:1}); await p;
  assert.equal(b.host.etat().dirty,false);
});
test('dirty false ne masque pas le contrôle préalable asynchrone', async () => {
  const d=differe(),b=banc({autoriser:()=>d.promise}); await b.ready(); await b.dirty(true);
  const p=b.save(); await b.dirty(false); assert.equal(b.host.etat().dirty,true);
  d.resolve(); await p; assert.equal(b.host.etat().dirty,false);
});
test('nouvelle saisie préservée après ancien acquittement', async () => {
  const d=differe(),b=banc({sauvegarder:()=>d.promise}); await b.ready(); await b.dirty(true);
  const p=b.save(); await b.dirty(false); await b.dirty(true);
  d.resolve({durable:true,revision:1}); await p;
  assert.equal(b.host.etat().dirty,true);
});
test('dirty false tardif ne nettoie pas une saisie postérieure au candidat', async () => {
  const d=differe(),b=banc({sauvegarder:()=>d.promise});await b.ready();await b.dirty(true);
  const p=b.save();await b.dirty(false);await b.dirty(true);
  d.resolve({durable:true,revision:1});await p;await b.dirty(false);
  assert.equal(b.host.etat().dirty,true);assert.equal(b.host.fermer(),false);
});
test('échec et résultat incertain conservent une copie récupérable', async () => {
  const b=banc({sauvegarder:async()=>{throw new Error('timeout');}}); await b.ready(); await b.dirty(true);
  const r=await b.save(); await b.dirty(false);
  assert.equal(r.saved,false); assert.equal(b.host.etat().dirty,true);
  assert.equal(b.host.recuperation.bytes.byteLength,3);
});
test('pas de répétition automatique après perte de réponse', async () => {
  let calls=0;
  const b=banc({sauvegarder:async()=>{calls++;throw new Error('timeout');}}); await b.ready();
  await b.save(); await b.save(); assert.equal(calls,1);
});
test('PDF exporté par GridCraft ne remplace jamais le classeur', async () => {
  let writes=0,exports=0; const b=banc({sauvegarder:async()=>{writes++;},exporter:async()=>{exports++;return{durable:true};}});
  await b.ready(); await b.dirty(true); const r=await b.save('tableau.pdf');
  assert.equal(r.exported,true);assert.equal(writes,0);assert.equal(exports,1);
  assert.equal(b.host.revision,0);assert.equal(b.host.etat().dirty,true);
});
test('nom de chemin ou extension inconnue refusés', async () => {
  for(const name of ['../dossier.xlsx','C:\\test.xlsx','a/b.xlsx','secret.exe']) {
    const b=banc();await b.ready();assert.equal((await b.save(name)).saved,false);
  }
});
test('bornes d octets et type de contenu', async () => {
  const b=banc({maxBytes:2});await b.ready();assert.equal((await b.save()).saved,false);
  const c=banc();await c.ready();
  assert.equal((await c.send({type:'gridcraft:save',name:'a.xlsx',bytes:'texte'})).saved,false);
});
test('révision ne progresse que sur preuve durable', async () => {
  for(const receipt of [{revision:1},{durable:true,revision:0},{durable:true,revision:'1'}]) {
    const b=banc({sauvegarder:async()=>receipt});await b.ready();
    assert.equal((await b.save()).saved,false);assert.equal(b.host.revision,0);
  }
});
test('enregistrements sérialisés et bonne version de base', async () => {
  const versions=[];const b=banc({sauvegarder:async p=>{versions.push(p.expectedRevision);return{durable:true,revision:p.expectedRevision+1};}});
  await b.ready();await b.dirty(true);await Promise.all([b.save(),b.save()]);
  assert.deepEqual(versions,[0,1]);
});
test('droits retirés empêchent toute transmission de fichier', async () => {
  let calls=0;const b=banc({autoriser:async()=>{throw new Error('droits');},sauvegarder:async()=>{calls++;}});
  await b.ready();assert.equal((await b.save()).saved,false);assert.equal(calls,0);
});
test('ouverture transmet une copie au seul cadre autorisé', async () => {
  const b=banc();await b.ready();await b.host.ouvrir({name:'a.xlsx',bytes:new Uint8Array([3,4])});
  const sent=b.calls.find(x=>x[0].type==='gridcraft:open');
  assert.equal(sent[1],b.host.origin);assert.equal(sent[0].bytes.byteLength,2);
});
test('fermeture propre interdit les messages suivants', async () => {
  const b=banc();await b.ready();assert.equal(b.host.fermer(),true);
  assert.equal((await b.save()).accepted,false);
});
test('une commande IA inconnue est refusée avant exécution', async () => {
  let calls=0;await assert.rejects(commandeIA({api:{execute:async()=>calls++},command:'file.open',params:{},schema:{},checkpoint:()=>{},currentRevision:()=>1,expectedRevision:1}));
  assert.equal(calls,0);
});
test('la pause et une révision périmée empêchent la commande IA', async () => {
  let calls=0;const args={api:{execute:async()=>calls++},command:'cell.set',params:{},schema:{'cell.set':()=>{}},checkpoint:()=>{},currentRevision:()=>2,expectedRevision:1};
  await assert.rejects(commandeIA(args));
  await assert.rejects(commandeIA({...args,currentRevision:()=>1,checkpoint:()=>{throw new Error('pause');}}));assert.equal(calls,0);
});
test('commande IA expirée rend un résultat incertain sans relance', async () => {
  let calls=0;const r=await commandeIA({api:{execute:async()=>{calls++;throw new Error('timeout');}},command:'cell.set',params:{},schema:{'cell.set':()=>{}},checkpoint:()=>{},currentRevision:()=>1,expectedRevision:1});
  assert.equal(r.statut,'indetermine');assert.equal(r.relanceAutomatique,false);assert.equal(calls,1);
});
test('API JavaScript renvoie directement le résultat et attend les gardes async', async () => {
  let calls=0;
  const args={api:{execute:async()=>{calls++;return{changed:1};}},command:'cell.set',params:{},schema:{'cell.set':async()=>{}},checkpoint:async()=>{},currentRevision:()=>1,expectedRevision:1};
  const r=await commandeIA(args);assert.equal(r.statut,'applique');assert.deepEqual(r.resultat,{changed:1});
  await assert.rejects(commandeIA({...args,checkpoint:async()=>{throw new Error('pause');}}));assert.equal(calls,1);
});
test('saved répond uniquement après preuve durable et avec le bon identifiant', async () => {
  const d=differe(),b=banc({sauvegarder:()=>d.promise});await b.ready();
  const p=b.send({type:'gridcraft:save',id:'envoi-1',name:'a.xlsx',bytes:new Uint8Array([1])});
  assert.equal(b.calls.some(x=>x[0].type==='gridcraft:saved'),false);
  d.resolve({durable:true,revision:1});await p;
  assert.deepEqual(b.calls.find(x=>x[0].type==='gridcraft:saved')[0],{type:'gridcraft:saved',id:'envoi-1'});
});

test('plan validé : seule la commande prévue avec ses paramètres exacts', async () => {
  let calls=0;const etape={commande:'cell.set',parametres:{sheet:1,cell:'B2',value:'12'}};
  const base={api:{execute:async()=>{calls++;return 1;}},checkpoint:()=>{},currentRevision:()=>3,expectedRevision:3,schema:schemaDuPlan(etape)};
  assert.equal((await commandeIA({...base,command:'cell.set',params:{sheet:1,cell:'B2',value:'12'}})).statut,'applique');
  await assert.rejects(commandeIA({...base,command:'cell.set',params:{sheet:1,cell:'B2',value:'=1+1'}}));
  await assert.rejects(commandeIA({...base,command:'file.open',params:{}}));
  assert.equal(calls,1);
});
test('plan vide ou mal formé : aucune commande admise', async () => {
  let calls=0;
  await assert.rejects(commandeIA({api:{execute:async()=>calls++},command:'cell.set',params:{},schema:schemaDuPlan(null),checkpoint:()=>{},currentRevision:()=>1,expectedRevision:1}));
  assert.equal(calls,0);
});
test('5.6.110 : refus certain du serveur expliqué, saisie suivante possible ; nom refusé sans blocage', async () => {
  let n=0;const b=banc({sauvegarder:async p=>{n++;if(n===1){const e=new Error('Le fichier reçu n’est pas lisible');e.certain=true;throw e;}return{durable:true,revision:p.expectedRevision+1};}});
  await b.ready();
  const r1=await b.save();assert.equal(r1.saved,false);assert.equal(r1.refus,true);assert.equal(b.host.erreur,'');
  const r2=await b.save('../a.xlsx');assert.equal(r2.saved,false);assert.equal(b.host.erreur,'');
  const r3=await b.save();assert.equal(r3.saved,true);assert.equal(b.host.revision,1);
  assert.equal(b.calls.some(x=>/:host$/.test(x[0].type)),false);
});
