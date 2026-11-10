/* 5.6.89 (chapitres 18 et 19) : contexte de la page pour l'assistant existant (v567.js). Pas de second assistant : ce script remplit le panneau du Pilote, ses champs et sa carte.
   Le navigateur décrit ce qu'il affiche ; le serveur revalide tout et répond par ce qui est permis. Aucune action n'est lancée sans clic, et rien ici n'envoie, ne signe, ne dépose, ne facture ni ne paie. */
(() => {
  'use strict';
  const cfg = JSON.parse(document.querySelector('#ws-ai-config')?.textContent || '{}');
  const zone = document.querySelector('#ia5689-panneau');
  if (!cfg.ia5689 || !zone || !cfg.fonction) return;
  const prefix = cfg.prefix || '', csrf = cfg.csrf || '';
  const dock = document.querySelector('#ws-ai-dock'), launcher = document.querySelector('#ws-ai-launcher');
  const matter = dock.querySelector('#ws-ai-matter'), question = dock.querySelector('#ws-ai-question'), intent = dock.querySelector('#ws-ai-intent');
  const q = new URLSearchParams(location.search);
  const FILTRES = ['vue', 'onglet', 'filtre', 'nature', 'etat', 'periode'];
  const INTENTIONS = {mail_draft: 'mail', document: 'document', action_proposal: 'facturation', analysis: 'analysis', answer: 'question'};
  let courant = null, fonction = cfg.fonction, numero = 0;
  const el = (tag, text, cls) => {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;};
  const note = text => window.axiorhubPilot?.note(text);
  const jeton = () => crypto.randomUUID ? crypto.randomUUID() : [...crypto.getRandomValues(new Uint8Array(20))].map(x => x.toString(16).padStart(2, '0')).join('');
  async function poster(route, data) {
    const r = await fetch(prefix + '/api440/m5689/' + route, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)});
    const v = await r.json();
    if (!r.ok) throw new Error(v.message || v.error || 'Demande refusée.');
    return v;
  }
  function objetsDePage() {
    if (courant) return courant;
    const principal = document.querySelector('[data-ia-page][data-ia-objets]');
    if (!principal) return [];
    fonction = principal.dataset.iaFonction || fonction;   // une visionneuse rangée sous une autre rubrique garde sa fonction
    return JSON.parse(principal.dataset.iaObjets || '[]');
  }
  function contexte() {
    const objets = [...objetsDePage()];
    (window.axiorhubPilot?.selectedDocuments() || []).forEach(p => {if (!objets.some(o => o.id === 'doc:' + p)) objets.push({id: 'doc:' + p, version_id: ''});});
    const cle = q.get('key') || '';
    if (/^[0-9a-f]{64}$/.test(cle) && !objets.some(o => o.id === 'mail:' + cle)) objets.push({id: 'mail:' + cle, version_id: ''});
    const filters = {};
    FILTRES.forEach(k => {if (q.get(k)) filters[k] = q.get(k);});
    return {function_id: fonction, matter_id: matter.value, selected_objects: objets.slice(0, 20), expected_result: 'auto', filters};
  }
  function poserDossier(id) {matter.value = id; matter.dispatchEvent(new Event('change'));}   // même événement qu'un choix à la main : les autres scripts du Pilote suivent
  function choisir(id) {
    if (![...matter.options].some(o => o.value === id)) matter.append(new Option(id, id));
    poserDossier(id);
  }
  function lancerPilote(a) {
    question.value = a.instruction;
    if (intent && INTENTIONS[a.attendu]) intent.value = a.attendu === 'action_proposal' && a.garde !== 'facturer' ? 'auto' : INTENTIONS[a.attendu];
    question.dispatchEvent(new Event('input')); question.focus();
    note('Instruction prête : relisez-la, puis envoyez-la.');
  }
  async function lancerAssistance(a, bouton) {
    bouton.disabled = true; note('Demande enregistrée…');
    try {window.axiorhubPilot?.show(await poster('ui/mission', {context: contexte(), action: a.id, request_key: jeton()}));}
    catch (e) {note(e.message); charger();}
    finally {bouton.disabled = false;}
  }
  function boutonAction(a) {
    const b = el('button', a.libelle, 'ia89-action'); b.type = 'button'; b.dataset.iaAction = a.id;
    b.title = [a.effet_libelle, a.garde_libelle ? 'Décision qui reste à vous : ' + a.garde_libelle : '', a.statut === 'livree' ? '' : a.statut_libelle].filter(Boolean).join(' · ');
    if (!a.disponible) {b.disabled = true; b.title = a.motif;}
    else b.addEventListener('click', () => (a.circuit === 'pilote' ? lancerPilote(a) : lancerAssistance(a, b)));
    const li = el('li'); li.append(b);
    if (!a.disponible) li.append(el('small', a.motif));
    else if (a.garde_libelle) li.append(el('small', 'Préparé seulement. ' + a.garde_libelle + ' reste à vous.'));
    else if (a.statut !== 'livree') li.append(el('small', a.statut_libelle));
    return li;
  }
  function liste(titre, lignes, cls) {
    if (!lignes.length) return null;
    const d = el('div', undefined, cls || ''); d.append(el('strong', titre));
    const ul = el('ul'); lignes.forEach(x => ul.append(el('li', x))); d.append(ul); return d;
  }
  function afficher(p) {
    zone.replaceChildren();
    const tete = el('p', undefined, 'ia89-tete'); tete.append(el('strong', p.fonction.libelle), ' · ', p.dossier.id ? p.dossier.label : 'Aucun dossier choisi');
    zone.append(tete);
    if (p.etat) zone.append(el('p', 'État de cette fonction : ' + p.etat.etat_libelle + (p.etat.motif_libelle ? ' (' + p.etat.motif_libelle + ')' : ''), 'ia89-etat'));
    if (p.objets.length) zone.append(liste('Élément(s) sélectionné(s)', p.objets.map(o => o.titre + (o.version_courante ? ' · version ' + o.version_courante.slice(0, 12) : '')), 'ia89-objets'));
    // 5.6.96 (U06) : la liste des dossiers n'est chargée qu'à l'ouverture du volet ; le dossier de l'objet, revalidé par le serveur, est ajouté s'il manque encore
    if (p.dossier.origine === 'objet' && p.dossier.id && matter.value !== p.dossier.id) {if (![...matter.options].some(o => o.value === p.dossier.id)) matter.append(new Option(p.dossier.label || p.dossier.id, p.dossier.id)); poserDossier(p.dossier.id);}
    p.ambiguites.forEach(a => {
      const box = el('div', undefined, 'ia89-ambigu'); box.setAttribute('role', 'alert'); box.append(el('p', a.message));
      a.candidats.forEach(c => {const b = el('button', c.label, 'ia89-action'); b.type = 'button'; b.addEventListener('click', () => choisir(c.id)); box.append(b, ' ');});
      zone.append(box);
    });
    const ul = el('ul', undefined, 'ia89-actions'); ul.setAttribute('aria-label', 'Actions proposées sur cette page');
    p.actions.forEach(a => ul.append(boutonAction(a))); zone.append(ul);
    if (p.fiche.manuel) zone.append(el('p', p.fiche.manuel, 'ia89-manuel'));
    const d = el('details', undefined, 'ia89-limites'); d.append(el('summary', 'Limites et garde-fous'));
    [liste('Limites', p.fiche.limites), liste('Toujours', p.garde_fous)].forEach(x => x && d.append(x));
    d.append(el('p', 'Droits : ' + p.fiche.droits), el('p', 'Preuve attendue : ' + p.fiche.preuve), el('p', 'Données : ' + p.politique));
    zone.append(d); zone.hidden = false;
    dock.querySelector('.ws-ai-auto-context')?.setAttribute('hidden', '');
  }
  async function charger() {
    const n = ++numero;
    try {
      const p = await poster('ui/contexte', contexte());
      if (n === numero) afficher(p);
    } catch (e) {
      if (n !== numero) return;
      zone.replaceChildren(); const m = el('p', e.message, 'ia89-refus'); m.setAttribute('role', 'alert'); zone.append(m); zone.hidden = false;
    }
  }
  /* Label IA : ce que l'IA a utilisé, ses limites et son fournisseur. Jamais de consigne ni de raisonnement interne : le serveur ne les renvoie pas. */
  function carte(m, c) {
    const r = m.resultat5689; if (!r) return;
    if (r.etat === 'partiel') c.append(el('p', r.etat_libelle + ' : à compléter avant tout usage.', 'ia89-reserve'));
    const blocs = [liste('Informations manquantes', r.informations_manquantes || []), liste('Points à décider', r.points_a_decider || []),
      liste('Actions proposées, rien n’est exécuté', (r.actions_proposees || []).map(a => a.libelle + (a.garde_libelle ? ' · ' + a.garde_libelle + ' reste à vous' : '')))];
    blocs.forEach(x => x && c.append(x));
    const manquants = r.champs_manquants || [];
    if (r.question_regroupee && r.assistance) {
      const f = el('form', undefined, 'ia89-question'); f.append(el('p', r.question_regroupee));
      manquants.forEach(nom => {const l = el('label', (window.axiorhubIa5689.libelles[nom] || nom) + ' '), i = el('input'); i.name = nom; l.append(i); f.append(l);});
      const b = el('button', 'Reprendre avec ces réponses'); b.type = 'submit'; f.append(b);
      f.addEventListener('submit', async ev => {
        ev.preventDefault(); b.disabled = true;
        try {window.axiorhubPilot?.show(await poster('ui/mission/repondre', {id: r.assistance, revision: r.revision, champs: Object.fromEntries(new FormData(f))}));}
        catch (e) {note(e.message); b.disabled = false;}
      });
      c.append(f);
    }
    const d = el('details', undefined, 'ia89-label');
    d.append(el('summary', 'Label IA · ' + r.etat_libelle));
    const f = r.fournisseur;
    d.append(el('p', f ? 'Fournisseur : ' + (f.nom || 'non renseigné') + (f.local === true ? ' (local)' : f.local === false ? ' (externe)' : '') + (f.modele ? ' · ' + f.modele : '') : 'Fournisseur : non renseigné pour ce travail'));
    const s = liste('Sources', (r.sources || []).map(x => [x.titre, x.version ? 'version ' + String(x.version).slice(0, 12) : '', x.emplacement].filter(Boolean).join(' · '))); if (s) d.append(s);
    const l = liste('Limites', r.limites || []); if (l) d.append(l);
    d.append(el('p', r.preuve_depot ? 'Dépôt vérifié : ' + r.preuve_depot.preuve : 'Aucun dépôt : ce résultat reste interne.'));
    c.append(d);
  }
  window.axiorhubIa5689 = {contexte, carte, libelles: {date_audience: 'Date de l’audience', juridiction: 'Juridiction'}};
  document.addEventListener('click', ev => {
    const b = ev.target.closest('.ui89-ia'); if (!b) return;
    courant = JSON.parse(b.dataset.iaObjets || '[]');
    fonction = b.dataset.iaFonction || cfg.fonction;
    if (dock.hidden) launcher.click();
    if (b.dataset.iaDossier && matter.value !== b.dataset.iaDossier && [...matter.options].some(o => o.value === b.dataset.iaDossier)) poserDossier(b.dataset.iaDossier);
    charger().then(() => zone.scrollIntoView({block: 'nearest'}));
  });
  launcher.addEventListener('click', () => {if (!dock.hidden) charger();});
  matter.addEventListener('change', charger);
  if (!dock.hidden) charger();
})();
