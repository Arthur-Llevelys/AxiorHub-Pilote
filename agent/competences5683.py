"""5.6.83 : compétences complètes : ressources versionnées, droits, décisions sur les scripts, chargement progressif, journal des étapes.

Le registre reste celui de ``competences5632`` ; ce module l'étend sans en créer un second.

- **Import** : inventaire complet ou refus explicite. Aucune troncature : au-delà de 300 entrées, l'archive est refusée. Chemins sortants, liens
  symboliques et doublons après normalisation sont refusés. Rien n'est exécuté, rien n'écrit hors du stockage des ressources.
- **Ressources** : octets conservés par version, avec chemin, type, empreinte et taille. L'export restitue le paquet et ses empreintes.
- **Droits** : licence déclarée et restriction conservées. Importer pour son usage et redistribuer sont deux choses différentes : une licence
  non commerciale ou un droit inconnu n'est jamais présenté comme distribuable.
- **Scripts** : jamais exécutés à l'import. Chacun reçoit une décision explicite : service équivalent ou indisponible, avec motif.
- **Chargement** : un noyau court, puis les sections et ressources utiles à l'étape, dans le budget réel du modèle. Une section n'est jamais
  coupée en son milieu, et ce qui n'est pas chargé est signalé.
- 5.6.108 (lot I) : un paquet fourni enregistre ses **ressources déclarées** (seulement l'intérieur du paquet, ni lien, ni script). Un paquet qui
  déclare un **plan de chargement** (``PLAN-AXIORHUB.json``) est chargé dans l'ordre réservé : adaptation AxiorHub, sections de méthode de
  l'étape, fiche de la tâche, puis compléments notés par mots-clés ; les passages sans objet ne partent plus. Sous un seuil de place, une
  **version courte** dérivée remplace le texte complet tronqué. Un dossier d'un autre droit ne reçoit que la méthode générale, avec mention.
  Ce qui est réellement envoyé est tracé juste avant l'appel (``skill_envois56108``).
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import unicodedata
import zipfile

from .common import Stop, fold

MAX_ENTRIES = 300
MAX_ARCHIVE = 12_000_000
MAX_TOTAL = 30_000_000
MAX_FILE = 5_000_000
TEXT_TYPES = {'.md': 'markdown', '.txt': 'texte', '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.csv': 'tableau', '.tex': 'texte'}
FILE_TYPES = {'.docx': 'modele', '.dotx': 'modele', '.xlsx': 'tableur', '.pdf': 'pdf'}
SCRIPT_TYPES = ('.py', '.pyc', '.js', '.mjs', '.cjs', '.sh', '.bash', '.php', '.pl', '.rb', '.ps1', '.bat', '.cmd')
# Décisions du cahier (SK04) : un script remplacé par un service testé, sinon indisponible avec son motif.
SCRIPT_SERVICES = {'verify_links': ('service', 'liens5683.verifier', 'Vérification des liens par le service réseau d’AxiorHub.'),
                   # 5.6.109 (lot K, F06, § 7) : outils transmis par le cabinet rapprochés des services natifs ; le script lui-même n'est jamais exécuté
                   'extract_references': ('service', 'references56108.extraire', 'Références candidates relevées par le service d’extraction '
                                          'd’AxiorHub ; un motif reconnu reste une référence à vérifier.'),
                   'format_citation': ('service', 'citations5697.normaliser', 'Forme normalisée par le service natif (fournisseur, zone, date utile), '
                                       'statut de vérification conservé.'),
                   'knowledge_memory_tool': ('service', 'savoir5659.select', 'Mémoire native versionnée du cabinet (portée, provenance, corrections) ; '
                                             'aucune base commune entre dossiers.'),
                   'llm_council': ('service', 'conseil56108.lancer', 'Conseil de modèles d’AxiorHub, facultatif, borné par la politique des données '
                                   'et le budget ; un vote de modèles ne prouve rien.'),
                   'skills_manager_tool_fix': ('service', 'competences5632.import_skill', 'Registre et import déclaratif natifs des compétences ; '
                                               'aucune installation par le modèle.'),
                   'legal_pleading_creator_v1_0': ('service', 'modeles5684.selectionner', 'Modèles du cabinet et édition Word native, versions '
                                                   'conservées dans le dossier.'),
                   'generate_bibliography': ('indisponible', '', 'Bibliographie : à produire depuis les références retenues et vérifiées ; formateur '
                                             'natif non livré dans cette version, le script n’est pas exécuté.'),
                   'hal_search': ('indisponible', '', 'Recherche HAL : adaptateur facultatif du registre de sources non livré dans cette version ; '
                                  'aucune requête n’est envoyée.'),
                   'frontier_ui': ('indisponible', '', 'Interface générée non reprise : les composants natifs suffisent ; aucune image, carte ni '
                                   'écriture de fichier n’est activée.')}
SCRIPT_UNAVAILABLE = 'Script importé non exécuté : aucun service équivalent livré, la fonction est indisponible.'
CORE_TITLES = ('role', 'posture', 'regle', 'principe', 'interdit', 'limite', 'controle', 'obligatoire')
PHASE_WORDS = {'analyse': 'analyse preuve fait qualification recherche', 'redaction_document': 'redaction acte document modele clause',
               'redaction_courriel': 'courriel lettre reponse', 'controle': 'controle verification relecture citation', 'decision': 'decision choix cadrage'}
RUN_STATES = ('en_cours', 'termine', 'bloque', 'interrompu')


def _now():
    return datetime.now(timezone.utc).isoformat()


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS skill_resources5683(skill TEXT NOT NULL, version TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL, sha256 TEXT NOT NULL,
      size INTEGER NOT NULL, content BLOB NOT NULL, read_scope TEXT NOT NULL DEFAULT 'competence', PRIMARY KEY(skill, version, path));
    CREATE TABLE IF NOT EXISTS skill_runs5683(id TEXT PRIMARY KEY, job_id INTEGER, operation TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '',
      phase TEXT NOT NULL DEFAULT '', skill TEXT NOT NULL, version TEXT NOT NULL, mode TEXT NOT NULL DEFAULT '', resources TEXT NOT NULL DEFAULT '[]', omitted TEXT NOT NULL DEFAULT '[]',
      state TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS skill_runs5683_job ON skill_runs5683(job_id);
    CREATE TABLE IF NOT EXISTS skill_envois56108(id TEXT PRIMARY KEY, job_id INTEGER, mission TEXT NOT NULL DEFAULT '', etape TEXT NOT NULL DEFAULT '', skill TEXT NOT NULL,
      version TEXT NOT NULL, sha256 TEXT NOT NULL DEFAULT '', forme TEXT NOT NULL, sections TEXT NOT NULL DEFAULT '[]', fiches TEXT NOT NULL DEFAULT '[]',
      omissions TEXT NOT NULL DEFAULT '[]', caracteres INTEGER NOT NULL DEFAULT 0, budget INTEGER NOT NULL DEFAULT 0, raison TEXT NOT NULL DEFAULT '',
      droit TEXT NOT NULL DEFAULT '', nom TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS skill_envois56108_job ON skill_envois56108(job_id, at);
    ''')


def _clean_path(name):
    """Chemin relatif sûr, ou Stop : aucun chemin sortant, absolu ou contenant une barre inverse."""
    text = str(name)
    path = PurePosixPath(text)
    if not text or text.startswith('/') or '\\' in text or re.match(r'^[A-Za-z]:', text) or '..' in path.parts:
        raise Stop('chemin_archive_competence_refuse')
    return unicodedata.normalize('NFC', text)


def inspect(raw):
    """Inventaire complet d'une archive, sans rien extraire sur le disque. Toute anomalie refuse l'archive entière."""
    if not 0 < len(raw) <= MAX_ARCHIVE:
        raise Stop('taille_archive_competence_invalide')
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise Stop('archive_competence_trop_de_fichiers')
        seen, entries, total = set(), [], 0
        for info in infos:
            path = _clean_path(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise Stop('chemin_archive_competence_refuse')
            if info.is_dir():
                continue
            key = path.casefold()
            if key in seen:
                raise Stop('chemin_archive_competence_duplique')
            seen.add(key)
            total += info.file_size
            if total > MAX_TOTAL:
                raise Stop('archive_competence_trop_grande')
            entries.append((path, info))
        skill = sorted((p for p, _ in entries if PurePosixPath(p).name.lower() == 'skill.md'), key=lambda p: p.count('/'))
        if not skill:
            raise Stop('skill_md_absent')
        root = str(PurePosixPath(skill[0]).parent)
        root = '' if root == '.' else root + '/'
        files, blocked, scripts = [], [], []
        for path, info in entries:
            rel = path[len(root):] if root and path.startswith(root) else path
            parts = PurePosixPath(rel).parts
            if any(p.startswith('.') or p == '__pycache__' for p in parts):
                blocked.append(rel)
                continue
            suffix = PurePosixPath(rel).suffix.lower()
            if suffix in SCRIPT_TYPES:
                scripts.append(rel)
                continue
            kind = TEXT_TYPES.get(suffix) or FILE_TYPES.get(suffix)
            if not kind or info.file_size > MAX_FILE:
                blocked.append(rel)
                continue
            data = z.read(info)
            if suffix in TEXT_TYPES and b'\x00' in data:
                blocked.append(rel)
                continue
            files.append({'path': rel, 'kind': kind, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'data': data})
    except zipfile.BadZipFile:
        raise Stop('archive_competence_invalide') from None
    return {'files': files, 'blocked': sorted(blocked), 'scripts': sorted(scripts), 'archive_sha256': hashlib.sha256(raw).hexdigest()}


def rights_of(meta, files):
    """Droits déclarés : jamais déduits de la licence d'AxiorHub, ni de celle d'un composant technique."""
    meta_block = meta.get('metadata') if isinstance(meta.get('metadata'), dict) else {}
    declared = str(meta.get('license') or meta_block.get('license') or '').strip()
    licence_files = [f for f in files if re.match(r'(licen[cs]e|copying)', PurePosixPath(f['path']).name, re.I)]
    text = declared + ' ' + ' '.join(f['data'][:6000].decode('utf-8', 'replace') for f in licence_files)
    hay = fold(text)
    non_commercial = bool(re.search(r'\bnc\b|non.?commercial|by-nc', hay))
    label = ('CC BY-NC-SA' if 'nc-sa' in hay or 'by-nc' in hay else 'AGPL-3.0' if 'agpl' in hay else 'MIT' if re.search(r'\bmit\b', hay) else
             'Apache-2.0' if 'apache' in hay else declared[:80] or 'non précisée')
    return {'declared': label, 'licence_file': licence_files[0]['path'] if licence_files else '', 'non_commercial': non_commercial,
            'status': 'restreint' if non_commercial else ('a_confirmer' if label == 'non précisée' else 'declare'),
            'commercial_distribution_ready': False, 'proof': ''}


def script_decisions(scripts):
    out = []
    for path in scripts:
        stem = PurePosixPath(path).stem
        decision, service, reason = SCRIPT_SERVICES.get(stem, ('indisponible', '', SCRIPT_UNAVAILABLE))
        out.append({'path': path, 'decision': decision, 'service': service, 'reason': reason, 'executed': False})
    return out


def store_resources(desk, sid, version, files):
    schema(desk)
    for f in files:
        desk.db.execute('INSERT OR REPLACE INTO skill_resources5683(skill,version,path,kind,sha256,size,content) VALUES(?,?,?,?,?,?,?)',
                        (sid, version, f['path'], f['kind'], f['sha256'], f['size'], f['data']))
    desk.db.commit()


def resources(desk, sid, version):
    schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT path,kind,sha256,size FROM skill_resources5683 WHERE skill=? AND version=? ORDER BY path', (sid, version))]


def resource(desk, sid, version, path):
    schema(desk)
    row = desk.db.execute('SELECT * FROM skill_resources5683 WHERE skill=? AND version=? AND path=?', (sid, version, str(path))).fetchone()
    if not row:
        raise Stop('ressource_competence_absente')
    return row


def export_package(desk, row):
    """Paquet déclaré et empreintes, tels qu'ils ont été importés : un réimport redonne les mêmes empreintes."""
    sid, version = row['id'], row['version']
    schema(desk)
    rows = desk.db.execute('SELECT path,sha256,content FROM skill_resources5683 WHERE skill=? AND version=? ORDER BY path', (sid, version)).fetchall()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        names = {r['path'] for r in rows}
        if 'SKILL.md' not in names:
            z.writestr('SKILL.md', row['instructions'])
        for r in rows:
            z.writestr(r['path'], bytes(r['content']))
        z.writestr('manifest.json', row['manifest'])
        z.writestr('empreintes.json', json.dumps({r['path']: r['sha256'] for r in rows}, ensure_ascii=False, indent=2))
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------ chargement progressif
def sections(text):
    """[(titre, texte)] : découpage aux titres de niveau 1 et 2, le préambule en tête ; le texte est restitué sans perte."""
    out, title, buf = [], '', []
    for line in str(text or '').split('\n'):
        if re.match(r'^#{1,2}\s', line) and buf:
            out.append((title, '\n'.join(buf).strip('\n')))
            buf = []
        if re.match(r'^#{1,2}\s', line):
            title = line.lstrip('#').strip()
        buf.append(line)
    if buf:
        out.append((title, '\n'.join(buf).strip('\n')))
    return [x for x in out if x[1].strip()]


def _score(text, words):
    hay = set(re.findall(r'[a-z0-9]{4,}', fold(text)))
    return len(hay & words)


def fit(text, budget, query='', phase='', extras=()):
    """(texte, omissions) : noyau, puis sections et ressources les plus utiles à l'étape. Jamais de coupe au milieu d'une section."""
    parts = sections(text)
    words = set(re.findall(r'[a-z0-9]{4,}', fold(str(query) + ' ' + PHASE_WORDS.get(phase, ''))))
    chosen, used, omitted = [], 0, []
    core = [i for i, (t, _) in enumerate(parts) if i == 0 or any(w in fold(t) for w in CORE_TITLES)]
    rest = sorted((i for i in range(len(parts)) if i not in core), key=lambda i: (-_score(parts[i][0] + ' ' + parts[i][1], words), i))
    for i in core + rest:
        size = len(parts[i][1]) + 2
        if used + size <= budget:
            chosen.append(i)
            used += size
        else:
            omitted.append(parts[i][0] or 'préambule')
    body = '\n\n'.join(parts[i][1] for i in sorted(chosen))
    loaded = []
    for path, content in sorted(extras, key=lambda x: -_score(x[0] + ' ' + x[1], words)):
        if _score(path + ' ' + content, words) and used + len(content) + 40 <= budget:
            body += '\n\n### Ressource %s\n%s' % (path, content)
            used += len(content) + 40
            loaded.append(path)
    return body, omitted, loaded


def load_for_step(desk, sel, row, budget, query='', phase=''):
    """Instructions d'une compétence pour une étape, dans le budget donné, avec les ressources textuelles utiles."""
    text = row['instructions']
    meta_end = re.match(r'^---[ \t]*\n.*?\n---[ \t]*\n', text, re.S)
    if meta_end:
        text = text[meta_end.end():]
    extras = []
    schema(desk)
    for r in desk.db.execute("SELECT path,content FROM skill_resources5683 WHERE skill=? AND version=? AND kind IN ('markdown','texte') AND path NOT LIKE '%SKILL.md' AND size<=40000",
                             (row['id'], row['version'])):
        extras.append((r['path'], bytes(r['content']).decode('utf-8', 'replace')))
    if len(text) <= budget and not extras:
        return text, [], []
    return fit(text, budget, query, phase, extras)


def budget_chars(cfg, messages, max_tokens):
    """Place réelle pour les méthodes, en caractères : contexte du modèle, moins les messages et la réponse attendue (≈ 3 caractères par jeton)."""
    context = int(cfg.get('num_ctx') or 0) or int(cfg.get('contexte5683') or 0)
    used = sum(len(str(m.get('content') or '')) for m in messages or []) // 3
    if not context:
        return 40_000
    room = context - used - int(max_tokens or 0) - 512
    return max(4_000, min(room * 3, 60_000))


def trim_block(block, room):
    """Bloc de méthode ramené à la place restante, par sections entières ; l'omission reste visible du modèle et du journal."""
    head, _, body = block.partition('\n')
    kept, omitted = fit(body, max(room - len(head) - 200, 0))[:2]
    if not kept.strip():
        return ''
    note = ('\n[Sections non chargées, faute de place : %s.]' % ', '.join(omitted[:12])) if omitted else ''
    return head + '\n' + kept + note


# ------------------------------------------------------------------------------------------------ contrat SkillRun
def start_run(desk, job_id, sel, phase='', matter='', owner='', operation='', mode='', loaded=(), omitted=()):
    schema(desk)
    ident = secrets.token_hex(12)
    desk.db.execute('INSERT INTO skill_runs5683(id,job_id,operation,matter,owner,phase,skill,version,mode,resources,omitted,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, job_id, operation, str(matter or ''), str(owner or ''), phase, sel['id'], sel['version'], mode, json.dumps(list(loaded), ensure_ascii=False),
                     json.dumps(list(omitted), ensure_ascii=False), 'en_cours', _now(), _now()))
    desk.db.commit()
    return ident


def finish_runs(desk, job_id, state, detail=''):
    if state not in RUN_STATES or not job_id:
        return
    schema(desk)
    desk.db.execute("UPDATE skill_runs5683 SET state=?,detail=?,updated=? WHERE job_id=? AND state='en_cours'", (state, str(detail)[:200], _now(), job_id))
    desk.db.commit()


def runs(desk, limit=30):
    schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM skill_runs5683 ORDER BY created DESC LIMIT ?', (max(1, min(int(limit), 200)),))]


# ------------------------------------------------------------------------------------------------ 5.6.108 (lot I) : paquet fourni
PAQUET_TYPES = {'.md': 'markdown', '.txt': 'texte', '.json': 'json'}
PAQUET_MAX_FICHIERS = 60
PAQUET_MAX_FICHIER = 64_000
PAQUET_MAX_TOTAL = 600_000
SEUIL_COURT = 16_000            # caractères : sous ce seuil de place, la version courte remplace le texte complet tronqué
SEUIL_SETTING = 'competences56108:seuil_court'
RESERVE_ENTETE = 400            # en-tête du bloc et mention des omissions, comptés dans la place
FORMES = {'complete': 'version complète', 'courte': 'version courte', 'methode_generale': 'méthode générale seulement', 'aucune': 'rien transmis',
          'ancienne': 'chargement par mots-clés'}
CLES_DROIT = ('droit_applicable', 'loi_applicable', 'systeme_juridique', 'droit')
SCORE_COMPLEMENT = 2            # un complément partage au moins deux mots utiles avec la consigne et l'étape
_CACHE = {}


def _chemin_paquet(racine, rel):
    """(chemin relatif, fichier) d'un paquet fourni : chemin sûr, aucun lien symbolique sur le parcours, toujours à l'intérieur de la racine."""
    rel = _clean_path(str(rel).strip().rstrip('/'))
    cible = racine
    for morceau in PurePosixPath(rel).parts:
        cible = cible / morceau
        if cible.is_symlink():
            raise Stop('ressource_competence_refusee')
    cible = cible.resolve(strict=True)      # absent : FileNotFoundError, le paquet n'est pas installé
    if cible != racine and racine not in cible.parents:
        raise Stop('ressource_competence_refusee')
    return rel, cible


def declare_ressources(manifest):
    m = manifest if isinstance(manifest, dict) else {}
    ax = m.get('axiorhub') if isinstance(m.get('axiorhub'), dict) else {}
    return bool(m.get('references') or ax.get('plan') or ax.get('version_courte'))


def ressources_declarees(base, manifest):
    """Ressources DÉCLARÉES d'un paquet fourni : ``references`` (dossier, premier niveau, ou liste), plan et version courte du bloc ``axiorhub``.

    Seulement l'intérieur du paquet : un chemin sortant ou un lien symbolique refuse le paquet entier, rien n'est enregistré à moitié. Un script ou
    un fichier exclu (``excluded_files``) n'est jamais lu ni enregistré, encore moins exécuté. Markdown, texte ou JSON en UTF-8 ; tailles bornées."""
    from .competences5632 import EXECUTABLE
    m = manifest if isinstance(manifest, dict) else {}
    racine = Path(base)
    if racine.is_symlink():
        raise Stop('ressource_competence_refusee')
    racine = racine.resolve(strict=True)
    exclus = {str(x).replace('\\', '/').strip('/') for x in m.get('excluded_files') or []}
    declares = []
    refs = m.get('references')
    if isinstance(refs, str) and refs.strip():
        declares.append(('dossier', refs))
    elif isinstance(refs, list):
        declares += [('fichier', str(x)) for x in refs]
    ax = m.get('axiorhub') if isinstance(m.get('axiorhub'), dict) else {}
    declares += [('fichier', str(ax[k])) for k in ('plan', 'version_courte') if ax.get(k)]
    chemins = []
    for nature, brut in declares:
        rel, cible = _chemin_paquet(racine, brut)
        if nature == 'dossier':
            if not cible.is_dir():
                raise Stop('ressource_competence_absente')
            for f in sorted(cible.iterdir(), key=lambda p: p.name):
                if f.is_symlink():
                    raise Stop('ressource_competence_refusee')
                if f.is_file():
                    chemins.append(rel + '/' + f.name)
        else:
            chemins.append(rel)
    out, total = [], 0
    for rel in dict.fromkeys(chemins):
        suffixe = PurePosixPath(rel).suffix.lower()
        if rel in exclus or suffixe in EXECUTABLE or suffixe not in PAQUET_TYPES:
            continue                       # aucun script : ni lu, ni enregistré, ni exécuté
        rel, cible = _chemin_paquet(racine, rel)
        if not cible.is_file():
            raise Stop('ressource_competence_absente')
        if cible.stat().st_size > PAQUET_MAX_FICHIER:
            raise Stop('ressource_competence_trop_grande')
        data = cible.read_bytes()
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            raise Stop('ressource_competence_illisible') from None
        if b'\x00' in data:
            raise Stop('ressource_competence_illisible')
        total += len(data)
        if total > PAQUET_MAX_TOTAL or len(out) >= PAQUET_MAX_FICHIERS:
            raise Stop('ressources_competence_trop_nombreuses')
        out.append({'path': rel, 'kind': PAQUET_TYPES[suffixe], 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'data': data})
    return out


def empreinte_paquet(instructions, files):
    """Empreinte d'une version : instructions ET ressources, une fiche modifiée la change. Sans ressource : l'empreinte historique des instructions."""
    texte = str(instructions or '')
    if files:
        texte += '\n\x00ressources\n' + '\n'.join('%s %s' % (f['path'], f['sha256']) for f in sorted(files, key=lambda f: f['path']))
    return hashlib.sha256(texte.encode('utf-8')).hexdigest()


# ------------------------------------------------------------------------------------------------ 5.6.108 (lot I) : plan de chargement
def _textes(desk, row):
    """Ressources textuelles d'une version (immuable) : lues une fois par base et par processus."""
    cle = (id(desk.db), row['id'], row['version'], row['sha256'])
    if cle in _CACHE:
        return _CACHE[cle]
    schema(desk)
    lignes = desk.db.execute("SELECT path,content FROM skill_resources5683 WHERE skill=? AND version=? AND kind IN ('markdown','texte','json')",
                             (row['id'], row['version'])).fetchall()
    out = {r['path']: bytes(r['content']).decode('utf-8', 'replace') for r in lignes}
    if out:
        if len(_CACHE) > 32:
            _CACHE.clear()
        _CACHE[cle] = out
    return out


def _fichiers_instructions(texte, rels):
    """Instructions d'un paquet fourni, par fichier : ``_builtin_package`` place ``### chemin`` en tête de chacun."""
    reperes, pos = [], 0
    for rel in rels or []:
        marque = '### %s\n' % rel
        i = str(texte).find(marque, pos)
        if i >= 0:
            reperes.append((str(rel), i, i + len(marque)))
            pos = i + len(marque)
    return {rel: texte[debut:(reperes[n + 1][1] if n + 1 < len(reperes) else len(texte))].strip('\n') for n, (rel, _i, debut) in enumerate(reperes)}


def _niveau3(bloc):
    out, titre, buf = [], '', []
    for line in bloc.split('\n'):
        if line.startswith('### ') and buf:
            out.append((titre, '\n'.join(buf).strip('\n')))
            buf = []
        if line.startswith('### '):
            titre = line[4:].strip()
        buf.append(line)
    if buf:
        out.append((titre, '\n'.join(buf).strip('\n')))
    return [x for x in out if x[1].strip()]


def _unites(texte, plan):
    """Unités de méthode : sections de niveau 1 et 2 (niveau 3 aussi pour une section que le plan déclare « à découper »), avec leur clé du plan.
    Les clés et titres viennent du plan du paquet : rien n'est écrit en dur ici. Un titre seul n'est pas une unité."""
    corps = re.sub(r'^---[ \t]*\n.*?\n---[ \t]*\n', '', str(texte or '').lstrip('\n'), count=1, flags=re.S)
    cles = plan.get('sections') if isinstance(plan.get('sections'), dict) else {}
    decouper = {str(x) for x in plan.get('decouper') or []}

    def cle_de(titre):
        t = titre.strip().casefold()
        return next((str(c) for c, prefixe in cles.items() if str(prefixe).strip() and t.startswith(str(prefixe).strip().casefold())), '')
    out = []
    for titre, bloc in sections(corps):
        if titre and not '\n'.join(bloc.split('\n')[1:]).strip():
            continue
        cle = cle_de(titre)
        if cle and cle in decouper:
            for sous, sous_bloc in _niveau3(bloc):
                if not '\n'.join(sous_bloc.split('\n')[1:]).strip():
                    continue                       # titre seul
                out.append({'cle': cle + '/' + sous if sous else cle, 'titre': (titre.split(' — ')[0] + ' / ' + sous) if sous else titre, 'texte': sous_bloc})
        else:
            out.append({'cle': cle, 'titre': titre or 'préambule', 'texte': bloc})
    return out


def _correspond(unite, entree):
    cle, entree = unite['cle'], str(entree)
    if '/' in entree:
        return cle.casefold().startswith(entree.casefold())
    return bool(cle) and (cle == entree or cle.startswith(entree + '/'))


def _mot(hay, mot):
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(fold(str(mot))) + r'(?![a-z0-9])', hay))


def choisir_fiche(plan, kind='', livrable='', query='', phase=''):
    """(fiche, motif) déterministe : livrable déclaré, puis type de travail, puis mots de la consigne, puis fiche par défaut de l'étape."""
    regles = [r for r in plan.get('choix_fiche') or [] if isinstance(r, dict) and r.get('fiche')]
    liv, q = ' ' + fold(str(livrable or '')) + ' ', ' ' + fold(str(query or '')) + ' '
    if str(livrable or '').strip():
        for r in regles:
            if any(_mot(liv, x) for x in r.get('livrables') or []):
                return str(r['fiche']), 'livrable'
    for r in regles:
        if kind and kind in (r.get('types') or []):
            return str(r['fiche']), 'type de travail'
    for r in regles:
        if any(_mot(q, x) for x in r.get('mots') or []):
            return str(r['fiche']), 'consigne'
    etapes = plan.get('etapes') if isinstance(plan.get('etapes'), dict) else {}
    etape = etapes.get(phase) or etapes.get('defaut') or {}
    return str(etape.get('fiche_defaut') or ''), 'étape'


def droit_du_dossier(desk, matter):
    """Droit applicable d'un dossier, lu sans rien écrire : fiche du dossier, puis métadonnées du dossier structuré (droit applicable, juridiction),
    puis droit par défaut du cabinet. Rien de trouvé : valeur vide (le plan ne présume alors aucun droit étranger)."""
    import sqlite3
    out = {'valeur': '', 'source': '', 'juridiction': ''}
    mid = str(matter or '')
    if mid:
        try:
            from .common import load_matters
            fiche = next((m for m in load_matters(desk.c, unfiltered=True) if str(m.get('id')) == mid), None)
        except Exception:   # noqa: BLE001 — registre illisible : aucun droit présumé
            fiche = None
        for cle in CLES_DROIT:
            if fiche and str(fiche.get(cle) or '').strip():
                return {**out, 'valeur': str(fiche[cle]).strip()[:80], 'source': 'fiche du dossier'}
        try:
            if desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='case_assertions5689'").fetchone():
                for r in desk.db.execute("SELECT cle_valeur,valeur,statement FROM case_assertions5689 WHERE matter=? AND kind='metadata' AND status NOT IN ('rejected','superseded') "
                                         "ORDER BY status='validated' DESC, updated DESC LIMIT 60", (mid,)):
                    cle, val = str(r['cle_valeur'] or ''), str(r['valeur'] or r['statement'] or '').strip()
                    if cle in CLES_DROIT and val and not out['valeur']:
                        out.update(valeur=val[:80], source='dossier structuré')
                    elif cle == 'juridiction' and val and not out['juridiction']:
                        out['juridiction'] = val[:200]
        except sqlite3.Error:
            pass
        if out['valeur']:
            return out
    try:
        from .bibliotheque5639 import default_law
        cabinet = default_law(desk)
        if cabinet and cabinet != 'FR':
            out.update(valeur=cabinet, source='réglage du cabinet')
    except Exception:   # noqa: BLE001
        pass
    return out


def droit_etranger(droit, plan):
    """Code ou libellé du droit étranger retenu, sinon ''. La liste des valeurs françaises et des juridictions étrangères vient du plan."""
    regle = plan.get('droit_etranger') if isinstance(plan.get('droit_etranger'), dict) else {}
    francais = {fold(str(x)).strip() for x in regle.get('francais') or []}
    droit = droit if isinstance(droit, dict) else {}
    valeur = str(droit.get('valeur') or '').strip()
    if valeur:
        return '' if fold(valeur) in francais else valeur
    juridiction = ' ' + fold(str(droit.get('juridiction') or '')) + ' '
    for code, mots in (regle.get('juridictions') or {}).items():
        if any(_mot(juridiction, m) for m in mots or []):
            return str(code)
    return ''


def charger_plan(desk, sel, row, budget, contexte=None):
    """Méthode d'une compétence selon le plan déclaré dans son paquet ; ``None`` sans plan (chargement historique par mots-clés).

    Ordre réservé : adaptation AxiorHub, sections de l'étape, forme courte éventuelle, fiche de la tâche, fiches de l'étape ; puis seulement
    les compléments notés par mots-clés. Sous le seuil de place, la version courte remplace le texte complet, suivie de la fiche si elle tient.
    Dossier d'un autre droit : la méthode générale seule, avec mention. Jamais de coupe : un élément réservé qui ne tient pas est signalé."""
    try:
        manifest = json.loads(row['manifest'])
    except (TypeError, ValueError):
        return None
    ax = manifest.get('axiorhub') if isinstance(manifest.get('axiorhub'), dict) else {}
    if not ax.get('plan'):
        return None
    textes = _textes(desk, row)
    try:
        plan = json.loads(textes.get(str(ax['plan'])) or '')
    except ValueError:
        return None
    if not isinstance(plan, dict) or not str(plan.get('format', '')).startswith('axiorhub-plan-competence/'):
        return None
    ctx = contexte if isinstance(contexte, dict) else {}
    phase, query = str(ctx.get('phase') or ''), str(ctx.get('query') or '')
    etapes = plan.get('etapes') if isinstance(plan.get('etapes'), dict) else {}
    etape = etapes.get(phase) or etapes.get('defaut') or {}
    fichiers = _fichiers_instructions(row['instructions'], manifest.get('instructions') or []) or {str(plan.get('instructions') or ''): row['instructions']}
    unites = _unites(fichiers.get(str(plan.get('instructions') or ''), ''), plan)
    rep = str(plan.get('fiches') or '').strip('/')
    fiches = {PurePosixPath(p).name: t for p, t in sorted(textes.items()) if rep and p.startswith(rep + '/') and p.endswith(('.md', '.txt'))}
    so = plan.get('sans_objet') if isinstance(plan.get('sans_objet'), dict) else {}
    so_fiches = {str(x) for x in so.get('fiches') or []}

    def sans_objet(u):
        return any(_correspond(u, e) for e in so.get('sections') or [])
    vc = plan.get('version_courte') if isinstance(plan.get('version_courte'), dict) else {}
    nom_courte = str(vc.get('fichier') or ax.get('version_courte') or '')
    courte = textes.get(nom_courte, '') if nom_courte else ''
    try:
        seuil = int(desk.settings(SEUIL_SETTING, None) or vc.get('seuil_caracteres') or SEUIL_COURT)
    except (TypeError, ValueError, AttributeError):
        seuil = SEUIL_COURT
    fiche, motif_fiche = choisir_fiche(plan, str(ctx.get('kind') or ''), str(ctx.get('livrable') or ''), query, phase)
    droit = ctx.get('droit') if isinstance(ctx.get('droit'), dict) else {}
    etranger = droit_etranger(droit, plan)
    out = {'competence': row['id'], 'nom': row['name'], 'version': row['version'], 'sha256': row['sha256'], 'mode': str(sel.get('mode') or ''),
           'raison_selection': str(sel.get('reason') or ''), 'phase': phase, 'forme': 'complete', 'raison': 'place_suffisante', 'sections': [], 'fiches': [],
           'complements': [], 'omissions': [], 'sans_objet': [], 'fiche_tache': fiche, 'motif_fiche': motif_fiche, 'budget': int(budget), 'seuil': seuil,
           'droit': etranger, 'source_droit': droit.get('source', '') if etranger else '', 'libelle_courte': str(vc.get('libelle') or ax.get('version_courte_statut') or ''),
           'texte': '', 'caracteres': 0}
    morceaux, place, pris = [], [int(budget)], set()
    lignes_so = tuple(str(x) for x in so.get('lignes') or [] if str(x).strip())
    # 5.6.108 (banc d'essai) : lignes qui CONTIENNENT un marqueur sans objet (« scripts/ », « COWORK »…), où qu'il soit dans la ligne :
    # un petit modèle recopiait les commandes de scripts des fiches dans sa réponse.
    contenant_so = tuple(str(x) for x in so.get('lignes_contenant') or [] if str(x).strip())
    fiches_courtes = plan.get('fiches_courtes') if isinstance(plan.get('fiches_courtes'), dict) else {}
    # 5.6.108 (lot R) : appel vers un petit modèle : aucun nom d'outil, de script ni de connecteur dans ce qui lui est transmis (le programme
    # fait la recherche). Déclaratif dans le plan du paquet : mentions retirées dans la ligne (la règle de fond reste), puis lignes qui en
    # contiennent encore retirées, dans TOUT ce qui part (adaptation, version courte, sections, fiches) ; consigne de citation ajoutée.
    pm = plan.get('petit_modele') if isinstance(plan.get('petit_modele'), dict) and ctx.get('petit_modele') else {}
    remplacer_pm = []
    for paire in pm.get('remplacer') or []:
        try:
            remplacer_pm.append((re.compile(str(paire[0])), str(paire[1])))
        except (re.error, IndexError, TypeError):
            continue
    contenant_pm = tuple(str(x) for x in pm.get('lignes_contenant') or [] if str(x).strip())

    def pour_petit(texte):
        if not pm:
            return texte
        for motif, par in remplacer_pm:
            texte = motif.sub(par, texte)
        return '\n'.join(x for x in str(texte).split('\n') if not any(m in x for m in contenant_pm))

    def nettoyer(texte, fiche=False):
        """Lignes déclarées sans objet par le plan (pré-requis d'environnement Cowork, scripts) retirées au chargement ; le fichier d'origine reste intact.
        Le filtre « contenant » ne vaut que pour les fiches (consignes d'outils ligne par ligne) : dans les sections du texte principal, une même ligne
        porte aussi des règles de fond (doctrine : identifiant vérifiable) qui ne doivent pas disparaître."""
        contenant = contenant_so if fiche else ()
        if not lignes_so and not contenant:
            return texte
        return '\n'.join(x for x in str(texte).split('\n')
                         if not (lignes_so and x.lstrip().startswith(lignes_so)) and not any(m in x for m in contenant))

    def prendre(nom, nature, texte, reserve, complement=False, ordre=1):
        texte = pour_petit(texte)
        taille = len(texte) + 2
        if texte.strip() and taille <= place[0]:
            morceaux.append((ordre, texte))
            place[0] -= taille
            pris.add((nature, nom))
            (out['fiches'] if nature == 'fiche' else out['sections']).append(nom)
            if complement:
                out['complements'].append(nom)
            return True
        if reserve:
            out['omissions'].append({'nom': nom, 'motif': 'place' if texte.strip() else 'absent'})
        return False

    def texte_fiche(nom):
        return ('### Fiche %s\n%s' % (nom, nettoyer(fiches[nom], fiche=True))) if nom in fiches else ''

    def prendre_fiche(nom):
        """Fiche réservée : complète si elle tient, sinon sa forme courte déclarée par le plan (ou d'emblée si le plan la préfère)."""
        fc = fiches_courtes.get(nom) if isinstance(fiches_courtes.get(nom), dict) else {}
        courte_fiche = ('### Fiche %s (forme courte, adaptation AxiorHub)\n%s' % (nom, fc['texte'])) if fc.get('texte') else ''
        if not (courte_fiche and fc.get('preferer')) and prendre(nom, 'fiche', texte_fiche(nom), not courte_fiche):
            return True
        if courte_fiche:
            pris.add(('fiche', nom))
            return prendre(nom + ' (forme courte)', 'fiche', courte_fiche, True)
        return False
    adaptation = fichiers.get(str(plan.get('adaptation') or ''), '')
    if pm.get('ajout'):   # 5.6.108 (lot R) : consigne de citation par numéro de source, réservée avant les compléments, placée en fin de méthode
        prendre('consigne de citation (petit modèle)', 'section', str(pm['ajout']), True, ordre=2)
    if etranger:
        out.update(forme='methode_generale', raison='droit_etranger', fiche_tache='')
        mention = str((plan.get('droit_etranger') or {}).get('mention') or 'Dossier relevant d’un autre droit (%s) : méthode générale seulement.')
        prendre('mention du droit applicable', 'section', '[' + mention.replace('%s', etranger) + ']', True, ordre=0)
        if courte:
            prendre('version courte (%s)' % nom_courte, 'section', courte, True)
    elif courte and budget < seuil:
        # Priorité : version courte, puis adaptation AxiorHub, puis fiche de la tâche ; ordre du texte : adaptation d'abord.
        out.update(forme='courte', raison='place_courte')
        prendre('version courte (%s)' % nom_courte, 'section', courte, True)
        prendre('adaptation AxiorHub', 'section', adaptation, True, ordre=0)
        if fiche:
            prendre_fiche(fiche)
    else:
        prendre('adaptation AxiorHub', 'section', adaptation, True, ordre=0)
        for cle in etape.get('sections') or []:
            trouvees = [u for u in unites if _correspond(u, cle) and not sans_objet(u)]
            if not trouvees:
                out['omissions'].append({'nom': str(cle), 'motif': 'absent'})
            for u in trouvees:
                prendre(u['titre'], 'section', nettoyer(u['texte']), True)
        formes = plan.get('formes_courtes') if isinstance(plan.get('formes_courtes'), dict) else {}
        for cle in etape.get('formes_courtes') or []:
            f = formes.get(cle) if isinstance(formes.get(cle), dict) else {}
            prendre(str(f.get('titre') or cle), 'section', ('## %s\n%s' % (f.get('titre') or cle, f.get('texte') or '')) if f.get('texte') else '', True)
            for u in unites:   # la forme courte remplace la section d'origine : celle-ci ne revient pas en complément
                if f.get('remplace') and _correspond(u, str(f['remplace'])):
                    pris.add(('section', u['titre']))
        for nom in ([fiche] if fiche else []) + [str(x) for x in etape.get('fiches') or [] if str(x) != fiche]:
            prendre_fiche(nom)
        words = set(re.findall(r'[a-z0-9]{4,}', fold(query + ' ' + PHASE_WORDS.get(phase, ''))))
        candidats = [(u['titre'], 'section', nettoyer(u['texte'])) for u in unites if not sans_objet(u) and ('section', u['titre']) not in pris]
        candidats += [(nom, 'fiche', texte_fiche(nom)) for nom in fiches if nom not in so_fiches and ('fiche', nom) not in pris]
        notes = sorted(((_score(nom + ' ' + texte, words), i, nom, nature, texte) for i, (nom, nature, texte) in enumerate(candidats)), key=lambda x: (-x[0], x[1]))
        plafond = etape.get('complements_max')
        plafond = int(plafond) if isinstance(plafond, int) and not isinstance(plafond, bool) and plafond >= 0 else None
        for score, _i, nom, nature, texte in notes:
            if score >= SCORE_COMPLEMENT and (plafond is None or len(texte) + 2 <= plafond):
                if prendre(nom, nature, texte, False, complement=True) and plafond is not None:
                    plafond -= len(texte) + 2
        out['sans_objet'] = [u['titre'] for u in unites if sans_objet(u)] + sorted(so_fiches & set(fiches))
    if pm:
        out['petit_modele'] = True
    if morceaux and all(o == 2 for o, _x in morceaux):   # seule la consigne de citation tenait : rien de la méthode n'est transmis
        morceaux.clear()
        out['sections'] = [s for s in out['sections'] if s != 'consigne de citation (petit modèle)']
    if not morceaux:
        out.update(forme='aucune', raison='place_insuffisante')
    out['texte'] = '\n\n'.join(x for _o, x in sorted(morceaux, key=lambda m: m[0]))
    out['caracteres'] = len(out['texte'])
    return out


# ------------------------------------------------------------------------------------------------ 5.6.108 (lot I) : trace de l'envoi
def tracer_envoi(desk, details, job_id=None, mission='', etape='', budget=0):
    """Juste avant l'appel au fournisseur : ce qui part réellement (et non ce qui a été sélectionné). Une ligne par compétence de l'appel."""
    schema(desk)
    maintenant = _now()
    for d in details or []:
        desk.db.execute('INSERT INTO skill_envois56108(id,job_id,mission,etape,skill,version,sha256,forme,sections,fiches,omissions,caracteres,budget,raison,droit,nom,at) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (secrets.token_hex(12), job_id, str(mission or ''), str(etape or ''), d['competence'], d['version'], d.get('sha256') or '', d.get('forme') or 'complete',
                         json.dumps(d.get('sections') or [], ensure_ascii=False), json.dumps(d.get('fiches') or [], ensure_ascii=False),
                         json.dumps(d.get('omissions') or [], ensure_ascii=False), int(d.get('caracteres') or 0), int(budget or d.get('budget') or 0),
                         str(d.get('raison') or '')[:80], str(d.get('droit') or '')[:80], str(d.get('nom') or '')[:120], maintenant))
    if secrets.randbelow(200) == 0:
        desk.db.execute('DELETE FROM skill_envois56108 WHERE at<?', ((datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),))
    desk.db.commit()


def _court(nom):
    nom = str(nom)
    return nom.split(' — ')[0] if nom.startswith('§') else nom[:60]


def _nombre(n):
    return '{:,}'.format(int(n)).replace(',', ' ')


def phrase_envoi(r):
    """« Méthode transmise : … » en français clair, depuis une ligne de trace."""
    r = dict(r)
    sections, fiches = json.loads(r.get('sections') or '[]'), json.loads(r.get('fiches') or '[]')
    omissions = [o for o in json.loads(r.get('omissions') or '[]') if isinstance(o, dict)]
    tete = 'Méthode transmise : %s v%s — ' % (r.get('nom') or r['skill'], r['version'])
    forme = r.get('forme') or 'complete'
    if forme == 'aucune':
        return tete + 'rien transmis : la place disponible pour le modèle ne suffit pas.'
    if forme == 'methode_generale':
        corps = 'méthode générale seulement, dossier relevant d’un autre droit (%s)' % (r.get('droit') or 'non précisé')
    elif forme == 'courte':
        corps = 'version courte (adaptation courte, à relire par l’avocat)'
    else:
        corps = FORMES.get(forme, forme)
    if forme in ('complete', 'ancienne') and sections:
        corps += ' : ' + ', '.join(_court(s) for s in sections[:12]) + (' …' if len(sections) > 12 else '')
    if fiches:
        corps += ' ; fiche%s : %s' % ('s' if len(fiches) > 1 else '', ', '.join(fiches[:6]))
    corps += ' ; %s caractères.' % _nombre(r.get('caracteres') or 0)
    manque = [_court(o.get('nom')) for o in omissions if o.get('motif') in ('place', 'absent')]
    if manque:
        corps += ' Non transmis : %s.' % ', '.join(manque[:8])
    return tete + corps


def methode_transmise(desk, job_id, limite=6):
    """Lignes « Méthode transmise » d'un travail, lues dans la trace d'envoi (lecture seule : sans trace, rien n'est affirmé)."""
    import sqlite3
    if not job_id:
        return []
    try:
        if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='skill_envois56108'").fetchone():
            return []
        rows = desk.db.execute('SELECT * FROM skill_envois56108 WHERE job_id=? ORDER BY at DESC LIMIT 60', (job_id,)).fetchall()
    except sqlite3.Error:
        return []
    vus, out = set(), []
    for r in rows:
        cle = (r['skill'], r['etape'])
        if cle not in vus:
            vus.add(cle)
            out.append(phrase_envoi(r))
    return out[:limite]
