"""5.6.51 (audit 5.6.50, A08) : apprentissage à partir des corrections, à portée choisie et réversible.

Après une correction utile, trois portées sont proposées : « Ce document », « Ce dossier », « Mon style habituel ». Une correction
factuelle reste locale par défaut ; une règle générale du cabinet n'est jamais créée ici (elle exige une portée explicite et les droits
correspondants, dans les réglages d'apprentissage existants).

- Catégories distinctes : exactitude factuelle, attribution, classement, stratégie, style, mise en forme. Une correction de faits,
  d'attribution, de classement ou de stratégie ne peut pas devenir une préférence de style (« Mon style ») ; une position négociée dans
  une affaire ne devient pas une doctrine générale.
- « Ce dossier » réutilise le mécanisme existant des règles métier (portée dossier, ``learning410``) : visible, désactivable, versionné.
- « Mon style habituel » est propre au compte ; l'exemple conservé est **expurgé** des données du dossier (noms des parties et alias,
  nombres, dates, adresses électroniques) avant d'être réutilisé ailleurs.
- Chaque règle garde texte avant/après, catégorie, contexte, auteur, portée, date, version ; désactiver une règle cesse son injection.
- Cas d'évaluation : chaque correction peut devenir un cas de référence ; une part est réservée à l'évaluation. Les campagnes
  conservent le modèle, la configuration et les versions employés pour comparer deux configurations sur les mêmes cas.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

from .common import Stop, fold

SCHEMA = '''
CREATE TABLE IF NOT EXISTS d51_learned(id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_value TEXT NOT NULL, owner TEXT NOT NULL, category TEXT NOT NULL,
  before TEXT NOT NULL DEFAULT '', after TEXT NOT NULL DEFAULT '', instruction TEXT NOT NULL, context TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1, rule410 INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS d51_learned_scope ON d51_learned(scope, scope_value, status);
CREATE TABLE IF NOT EXISTS d51_eval_cases(id TEXT PRIMARY KEY, category TEXT NOT NULL, task TEXT NOT NULL, input TEXT NOT NULL, expected TEXT NOT NULL,
  reserved INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d51_eval_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, model TEXT NOT NULL, config TEXT NOT NULL,
  cases INTEGER NOT NULL, passed INTEGER NOT NULL, details TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL);
'''
SCOPES = {'document': 'Ce document', 'dossier': 'Ce dossier', 'style': 'Mon style habituel'}
CATEGORIES = {'exactitude': 'Exactitude factuelle', 'attribution': 'Attribution', 'classement': 'Classement', 'strategie': 'Stratégie',
              'style': 'Style', 'mise_en_forme': 'Mise en forme'}
STYLE_CATEGORIES = ('style', 'mise_en_forme')
LOCAL_DEFAULT = ('exactitude', 'attribution', 'classement')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def redact(desk, mid, text):
    """Exemple de style expurgé des données du dossier : parties et alias, montants et nombres, dates, adresses, courriels."""
    out = str(text or '')
    names = set()
    try:
        from . import modele5651
        for p in modele5651.parties(desk, mid, include_merged=True):
            names.add(p['display_name'])
            names.update(a['alias'] for a in p['aliases'])
    except Exception:   # noqa: BLE001
        pass
    try:
        from .common import load_matters
        m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
        if m and m.get('client_name'):
            names.add(m['client_name'])
    except Exception:   # noqa: BLE001
        pass
    for n in sorted((x for x in names if x and len(x) >= 3), key=len, reverse=True):
        out = re.sub(re.escape(n), '[PARTIE]', out, flags=re.I)
        for token in re.findall(r'[A-ZÀ-Ý][\w’\'-]{3,}', n):
            if token.lower() not in ('madame', 'monsieur', 'societe', 'société'):
                out = re.sub(r'\b%s\b' % re.escape(token), '[PARTIE]', out, flags=re.I)
    out = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '[COURRIEL]', out)
    out = re.sub(r'\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b', '[DATE]', out)
    out = re.sub(r'\d[\d\s., \xa0]*\d|\d', '[NOMBRE]', out)
    return out


def learn(desk, author, data):
    """Enregistre une correction avec la portée choisie ; renvoie la règle (ou refuse une portée incompatible)."""
    ensure_schema(desk)
    scope = str(data.get('portee') or '')
    category = str(data.get('categorie') or '')
    if scope not in SCOPES:
        raise Stop('portee_apprentissage_invalide')
    if category not in CATEGORIES:
        raise Stop('categorie_correction_invalide')
    if scope == 'style' and category not in STYLE_CATEGORIES:
        raise Stop('correction_factuelle_hors_style')     # une correction de faits ne devient jamais une préférence de style
    mid = str(data.get('matter') or '')
    before = str(data.get('avant') or '')[:4000]
    after = str(data.get('apres') or '')[:4000]
    instruction = re.sub(r'\s+', ' ', str(data.get('consigne') or '')).strip()[:1000]
    if not instruction and not after:
        raise Stop('correction_vide')
    if scope in ('document', 'dossier') and not mid:
        raise Stop('dossier_requis')
    scope_value = {'document': str(data.get('document') or ''), 'dossier': mid, 'style': author}[scope]
    if scope == 'document' and not scope_value:
        raise Stop('document_requis')
    if scope == 'style':          # l'exemple quitte le dossier : expurgé
        before, after, instruction = redact(desk, mid, before), redact(desk, mid, after), redact(desk, mid, instruction)
        mid_kept = ''
    else:
        mid_kept = mid
    if not instruction:
        instruction = 'Préférer la formulation corrigée : « %s »' % after[:600]
    now = _now()
    ident = hashlib.sha256('|'.join((scope, scope_value, category, instruction, now)).encode()).hexdigest()[:24]
    version = 1 + desk.db.execute('SELECT COUNT(*) FROM d51_learned WHERE scope=? AND scope_value=? AND category=?', (scope, scope_value, category)).fetchone()[0]
    rule410 = None
    if scope == 'dossier':        # mécanisme existant des règles métier (portée dossier) : injecté dans les rédactions du dossier
        from .learning410 import save_rule
        rtype = {'style': 'style', 'mise_en_forme': 'structure', 'strategie': 'legal_position'}.get(category, 'other')
        rule410 = save_rule(desk, 'matter', mid, 'all', rtype, instruction, 'correction5651', ident)['id']
    desk.db.execute('INSERT INTO d51_learned(id,scope,scope_value,owner,category,before,after,instruction,context,matter,version,rule410,created,updated) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, scope, scope_value, author, category, before, after, instruction,
                                                          str(data.get('contexte') or '')[:500] if scope != 'style' else '', mid_kept, version, rule410, now, now))
    desk.db.commit()
    if data.get('cas_reference') in (True, 'true', 'on', '1'):
        add_case(desk, category, str(data.get('tache') or 'redaction'), before, after, source=ident)
    return dict(desk.db.execute('SELECT * FROM d51_learned WHERE id=?', (ident,)).fetchone())


def resolve_rule(desk, ident):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM d51_learned WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('regle_absente')
    return row


def authorize_rule(row, author, expected_matter, admin=False):
    """5.6.52 (audit 5.6.51, F01) : une règle de dossier ou de document appartient à SON dossier, comparé au dossier autorisé de la
    requête (jamais l'inverse) ; une règle de style appartient à son auteur. Refus sans effet ni contenu (« règle absente »)."""
    if row['scope'] == 'style':
        if row['owner'] != author and not admin:
            raise Stop('role_insuffisant')
        return
    if not expected_matter or str(row['matter']) != str(expected_matter):
        raise Stop('regle_absente')
    from .membres5650 import viewer_hidden
    if str(row['matter']) in (viewer_hidden() or frozenset()):
        raise Stop('regle_absente')


def set_status(desk, ident, author, status, admin=False, expected_matter=None, expected_version=None):
    """Désactiver, réactiver ou annuler (retirer) une règle : objet résolu sur le serveur, dossier réel contrôlé, version attendue."""
    if status not in ('active', 'desactivee', 'annulee'):
        raise Stop('etat_regle_invalide')
    row = resolve_rule(desk, ident)
    authorize_rule(row, author, expected_matter, admin)
    version = row['version'] if expected_version in (None, '') else int(expected_version)
    cur = desk.db.execute('UPDATE d51_learned SET status=?,version=version+1,updated=? WHERE id=? AND version=?', (status, _now(), ident, version))
    if not cur.rowcount:
        raise Stop('version_en_conflit')
    if row['rule410']:
        from .learning410 import set_rule_status
        try:
            set_rule_status(desk, row['rule410'], 'active' if status == 'active' else ('archived' if status == 'annulee' else 'paused'))
        except Stop:
            pass
    desk.db.commit()
    return dict(desk.db.execute('SELECT * FROM d51_learned WHERE id=?', (ident,)).fetchone())


def rules(desk, owner, mid='', include_inactive=True):
    ensure_schema(desk)
    sql = ("SELECT * FROM d51_learned WHERE ((scope='style' AND scope_value=?) OR (scope IN ('dossier','document') AND matter=?))" +
           ('' if include_inactive else " AND status='active'") + ' ORDER BY updated DESC')
    return [dict(r) for r in desk.db.execute(sql, (owner, mid))]


def rules_for(desk, mid, owner, document=''):
    """Règles injectées dans une rédaction : celles du compte (style), du dossier et, s'il y a lieu, du document ; actives seulement."""
    ensure_schema(desk)
    out = []
    for r in desk.db.execute("SELECT * FROM d51_learned WHERE status='active' AND ((scope='style' AND scope_value=?) OR (scope='dossier' AND scope_value=?) "
                             "OR (scope='document' AND matter=? AND scope_value=?)) ORDER BY scope, updated DESC LIMIT 30", (owner, mid, mid, document or '\x00')):
        out.append({'portee': SCOPES[r['scope']], 'categorie': CATEGORIES[r['category']], 'consigne': r['instruction'],
                    **({'exemple_avant': r['before'][:600], 'exemple_apres': r['after'][:600]} if r['scope'] == 'style' and r['after'] else {})})
    return out


# ------------------------------------------------------------------------------------------------ cas d'évaluation
def add_case(desk, category, task, input_text, expected, reserved=None, source=''):
    ensure_schema(desk)
    if category not in CATEGORIES:
        raise Stop('categorie_correction_invalide')
    ident = hashlib.sha256('|'.join((category, task, input_text, expected)).encode()).hexdigest()[:24]
    if reserved is None:          # environ un cas sur cinq réservé à l'évaluation, de façon déterministe
        reserved = int(ident[:2], 16) % 5 == 0
    desk.db.execute('INSERT OR IGNORE INTO d51_eval_cases(id,category,task,input,expected,reserved,source,created) VALUES(?,?,?,?,?,?,?,?)',
                    (ident, category, task[:60], str(input_text)[:20000], str(expected)[:20000], 1 if reserved else 0, source, _now()))
    desk.db.commit()
    return ident


def _score(expected, produced):
    """Critère déterministe minimal : part des éléments significatifs attendus (nombres, dates, noms propres, mots longs) présents."""
    keys = set(re.findall(r'\d[\d\s.,]*\d|\d|[A-ZÀ-Ý][\w-]{3,}|\b\w{9,}\b', expected))
    if not keys:
        return 1.0
    found = sum(1 for k in keys if fold(k) in fold(produced))
    return found / len(keys)


def evaluate(desk, label, runner, model_name, config, reserved_only=True, threshold=0.8):
    """Campagne sur les mêmes cas : ``runner(case) -> texte``. Le succès d'un format JSON n'est pas un critère de qualité juridique."""
    ensure_schema(desk)
    cases = [dict(r) for r in desk.db.execute('SELECT * FROM d51_eval_cases' + (' WHERE reserved=1' if reserved_only else '') + ' ORDER BY id')]
    details, passed = [], 0
    for c in cases:
        try:
            produced = str(runner(c) or '')
            score = _score(c['expected'], produced)
        except Exception as ex:   # noqa: BLE001 — un cas en échec compte comme échoué, la campagne continue
            produced, score = 'erreur : %s' % str(ex)[:120], 0.0
        ok = score >= threshold
        passed += ok
        details.append({'case': c['id'], 'category': c['category'], 'score': round(score, 3), 'ok': ok})
    desk.db.execute('INSERT INTO d51_eval_runs(label,model,config,cases,passed,details,created) VALUES(?,?,?,?,?,?,?)',
                    (label[:120], str(model_name)[:120], json.dumps(config, ensure_ascii=False, default=str)[:4000], len(cases), passed,
                     json.dumps(details, ensure_ascii=False), _now()))
    desk.db.commit()
    return {'cases': len(cases), 'passed': passed, 'details': details}


def compare_runs(desk, a, b):
    ensure_schema(desk)
    ra = desk.db.execute('SELECT * FROM d51_eval_runs WHERE id=?', (int(a),)).fetchone()
    rb = desk.db.execute('SELECT * FROM d51_eval_runs WHERE id=?', (int(b),)).fetchone()
    if not ra or not rb:
        raise Stop('campagne_absente')
    sa = {d['case']: d for d in json.loads(ra['details'])}
    sb = {d['case']: d for d in json.loads(rb['details'])}
    common = sorted(set(sa) & set(sb))
    return {'common_cases': len(common), 'a': {'model': ra['model'], 'passed': sum(sa[c]['ok'] for c in common)},
            'b': {'model': rb['model'], 'passed': sum(sb[c]['ok'] for c in common)},
            'regressions': [c for c in common if sa[c]['ok'] and not sb[c]['ok']], 'improvements': [c for c in common if sb[c]['ok'] and not sa[c]['ok']]}
