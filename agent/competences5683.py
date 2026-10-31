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
"""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import PurePosixPath
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
SCRIPT_SERVICES = {'verify_links': ('service', 'liens5683.verifier', 'Vérification des liens par le service réseau d’AxiorHub.')}
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
