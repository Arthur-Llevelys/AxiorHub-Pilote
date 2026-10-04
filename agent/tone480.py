"""Profils de ton 4.8.0 : un style distinct selon le destinataire, détecté automatiquement et corrigeable.

Cinq profils : client, confrère, greffe, adversaire, administration. La détection part du rôle enregistré dans le
dossier, puis de l'adresse ; une correction de l'avocat (par adresse ou par domaine) prime toujours. En cas de doute,
aucun profil n'est imposé : l'agent garde son style par défaut et le signale.
"""
import json
import re

from .common import Stop, load_matters
from . import style480

PROFILES = ('client', 'confrere', 'greffe', 'adversaire', 'administration')
FORMALITY_RANK = {'client': 1, 'confrere': 2, 'adversaire': 3, 'administration': 4, 'greffe': 5}
ROLE_MAP = {'client': 'client', 'prospect': 'client', 'confrere_adverse': 'confrere'}

DEFAULTS = {
    'client': {'label': 'Client', 'formality': 'cordial et clair', 'vouvoiement': True,
               'opening': 'Bonjour', 'closing': 'Bien cordialement', 'max_words': 250,
               'directives': 'Ton chaleureux mais professionnel. Phrases courtes, sans jargon inutile : expliquer en termes simples. Terminer par la prochaine étape.'},
    'confrere': {'label': 'Confrère', 'formality': 'confraternel et précis', 'vouvoiement': True,
                 'opening': 'Cher Confrère / Chère Consœur', 'closing': 'Confraternellement', 'max_words': 200,
                 'directives': 'Ton confraternel, concis et précis. Aucune confidence sur le client. Références du dossier et des pièces citées exactement.'},
    'greffe': {'label': 'Greffe', 'formality': 'institutionnel, très formel', 'vouvoiement': True,
               'opening': 'Madame, Monsieur', 'closing': 'Salutations distinguées (formule complète)', 'max_words': 120,
               'directives': 'Très formel et bref. Rappeler le numéro de RG, la juridiction et la partie représentée. Une demande précise par courriel.'},
    'adversaire': {'label': 'Adversaire', 'formality': 'neutre, factuel et réservé', 'vouvoiement': True,
                   'opening': 'Madame, Monsieur', 'closing': 'Cordialement', 'max_words': 150,
                   'directives': 'Ton neutre et factuel. Aucun aveu, aucune concession, aucune opinion ; réserver expressément les droits du client. Ne rien divulguer du dossier.'},
    'administration': {'label': 'Administration', 'formality': 'formel et référencé', 'vouvoiement': True,
                       'opening': 'Madame, Monsieur', 'closing': 'Salutations distinguées (formule complète)', 'max_words': 150,
                       'directives': 'Formel. Citer les références du dossier ou du contribuable, formuler une demande précise avec le délai attendu.'},
}
INSTITUTION_RE = re.compile(r'(justice\.fr|greffe|tribunal|cour-?appel|ca-[a-z]+\.justice)', re.I)
ADMIN_RE = re.compile(r'(\.gouv\.fr|urssaf|impots|dgfip|douane|caf\.fr|pole-?emploi|francetravail|cpam|ameli|prefecture|mairie|inpi|infogreffe)', re.I)
BAR_RE = re.compile(r'(avocat|barreau|cabinet|law|\bscp\b|selarl|selas)', re.I)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS tone_profiles480(
  role TEXT PRIMARY KEY, formality TEXT NOT NULL, vouvoiement INTEGER NOT NULL, opening TEXT NOT NULL,
  closing TEXT NOT NULL, max_words INTEGER NOT NULL, directives TEXT NOT NULL, source TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tone_overrides480(
  kind TEXT NOT NULL, value TEXT NOT NULL, role TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(kind,value));
'''


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def profile(desk, role):
    ensure_schema(desk)
    if role not in PROFILES:
        raise Stop('profil_de_ton_inconnu')
    base = dict(DEFAULTS[role])
    row = desk.db.execute('SELECT * FROM tone_profiles480 WHERE role=?', (role,)).fetchone()
    if row:
        base.update({'formality': row['formality'], 'vouvoiement': bool(row['vouvoiement']), 'opening': row['opening'],
                     'closing': row['closing'], 'max_words': row['max_words'], 'directives': row['directives']})
        base['source'] = row['source']
    else:
        base['source'] = 'defaut'
    base['role'] = role
    return base


def all_profiles(desk):
    return [profile(desk, r) for r in PROFILES]


def save_profile(desk, role, data):
    ensure_schema(desk)
    if role not in PROFILES:
        raise Stop('profil_de_ton_inconnu')
    current = profile(desk, role)

    def text(key, limit):
        value = str(data.get(key, current[key]) or '').strip()
        if len(value) > limit or '\x00' in value:
            raise Stop('profil_de_ton_invalide')
        return value
    words = data.get('max_words', current['max_words'])
    try:
        words = int(words)
    except (TypeError, ValueError):
        raise Stop('profil_de_ton_invalide') from None
    if not 30 <= words <= 800:
        raise Stop('profil_de_ton_invalide')
    desk.db.execute('''INSERT OR REPLACE INTO tone_profiles480(role,formality,vouvoiement,opening,closing,max_words,directives,source,updated)
      VALUES(?,?,?,?,?,?,?,?,?)''', (role, text('formality', 120), 1 if data.get('vouvoiement', current['vouvoiement']) else 0,
                                     text('opening', 120), text('closing', 160), words, text('directives', 800), 'manuel', desk.now()))
    desk.db.commit()
    desk.audit('ton_480_profil_modifie', {'role': role})
    return profile(desk, role)


def reset_profile(desk, role):
    ensure_schema(desk)
    if role not in PROFILES:
        raise Stop('profil_de_ton_inconnu')
    desk.db.execute('DELETE FROM tone_profiles480 WHERE role=?', (role,))
    desk.db.commit()
    desk.audit('ton_480_profil_reinitialise', {'role': role})
    return profile(desk, role)


# ------------------------------------------------------------------ détection
def _email(value):
    value = str(value or '').strip().lower()
    return value if re.fullmatch(r"[a-z0-9._%+'\-]{1,64}@[a-z0-9.\-]{1,255}\.[a-z]{2,}", value) else ''


def set_override(desk, kind, value, role):
    """Correction de l'avocat : `kind` = 'adresse' ou 'domaine'. role='' supprime la correction."""
    ensure_schema(desk)
    if kind not in ('adresse', 'domaine'):
        raise Stop('correction_ton_invalide')
    value = str(value or '').strip().lower().lstrip('@')
    if kind == 'adresse' and not _email(value):
        raise Stop('adresse_invalide')
    if kind == 'domaine' and not re.fullmatch(r'[a-z0-9.\-]{3,255}\.[a-z]{2,}', value):
        raise Stop('correction_ton_invalide')
    if role == '':
        desk.db.execute('DELETE FROM tone_overrides480 WHERE kind=? AND value=?', (kind, value))
    else:
        if role not in PROFILES:
            raise Stop('profil_de_ton_inconnu')
        desk.db.execute('INSERT OR REPLACE INTO tone_overrides480(kind,value,role,updated) VALUES(?,?,?,?)', (kind, value, role, desk.now()))
    desk.db.commit()
    desk.audit('ton_480_correction', {'kind': kind, 'role': role or 'supprimee'})
    return {'kind': kind, 'value': value, 'role': role}


def overrides(desk):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT kind,value,role,updated FROM tone_overrides480 ORDER BY kind,value')]


def classify_address(desk, address, matter_role=''):
    """-> (profil | None, motif, confiance). Ordre : correction par adresse, par domaine, rôle du dossier, adresse."""
    ensure_schema(desk)
    address = _email(address)
    if not address:
        return None, 'adresse_invalide', 'incertain'
    domain = address.rsplit('@', 1)[1]
    row = desk.db.execute("SELECT role FROM tone_overrides480 WHERE kind='adresse' AND value=?", (address,)).fetchone()
    if row:
        return row['role'], 'Correction de l’avocat pour cette adresse', 'confirmé'
    row = desk.db.execute("SELECT role FROM tone_overrides480 WHERE kind='domaine' AND value=?", (domain,)).fetchone()
    if row:
        return row['role'], 'Correction de l’avocat pour le domaine ' + domain, 'confirmé'
    if INSTITUTION_RE.search(address):
        return 'greffe', 'Adresse d’une juridiction ou d’un greffe', 'probable'
    if ADMIN_RE.search(address):
        return 'administration', 'Adresse d’une administration', 'probable'
    if matter_role in ROLE_MAP:
        return ROLE_MAP[matter_role], 'Rôle « %s » enregistré dans le dossier' % matter_role, 'confirmé'
    if matter_role == 'tiers':
        if BAR_RE.search(domain):
            return 'confrere', 'Domaine de cabinet d’avocats (tiers au dossier)', 'probable'
        return 'adversaire', 'Tiers au dossier (partie adverse ou autre) : ton réservé par prudence', 'probable'
    if BAR_RE.search(domain):
        return 'confrere', 'Domaine de cabinet d’avocats', 'probable'
    return None, 'Destinataire non rattaché à un rôle', 'incertain'


def _matter_roles(desk, matter_id):
    try:
        matters = load_matters(desk.c)
    except Exception:
        return {}
    for m in matters:
        if m.get('id') == matter_id:
            return {str(p.get('email', '')).lower(): p.get('role', '') for p in m.get('correspondents', [])}
    return {}


def detect(desk, matter_id, recipients):
    roles = _matter_roles(desk, matter_id) if matter_id else {}
    found, reasons, worst = [], [], 'confirmé'
    for address in recipients or []:
        key = _email(address)
        role, why, conf = classify_address(desk, key, roles.get(key, ''))
        if role is None:
            return {'profile': None, 'confidence': 'incertain', 'reasons': [key + ' : ' + why], 'mixed': False}
        found.append(role)
        reasons.append('%s : %s' % (key, why))
        if conf == 'probable':
            worst = 'probable'
    if not found:
        return {'profile': None, 'confidence': 'incertain', 'reasons': ['Aucun destinataire'], 'mixed': False}
    unique = set(found)
    chosen = max(unique, key=lambda r: FORMALITY_RANK[r])
    return {'profile': chosen, 'confidence': worst, 'reasons': reasons, 'mixed': len(unique) > 1}


def context(desk, matter_id, recipients):
    """Bloc injecté dans la requête de rédaction. None si aucun profil fiable."""
    result = detect(desk, matter_id, recipients)
    if not result['profile']:
        return None
    p = profile(desk, result['profile'])
    lines = ['Registre : ' + p['formality'] + ('' if p['vouvoiement'] else ' (tutoiement autorisé)') + '.',
             'Formule d’ouverture attendue : ' + p['opening'] + '. Formule de fin attendue : ' + p['closing'] + '.',
             'Longueur : ' + str(p['max_words']) + ' mots au maximum, sauf nécessité.', p['directives']]
    if not p['vouvoiement']:
        lines[0] = lines[0]
    elif p['vouvoiement']:
        lines.append('Vouvoyer le destinataire.')
    if result['mixed']:
        lines.append('Destinataires de profils différents : le registre le plus formel est retenu.')
    return {'profil': result['profile'], 'libelle': p['label'], 'confiance': result['confidence'],
            'consignes': lines, 'motifs': result['reasons'], 'mixte': result['mixed'],
            'usage': 'Consignes de forme uniquement ; elles ne prouvent aucun fait et ne changent aucune règle de confidentialité.'}


def learned_suggestions(desk, min_count=3, min_share=0.5):
    """Formules réellement envoyées par profil (sans nom propre) quand elles diffèrent du profil enregistré."""
    ensure_schema(desk)
    try:
        rows = desk.db.execute('SELECT role,closing,opening FROM outcomes480 WHERE role<>\'\'').fetchall()
    except Exception:
        return []
    by_role = {}
    for r in rows:
        data = by_role.setdefault(r['role'], {'n': 0, 'closing': {}, 'opening': {}})
        data['n'] += 1
        data['closing'][r['closing']] = data['closing'].get(r['closing'], 0) + 1
        data['opening'][r['opening']] = data['opening'].get(r['opening'], 0) + 1
    out = []
    for role, data in sorted(by_role.items()):
        if role not in PROFILES or data['n'] < min_count:
            continue
        p = profile(desk, role)
        for field in ('closing', 'opening'):
            label, count = max(data[field].items(), key=lambda kv: kv[1])
            if label in ('Aucune formule', '') or label == p[field]:
                continue
            if count >= min_count and count / data['n'] >= min_share:
                out.append({'role': role, 'field': field, 'value': label, 'count': count, 'of': data['n'], 'current': p[field]})
    return out


def adopt(desk, role, field, value):
    if field not in ('opening', 'closing'):
        raise Stop('profil_de_ton_invalide')
    known = {x[0] for x in style480.OPENINGS} | {x[0] for x in style480.CLOSINGS}
    if value not in known:
        raise Stop('profil_de_ton_invalide')
    return save_profile(desk, role, {field: value})
