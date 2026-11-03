"""5.6.32 (cahier des charges additionnel, R16 / SK01–SK13) : compétences personnalisables de l'IA.

- Registre versionné : origine (fournie avec le logiciel, fichier importé, création personnelle, copie), propriétaire, portée
  (cabinet ou personnelle), version, empreinte, état, instructions et manifeste ; une version utilisée reste retrouvable.
- Trois compétences fournies, installées sans import (installation neuve et mise à jour) : Assistant juridique (œuvre AGPL-3.0 de
  Christophe Quézel-Ambrunaz, adaptée), Approbation des comptes, Politique de confidentialité RGPD (créations AxiorHub). La
  désactivation et les copies personnelles de l'avocat sont conservées lors des mises à jour.
- Import d'un SKILL.md ou d'une archive déclarative (aucun script exécuté, fichiers exécutables refusés) ; création assistée par
  formulaire, sans YAML à écrire ; modification = nouvelle version ; retour à une version précédente ; export.
- Sélection par tâche : choix manuel de l'avocat ou, si le mode automatique est activé, rapprochement déterministe des situations
  d'emploi et mots-clés déclarés, limité aux compétences autorisées et dont les prérequis sont réunis ; raisons conservées ; au plus
  deux compétences ; seules leurs instructions sont transmises au modèle (plus de concaténation de toutes les compétences actives).
- Une compétence décrit une méthode : elle n'accorde aucune permission ; le moteur garde le contrôle des effets.
- 5.6.89 (chapitre 42) : manifeste complet (``manifeste``), compétence périmée non retenue, version figée dans la mission (``figer``), seules les
  compétences de l'étape sont chargées. « Outils permis » et « fournisseurs qualifiés » ne font que restreindre : ils ne desserrent jamais la politique.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import threading
import zipfile

from .common import Stop, fold

ROOT = Path(__file__).resolve().parents[1] / 'resources' / 'skills'
BUILTIN = ('assistant-juridique', 'approbation-comptes', 'politique-rgpd',
           # 5.6.43 (cahier v9, § 3) : méthodes de phase, installées désactivées (à relire, adapter puis activer)
           'methode-decider', 'methode-analyser', 'methode-courriel', 'methode-document', 'methode-controler')
MAX_SELECTED = 2
BUDGET = 60_000
_ctx = threading.local()
EXECUTABLE = ('.py', '.pyc', '.js', '.mjs', '.cjs', '.sh', '.bash', '.php', '.pl', '.rb', '.exe', '.dll', '.so', '.dylib', '.jar', '.wasm', '.ps1', '.bat', '.cmd')


def _now():
    return datetime.now(timezone.utc).isoformat()


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS skills5632(id TEXT NOT NULL, version TEXT NOT NULL, origin TEXT NOT NULL, owner TEXT NOT NULL, scope TEXT NOT NULL,
      name TEXT NOT NULL, description TEXT NOT NULL, manifest TEXT NOT NULL, instructions TEXT NOT NULL, sha256 TEXT NOT NULL, parent TEXT NOT NULL,
      created TEXT NOT NULL, PRIMARY KEY(id, version));
    CREATE TABLE IF NOT EXISTS skill_state5632(id TEXT PRIMARY KEY, active_version TEXT NOT NULL, enabled INTEGER NOT NULL, owner TEXT NOT NULL,
      scope TEXT NOT NULL, user_choice INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS skill_usage5632(id INTEGER PRIMARY KEY AUTOINCREMENT, skill TEXT NOT NULL, version TEXT NOT NULL, job_id INTEGER,
      kind TEXT NOT NULL, mode TEXT NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mission_skills5689(mission TEXT NOT NULL, skill TEXT NOT NULL, version TEXT NOT NULL, sha256 TEXT NOT NULL, etape TEXT NOT NULL, at TEXT NOT NULL,
      PRIMARY KEY(mission, skill));
    ''')


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _builtin_package(folder):
    base = ROOT / folder
    manifest = json.loads((base / 'manifest.json').read_text(encoding='utf-8'))
    parts = []
    for rel in manifest.get('instructions', []):
        parts.append('### ' + rel + '\n' + (base / rel).read_text(encoding='utf-8'))
    instructions = '\n\n'.join(parts)
    return manifest, instructions


def ensure_builtin(desk):
    """Installe ou met à jour les compétences fournies ; l'état choisi par l'avocat (désactivation) et ses copies sont conservés."""
    schema(desk)
    installed = []
    for folder in BUILTIN:
        try:
            manifest, instructions = _builtin_package(folder)
        except (OSError, ValueError):
            continue
        sid, version = manifest['id'], manifest['version']
        sha = _sha(instructions)
        if not desk.db.execute('SELECT 1 FROM skills5632 WHERE id=? AND version=?', (sid, version)).fetchone():
            desk.db.execute('INSERT INTO skills5632 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                            (sid, version, 'builtin', 'cabinet', 'cabinet', manifest['name'], manifest.get('description', ''),
                             json.dumps(manifest, ensure_ascii=False), instructions, sha, '', _now()))
        state = desk.db.execute('SELECT * FROM skill_state5632 WHERE id=?', (sid,)).fetchone()
        if not state:
            desk.db.execute('INSERT INTO skill_state5632 VALUES(?,?,?,?,?,0,?)',
                            (sid, version, 0 if manifest.get('enabled_default') is False else 1, 'cabinet', 'cabinet', _now()))
        elif state['active_version'] != version:
            desk.db.execute('UPDATE skill_state5632 SET active_version=?, updated=? WHERE id=?', (version, _now(), sid))   # désactivation conservée
        installed.append(sid)
    desk.db.commit()
    return installed


MANIFESTE = (('id', 'id'), ('version', 'version'), ('responsable', 'owner_ref'), ('licence', 'license'), ('juridictions', 'jurisdictions'), ('validite', 'valid_until'),
             ('entrees', 'input_schema'), ('sorties', 'output_schema'), ('outils_permis', 'allowed_tools'), ('tests', 'tests'), ('fournisseurs_qualifies', 'qualified_providers'))


def manifeste(manifest):
    """Manifeste de compétence lisible : une valeur absente reste « None », jamais une qualification supposée. Les anciennes compétences ne sont ni certifiées ni réécrites."""
    manifest = manifest if isinstance(manifest, dict) else {}
    out = {}
    for champ, cle in MANIFESTE:
        valeur = manifest.get(champ, manifest.get(cle))
        out[champ] = valeur if valeur not in ('', [], {}) else None
    out['complet'] = all(out[c] is not None for c, _ in MANIFESTE)
    return out


def perimee(manifest, moment=None):
    limite = manifeste(manifest)['validite']
    if not limite:
        return False
    return str(limite)[:10] < (moment or datetime.now(timezone.utc)).strftime('%Y-%m-%d')


def _restrictions(champ):
    """Listes déclarées par les compétences de l'étape, relues sur la version retenue. Aucune liste déclarée : aucune restriction ajoutée."""
    out = []
    for sel in current_selection():
        liste = sel.get(champ)
        if isinstance(liste, list) and liste:
            out.append(set(str(x) for x in liste))
    return out


def fournisseur_qualifie(provider_id, kind=''):
    """Une compétence peut réduire les fournisseurs admis ; le routeur n'y voit jamais une exception à la politique des données."""
    return all(str(provider_id) in liste or str(kind) in liste for liste in _restrictions('fournisseurs_qualifies'))


def outils_autorises(tools):
    """Intersection seulement : un outil absent des outils vérifiés n'apparaît jamais, quoi que déclare le manifeste."""
    listes = _restrictions('outils_permis')
    return [t for t in tools if all(t['outil'] in liste for liste in listes)]


def figer(desk, mission, etape, selection):
    """Version figée dans la mission : la première retenue reste celle de toute la mission, une mise à jour n'altère pas une analyse en cours."""
    out = []
    for sel in selection:
        row = _row(desk, sel['id'], sel['version'])
        if mission:
            desk.db.execute('INSERT OR IGNORE INTO mission_skills5689 VALUES(?,?,?,?,?,?)', (str(mission), sel['id'], sel['version'], row['sha256'], str(etape or ''), _now()))
            fige = desk.db.execute('SELECT version FROM mission_skills5689 WHERE mission=? AND skill=?', (str(mission), sel['id'])).fetchone()
            if fige['version'] != sel['version']:
                row = _row(desk, sel['id'], fige['version'])
                sel = {**sel, 'version': fige['version'], 'reason': sel['reason'] + ' (version figée dans la mission)'}
        fiche = manifeste(json.loads(row['manifest']))
        out.append({**sel, 'outils_permis': fiche['outils_permis'] or [], 'fournisseurs_qualifies': fiche['fournisseurs_qualifies'] or []})
    if mission:
        desk.db.execute('DELETE FROM mission_skills5689 WHERE at<?', ((datetime.now(timezone.utc) - timedelta(days=800)).isoformat(),))
        desk.db.commit()
    return out


def _racine(desk, row, args):
    """Mission d'un travail : celle qu'il nomme, sinon le travail racine."""
    nommee = str((args or {}).get('mission') or (args or {}).get('mission_id') or '')
    if nommee or not row:
        return nommee
    ident = row['id']
    for _ in range(12):
        parent = desk.db.execute('SELECT parent_job_id FROM jobs WHERE id=?', (ident,)).fetchone()
        if not parent or not parent[0]:
            break
        ident = parent[0]
    return str(ident)


def _row(desk, sid, version=None):
    schema(desk)
    if version is None:
        st = desk.db.execute('SELECT * FROM skill_state5632 WHERE id=?', (sid,)).fetchone()
        if not st:
            raise Stop('competence_absente')
        version = st['active_version']
    row = desk.db.execute('SELECT * FROM skills5632 WHERE id=? AND version=?', (sid, version)).fetchone()
    if not row:
        raise Stop('version_competence_absente')
    return row


def _visible(st, owner):
    return st['scope'] == 'cabinet' or st['owner'] == owner


def listing(desk, owner='cabinet', caps=None):
    """Compétences visibles par l'utilisateur, avec prérequis réunis ou manquants (SK09) et versions disponibles."""
    ensure_builtin(desk)
    caps = available_capabilities(desk) if caps is None else caps
    out = []
    for st in desk.db.execute('SELECT * FROM skill_state5632 ORDER BY id').fetchall():
        if not _visible(st, owner):
            continue
        row = _row(desk, st['id'], st['active_version'])
        manifest = json.loads(row['manifest'])
        req = manifest.get('requires') or {}
        need = req.get('analysis') or []
        missing = [c for c in need if c not in caps]
        verified_missing = [c for c in (req.get('verified_draft') or []) if c not in caps]
        versions = [r['version'] for r in desk.db.execute('SELECT version FROM skills5632 WHERE id=? ORDER BY created', (st['id'],))]
        from .competences5683 import resources as _resources83
        kept = len(_resources83(desk, st['id'], st['active_version']))
        out.append({'id': st['id'], 'name': row['name'], 'description': row['description'], 'origin': row['origin'], 'scope': st['scope'],
                    'owner': st['owner'], 'version': st['active_version'], 'versions': versions, 'enabled': bool(st['enabled']),
                    'sha256': row['sha256'], 'license': manifest.get('license', ''), 'source': manifest.get('source', ''),
                    'author': manifest.get('author', ''), 'missing': missing, 'verified_missing': verified_missing,
                    'situations': manifest.get('situations', []), 'parent': row['parent'], 'rights': manifest.get('rights') or {}, 'resources': kept,
                    'scripts': manifest.get('script_decisions') or [],
                    # 5.6.83 : ancien import dont les références n'ont pas été conservées : à réimporter, rien n'est fabriqué
                    'a_reimporter': row['origin'] == 'import' and not kept and len(manifest.get('files') or []) > 1})
    return out


def available_capabilities(desk, prouvees=False):
    """Capacités utilisables par une compétence, déclarées par la configuration. Une déclaration n'est pas une preuve : 5.6.89, une capacité révoquée
    disparaît toujours, et ``prouvees`` ne garde que ce qu'une preuve encore valide soutient (``connector_readiness5689``, seul juge de « Opérationnel »)."""
    caps = {'ai.generate', 'docx.generate'}
    from .fonctions5631 import capabilities
    c = capabilities(desk.c)
    if 'storage' in c:
        caps.add('storage.read')
    try:
        from .sources5632 import enabled_items
        if enabled_items(desk):
            caps.add('legal.text')
    except Exception:   # noqa: BLE001
        pass
    from .connector_readiness5689 import filtrer_capacites
    return filtrer_capacites(desk, caps, prouvees)


# --------------------------------------------------------------------------------------------- création, import, versions
FORM_FIELDS = ('nom', 'objectif', 'situations', 'etapes', 'resultat', 'controles', 'exemples', 'limites')


def skill_md_from_form(data):
    """Formulaire de création (SK02) → SKILL.md structuré ; l'avocat n'écrit ni YAML ni code."""
    name = str(data.get('nom') or '').strip()
    if not 3 <= len(name) <= 80:
        raise Stop('nom_competence_invalide')
    goal = str(data.get('objectif') or '').strip()
    if len(goal) < 10:
        raise Stop('objectif_competence_requis')
    lines = lambda key: [x.strip(' -•\t') for x in str(data.get(key) or '').splitlines() if x.strip(' -•\t')]   # noqa: E731
    slug = re.sub(r'[^a-z0-9]+', '-', fold(name)).strip('-')[:60] or 'competence'
    desc = goal.replace('"', '’')[:400]
    md = ['---', 'name: ' + slug, 'description: "' + desc + '"', '---', '', '# ' + name, '', '## Objectif', goal, '']
    for key, title in (('situations', 'Quand l’utiliser'), ('etapes', 'Étapes'), ('resultat', 'Résultat attendu'), ('controles', 'Contrôles'),
                       ('exemples', 'Exemples fictifs'), ('limites', 'Limites')):
        items = lines(key)
        if items:
            md += ['## ' + title] + [('%d. %s' % (i + 1, x)) if key == 'etapes' else '- ' + x for i, x in enumerate(items)] + ['']
    md += ['## Règles AxiorHub', '- Cette compétence décrit une méthode ; elle n’accorde aucune permission (envoi, agenda, dépôt).',
           '- Les références juridiques citées passent par le contrôle des sources d’AxiorHub.']
    return slug, name, desc, '\n'.join(md) + '\n', lines('situations')


def _manifest_for(sid, name, desc, version, origin, situations, extra=None):
    m = {'id': sid, 'name': name, 'version': version, 'origin': origin, 'description': desc, 'situations': situations,
         'auto_select_tags': [fold(s)[:40] for s in situations][:20], 'requires': {'analysis': ['ai.generate']}, 'license': '', 'source': ''}
    m.update(extra or {})
    return m


def _insert(desk, sid, version, origin, owner, scope, name, desc, manifest, instructions, parent=''):
    if len(instructions) > 200_000:
        raise Stop('instructions_competence_trop_longues')
    desk.db.execute('INSERT INTO skills5632 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (sid, version, origin, owner, scope, name[:120], desc[:4000], json.dumps(manifest, ensure_ascii=False), instructions, _sha(instructions), parent, _now()))
    st = desk.db.execute('SELECT * FROM skill_state5632 WHERE id=?', (sid,)).fetchone()
    if st:
        desk.db.execute('UPDATE skill_state5632 SET active_version=?, updated=? WHERE id=?', (version, _now(), sid))
    else:
        desk.db.execute('INSERT INTO skill_state5632 VALUES(?,?,?,?,?,1,?)', (sid, version, 0, owner, scope, _now()))   # à activer après revue
    desk.db.commit()


def create(desk, owner, data, scope='personal'):
    schema(desk)
    slug, name, desc, md, situations = skill_md_from_form(data)
    sid = 'perso.' + slug
    if desk.db.execute('SELECT 1 FROM skill_state5632 WHERE id=?', (sid,)).fetchone():
        raise Stop('competence_deja_existante')
    _insert(desk, sid, '1.0.0', 'personal', owner, 'cabinet' if scope == 'cabinet' else 'personal', name, desc,
            _manifest_for(sid, name, desc, '1.0.0', 'personal', situations), md)
    desk.audit('competence5632_creee', {'id': sid, 'owner': owner})
    return {'id': sid, 'version': '1.0.0', 'preview': md}


def _frontmatter(text):
    from .yaml5683 import frontmatter
    return frontmatter(text)[0]     # 5.6.83 (SK01) : lecture stricte : blocs, listes, métadonnées imbriquées, clés dupliquées refusées


def _items(value):
    """Situations ou phases : liste YAML, ou texte séparé par des virgules (forme historique)."""
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in re.split(r'[,;]', str(value or '')) if x.strip()]


def import_skill(desk, owner, raw, filename, scope='personal'):
    """SKILL.md seul ou archive .zip : inventaire complet ou refus ; aucun script exécuté ; ressources conservées par version (5.6.83)."""
    from . import competences5683 as c83
    schema(desk)
    c83.schema(desk)
    name = PurePosixPath(str(filename or '').replace('\\', '/')).name.lower()
    package = {'files': [], 'blocked': [], 'scripts': [], 'archive_sha256': ''}
    if name.endswith('.zip'):
        package = c83.inspect(raw)
        skill_file = next(f for f in package['files'] if PurePosixPath(f['path']).name.lower() == 'skill.md')
        skill = skill_file['data'].decode('utf-8', 'replace')
    elif name.endswith('.md'):
        skill = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
    else:
        raise Stop('format_competence_invalide')
    meta = _frontmatter(skill)
    slug = re.sub(r'[^a-z0-9]+', '-', fold(str(meta.get('name') or PurePosixPath(name).stem))).strip('-')[:60] or 'competence'
    sid = 'import.' + slug
    desc = str(meta.get('description') or '')[:4000]
    existing = desk.db.execute('SELECT COUNT(*) FROM skills5632 WHERE id=?', (sid,)).fetchone()[0]
    version = '1.%d.0' % existing
    sha = _sha(skill)
    if desk.db.execute('SELECT 1 FROM skills5632 WHERE id=? AND sha256=?', (sid, sha)).fetchone():
        raise Stop('competence_deja_importee')
    situations = _items(meta.get('situations'))
    phases = _items(meta.get('phases') or meta.get('phase'))   # 5.6.43 (§ 26.1)
    decisions = c83.script_decisions(package['scripts'])
    manifest = _manifest_for(sid, str(meta.get('name') or slug), desc, version, 'import', situations,
                             {'files': sorted(f['path'] for f in package['files']) or ['SKILL.md'], 'blocked_files': sorted(package['blocked'] + package['scripts']),
                              'scripts_executed': False, 'filename': name, 'phases': phases, 'references_injected': False, 'schema_version': 'axiorhub.skill/2',
                              'metadata': meta.get('metadata') if isinstance(meta.get('metadata'), dict) else {}, 'archive_sha256': package['archive_sha256'],
                              'rights': c83.rights_of(meta, package['files']), 'script_decisions': decisions,
                              'resources': [{k: f[k] for k in ('path', 'kind', 'sha256', 'size')} for f in package['files']]})
    manifest['license'] = manifest['rights']['declared']
    for champ, cle in (('responsable', 'responsable'), ('juridictions', 'jurisdictions'), ('validite', 'valid_until'), ('entrees', 'inputs'), ('sorties', 'outputs'),
                       ('outils_permis', 'allowed_tools'), ('tests', 'tests'), ('fournisseurs_qualifies', 'qualified_providers')):   # 5.6.89 : déclarations conservées, sans valeur d'autorisation
        if meta.get(cle) not in (None, '', []):
            manifest[champ] = _items(meta[cle]) if champ not in ('responsable', 'validite') else str(meta[cle])[:120]
    _insert(desk, sid, version, 'import', owner, 'cabinet' if scope == 'cabinet' else 'personal', str(meta.get('name') or slug), desc, manifest, skill)
    c83.store_resources(desk, sid, version, package['files'])
    desk.audit('competence5632_importee', {'id': sid, 'version': version, 'blocked': len(package['blocked']), 'scripts': len(package['scripts']), 'scripts_executed': False})
    return {'id': sid, 'version': version, 'blocked_files': manifest['blocked_files'], 'files': manifest['files'], 'state': 'a_relire', 'rights': manifest['rights'],
            'script_decisions': decisions}


def edit(desk, owner, sid, instructions):
    """Toute modification crée une nouvelle version, à relire et activer ; la version d'origine reste disponible."""
    row = _row(desk, sid)
    st = desk.db.execute('SELECT * FROM skill_state5632 WHERE id=?', (sid,)).fetchone()
    if row['origin'] == 'builtin':
        raise Stop('copier_avant_de_modifier')
    if st['scope'] == 'personal' and st['owner'] != owner:
        raise Stop('competence_autre_utilisateur')
    count = desk.db.execute('SELECT COUNT(*) FROM skills5632 WHERE id=?', (sid,)).fetchone()[0]
    version = '%s.%d' % (row['version'].rsplit('.', 1)[0], count)
    manifest = json.loads(row['manifest'])
    manifest['version'] = version
    _insert(desk, sid, version, row['origin'], st['owner'], st['scope'], row['name'], row['description'], manifest, str(instructions or ''), parent=row['version'])
    desk.db.execute('UPDATE skill_state5632 SET enabled=0 WHERE id=?', (sid,))
    desk.db.commit()
    return {'id': sid, 'version': version, 'enabled': False}


def copy(desk, owner, sid):
    """Copie personnelle (d'une compétence fournie notamment) ; l'original reste intact lors des mises à jour."""
    row = _row(desk, sid)
    new = 'copie.' + re.sub(r'[^a-z0-9]+', '-', fold(owner))[:30] + '.' + sid.split('.', 1)[-1]
    if desk.db.execute('SELECT 1 FROM skill_state5632 WHERE id=?', (new,)).fetchone():
        raise Stop('copie_deja_existante')
    manifest = json.loads(row['manifest'])
    manifest.update({'id': new, 'origin': 'copy', 'version': '1.0.0', 'parent': sid + '@' + row['version']})
    _insert(desk, new, '1.0.0', 'copy', owner, 'personal', row['name'] + ' (copie)', row['description'], manifest, row['instructions'], parent=sid + '@' + row['version'])
    return {'id': new, 'version': '1.0.0'}


def set_enabled(desk, owner, sid, on, version=None):
    st = desk.db.execute('SELECT * FROM skill_state5632 WHERE id=?', (sid,)).fetchone()
    if not st:
        raise Stop('competence_absente')
    if st['scope'] == 'personal' and st['owner'] != owner:
        raise Stop('competence_autre_utilisateur')
    if version:
        _row(desk, sid, version)
        desk.db.execute('UPDATE skill_state5632 SET active_version=? WHERE id=?', (version, sid))   # retour à une version précédente
    desk.db.execute('UPDATE skill_state5632 SET enabled=?, user_choice=1, updated=? WHERE id=?', (1 if on else 0, _now(), sid))
    desk.db.commit()
    desk.audit('competence5632_etat', {'id': sid, 'enabled': bool(on), 'version': version or st['active_version']})
    return {'id': sid, 'enabled': bool(on)}


def export(desk, sid, version=None):
    from .competences5683 import export_package
    return export_package(desk, _row(desk, sid, version))    # 5.6.83 (SK02) : ressources et empreintes restituées


# --------------------------------------------------------------------------------------------- sélection par tâche
def auto_mode(desk):
    return bool(desk.settings('competences5632:auto', True))


def resolve(desk, text, owner='cabinet', manual=None, auto=None):
    """Compétences retenues pour une tâche : choix manuel d'abord ; sinon (mode automatique) rapprochement déterministe des situations
    d'emploi et mots-clés ; seulement parmi les compétences activées, visibles et dont les prérequis d'analyse sont réunis."""
    caps = available_capabilities(desk)
    rows = [s for s in listing(desk, owner, caps) if s['enabled'] and not s['missing'] and not perimee(json.loads(_row(desk, s['id'], s['version'])['manifest']))]
    chosen = []
    if manual:
        for sid in manual:
            s = next((x for x in rows if x['id'] == sid), None)
            if s:
                chosen.append({'id': s['id'], 'version': s['version'], 'mode': 'manuel', 'reason': 'Choisie par l’avocat'})
        return chosen[:MAX_SELECTED]
    if auto is False or (auto is None and not auto_mode(desk)):
        return []
    hay = ' ' + fold(text or '') + ' '
    scored = []
    for s in rows:
        manifest = json.loads(_row(desk, s['id'], s['version'])['manifest'])
        tags = [fold(t) for t in (manifest.get('auto_select_tags') or []) + (manifest.get('situations') or []) if t]
        hits = sorted({t for t in tags if len(t) >= 3 and re.search(r'(?<![a-z0-9])' + re.escape(t) + r'(?![a-z0-9])', hay)})
        if hits:
            scored.append((len(hits), s, hits))
    scored.sort(key=lambda x: (-x[0], x[1]['id']))
    for n, s, hits in scored[:MAX_SELECTED]:
        chosen.append({'id': s['id'], 'version': s['version'], 'mode': 'automatique', 'reason': 'Situations ou mots-clés reconnus : ' + ', '.join(hits[:6])})
    return chosen


def instructions_for(desk, selection, budget=BUDGET, query='', phase=''):
    """5.6.83 (SK03) : noyau et sections utiles à l'étape dans le budget ; une section n'est jamais coupée, ce qui reste hors contexte est signalé."""
    from .competences5683 import load_for_step
    blocks, used = [], 0
    _ctx.loaded = {}
    for sel in selection:
        row = _row(desk, sel['id'], sel['version'])
        room = budget - used
        text, omitted, loaded = load_for_step(desk, sel, row, room, query, phase) if room > 0 else ('', [], [])
        _ctx.loaded[sel['id']] = (loaded, omitted)
        if not text.strip():   # 5.6.43 (§ 7.3) : jamais coupée au milieu de ses limites ou contrôles : non transmise, signalée
            blocks.append('[Compétence « %s » non transmise : elle dépasse la place disponible dans le contexte.]' % row['name'])
            continue
        note = '\n[Sections non chargées à cette étape : %s.]' % ', '.join(omitted[:12]) if omitted else ''
        blocks.append('COMPÉTENCE « %s » version %s (%s)\n%s%s' % (row['name'], row['version'], sel['reason'], text, note))
        used += len(text)
    return '\n\n'.join(blocks)


def current_instructions():
    return getattr(_ctx, 'text', '')


def current_selection():
    return list(getattr(_ctx, 'selection', []) or [])


@contextmanager
def pour_etape(desk, mission, etape, texte, owner='cabinet'):
    """5.6.89 : compétences de cette seule étape d'une mission, à leur version figée. Une compétence indisponible n'empêche pas l'étape : rien de plus n'est transmis."""
    import sqlite3
    avant = (getattr(_ctx, 'selection', []), getattr(_ctx, 'text', ''))
    try:
        selection = figer(desk, mission, etape, resolve(desk, texte, owner))
        _ctx.selection, _ctx.text = selection, instructions_for(desk, selection, query=texte, phase=etape) if selection else ''
    except (Stop, sqlite3.Error):
        _ctx.selection, _ctx.text = [], ''
    try:
        yield _ctx.selection
    finally:
        _ctx.selection, _ctx.text = avant


TEXT_KEYS = ('instruction', 'question', 'demande', 'request', 'prompt', 'consigne', 'text')


@contextmanager
def for_job(desk, row, args):
    """Contexte d'un travail : compétences retenues (manuel via args['skills'] ou automatique), instructions transmises au modèle,
    usage enregistré (version réellement utilisée)."""
    selection = []
    text, phase = '', ''
    try:   # 5.6.43 (cahier v9, § 7.1-7.2) : vraie consigne relue en base et méthode choisie selon la phase du travail
        from .competences5643 import real_instruction, select
        manual = args.get('skills') if isinstance(args, dict) else None
        kind = row['kind'] if row else ''
        text, _deliverable, phase = real_instruction(desk, kind, args)
        owner = str((args or {}).get('owner') or 'cabinet')
        if manual or text or phase:
            selection = figer(desk, _racine(desk, row, args), phase, select(desk, text, phase, owner, manual=[str(x) for x in manual] if isinstance(manual, list) else None, kind=kind))
    except Exception:   # noqa: BLE001 — une compétence indisponible n'empêche jamais le travail
        selection = []
    _ctx.selection = selection
    from .competences5643 import CHAT_BUDGET
    _ctx.text = instructions_for(desk, selection, CHAT_BUDGET if row and row['kind'] == 'assistant_answer' else BUDGET, query=text, phase=phase) if selection else ''
    try:   # 5.6.59 (A07) : savoir-faire approuvé du cabinet, rapproché de la vraie consigne et de la phase
        from .savoir5659 import for_job_text
        extra, _chosen = for_job_text(desk, row, args, text, phase)
        if extra:
            _ctx.text = (_ctx.text + '\n\n' + extra) if _ctx.text else extra
    except Exception:   # noqa: BLE001 — une bibliothèque indisponible n'empêche jamais le travail
        pass
    try:
        if selection:
            for sel in selection:
                desk.db.execute('INSERT INTO skill_usage5632(skill,version,job_id,kind,mode,reason,at) VALUES(?,?,?,?,?,?,?)',
                                (sel['id'], sel['version'], row['id'] if row else None, row['kind'] if row else '', sel['mode'], sel['reason'][:300], _now()))
            desk.db.commit()
            from . import competences5683   # 5.6.83 : journal des étapes : version, ressources chargées et sections omises
            for sel in selection:
                loaded, omitted = (getattr(_ctx, 'loaded', None) or {}).get(sel['id'], ((), ()))
                competences5683.start_run(desk, row['id'] if row else None, sel, phase, str((args or {}).get('matter') or ''), str((args or {}).get('owner') or ''),
                                          str((args or {}).get('id') or ''), sel['mode'], loaded, omitted)
        try:
            yield selection
        except BaseException as error:
            if selection and row:
                from . import competences5683
                competences5683.finish_runs(desk, row['id'], 'bloque', str(error))
            raise
        if selection and row:
            from . import competences5683
            competences5683.finish_runs(desk, row['id'], 'termine')
    finally:
        _ctx.selection, _ctx.text = [], ''
