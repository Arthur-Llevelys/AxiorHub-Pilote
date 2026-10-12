"""5.6.27 : page Dossier refondue — une seule page, simple, pour comprendre le dossier et travailler avec l'agent.

- **Fiche de travail préparée par l'agent** (travail ``fiche_dossier5627``) : résumé, rappel des faits rédigé (récit, pas une liste
  de pièces), parties au litige seulement (qualité, conseil, client du cabinet ; ni magistrats, ni commissaires de justice, ni
  personnes citées), procédure (juridiction, RG, stade, prochaine étape), chronologie des événements déterminants, enjeux et points
  à vérifier. Les pièces sont choisies selon leur nature (assignation, conclusions, décisions, contrats…) ; les factures et frais
  passent après. Chaque paragraphe cite ses pièces ; une référence inconnue est retirée, jamais inventée.
- **Corrections de l'avocat** : résumé, rappel des faits et parties se corrigent sur la page ; la correction s'affiche aussitôt et
  est transmise à l'agent comme prioritaire lors de la mise à jour suivante.
- **Travailler avec l'agent** : instruction libre ou raccourcis (courrier, conclusions, note, question) dans le dossier ; les
  réponses et documents produits s'affichent sur la page.
- Onglets : Fiche, Pièces, Courriels, Travaux de l'agent ; les vues détaillées (rubriques 5.6.24, faits à valider, chronologie
  complète, mémoire, stratégie) restent accessibles par « Plus ».
"""
from datetime import datetime, timezone
from html import escape as e
import json
from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import urlencode

from .common import Stop, load_matters, matter_display

SCHEMA = '''CREATE TABLE IF NOT EXISTS fiche5627(
 matter TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, sources TEXT NOT NULL, edits TEXT NOT NULL DEFAULT '{}',
 created TEXT NOT NULL, PRIMARY KEY(matter, version));'''
JOB = 'fiche_dossier5627'
QUALITES = {'demandeur': 'Demandeur', 'defendeur': 'Défendeur', 'appelant': 'Appelant', 'intime': 'Intimé', 'intervenant': 'Intervenant',
            'client': 'Client', 'cocontractant': 'Cocontractant', 'autre': 'Autre partie'}
# Nature des pièces : les actes et décisions d'abord, les pièces comptables du cabinet en dernier.
PRIORITY = ((r'assignation|citation|requete|saisine|acte introductif', 10), (r'conclusion', 9), (r'jugement|arret|ordonnance|decision|sentence', 9),
            (r'declaration d.appel|appel', 8), (r'mise en demeure|sommation|lrar', 7),
            (r'contrat|bail|statuts|protocole|convention|cgv|devis|bon de commande|accord', 7),
            (r'constat|proces.verbal|signification|expertise|rapport', 6), (r'note|plaidoirie|consultation|synthese|memo', 5),
            (r'courrier|lettre|courriel|mail|echange', 3))
LOW = r'facture|honoraire|frais|recu|justificatif|tampon|img[_ -]?\d|scan|inscription|greffe|provision'
JURISPRUDENCE = r'(^|[\s_-])(jp|jurisprudence)([\s_-]|$)|cass|cour de cassation'
KINDS = (('question', 'Question à l’agent'), ('courrier', 'Courrier'), ('courriel', 'Projet de courriel'), ('conclusions', 'Conclusions'),
         ('note', 'Note / consultation'), ('mise_en_demeure', 'Mise en demeure'), ('assignation', 'Assignation / requête'), ('autre', 'Autre document'))
SHORTCUTS = (('courrier', 'Courrier au client', 'Prépare un courrier au client faisant le point sur le dossier : situation actuelle, prochaine étape et ce que nous attendons de lui.'),
             ('note', 'Note de synthèse', 'Prépare une note de synthèse du dossier : faits, procédure, positions des parties, risques et prochaines étapes.'),
             ('conclusions', 'Projet de conclusions', 'Prépare un projet de conclusions pour notre client à partir des pièces et des écritures adverses du dossier.'),
             ('question', 'Prochaine échéance ?', 'Quelle est la prochaine échéance ou audience de ce dossier, et que reste-t-il à faire avant ?'))


def ensure_schema(desk):
    desk.db.executescript(SCHEMA + SCHEMA5634)
    _migrate_edits(desk)


# ------------------------------------------------------------------------------------------------ 5.6.34 (B04) : corrections de l'avocat
SCHEMA5634 = '''
CREATE TABLE IF NOT EXISTS fiche_corrections5634(id INTEGER PRIMARY KEY AUTOINCREMENT, matter TEXT NOT NULL, section TEXT NOT NULL,
  text TEXT NOT NULL, author TEXT NOT NULL, at TEXT NOT NULL, base_version INTEGER NOT NULL, revision INTEGER NOT NULL,
  status TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'avocat');
CREATE INDEX IF NOT EXISTS fiche_corrections5634_m ON fiche_corrections5634(matter, section, status);
CREATE TABLE IF NOT EXISTS party_links5634(matter TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL, decision TEXT NOT NULL, by TEXT NOT NULL,
  at TEXT NOT NULL, PRIMARY KEY(matter, a, b));'''
SECTIONS = ('resume', 'faits', 'parties')


def _migrate_edits(desk):
    """Les corrections enregistrées sur d'anciennes versions (perdues à l'affichage depuis une mise à jour de la fiche) deviennent la
    couche de corrections : la dernière correction de chaque rubrique redevient la valeur affichée et exportée."""
    try:
        if desk.settings('fiche5634:migration_corrections', False):
            return
    except Exception:   # noqa: BLE001
        return
    found = {}
    for row in desk.db.execute("SELECT matter,version,edits FROM fiche5627 WHERE edits<>'{}' ORDER BY version"):
        try:
            edits = json.loads(row['edits'] or '{}')
        except ValueError:
            continue
        for section, value in edits.items():
            if section in SECTIONS and isinstance(value, dict) and str(value.get('text') or '').strip():
                found[(row['matter'], section)] = (row['version'], value)
    for (mid, section), (version, value) in found.items():
        if not desk.db.execute("SELECT 1 FROM fiche_corrections5634 WHERE matter=? AND section=?", (mid, section)).fetchone():
            desk.db.execute('INSERT INTO fiche_corrections5634(matter,section,text,author,at,base_version,revision,status,origin) VALUES(?,?,?,?,?,?,?,?,?)',
                            (mid, section, str(value['text']), str(value.get('by') or 'cabinet'), str(value.get('at') or desk.now()), version, 1, 'active', 'migration'))
    desk.db.commit()
    desk.setting('fiche5634:migration_corrections', True)


def corrections(desk, mid):
    """Corrections actives, indépendantes des versions produites par l'IA : {rubrique: {text, by, at, revision, base_version, id}}."""
    out = {}
    for r in desk.db.execute("SELECT * FROM fiche_corrections5634 WHERE matter=? AND status='active' ORDER BY revision", (mid,)):
        out[r['section']] = {'text': r['text'], 'by': r['author'], 'at': r['at'], 'revision': r['revision'], 'base_version': r['base_version'], 'id': r['id']}
    return out


def correction_history(desk, mid, section):
    return [dict(r) for r in desk.db.execute('SELECT id,text,author,at,base_version,revision,status FROM fiche_corrections5634 '
                                             'WHERE matter=? AND section=? ORDER BY revision DESC LIMIT 20', (mid, section))]


def _current_revision(desk, mid, section):
    row = desk.db.execute('SELECT MAX(revision) FROM fiche_corrections5634 WHERE matter=? AND section=?', (mid, section)).fetchone()
    return int(row[0] or 0)


def correction_action(desk, mid, section, action, owner='cabinet', ident=None):
    """Conflit avec une nouvelle version de l'agent : garder la correction, adopter la proposition, ou restaurer une correction passée."""
    if section not in SECTIONS:
        raise Stop('rubrique_inconnue')
    current = corrections(desk, mid).get(section)
    latest_version = (latest(desk, mid) or {}).get('version', 0)
    if action == 'garder':
        if not current:
            raise Stop('correction_absente')
        desk.db.execute('UPDATE fiche_corrections5634 SET base_version=? WHERE id=?', (latest_version, current['id']))
        message = 'Votre correction reste la valeur affichée et exportée.'
    elif action == 'adopter':
        if not current:
            raise Stop('correction_absente')
        desk.db.execute("UPDATE fiche_corrections5634 SET status='remplacee_par_agent' WHERE id=?", (current['id'],))
        message = 'Proposition de l’agent adoptée ; votre correction reste dans l’historique.'
    elif action == 'restaurer':
        row = desk.db.execute('SELECT * FROM fiche_corrections5634 WHERE id=? AND matter=? AND section=?', (int(ident or 0), mid, section)).fetchone()
        if not row:
            raise Stop('correction_absente')
        return save_edit(desk, mid, section, row['text'], owner, base=_current_revision(desk, mid, section), origin='restauration')
    else:
        raise Stop('action_invalide')
    desk.db.commit()
    desk.audit('dossier5634_correction_' + action, {'matter': mid, 'section': section})
    return {'message': message}


def parties_from_text(text):
    """Liste corrigée par l'avocat (« nom — qualité — conseil ») en parties structurées."""
    labels = {_fold(v): k for k, v in QUALITES.items()}
    out = []
    for line in str(text or '').splitlines():
        parts = [x.strip() for x in re.split(r'\s+[—–-]\s+', line) if x.strip()]
        if not parts:
            continue
        quality = labels.get(_fold(parts[1])) if len(parts) > 1 else ''
        conseil = next((re.sub(r'(?i)^conseil\s*:\s*', '', x) for x in parts[1:] if _fold(x).startswith('conseil')), '')
        out.append({'nom': parts[0], 'qualite': quality or '', 'conseil': conseil, 'client_du_cabinet': quality == 'client'})
    return out


def _fold(text):
    return ''.join(ch for ch in unicodedata.normalize('NFKD', str(text or '').lower()) if not unicodedata.combining(ch))


def _matter(desk, mid):
    m = next((x for x in load_matters(desk.c) if x['id'] == str(mid or '')), None)
    if not m:
        raise Stop('dossier_absent')
    return m


def latest(desk, mid):
    ensure_schema(desk)
    row = desk.db.execute('SELECT version,data,sources,edits,created FROM fiche5627 WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
    if not row:
        edits = corrections(desk, mid)   # 5.6.34 (B04) : des corrections sans fiche générée restent affichées et exportées
        return {'version': 0, 'data': {}, 'sources': [], 'edits': edits, 'created': ''} if edits else None
    try:
        return {'version': row['version'], 'data': json.loads(row['data']), 'sources': json.loads(row['sources']), 'edits': corrections(desk, mid),
                'created': row['created']}
    except ValueError:
        return None


def version(desk, mid, number):
    """5.6.35 (F12) : une version précise de la fiche (contenu figé, sans les corrections ultérieures)."""
    ensure_schema(desk)
    try:
        number = int(number)
    except (TypeError, ValueError):
        return None
    row = desk.db.execute('SELECT version,data,sources,created FROM fiche5627 WHERE matter=? AND version=?', (mid, number)).fetchone()
    if not row:
        return None
    return {'version': row['version'], 'data': json.loads(row['data']), 'sources': json.loads(row['sources']), 'created': row['created']}


def pending(desk, mid):
    try:
        row = desk.db.execute("SELECT id,status FROM jobs WHERE kind=? AND status IN ('pending','running') AND json_extract(args,'$.matter')=? ORDER BY id DESC LIMIT 1",
                              (JOB, mid)).fetchone()
        return dict(row) if row else None
    except Exception:   # pylint: disable=broad-except
        return None


def last_error(desk, mid):
    try:
        row = desk.db.execute("SELECT result,finished FROM jobs WHERE kind=? AND status='error' AND json_extract(args,'$.matter')=? ORDER BY id DESC LIMIT 1",
                              (JOB, mid)).fetchone()
        if not row:
            return None
        res = json.loads(row['result'] or '{}') or {}
        return {'error': res.get('erreur', 'erreur'), 'detail': res.get('detail') or {}, 'updated': row['finished'] or ''}
    except Exception:   # pylint: disable=broad-except
        return None


def request(desk, mid, with_index=True, only=None, versions=None):
    """Demande (ou rejoint) la préparation de la fiche ; une seule demande en file par dossier.
    5.6.35 (F11) : ``only`` = pièces choisies (chemins de l'index) ; la préparation ne lit alors QUE ces pièces (travail distinct)."""
    _matter(desk, mid)
    ensure_schema(desk)
    if only:
        paths = sorted({str(p) for p in only})[:500]
        job = desk.enqueue(JOB, {'matter': mid, 'index': bool(with_index), 'only': paths, **({'versions': dict(versions)} if versions else {})}, priority=5)
        desk.audit('dossier5627_fiche_demandee', {'matter': mid, 'job': job, 'selection': len(paths)})
        return {'job_id': job, 'message': 'Préparation de la fiche lancée sur %d pièce(s) choisie(s).' % len(paths)}
    # 5.6.34 (B08) : clé d'idempotence atomique par dossier — deux clics, deux onglets ou une reconnexion rejoignent le même travail.
    desk.db.execute('CREATE TABLE IF NOT EXISTS fiche_requests5634(matter TEXT PRIMARY KEY, job INTEGER, at TEXT NOT NULL)')
    desk.db.execute('INSERT OR IGNORE INTO fiche_requests5634(matter,job,at) VALUES(?,NULL,?)', (mid, desk.now()))
    desk.db.commit()
    row = desk.db.execute('SELECT job FROM fiche_requests5634 WHERE matter=?', (mid,)).fetchone()
    old = row['job'] if row else None
    if old is not None:
        state = desk.db.execute('SELECT status FROM jobs WHERE id=?', (old,)).fetchone()
        if state and state['status'] in ('pending', 'running', 'cancel_requested'):
            return {'job_id': old, 'deja_en_cours': True, 'message': 'La fiche du dossier est déjà en préparation : le suivi reprend.'}
    current = pending(desk, mid)
    if current:
        desk.db.execute('UPDATE fiche_requests5634 SET job=? WHERE matter=?', (current['id'], mid))
        desk.db.commit()
        return {'job_id': current['id'], 'deja_en_cours': True, 'message': 'La fiche du dossier est déjà en préparation.'}
    claim = desk.db.execute('UPDATE fiche_requests5634 SET job=-1, at=? WHERE matter=? AND job IS ?', (desk.now(), mid, old))
    desk.db.commit()
    if not claim.rowcount:   # un autre onglet vient de lancer la préparation
        row = desk.db.execute('SELECT job FROM fiche_requests5634 WHERE matter=?', (mid,)).fetchone()
        return {'job_id': row['job'], 'deja_en_cours': True, 'message': 'La fiche du dossier est déjà en préparation.'}
    try:
        job = desk.enqueue(JOB, {'matter': mid, 'index': bool(with_index)}, priority=5)
    except Exception:
        desk.db.execute('UPDATE fiche_requests5634 SET job=? WHERE matter=?', (old, mid))
        desk.db.commit()
        raise
    desk.db.execute('UPDATE fiche_requests5634 SET job=? WHERE matter=?', (job, mid))
    desk.db.commit()
    desk.audit('dossier5627_fiche_demandee', {'matter': mid, 'job': job})
    return {'job_id': job, 'message': 'Préparation de la fiche lancée : lecture des pièces puis rédaction par l’agent (quelques minutes).'}


# ------------------------------------------------------------------------------------------------ sources
def _iso(value):
    """Date de fichier au format AAAA-MM-JJ, que l'index l'ait gardée en ISO ou en date HTTP (« Fri, 13 Mar 2026 14:57:33 GMT »)."""
    value = str(value or '').strip()
    if re.match(r'\d{4}-\d{2}-\d{2}', value):
        return value[:10]
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ''


def _doc_score(path):
    name = _fold(PurePosixPath(path).name)
    score = 1
    for pattern, weight in PRIORITY:
        if re.search(pattern, name):
            score = max(score, weight)
    if re.search(LOW, name):
        score = min(score, 0)
    if re.search(JURISPRUDENCE, name):   # décision citée d'une autre affaire : utile, mais pas un événement du dossier
        score = min(score, 2)
    return score


def select_sources(desk, m, budget=36000):
    """Extraits des pièces du dossier, les plus utiles d'abord (nature de la pièce, puis date), dans un budget de caractères."""
    from .index import DocumentIndex
    try:
        rows = DocumentIndex(desk.c['state_dir']).db.execute(
            "SELECT path,modified,substr(text,1,6000) FROM docs WHERE matter=? AND COALESCE(error,'')='' AND COALESCE(text,'')<>''", (m['id'],)).fetchall()
    except Exception:   # pylint: disable=broad-except
        rows = []
    docs = sorted(((_doc_score(r[0]), _iso(r[1]), r[0], r[2]) for r in rows), key=lambda x: x[1], reverse=True)   # les plus récentes d'abord…
    docs.sort(key=lambda x: -x[0])                                                                                       # …à nature égale
    out, used = [], 0
    for n, (score, modified, path, text) in enumerate(docs, 1):
        size = 5000 if score >= 9 else (3000 if score >= 5 else 1500)
        excerpt = re.sub(r'[ \t]+', ' ', str(text))[:size].strip()
        if not excerpt:
            continue
        if used + len(excerpt) > budget:
            if score >= 9 and budget - used > 1200:
                excerpt = excerpt[:budget - used]
            else:
                continue
        out.append({'id': 'P%d' % (len(out) + 1), 'path': path, 'nom': PurePosixPath(path).name, 'date_fichier': modified[:10],
                    'nature': 'jurisprudence citée' if re.search(JURISPRUDENCE, _fold(PurePosixPath(path).name)) else '', 'extrait': excerpt})
        used += len(excerpt)
        if used >= budget or len(out) >= 40:
            break
    return out


def _validated_facts(desk, mid):
    try:
        from .legal_memory import memory_records
        return [{'id': 'V%d' % n, 'type': r['record_type'], 'texte': (r['title'] + ' — ' + r['content'])[:600], 'date': r['event_date'] or ''}
                for n, r in enumerate(memory_records(desk, mid, ['pinned', 'validated'], 40), 1)]
    except Exception:   # pylint: disable=broad-except
        return []


# ------------------------------------------------------------------------------------------------ préparation
FORMES = r'sa|sas|sasu|sarl|eurl|sci|snc|selarl|selas|scp|gie|scm|scop|association|fondation|gaec|earl'


def _party_identity(name):
    """(forme sociale, dénomination sans forme ni civilité, SIREN) ; la forme sociale fait partie de l'identité (B09)."""
    folded = _fold(name)
    forme = next(iter(re.findall(r'\b(' + FORMES + r')\b', folded)), '')
    siren = next(iter(re.findall(r'\b(\d{3} ?\d{3} ?\d{3})\b', folded)), '').replace(' ', '')
    core = re.sub(r'\b(' + FORMES + r'|ste|societe|monsieur|madame|m|mme|me|maitre)\b|\b\d{3} ?\d{3} ?\d{3}\b|[^a-z0-9 ]', ' ', folded)
    return forme, re.sub(r'\s+', ' ', core).strip(), siren


def _clean(result, valid, sources_info=None):
    """Nettoyage de la fiche produite par l'IA. 5.6.34 : une affirmation dont tous les rattachements sont invalides ne passe pas pour
    un fait documenté (rubrique « À vérifier ») ; une date impossible est refusée ; une date égale à la date du FICHIER cité, absente
    de son extrait, est signalée ; deux personnes morales de formes différentes ne sont jamais fusionnées d'office."""
    from .preuve5634 import normalize_date
    info = sources_info or {}
    juris = {k for k, v in info.items() if v.get('jurisprudence')}
    a_verifier = []

    def ids(x):
        return [i for i in (x or []) if i in valid][:8]
    out = {'resume': str(result.get('resume') or '').strip()[:3000],
           'procedure': {**{k: str(result['procedure'].get(k) or '').strip()[:400] for k in ('juridiction', 'numero_rg', 'stade', 'prochaine_etape')},
                         'source_ids': ids(result['procedure'].get('source_ids'))},
           'enjeux': [str(x).strip()[:400] for x in result.get('enjeux', []) if str(x).strip()][:6],
           'points_a_verifier': [str(x).strip()[:400] for x in result.get('points_a_verifier', []) if str(x).strip()][:8],
           'limits': [str(x).strip()[:300] for x in result.get('limits', []) if str(x).strip()][:6]}
    facts = []
    for p in result.get('rappel_des_faits', []):
        text = str(p.get('texte', '')).strip()[:2500]
        if not text:
            continue
        refs = ids(p.get('source_ids'))
        if refs:
            facts.append({'texte': text, 'source_ids': refs})
        else:
            a_verifier.append({'texte': text, 'cause': 'aucune pièce valide citée' if p.get('source_ids') else 'aucune pièce citée', 'rubrique': 'faits'})
    out['rappel_des_faits'] = facts
    parties, seen, by_core = [], {}, {}
    rapprochements = []
    for p in result.get('parties', []):
        name = re.sub(r'\s+', ' ', str(p.get('nom') or '')).strip()[:200]
        if not name or re.search(r'\b(juge|president|greffi|commissaire de justice|huissier|expert judiciaire|conseiller)\b', _fold(name)):
            continue
        forme, core, siren = _party_identity(name)
        refs = ids(p.get('source_ids'))
        if refs and all(r in juris for r in refs):   # partie d'un litige cité dans une décision : pas une partie du dossier
            a_verifier.append({'texte': name, 'cause': 'partie citée seulement dans une décision d’une autre affaire', 'rubrique': 'parties'})
            continue
        key = siren or (forme, core)
        if key in seen:
            continue
        if core and core in by_core and not siren:
            other = by_core[core]
            if other['forme'] != forme:
                rapprochements.append({'a': other['nom'], 'b': name, 'motif': 'même dénomination, forme sociale différente ou absente : à confirmer'})
        seen[key] = True
        entry = {'nom': name, 'qualite': p['qualite'] if p.get('qualite') in QUALITES else 'autre', 'conseil': str(p.get('conseil') or '').strip()[:200],
                 'client_du_cabinet': bool(p.get('client_du_cabinet')), 'source_ids': refs, 'forme_sociale': forme.upper(), 'siren': siren}
        by_core.setdefault(core, {'nom': name, 'forme': forme})
        parties.append(entry)
    out['parties'] = parties
    out['rapprochements'] = rapprochements[:10]
    events = []
    for ev in result.get('chronologie', []):
        text = str(ev.get('evenement') or '').strip()[:500]
        if not text:
            continue
        refs = ids(ev.get('source_ids'))
        raw = str(ev.get('date') or '').strip()[:40]
        d = normalize_date(raw) if raw else {'valide': True, 'iso': '', 'precision': '', 'origine': '', 'motif': ''}
        if raw and not d['valide']:
            a_verifier.append({'texte': '%s — %s' % (raw, text), 'cause': 'date impossible' if d['motif'] == 'date_impossible' else 'date non reconnue',
                               'rubrique': 'chronologie'})
            continue
        if not refs:
            a_verifier.append({'texte': '%s — %s' % (raw or 'date inconnue', text), 'cause': 'aucune pièce valide citée', 'rubrique': 'chronologie'})
            continue
        if d['iso'] and refs and all(info.get(r, {}).get('date_fichier') == d['iso'] and d['iso'] not in info.get(r, {}).get('dates', set()) for r in refs):
            a_verifier.append({'texte': '%s — %s' % (raw, text), 'cause': 'date identique à la date du fichier et absente du texte cité', 'rubrique': 'chronologie'})
            continue
        events.append({'date': d['iso'], 'date_origine': raw if raw != d['iso'] else '', 'precision': d['precision'], 'evenement': text, 'source_ids': refs})
    out['chronologie'] = sorted(events, key=lambda x: (x['date'] == '', x['date']))
    out['a_verifier'] = a_verifier[:30]
    return out


def prepare(desk, args):
    """Travail « fiche_dossier5627 » : indexation (facultative), choix des pièces, rédaction par le modèle, enregistrement versionné."""
    mid = str(args.get('matter') or '')
    m = _matter(desk, mid)
    ensure_schema(desk)
    from .erreurs5634 import stage
    if args.get('index'):
        with stage('inventaire', 'nextcloud' if not (desk.c.get('nextcloud') or {}).get('local_path') else 'stockage_local'):
            for _ in range(6):
                res = desk.perform('index', {'matter': mid}) or {}
                if not res.get('en_attente'):
                    break
    from .model import Model, routed_config, validate, FICHE5627
    with stage('modele_show', 'ollama'):
        cfg = routed_config(desk.c, 'legal_analysis')
    limit = int(cfg.get('max_context_chars') or desk.c.get('ollama', {}).get('max_context_chars', 65000))
    previous = latest(desk, mid)
    corrections = {k: v.get('text', '') for k, v in ((previous or {}).get('edits') or {}).items() if isinstance(v, dict)}
    # 5.6.32 : dossier ENTIER — fiches de pièces (une par version de pièce) et compression étagée ; les fiches manquantes sont préparées.
    from . import synthese5632
    budget = max(8000, int(limit * 0.6))
    only = set(args.get('only') or []) or None   # 5.6.35 (F11) : sélection de pièces (application Nextcloud) = filtre réel
    changed = []
    if only and args.get('versions'):   # 5.6.37 (audit F04) : version revérifiée avant lecture ; pièce modifiée = retirée et signalée
        from .integration5634 import _norm_etag
        now = {d['path']: _norm_etag(d['etag']) for d in synthese5632.inventory(desk, mid)}
        changed = sorted(p for p, e in args['versions'].items() if p in only and now.get(p) != _norm_etag(e))
        only = only - set(changed)
        if not only:
            raise Stop('pieces_modifiees_depuis_la_selection')
    with stage('extraction', 'index'):
        sources, coverage = synthese5632.sources_for(desk, m, budget, score=_doc_score, only=only)
    if coverage['sans_fiche']:
        desk._then('digest_pieces5632', {'matter': mid}, priority=75)
    facts = _validated_facts(desk, mid)
    if not sources and not facts:
        raise Stop('aucune_piece_lisible_dans_le_dossier')
    payload = {'dossier': {'reference': m['id'], 'nom': matter_display(m), 'client_du_cabinet': m.get('client_name', '')},
               'date_du_jour': datetime.now(timezone.utc).date().isoformat(), 'corrections_de_l_avocat': corrections,
               'consignes_permanentes_du_dossier': __import__('agent.pause5629', fromlist=['consignes']).consignes(desk, mid),
               'faits_valides_par_avocat': facts, 'pieces': [{k: s[k] for k in ('id', 'nom', 'date_fichier', 'nature', 'extrait')} for s in sources]}
    while len(json.dumps(payload, ensure_ascii=False)) > limit - 4000 and len(payload['pieces']) > 3:
        payload['pieces'].pop()
    kept = {p['id'] for p in payload['pieces']}
    sources = [s for s in sources if s['id'] in kept]
    try:
        with stage('generation', 'ollama'):
            result = Model(cfg).ask('dossier_fiche', payload)
    except Stop as ex:
        if str(ex) not in ('contexte_ia_insuffisant', 'generation_ia_incomplete'):
            raise
        # 5.6.32 : une seule relance, entrée compressée de moitié (toutes les pièces restent représentées, avec moins de détail).
        sources, coverage = synthese5632.sources_for(desk, m, budget // 2, score=_doc_score, only=only)
        payload['pieces'] = [{k: s[k] for k in ('id', 'nom', 'date_fichier', 'nature', 'extrait')} for s in sources]
        kept = {p['id'] for p in payload['pieces']}
        with stage('generation', 'ollama'):
            result = Model(cfg).ask('dossier_fiche', payload)
    validate(result, FICHE5627)
    from .preuve5634 import dates_in_text
    info = {s['id']: {'date_fichier': s.get('date_fichier', ''), 'dates': dates_in_text(s.get('extrait', '')),
                      'jurisprudence': bool(re.search(JURISPRUDENCE, _fold(PurePosixPath(s['path']).name)))} for s in sources}
    data = _clean(result, kept | {f['id'] for f in facts}, info)
    coverage = synthese5632.coverage(desk, mid, sources, only=only)   # 5.6.34 (B05) : bilan recalculé après compression et relance
    if only:
        coverage['selection'] = {'demandees': len(only) + len(changed), 'trouvees': len({d['path'] for d in synthese5632.inventory(desk, mid)} & only),
                                 'modifiees_depuis_selection': changed}
    data['pieces_lues'] = len(sources)   # compatibilité : nombre de sources transmises (libellé exact dans la vue)
    data['couverture'] = coverage
    data['generation'] = {'profil': cfg.get('profile', ''), 'version_profil': cfg.get('profile_version', 0), 'modele': cfg.get('model', ''),
                          'temperature': cfg.get('temperature'), 'sources': [{'path': s['path'], 'version': s.get('version', ''), 'niveau': s.get('niveau')} for s in sources]}
    version = desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM fiche5627 WHERE matter=?', (mid,)).fetchone()[0]
    stored = [{'id': s['id'], 'path': s['path'], 'nom': s['nom']} for s in sources] + [{'id': f['id'], 'path': '', 'nom': 'Fait validé par l’avocat'} for f in facts]
    with stage('enregistrement', 'base'):
        desk.db.execute('INSERT INTO fiche5627(matter,version,data,sources,edits,created) VALUES(?,?,?,?,?,?)',
                        (mid, version, json.dumps(data, ensure_ascii=False), json.dumps(stored, ensure_ascii=False), '{}', desk.now()))
        desk.db.commit()
    desk.audit('dossier5627_fiche_preparee', {'matter': mid, 'version': version, 'pieces': len(sources)})
    return {'status': 'prepared', 'matter': mid, 'version': version, 'pieces': len(sources),
            'coverage': coverage,
            'message': 'Fiche du dossier préparée (version %d) : %s.' % (version, synthese5632.coverage_label(coverage))}


def save_edit(desk, mid, section, text, owner='cabinet', base=None, origin='avocat'):
    """Correction de l'avocat (résumé, rappel des faits, parties) : enregistrement INDÉPENDANT des versions produites par l'IA (B04),
    appliqué dans toutes les vues, exports et missions ; deux corrections concurrentes produisent un conflit explicite."""
    if section not in SECTIONS:
        raise Stop('rubrique_inconnue')
    text = str(text or '').replace('\r\n', '\n').strip()
    if len(text) > 20000:
        raise Stop('texte_trop_long_20000_maximum')
    _matter(desk, mid)
    ensure_schema(desk)
    revision = _current_revision(desk, mid, section)
    if base not in (None, '') and int(base) != revision:
        raise Stop('conflit_de_correction')
    current = latest(desk, mid)
    version = current['version'] if current else 0
    desk.db.execute("UPDATE fiche_corrections5634 SET status='remplacee' WHERE matter=? AND section=? AND status='active'", (mid, section))
    desk.db.execute('INSERT INTO fiche_corrections5634(matter,section,text,author,at,base_version,revision,status,origin) VALUES(?,?,?,?,?,?,?,?,?)',
                    (mid, section, text, owner, desk.now(), version, revision + 1, 'active' if text else 'retiree', origin))
    desk.db.commit()
    desk.audit('dossier5627_fiche_corrigee', {'matter': mid, 'section': section, 'revision': revision + 1})
    return {'message': 'Correction enregistrée : elle reste affichée et exportée lors des prochaines mises à jour de la fiche.' if text else
            'Correction retirée : la rédaction de l’agent est de nouveau affichée.', 'revision': revision + 1}


# ------------------------------------------------------------------------------------------------ agent
def ask_agent(desk, mid, text, kind='question'):
    """Instruction de l'avocat dans le dossier : question (réponse sourcée) ou document à préparer (dans « À relire »)."""
    _matter(desk, mid)
    text = re.sub(r'[ \t]+', ' ', str(text or '')).strip()
    if kind not in dict(KINDS):
        raise Stop('type_de_demande_invalide')
    from . import cockpit530
    if kind == 'question':
        return cockpit530.ask(desk, {'text': text, 'matter': mid})
    if not 8 <= len(text) <= 4000:
        raise Stop('instruction_vide_ou_trop_longue')
    from . import docrequest520 as dr
    out = dr.submit(desk, text, mid, kind if kind in dr.KINDS else 'auto')
    cockpit530.ensure_schema(desk)
    now = desk.now()
    desk.db.execute('INSERT INTO cockpit530_messages(role,text,matter,ref,created) VALUES(?,?,?,?,?)', ('user', text, mid, '', now))
    desk.db.execute('INSERT INTO cockpit530_messages(role,text,matter,ref,created) VALUES(?,?,?,?,?)',
                    ('agent', 'Compris : je prépare ce document à partir des pièces, des courriels et de l’agenda du dossier. Il arrivera dans « À relire ».',
                     mid, 'docreq:' + out['request'], now))
    desk.db.commit()
    desk.audit('dossier5627_instruction', {'matter': mid, 'kind': kind})
    return {'message': 'Instruction transmise à l’agent.', 'matter': mid, 'ref': 'docreq:' + out['request']}


def thread_html(desk, prefix, mid, limit=8):
    """Échanges avec l'agent pour ce dossier, du plus ancien au plus récent ; (html, en attente ?)."""
    from . import cockpit530
    cockpit530.ensure_schema(desk)
    rows = list(reversed(desk.db.execute('SELECT * FROM cockpit530_messages WHERE matter=? ORDER BY id DESC LIMIT ?', (mid, limit)).fetchall()))
    if not rows:
        return ('<p class="d27-muted">Demandez à l’agent un courrier, une note, des conclusions ou posez une question sur ce dossier. '
                'Il travaille sur les pièces, les courriels et l’agenda du dossier ; rien n’est envoyé sans vous.</p>'), False
    out, waiting = [], False
    for r in rows:
        if r['role'] == 'user':
            out.append('<div class="d27-msg me"><p>%s</p><small>%s</small></div>' % (e(r['text']), e(str(r['created'])[:16].replace('T', ' '))))
            continue
        follow, wait = cockpit530._follow_up(desk, prefix, r['ref']) if r['ref'] else ('', False)   # pylint: disable=protected-access
        waiting = waiting or wait
        out.append('<div class="d27-msg agent"><p>%s</p>%s</div>' % (e(r['text']), ('<div class="d27-follow">%s</div>' % follow) if follow else ''))
    return ''.join(out), waiting


# ------------------------------------------------------------------------------------------------ rendu
def _a(prefix, href, label, cls=''):
    return '<a%s href="%s">%s</a>' % ((' class="%s"' % cls) if cls else '', e(prefix + href, quote=True), e(label))


def _refs(prefix, mid, ids, sources):
    from .fiche460 import doc_href
    by = {s['id']: s for s in sources}
    out = []
    for i in ids or []:
        s = by.get(i)
        if not s:
            continue
        if s.get('path'):
            out.append('<a class="d27-ref" href="%s" title="%s">%s</a>' % (e(prefix + doc_href(s['path'], mid), quote=True), e(s['nom'], quote=True), e(_short(s['nom']))))
        else:
            out.append('<span class="d27-ref" title="%s">fait validé</span>' % e(s['nom'], quote=True))
    return ('<span class="d27-refs">%s</span>' % ''.join(out)) if out else ''


def _short(name):
    stem = re.sub(r'\.(pdf|docx?|odt|txt|eml|msg|jpe?g|png)$', '', str(name), flags=re.I)
    return stem if len(stem) <= 42 else stem[:40] + '…'


def _date_fr(value):
    value = str(value or '')
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', value)
    if m:
        return '%s/%s/%s' % (m.group(3), m.group(2), m.group(1))
    m = re.fullmatch(r'(\d{4})-(\d{2})', value)
    return ('%s/%s' % (m.group(2), m.group(1))) if m else value


def _edit_block(section, label, text, rows=6, edits=None):
    revision = ((edits or {}).get(section) or {}).get('revision', 0)
    return ('<form class="d27-edit" data-d27-section="%s" data-d34-revision="' + str(int(revision)) + '" hidden><label><span>%s</span><textarea rows="%d" maxlength="20000">%s</textarea></label>'
            '<div class="d27-actions"><button class="ax-btn" type="submit">Enregistrer ma correction</button>'
            '<button class="ax-btn ghost" type="button" data-d27-cancel>Annuler</button></div></form>') % (e(section, quote=True), e(label), rows, e(text))


def _card(title, body, extra='', cls=''):
    return '<section class="d27-card%s"><div class="d27-card-head"><h2>%s</h2>%s</div>%s</section>' % ((' ' + cls) if cls else '', e(title), extra, body)


def _edit_button(section):
    return '<button type="button" class="d27-icon" data-d27-edit="%s" title="Corriger" aria-label="Corriger">✎</button>' % e(section, quote=True)


def _counts(desk, mid):
    out = {'pieces': 0, 'courriels': 0, 'travaux': 0}
    try:
        from .index import DocumentIndex
        out['pieces'] = DocumentIndex(desk.c['state_dir']).db.execute('SELECT COUNT(*) FROM docs WHERE matter=?', (mid,)).fetchone()[0]
    except Exception:   # pylint: disable=broad-except
        pass
    try:
        out['courriels'] = desk.db.execute('SELECT COUNT(*) FROM work_items WHERE matter=?', (mid,)).fetchone()[0]
    except Exception:   # pylint: disable=broad-except
        pass
    try:
        out['travaux'] = desk.db.execute('SELECT COUNT(*) FROM docreq520 WHERE matter=?', (mid,)).fetchone()[0]
    except Exception:   # pylint: disable=broad-except
        pass
    return out


def _next_deadline(desk, mid):
    try:
        from .fiche460 import build_fiche
        return build_fiche(desk, mid).get('next_deadline')
    except Exception:   # pylint: disable=broad-except
        return None


def header_html(desk, prefix, m, view, counts):
    mid = m['id']
    q = lambda v: '/dossier?' + urlencode({'id': mid, **({'vue': v} if v != 'fiche' else {})})
    tabs = (('fiche', 'Fiche', None), ('pieces', 'Pièces', counts['pieces']), ('courriels', 'Courriels', counts['courriels']), ('travaux', 'Travaux de l’agent', counts['travaux']))
    more = ''.join('<li>%s</li>' % _a(prefix, href, label) for href, label in (
        ('/fiche?' + urlencode({'matter': mid}), 'Vue détaillée par rubriques'), ('/fiche?' + urlencode({'matter': mid, 'onglet': 'faits'}), 'Faits proposés à valider'),
        ('/chronologie?' + urlencode({'matter': mid}), 'Chronologie complète'), ('/strategy?' + urlencode({'matter': mid}), 'Stratégie'),
        ('/modeles-word?' + urlencode({'matter': mid}), 'Créer un courrier Word'), ('/documents?' + urlencode({'matter': mid}), 'Documents du dossier'),
        ('/matter?' + urlencode({'id': mid}), 'Ancienne page (surveillance, mémoire, réglages)')))
    from .pause5629 import is_paused
    on_pause = is_paused(desk, mid)
    pause_btn = ('<button type="button" class="ax-btn d27-resume" data-d27-pause="0" title="L’IA reprend le travail automatique sur ce dossier">▶ Reprendre l’IA</button>'
                 if on_pause else
                 '<button type="button" class="ax-btn ghost d27-pausebtn" data-d27-pause="1" title="Plus aucun travail automatique de l’IA sur ce dossier">⏸ Mettre l’IA en pause</button>')
    banner = ('<p class="d27-paused" role="status">⏸ <strong>IA en pause sur ce dossier.</strong> Aucun travail automatique (projets, brouillons de '
              'réponse, mises à jour) n’est lancé ; vos demandes restent possibles.</p>') if on_pause else ''
    return ('<div class="d27" data-d27-matter="%s">'
            '<nav class="d27-back">%s</nav>'
            '<header class="d27-head"><div class="d27-title"><h1>%s</h1><p class="d27-muted">Référence %s%s</p></div>'
            '<div class="d27-head-actions">' + pause_btn + '<button type="button" class="ax-btn" data-d27-prepare>Mettre à jour la fiche</button>'
            '<details class="d27-more"><summary class="ax-btn ghost">Plus ▾</summary><ul>%s</ul></details></div></header>'
            '' + banner + '<nav class="d27-tabs" aria-label="Vues du dossier">%s</nav>') % (
        e(mid, quote=True), _a(prefix, '/dossiers', '← Tous les dossiers'), e(matter_display(m)), e(mid),
        (' · client : ' + e(m['client_name'])) if m.get('client_name') else '', more,
        ''.join('<a href="%s"%s>%s%s</a>' % (e(prefix + q(key), quote=True), ' aria-current="page"' if key == view else '', e(label),
                                             (' <span class="d27-count">%d</span>' % n) if n else '') for key, label, n in tabs))


def _proposal_box(desk, mid, section, edits, f, generated):
    """B04 : la fiche a été régénérée APRÈS la correction et l'agent propose autre chose : les deux versions, leur origine, le choix."""
    c = edits.get(section)
    if not c or not f or f['version'] <= int(c.get('base_version') or 0) or not str(generated or '').strip():
        return ''
    if ' '.join(str(generated).split()) == ' '.join(str(c['text']).split()):
        return ''
    return ('<div class="d34-conflict" role="note"><p><strong>L’agent propose une autre rédaction</strong> (version %d, issue des pièces) ; '
            'votre correction du %s reste affichée et exportée tant que vous ne choisissez pas.</p><details><summary>Voir la proposition de l’agent</summary>'
            '<div class="d34-proposal">%s</div></details><div class="d27-actions"><button type="button" class="ax-btn ghost" data-d34-correction="garder" '
            'data-section="%s">Garder ma correction</button><button type="button" class="ax-btn ghost" data-d34-correction="adopter" data-section="%s">'
            'Adopter la proposition</button></div></div>') % (f['version'], _date_fr(str(c['at'])[:10]), e(generated).replace('\n', '<br>'), e(section, quote=True), e(section, quote=True))


def _history_box(desk, mid, section):
    rows = [r for r in correction_history(desk, mid, section) if r['status'] != 'active']
    if not rows:
        return ''
    return ('<details class="d34-history"><summary>Historique des corrections (%d)</summary><ul>%s</ul></details>' % (len(rows), ''.join(
        '<li><small>%s · %s · %s</small><p>%s</p><button type="button" class="ax-btn ghost" data-d34-correction="restaurer" data-section="%s" data-id="%d">'
        'Restaurer</button></li>' % (e(_date_fr(str(r['at'])[:10])), e(r['author']), e({'remplacee': 'remplacée', 'retiree': 'retirée',
                                                                                        'remplacee_par_agent': 'remplacée par la proposition de l’agent'}.get(r['status'], r['status'])),
                                     e(r['text'][:400]), e(section, quote=True), r['id']) for r in rows[:10])))


def fiche_html(desk, prefix, m):
    mid = m['id']
    f = latest(desk, mid)
    job = pending(desk, mid)
    err = last_error(desk, mid) if not job else None
    status = ''
    if job:
        status = '<p class="d27-status" role="status" data-d27-waiting>⏳ L’agent prépare la fiche du dossier (lecture des pièces puis rédaction)… la page se mettra à jour.</p>'
    elif err and (not f or str(err.get('updated') or '') > str(f['created'])):
        from .erreurs5634 import explain
        status = '<p class="d27-status bad" role="alert">La dernière préparation a échoué : %s La dernière fiche valable reste affichée.</p>' % e(explain(str(err.get('error') or 'erreur'), err.get('detail')))
    data = (f or {}).get('data') or {}
    sources = (f or {}).get('sources') or []
    ensure_schema(desk)
    edits = corrections(desk, mid)   # 5.6.34 (B04) : couche de corrections, indépendante des versions de l'IA
    from .synthese5632 import coverage_label
    when = ('Préparée le %s · version %d · %s' % (_date_fr(str(f['created'])[:10]), f['version'], coverage_label(data.get('couverture') or {'representees': data.get('pieces_lues', 0), 'pieces': data.get('pieces_lues', 0)}))) if f and data.get('pieces_lues') is not None else ''
    # --- résumé
    if edits.get('resume'):
        resume = '<p>%s</p><p class="d27-note">Corrigé par vous le %s.</p>' % (e(edits['resume']['text']).replace('\n', '<br>'), _date_fr(str(edits['resume']['at'])[:10]))
        resume += _proposal_box(desk, mid, 'resume', edits, f, data.get('resume', ''))
    elif data.get('resume'):
        resume = '<p>%s</p>' % e(data['resume'])
    else:
        resume = ('<div class="d27-empty"><p>La fiche de ce dossier n’a pas encore été préparée.</p><p>L’agent lit les actes (assignation, conclusions, '
                  'décisions, contrats…) et rédige un résumé, le rappel des faits, les parties et la chronologie, avec les pièces citées.</p>'
                  '<button type="button" class="ax-btn" data-d27-prepare>Préparer la fiche du dossier</button></div>')
    resume_text = (edits.get('resume') or {}).get('text') or data.get('resume', '')
    from .synthese5632 import METHOD
    used = (data.get('couverture') or {}).get('methode')
    if f and data and used != METHOD:   # 5.6.35 (audit F01–F02) : preuves contrôlées par une méthode antérieure, plus permissive
        status += ('<p class="notice">Cette fiche a été établie avec une méthode de contrôle des preuves antérieure (%s) : dates et montants '
                   'peuvent y être marqués « documentés » sans figurer dans l’extrait cité. Relancez « Préparer la fiche du dossier » pour '
                   'recontrôler les preuves ; vos corrections sont conservées.</p>') % e(used or 'avant 5.6.34')
    main = _card('De quoi s’agit-il ?', status + resume + _history_box(desk, mid, 'resume') + _edit_block('resume', 'Résumé du dossier', resume_text, 5, edits),
                 ('<span class="d27-muted">%s</span>' % e(when) if when else '') + _edit_button('resume'), 'd27-summary')
    # --- rappel des faits
    if edits.get('faits'):
        faits = ''.join('<p>%s</p>' % e(p) for p in re.split(r'\n\s*\n', edits['faits']['text']) if p.strip()) + '<p class="d27-note">Rappel corrigé par vous.</p>'
        faits += _proposal_box(desk, mid, 'faits', edits, f, '\n\n'.join(p['texte'] for p in data.get('rappel_des_faits') or []))
        faits_text = edits['faits']['text']
    elif data.get('rappel_des_faits'):
        faits = ''.join('<p>%s %s</p>' % (e(p['texte']), _refs(prefix, mid, p['source_ids'], sources)) for p in data['rappel_des_faits'])
        faits_text = '\n\n'.join(p['texte'] for p in data['rappel_des_faits'])
    else:
        faits, faits_text = '<p class="d27-muted">Le rappel des faits apparaîtra ici une fois la fiche préparée.</p>', ''
    main += _card('Rappel des faits', faits + _history_box(desk, mid, 'faits') + _edit_block('faits', 'Rappel des faits (un paragraphe par bloc, séparés par une ligne vide)', faits_text, 14, edits),
                  _edit_button('faits'))
    # --- chronologie
    events = data.get('chronologie') or []
    if events:
        chrono = '<ol class="d27-chrono">%s</ol>' % ''.join('<li><time>%s</time><span>%s %s</span></li>' % (
            e(_date_fr(ev['date']) or 'date inconnue'), e(ev['evenement']), _refs(prefix, mid, ev['source_ids'], sources)) for ev in events)
    else:
        chrono = '<p class="d27-muted">Les événements déterminants du dossier (actes, audiences, décisions) apparaîtront ici.</p>'
    main += _card('Chronologie clé', chrono, _a(prefix, '/chronologie?' + urlencode({'matter': mid}), 'Chronologie complète', 'd27-link'))
    if data.get('a_verifier'):   # 5.6.34 (B10) : affirmations sans source valide, dates impossibles — jamais présentées comme documentées
        main += _card('À vérifier (non confirmé par une pièce)', '<p class="d27-muted">Ces éléments proposés par l’agent ne sont rattachés à aucune pièce '
                      'valable ou portent une date douteuse : ils ne figurent ni dans le rappel des faits ni dans la chronologie.</p><ul class="d27-check">%s</ul>' % ''.join(
            '<li>%s <span class="d27-badge bad">%s</span></li>' % (e(x['texte']), e(x['cause'])) for x in data['a_verifier']))
    if data.get('enjeux') or data.get('points_a_verifier'):
        body = ''
        if data.get('enjeux'):
            body += '<h3>Enjeux</h3><ul>%s</ul>' % ''.join('<li>%s</li>' % e(x) for x in data['enjeux'])
        if data.get('points_a_verifier'):
            body += '<h3>À vérifier</h3><ul class="d27-check">%s</ul>' % ''.join('<li>%s</li>' % e(x) for x in data['points_a_verifier'])
        main += _card('Enjeux et points à vérifier', body)
    # --- colonne de droite : agent, parties, procédure, courriels
    thread, waiting = thread_html(desk, prefix, mid)
    options = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in KINDS)
    chips = ''.join('<button type="button" class="d27-chip" data-d27-kind="%s" data-d27-text="%s">%s</button>' % (k, e(t, quote=True), e(label)) for k, label, t in SHORTCUTS)
    side = _card('Travailler avec l’agent', (
        '<div class="d27-thread" id="d27-thread" aria-live="polite"%s>%s</div>'
        '<form class="d27-ask" id="d27-ask"><div class="d27-chips">%s</div>'
        '<label class="d27-sr" for="d27-text">Votre demande</label><textarea id="d27-text" name="text" rows="3" maxlength="4000" '
        'placeholder="Exemple : prépare une réponse au confrère sur la communication des pièces 12 à 15."></textarea>'
        '<div class="d27-ask-row"><select name="kind" aria-label="Type de demande">%s</select><button class="ax-btn" type="submit">Envoyer à l’agent</button></div>'
        '<p class="d27-muted">Les documents arrivent dans « À relire » ; rien n’est envoyé sans vous.</p></form>') % (
        ' data-d27-waiting' if waiting else '', thread, chips, options), '', 'd27-agent')
    parties_edit = (edits.get('parties') or {}).get('text')
    if parties_edit:
        parties = '<ul class="d27-parties">%s</ul><p class="d27-note">Liste corrigée par vous.</p>' % ''.join('<li>%s</li>' % e(l) for l in parties_edit.splitlines() if l.strip())
        parties_text = parties_edit
    elif data.get('parties'):
        rows = []
        for p in sorted(data['parties'], key=lambda p: not p['client_du_cabinet']):
            rows.append('<li%s><strong>%s</strong>%s<br><span class="d27-muted">%s%s</span> %s</li>' % (
                ' class="d27-client"' if p['client_du_cabinet'] else '', e(p['nom']), ' <span class="d27-badge">client</span>' if p['client_du_cabinet'] else '',
                e(QUALITES.get(p['qualite'], 'Partie')), (' · conseil : ' + e(p['conseil'])) if p['conseil'] else '', _refs(prefix, mid, p['source_ids'], sources)))
        parties = '<ul class="d27-parties">%s</ul>' % ''.join(rows)
        parties_text = '\n'.join('%s — %s%s' % (p['nom'], QUALITES.get(p['qualite'], 'Partie'), (' — conseil : ' + p['conseil']) if p['conseil'] else '') for p in data['parties'])
    else:
        client = m.get('client_name') or ''
        parties = '<ul class="d27-parties"><li class="d27-client"><strong>%s</strong> <span class="d27-badge">client</span></li></ul>' % e(client) if client else '<p class="d27-muted">Parties à identifier.</p>'
        parties_text = (client + ' — Client') if client else ''
    from .pause5629 import consignes, blocked, FAILURES
    cons = consignes(desk, mid)
    side += _card('Consignes pour l’agent', (
        '<p class="d27-muted">Suivies par l’agent pour chaque travail sur ce dossier (projets, réponses, fiche).</p>'
        '<form class="d27-consignes" data-d27-consignes><textarea rows="4" maxlength="4000" aria-label="Consignes pour l’agent sur ce dossier" '
        'placeholder="Ex. : ne plus préparer de projet sans ma demande ; ton ferme avec le confrère ; attendre le jugement avant toute relance.">%s</textarea>'
        '<div class="d27-actions"><button class="ax-btn" type="submit">Enregistrer les consignes</button></div></form>') % e(cons))
    stuck = blocked(desk, mid)
    if stuck:
        from .web440 import human
        from .web import JOB_LABELS
        side += _card('Relances suspendues', '<p class="d27-muted">Ces travaux automatiques ont échoué %d fois de suite : l’agent ne les relance plus seul.</p>'
                      '<ul class="d27-stuck">%s</ul>' % (FAILURES, ''.join(
                          '<li><strong>%s</strong><br><span class="d27-muted">%s</span> <button type="button" class="ax-btn ghost" data-d27-reset="%s">Réautoriser</button></li>' % (
                              e(JOB_LABELS.get(s['kind'], s['kind'])), e(human(s['error']) if s['error'] else 'échec'), e(s['kind'], quote=True)) for s in stuck)))
    if parties_edit:
        parties += _proposal_box(desk, mid, 'parties', edits, f, '\n'.join(p['nom'] for p in data.get('parties') or []))
    if data.get('rapprochements') and not parties_edit:   # 5.6.34 (B09) : rapprochement proposé, jamais décidé d'office
        decided = {(r['a'], r['b']) for r in desk.db.execute('SELECT a,b FROM party_links5634 WHERE matter=?', (mid,))}
        todo = [r for r in data['rapprochements'] if (r['a'], r['b']) not in decided]
        if todo:
            parties += '<div class="d34-merge"><p class="d27-muted">Rapprochements à confirmer :</p><ul>%s</ul></div>' % ''.join(
                '<li>%s / %s <small>(%s)</small><br><button type="button" class="ax-btn ghost" data-d34-merge="meme_partie" data-a="%s" data-b="%s">Même partie</button>'
                '<button type="button" class="ax-btn ghost" data-d34-merge="distinctes" data-a="%s" data-b="%s">Parties distinctes</button></li>' % (
                    e(r['a']), e(r['b']), e(r['motif']), e(r['a'], quote=True), e(r['b'], quote=True), e(r['a'], quote=True), e(r['b'], quote=True)) for r in todo)
    side += _card('Parties', parties + _history_box(desk, mid, 'parties') + _edit_block('parties', 'Une partie par ligne : nom — qualité — conseil', parties_text, 6, edits), _edit_button('parties'))
    proc = data.get('procedure') or {}
    nd = _next_deadline(desk, mid)
    rows = [(label, proc.get(key)) for key, label in (('juridiction', 'Juridiction'), ('numero_rg', 'N° RG'), ('stade', 'Stade'), ('prochaine_etape', 'Prochaine étape'))]
    if nd:
        rows.append(('Prochaine échéance', '%s — %s%s' % (nd['due_label'], nd['label'], '' if nd.get('validated') else ' (à confirmer)')))
    proc_html = '<dl class="d27-dl">%s</dl>' % ''.join('<dt>%s</dt><dd>%s</dd>' % (e(k), e(v)) for k, v in rows if v) if any(v for _, v in rows) else \
        '<p class="d27-muted">Juridiction, numéro RG et stade apparaîtront ici.</p>'
    side += _card('Procédure', proc_html + _refs(prefix, mid, proc.get('source_ids'), sources))
    mails = []
    try:
        mails = desk.db.execute('SELECT subject,sender,received,state FROM work_items WHERE matter=? ORDER BY COALESCE(received,updated) DESC LIMIT 5', (mid,)).fetchall()
    except Exception:   # pylint: disable=broad-except
        pass
    side += _card('Derniers courriels', ('<ul class="d27-mails">%s</ul>' % ''.join('<li><strong>%s</strong><br><span class="d27-muted">%s · %s</span></li>' % (
        e(r['subject'] or 'sans objet'), e(str(r['sender'] or '')[:60]), e(_date_fr(str(r['received'] or '')[:10]))) for r in mails)) if mails else
        '<p class="d27-muted">Aucun courriel rattaché à ce dossier.</p>', _a(prefix, '/courriels?vue=boite', 'Messagerie', 'd27-link'))
    if data.get('limits'):
        main += '<p class="d27-muted d27-limits">Limites : %s</p>' % e(' ; '.join(data['limits']))
    return '<div class="d27-grid"><div class="d27-main">%s</div><div class="d27-side" role="complementary" aria-label="Agent, parties et procédure">%s</div></div>' % (main, side)


def pieces_html(desk, prefix, m):
    from .fiche460 import doc_href
    from .index import DocumentIndex
    try:
        rows = DocumentIndex(desk.c['state_dir']).db.execute('SELECT path,modified,error FROM docs WHERE matter=? LIMIT 2000', (m['id'],)).fetchall()
    except Exception:   # pylint: disable=broad-except
        rows = []
    docs = sorted(({'path': r[0], 'name': PurePosixPath(r[0]).name, 'modified': _iso(r[1]), 'error': r[2] or ''} for r in rows), key=lambda d: d['modified'], reverse=True)
    # 5.6.32 : provenance des pièces d'après les dossiers de travail (Nos pièces, Pièces adverses — partie).
    from . import dossiers5632
    try:
        resolved = dossiers5632.resolve(desk, m)
    except Exception:   # noqa: BLE001
        resolved = []
    for d in docs:
        d['usage'] = dossiers5632.usage_of(desk, m, d['path'], resolved) if resolved else None
    rows = ''.join('<li data-d27-search="%s"><a href="%s">%s</a><span class="d27-muted">%s</span>%s%s</li>' % (
        e(_fold(d['name'] + ' ' + (d['usage']['label'] if d['usage'] else '')), quote=True), e(prefix + doc_href(d['path'], m['id']), quote=True), e(d['name']),
        e(_date_fr(d['modified'])), (' <span class="d27-badge">%s</span>' % e(d['usage']['label'])) if d['usage'] else '',
        ' <span class="d27-badge bad">illisible</span>' if d['error'] else '') for d in docs)
    return _card('Pièces du dossier (%d)' % len(docs), (
        '<input type="search" class="d27-filter" data-d27-filter="#d27-pieces" placeholder="Filtrer par nom" aria-label="Filtrer les pièces">'
        '<ul class="d27-list" id="d27-pieces">%s</ul>') % (rows or '<li class="d27-muted">Aucune pièce indexée : cliquez sur « Mettre à jour la fiche ».</li>'),
        _a(prefix, '/documents?' + urlencode({'matter': m['id']}), 'Gérer les documents', 'd27-link')) + (
        '<section class="d27-card"><div class="d27-card-head"><h2>Registre et écritures</h2></div><p class="d27-muted">Commandes du dossier '
        '(anciennement dans Recherche juridique).</p><div class="d27-actions"><button type="button" class="ax-btn ghost" data-m34-post="pieces/ecritures" '
        'data-matter="%s">Identifier les dernières écritures</button><button type="button" class="ax-btn ghost" data-m34-post="pieces/registre" data-matter="%s">'
        'Actualiser le registre des pièces</button></div><p role="status" class="m34-status"></p></section>') % (e(m['id'], quote=True), e(m['id'], quote=True))


STATE_LABELS = {'draft_ready': 'Brouillon prêt à relire', 'done': 'Traité', 'ignored': 'Ignoré', 'needs_decision': 'Décision à prendre', 'error': 'Erreur',
                'new': 'Nouveau', 'resolved': 'Traité'}


def courriels_html(desk, prefix, m):
    try:
        rows = desk.db.execute('SELECT subject,sender,received,state FROM work_items WHERE matter=? ORDER BY COALESCE(received,updated) DESC LIMIT 200', (m['id'],)).fetchall()
    except Exception:   # pylint: disable=broad-except
        rows = []
    body = ''.join('<li><strong>%s</strong><span class="d27-muted">%s · %s · %s</span></li>' % (
        e(r['subject'] or 'sans objet'), e(str(r['sender'] or '')[:80]), e(_date_fr(str(r['received'] or '')[:10])), e(STATE_LABELS.get(r['state'], r['state'] or ''))) for r in rows)
    return _card('Courriels rattachés (%d)' % len(rows), '<ul class="d27-list">%s</ul>' % (body or '<li class="d27-muted">Aucun courriel rattaché à ce dossier.</li>'),
                 _a(prefix, '/courriels?vue=boite', 'Ouvrir la messagerie', 'd27-link'))


DOC_STATUS = {'cree': 'Prêt à relire', 'en_cours': 'En rédaction', 'en_attente': 'En file', 'echec': 'Échec', 'annule': 'Arrêté', 'dossier_a_choisir': 'Dossier à préciser'}


def travaux_html(desk, prefix, m):
    from .fiche460 import doc_href
    try:
        rows = desk.db.execute('SELECT request,kind,status,path,updated FROM docreq520 WHERE matter=? ORDER BY updated DESC LIMIT 100', (m['id'],)).fetchall()
    except Exception:   # pylint: disable=broad-except
        rows = []
    body = ''.join('<li><strong>%s</strong><span class="d27-muted">%s · %s</span>%s</li>' % (
        e(r['request'][:180]), e(DOC_STATUS.get(r['status'], r['status'])), e(_date_fr(str(r['updated'])[:10])),
        (' ' + '<a href="%s">%s</a>' % (e(prefix + doc_href(r['path'], m['id']), quote=True), e(PurePosixPath(r['path']).name))) if r['path'] else '') for r in rows)
    thread, waiting = thread_html(desk, prefix, m['id'], 30)
    return (_card('Documents préparés par l’agent', '<ul class="d27-list">%s</ul>' % (body or '<li class="d27-muted">Aucun document demandé pour ce dossier.</li>'))
            + _card('Échanges avec l’agent', '<div class="d27-thread" id="d27-thread"%s>%s</div>' % (' data-d27-waiting' if waiting else '', thread)))


def page(desk, auth, prefix, args, shell):
    mid = str(args.get('id') or args.get('matter') or '')
    m = _matter(desk, mid)
    view = str(args.get('vue') or 'fiche')
    if view not in ('fiche', 'pieces', 'courriels', 'travaux'):
        view = 'fiche'
    counts = _counts(desk, mid)
    body = header_html(desk, prefix, m, view, counts)
    body += {'fiche': fiche_html, 'pieces': pieces_html, 'courriels': courriels_html, 'travaux': travaux_html}[view](desk, prefix, m)
    if view == 'pieces':   # 5.6.32 (R15) : documents de procédure → agenda → brouillons, dans la page du dossier
        from .web5632 import procedure_panel, working_folders_panel
        body += working_folders_panel(desk, prefix, m)
        body += procedure_panel(desk, prefix, m)
        body += ('<p class="d5636-link"><a class="ax-btn ghost" href="%s">Données des pièces (types, parties, dates, montants, textes cités)</a></p>'
                 % e(prefix + '/pieces-donnees?dossier=' + m['id'], quote=True))   # 5.6.36 : GED
    if view == 'fiche':   # 5.6.36 : intervenants du dossier (contacts & CRM)
        try:
            from .crm5636 import matter_block
            body += matter_block(desk, prefix, m['id'])
        except Stop:
            pass
    body += '</div>'
    head = '<script defer src="%s/static/v5627.js"></script>' % e(prefix)
    return shell(matter_display(m), body, prefix, auth['csrf'], '/dossiers', head)


# ------------------------------------------------------------------------------------------------ API
def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5627/'):].split('?', 1)[0]
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    if method == 'GET':
        mid = str(args.get('matter') or '')
        if route == 'fil':
            html, waiting = thread_html(desk, prefix, _matter(desk, mid)['id'], int(args.get('n') or 8) if str(args.get('n') or '8').isdigit() else 8)
            return json_out({'html': html, 'waiting': waiting})
        if route == 'etat':   # 5.6.34 (B08) : état explicite du travail suivi (job_id, étape, échec qualifié)
            f = latest(desk, _matter(desk, mid)['id'])
            job = pending(desk, mid)
            out = {'pending': bool(job), 'version': f['version'] if f else 0, 'state': 'running' if job and job['status'] == 'running' else ('pending' if job else '')}
            if job:
                row = desk.db.execute('SELECT progress FROM jobs WHERE id=?', (job['id'],)).fetchone()
                out.update(job_id=job['id'], step=str((row['progress'] if row else '') or ''))
            else:
                row = desk.db.execute("SELECT id,status,result FROM jobs WHERE kind=? AND json_extract(args,'$.matter')=? ORDER BY id DESC LIMIT 1", (JOB, mid)).fetchone()
                if row:
                    res = json.loads(row['result'] or '{}') or {}
                    out.update(job_id=row['id'], state={'done': 'succeeded', 'error': 'failed', 'cancelled': 'cancelled'}.get(row['status'], row['status']))
                    if row['status'] == 'error':
                        from .erreurs5634 import explain
                        out['message'] = explain(res.get('erreur', 'erreur'), res.get('detail'))
            return json_out(out)
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    mid = str(data.get('matter') or '')
    if route == 'fiche/preparer':
        return json_out(request(desk, mid))
    if route == 'fiche/corriger':
        return json_out(save_edit(desk, _matter(desk, mid)['id'], str(data.get('section') or ''), data.get('text', ''), owner, base=data.get('revision')))
    if route == 'fiche/correction':   # 5.6.34 (B04) : garder, adopter la proposition, restaurer
        return json_out(correction_action(desk, _matter(desk, mid)['id'], str(data.get('section') or ''), str(data.get('action') or ''), owner, data.get('id')))
    if route == 'fiche/rapprocher':   # 5.6.34 (B09) : rapprochement de deux parties, seulement après confirmation de l'avocat
        ensure_schema(desk)
        a, b, decision = str(data.get('a') or '')[:200], str(data.get('b') or '')[:200], str(data.get('decision') or '')
        if not a or not b or decision not in ('meme_partie', 'distinctes'):
            raise Stop('rapprochement_invalide')
        desk.db.execute('INSERT OR REPLACE INTO party_links5634 VALUES(?,?,?,?,?,?)', (_matter(desk, mid)['id'], a, b, decision, owner, desk.now()))
        desk.db.commit()
        return json_out({'message': 'Rapprochement confirmé : une seule partie.' if decision == 'meme_partie' else 'Deux parties distinctes conservées.'})
    if route == 'pause':   # 5.6.29
        from .pause5629 import set_pause
        return json_out(set_pause(desk, _matter(desk, mid)['id'], str(data.get('on')) in ('1', 'true', 'True'), owner))
    if route == 'consignes':
        from .pause5629 import set_consignes
        return json_out(set_consignes(desk, _matter(desk, mid)['id'], data.get('text', ''), owner))
    if route == 'reautoriser':
        from .pause5629 import reset
        return json_out(reset(desk, str(data.get('kind') or ''), _matter(desk, mid)['id'], owner))
    if route == 'agent':
        return json_out(ask_agent(desk, mid, data.get('text', ''), str(data.get('kind') or 'question')))
    raise Stop('route_inconnue')
