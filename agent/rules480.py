"""Règles « toujours faire comme ça » 4.8.0.

Une correction de l'avocat devient une règle lisible en une phrase, limitée à un périmètre (dossier, destinataire,
type de courriel ou profil de ton), suspendable et supprimable. Garde-fous :
- aucune règle n'est créée ni activée sans confirmation explicite (jamais silencieusement) ;
- une règle ne s'applique que dans son périmètre ;
- à périmètre égal, deux règles contradictoires sont toutes deux écartées et signalées ; sinon la plus précise
  prévaut (destinataire > dossier > profil > type) ;
- une règle ne porte que sur la forme (formules, longueur, registre, consigne rédigée par l'avocat), jamais sur un fait.
"""
import re

from .common import Stop, load_matters
from . import style480, tone480

SCOPES = ('destinataire', 'dossier', 'profil', 'type')
SPECIFICITY = {'destinataire': 4, 'dossier': 3, 'profil': 2, 'type': 1}
TYPES = ('formule_ouverture', 'formule_fermeture', 'longueur_max', 'registre', 'consigne')
STATUSES = ('active', 'suspendue')
INTENTS = {'appointment': 'rendez-vous', 'status': 'point sur le dossier', 'documents': 'pièces et documents',
           'administrative': 'administratif', 'clarification': 'demande de précisions'}
MAX_RULES = 200
NEGATION = (' ne ', ' jamais ', ' interdit ', ' sans ', ' pas ', ' éviter ', ' ne pas ')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS rules480(
  id INTEGER PRIMARY KEY, status TEXT NOT NULL, scope TEXT NOT NULL, scope_value TEXT NOT NULL, rule_type TEXT NOT NULL,
  value TEXT NOT NULL, sentence TEXT NOT NULL, origin TEXT NOT NULL, origin_ref TEXT NOT NULL DEFAULT '',
  evidence INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, confirmed TEXT NOT NULL, updated TEXT NOT NULL,
  uses INTEGER NOT NULL DEFAULT 0, last_used TEXT NOT NULL DEFAULT '');
'''


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()
    tone480.ensure_schema(desk)


# ------------------------------------------------------------------ phrases
def _matter_name(desk, matter_id):
    try:
        for m in load_matters(desk.c):
            if m.get('id') == matter_id:
                return str(m.get('client_name') or matter_id)
    except Exception:
        pass
    return matter_id


def scope_text(desk, scope, value):
    if scope == 'dossier':
        return 'Dans le dossier « %s »' % _matter_name(desk, value)
    if scope == 'destinataire':
        return 'Pour les courriels adressés à %s' % value
    if scope == 'profil':
        label = tone480.DEFAULTS.get(value, {}).get('label', value).lower()
        return 'Pour les courriels à un destinataire de profil « %s »' % label
    if scope == 'type':
        return 'Pour les courriels de type « %s »' % INTENTS.get(value, value)
    raise Stop('portee_regle_invalide')


def action_text(rule_type, value):
    if rule_type == 'formule_ouverture':
        return 'commencer par « %s »' % value
    if rule_type == 'formule_fermeture':
        return 'terminer par « %s »' % value
    if rule_type == 'longueur_max':
        return 'ne pas dépasser %s mots' % value
    if rule_type == 'registre':
        return 'vouvoyer' if value == 'vous' else 'tutoyer'
    return value[:1].lower() + value[1:] if value else ''


def sentence(desk, scope, scope_value, rule_type, value):
    return '%s : toujours %s.' % (scope_text(desk, scope, scope_value), action_text(rule_type, value))


# ------------------------------------------------------------------ validation / cycle de vie
def _validate(desk, scope, scope_value, rule_type, value):
    if scope not in SCOPES or rule_type not in TYPES:
        raise Stop('regle_invalide')
    scope_value = str(scope_value or '').strip()
    value = str(value or '').strip()
    if scope == 'dossier':
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', scope_value):
            raise Stop('dossier_invalide')
        if not any(m.get('id') == scope_value for m in load_matters(desk.c)):
            raise Stop('dossier_absent')
    elif scope == 'destinataire':
        scope_value = scope_value.lower()
        if not tone480._email(scope_value):
            raise Stop('adresse_invalide')
    elif scope == 'profil':
        if scope_value not in tone480.PROFILES:
            raise Stop('profil_de_ton_inconnu')
    elif scope == 'type':
        if scope_value not in INTENTS:
            raise Stop('type_courriel_invalide')
    if rule_type == 'formule_ouverture':
        if value not in {x[0] for x in style480.OPENINGS}:
            raise Stop('regle_invalide')
    elif rule_type == 'formule_fermeture':
        if value not in {x[0] for x in style480.CLOSINGS}:
            raise Stop('regle_invalide')
    elif rule_type == 'longueur_max':
        if not value.isdigit() or not 30 <= int(value) <= 800:
            raise Stop('regle_invalide')
        value = str(int(value))
    elif rule_type == 'registre':
        if value not in ('vous', 'tu'):
            raise Stop('regle_invalide')
    else:
        if not 5 <= len(value) <= 300 or '\x00' in value or '\n' in value:
            raise Stop('regle_invalide')
    return scope_value, value


def create(desk, rule_type, value, scope, scope_value, confirm='', origin='manuelle', origin_ref='', evidence=0):
    ensure_schema(desk)
    if str(confirm) != 'yes':
        raise Stop('confirmation_regle_requise')
    if origin not in ('manuelle', 'correction'):
        origin = 'manuelle'
    scope_value, value = _validate(desk, scope, scope_value, rule_type, value)
    if desk.db.execute('SELECT COUNT(*) FROM rules480').fetchone()[0] >= MAX_RULES:
        raise Stop('trop_de_regles')
    twin = desk.db.execute('SELECT id FROM rules480 WHERE scope=? AND scope_value=? AND rule_type=? AND value=?',
                           (scope, scope_value, rule_type, value)).fetchone()
    if twin:
        desk.db.execute("UPDATE rules480 SET status='active',updated=? WHERE id=?", (desk.now(), twin['id']))
        desk.db.commit()
        return {'id': twin['id'], 'created': False, 'sentence': desk.db.execute('SELECT sentence FROM rules480 WHERE id=?', (twin['id'],)).fetchone()[0],
                'conflicts_with': conflicts_with(desk, twin['id'])}
    stamp = desk.now()
    text = sentence(desk, scope, scope_value, rule_type, value)
    cur = desk.db.execute('''INSERT INTO rules480(status,scope,scope_value,rule_type,value,sentence,origin,origin_ref,evidence,
      created,confirmed,updated) VALUES('active',?,?,?,?,?,?,?,?,?,?,?)''',
                          (scope, scope_value, rule_type, value, text, origin, str(origin_ref)[:120], int(evidence), stamp, stamp, stamp))
    desk.db.commit()
    desk.audit('regle_480_creee', {'id': cur.lastrowid, 'scope': scope, 'rule_type': rule_type, 'origin': origin})
    return {'id': cur.lastrowid, 'created': True, 'sentence': text, 'conflicts_with': conflicts_with(desk, cur.lastrowid)}


def _rule(desk, rule_id):
    try:
        rule_id = int(rule_id)
    except (TypeError, ValueError):
        raise Stop('regle_absente') from None
    row = desk.db.execute('SELECT * FROM rules480 WHERE id=?', (rule_id,)).fetchone()
    if not row:
        raise Stop('regle_absente')
    return row


def set_status(desk, rule_id, status):
    ensure_schema(desk)
    row = _rule(desk, rule_id)
    if status not in STATUSES:
        raise Stop('etat_regle_invalide')
    desk.db.execute('UPDATE rules480 SET status=?,updated=? WHERE id=?', (status, desk.now(), row['id']))
    desk.db.commit()
    desk.audit('regle_480_etat', {'id': row['id'], 'status': status})
    return {'id': row['id'], 'status': status}


def delete(desk, rule_id):
    ensure_schema(desk)
    row = _rule(desk, rule_id)
    desk.db.execute('DELETE FROM rules480 WHERE id=?', (row['id'],))
    desk.db.commit()
    desk.audit('regle_480_supprimee', {'id': row['id'], 'scope': row['scope'], 'rule_type': row['rule_type']})
    return {'id': row['id'], 'deleted': True}


def _polarity(text):
    padded = ' ' + text.lower() + ' '
    return any(n in padded for n in NEGATION)


def _words(text):
    return set(re.findall(r'[a-zà-ÿ]{4,}', text.lower()))


def _contradict(a, b):
    """Deux règles actives de même périmètre se contredisent."""
    if a['scope'] != b['scope'] or a['scope_value'] != b['scope_value'] or a['rule_type'] != b['rule_type']:
        return False
    if a['rule_type'] != 'consigne':
        return a['value'] != b['value']
    wa, wb = _words(a['value']), _words(b['value'])
    return len(wa & wb) / max(1, len(wa | wb)) >= 0.25 and _polarity(a['value']) != _polarity(b['value'])


def conflicts_with(desk, rule_id):
    row = _rule(desk, rule_id)
    return [r['id'] for r in desk.db.execute("SELECT * FROM rules480 WHERE status='active' AND id<>?", (row['id'],)) if _contradict(row, r)]


def listing(desk):
    ensure_schema(desk)
    rows = [dict(r) for r in desk.db.execute('SELECT * FROM rules480 ORDER BY CASE status WHEN \'active\' THEN 0 ELSE 1 END, id DESC')]
    active = [r for r in rows if r['status'] == 'active']
    for r in rows:
        r['conflicts'] = [o['id'] for o in active if o['id'] != r['id'] and r['status'] == 'active' and _contradict(r, o)]
    return rows


# ------------------------------------------------------------------ application
def matter_for(desk, recipients):
    """Dossier unique dont tous les destinataires sont des correspondants enregistrés ('' sinon)."""
    wanted = {tone480._email(x) for x in recipients or [] if tone480._email(x)}
    if not wanted:
        return ''
    found = []
    try:
        for m in load_matters(desk.c):
            mails = {str(p.get('email', '')).lower() for p in m.get('correspondents', [])}
            if wanted <= mails:
                found.append(m['id'])
    except Exception:
        return ''
    return found[0] if len(found) == 1 else ''


def applicable(desk, matter, recipients, intent='', profile=None):
    """Règles actives dont le périmètre contient ce courriel, après arbitrage."""
    ensure_schema(desk)
    wanted = {tone480._email(x) for x in recipients or [] if tone480._email(x)}
    matches = []
    for r in desk.db.execute("SELECT * FROM rules480 WHERE status='active'"):
        if ((r['scope'] == 'dossier' and r['scope_value'] == matter and matter) or
                (r['scope'] == 'destinataire' and r['scope_value'] in wanted) or
                (r['scope'] == 'type' and r['scope_value'] == intent and intent) or
                (r['scope'] == 'profil' and r['scope_value'] == profile and profile)):
            matches.append(dict(r))
    applied, withheld = [], []
    for rule_type in TYPES:
        group = [r for r in matches if r['rule_type'] == rule_type]
        if not group:
            continue
        if rule_type == 'consigne':
            for r in group:
                if any(_contradict(r, o) for o in group if o is not r):
                    withheld.append({**r, 'reason': 'Contredite par une autre règle de même périmètre : arbitrage nécessaire.'})
                else:
                    applied.append(r)
            continue
        top = max(SPECIFICITY[r['scope']] for r in group)
        best = [r for r in group if SPECIFICITY[r['scope']] == top]
        for r in group:
            if SPECIFICITY[r['scope']] < top:
                withheld.append({**r, 'reason': 'Écartée : une règle plus précise s’applique.'})
        if len({r['value'] for r in best}) > 1:
            for r in best:
                withheld.append({**r, 'reason': 'Contredite par une autre règle de même périmètre : arbitrage nécessaire.'})
        else:
            applied.append(best[0])
    return {'applied': applied, 'withheld': withheld}


def violations(applied, body):
    out = []
    for r in applied:
        t, v = r['rule_type'], r['value']
        if t == 'formule_ouverture' and style480.opening(body) != v:
            out.append('Règle %d : le courriel ne commence pas par « %s ».' % (r['id'], v))
        elif t == 'formule_fermeture' and style480.closing(body) != v:
            out.append('Règle %d : le courriel ne se termine pas par « %s ».' % (r['id'], v))
        elif t == 'longueur_max' and len(style480._words(style480.core(body))) > int(v):
            out.append('Règle %d : le courriel dépasse %s mots.' % (r['id'], v))
        elif t == 'registre':
            current = style480.register(body)
            if current not in ('neutre', v):
                out.append('Règle %d : le registre attendu est « %s ».' % (r['id'], 'vous' if v == 'vous' else 'tu'))
    return out


def prepare(desk, payload, matter, recipients, intent=''):
    """Ajoute à la requête de rédaction le profil de ton et les règles applicables. Retourne un résumé traçable."""
    try:
        ensure_schema(desk)
        tone = tone480.context(desk, matter, recipients)
        profile = tone['profil'] if tone else None
        result = applicable(desk, matter, recipients, intent, profile)
        if tone:
            payload['profil_de_ton'] = tone
        if result['applied']:
            payload['regles_du_cabinet'] = [{'id': r['id'], 'regle': r['sentence'], 'type': r['rule_type'], 'valeur': r['value']}
                                            for r in result['applied']]
            stamp = desk.now()
            for r in result['applied']:
                desk.db.execute('UPDATE rules480 SET uses=uses+1,last_used=? WHERE id=?', (stamp, r['id']))
            desk.db.commit()
        from . import trace480
        trace480.note(regles=[r['id'] for r in result['applied']], regles_ecartees=[r['id'] for r in result['withheld']],
                      profil_de_ton=profile or '')
        return {'applied': [r['id'] for r in result['applied']], 'withheld': [r['id'] for r in result['withheld']],
                'profile': profile, 'rules': result['applied']}
    except Stop:
        return {'applied': [], 'withheld': [], 'profile': None, 'rules': []}


# ------------------------------------------------------------------ proposition depuis une correction
def _evidence(desk, cat, old, new):
    try:
        rows = desk.db.execute("SELECT changes FROM outcomes480 WHERE outcome<>'tel_quel'").fetchall()
    except Exception:
        return 0
    import json
    count = 0
    for r in rows:
        if any(c['cat'] == cat and c['from'] == old and c['to'] == new for c in json.loads(r['changes'] or '[]')):
            count += 1
    return count


def candidates_from_correction(desk, original, corrected, recipients=(), matter='', intent=''):
    """Règles possibles déduites d'une correction. Rien n'est créé : l'avocat choisit le périmètre et confirme."""
    ensure_schema(desk)
    original, corrected = str(original or ''), str(corrected or '')
    if not corrected.strip():
        raise Stop('texte_vide')
    recipients = [tone480._email(x) for x in recipients or [] if tone480._email(x)]
    matter = matter or matter_for(desk, recipients)
    detected = tone480.detect(desk, matter, recipients) if recipients else {'profile': None}
    found = []
    for c in style480.changes(original, corrected):
        cat, old, new = c['cat'], c['from'], c['to']
        if cat == 'formule_ouverture' and new != 'Aucune formule':
            found.append(('formule_ouverture', new, cat, old, new))
        elif cat == 'formule_fermeture' and new != 'Aucune formule':
            found.append(('formule_fermeture', new, cat, old, new))
        elif cat == 'registre':
            found.append(('registre', new, cat, old, new))
        elif cat == 'plus_court':
            limit = max(30, min(800, int(round(int(new) / 10.0)) * 10 + 10))
            found.append(('longueur_max', str(limit), cat, old, new))
        elif cat == 'politesse_retiree':
            found.append(('consigne', 'Ne pas écrire « %s »' % old, cat, old, new))
        elif cat == 'politesse_ajoutee':
            found.append(('consigne', 'Inclure la formule « %s »' % new, cat, old, new))
    out = []
    for rule_type, value, cat, old, new in found:
        evidence = _evidence(desk, cat, old, new)
        options = []
        for r in recipients[:1] if len(recipients) == 1 else []:
            options.append(('destinataire', r))
        if matter:
            options.append(('dossier', matter))
        if detected.get('profile'):
            options.append(('profil', detected['profile']))
        if intent in INTENTS:
            options.append(('type', intent))
        built = []
        for scope, scope_value in options:
            try:
                scope_value, norm = _validate(desk, scope, scope_value, rule_type, value)
            except Stop:
                continue
            built.append({'scope': scope, 'scope_value': scope_value, 'sentence': sentence(desk, scope, scope_value, rule_type, norm)})
        if not built:
            continue
        preferred = 'profil' if evidence >= 3 and any(o['scope'] == 'profil' for o in built) else \
            'dossier' if any(o['scope'] == 'dossier' for o in built) else built[0]['scope']
        out.append({'rule_type': rule_type, 'value': value, 'label': style480.LABELS.get(cat, cat),
                    'from': old, 'to': new, 'evidence_count': evidence, 'options': built,
                    'recommended_scope': preferred,
                    'warning': ('Cas isolé : aucun envoi antérieur ne montre la même correction. Gardez un périmètre étroit.'
                                if evidence < 2 else 'Même correction observée dans %d envoi(s) antérieur(s).' % evidence)})
    return {'matter': matter, 'profile': detected.get('profile'), 'candidates': out}
