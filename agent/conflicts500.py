"""Conflits d'intérêts (AxiorHub 5.0.0).

À l'ouverture d'un dossier, l'agent cherche les parties et les adversaires dans les dossiers existants et produit un
rapport SOURCÉ pour décision de l'avocat. Il ne décide rien :
  - le résultat est affiché avant tout acte : tant que l'avocat n'a pas pris sa décision sur un dossier nouveau, la
    production d'actes et de courriers pour ce dossier est refusée (voir ``gate``) ;
  - l'ABSENCE DE RÉSULTAT N'EST JAMAIS PRÉSENTÉE COMME UNE GARANTIE : le rapport dit toujours ce qui a été cherché,
    ce qui ne l'a pas été, et que seule l'analyse de l'avocat fait foi ;
  - traitement local uniquement (aucun modèle, aucun service externe), lecture journalisée.

Sources interrogées : noms et alias des clients du registre des dossiers, parties enregistrées lors des recherches
antérieures, parties de la mémoire de dossier, et mentions dans les courriels, fichiers, agenda et notes indexés
localement. Les rapprochements sont de trois niveaux : identique, proche (inclusion ou forte ressemblance), homonyme
possible (un seul mot en commun).
"""
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
import re
import sqlite3
import unicodedata

from .common import Stop, digest
from . import metier500 as m5

ROLES = {'client': 'Client', 'adverse': 'Adversaire / partie adverse', 'tiers': 'Autre partie (tiers, caution, garant…)'}
DECISIONS = {'accepter': 'Poursuivre : résultat examiné', 'decliner': 'Décliner le dossier', 'a_etudier': 'À étudier encore'}
RISK_LABELS = {'conflit_potentiel': 'Conflit potentiel', 'a_verifier': 'À vérifier', 'meme_cote': 'Même côté (information)'}
RISK_ORDER = {'conflit_potentiel': 0, 'a_verifier': 1, 'meme_cote': 2}
LEVEL_LABELS = {'identique': 'Identique', 'proche': 'Proche', 'homonyme_possible': 'Homonyme possible', 'mention': 'Mentionné'}
GATED_KINDS = {'prepare_document_project', 'create_document_files', 'draft_act', 'prepare_hearing', 'create_hearing_files',
               'prepare_word_project', 'create_word_files', 'prepare_legal_opinion', 'prepare_cabinet_letter',
               'prepare_cabinet_revision', 'create_cabinet_letter', 'studio_prepare420', 'pieces_create510', 'docrequest520'}
STOP = {'de', 'du', 'des', 'la', 'le', 'les', 'd', 'l', 'et', 'and', 'the', 'au', 'aux', 'en', 'sur', 'sous'}
CIVILITY = {'m', 'mr', 'monsieur', 'mme', 'madame', 'mlle', 'mademoiselle', 'me', 'maitre', 'dr', 'docteur', 'pr', 'professeur', 'mrs', 'ms'}
LEGAL_FORMS = {'sas', 'sasu', 'sarl', 'eurl', 'sa', 'sci', 'snc', 'scp', 'selarl', 'selas', 'sel', 'sc', 'ltd', 'limited', 'gmbh', 'inc',
               'llc', 'spa', 'srl', 'bv', 'nv', 'ag', 'plc', 'societe', 'ste', 'association', 'asso', 'cie', 'compagnie'}
MAX_PARTIES = 12


def fold(text):
    text = unicodedata.normalize('NFKD', str(text or '').replace('’', "'").replace('œ', 'oe'))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def tokens(name):
    words = re.findall(r'[a-z0-9]+', fold(name))
    return [w for w in words if w not in STOP and w not in CIVILITY and w not in LEGAL_FORMS and (len(w) > 1 or w.isdigit())]


def norm(name):
    return ' '.join(sorted(set(tokens(name))))


def similarity(a, b):
    """('identique'|'proche'|'homonyme_possible'|'') entre deux noms."""
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return ''
    if ta == tb:
        return 'identique'
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if small <= big:
        if len(small) >= 2:
            return 'proche'
        word = next(iter(small))
        return 'homonyme_possible' if len(word) >= 4 else ''
    na, nb = ' '.join(sorted(ta)), ' '.join(sorted(tb))
    if min(len(na), len(nb)) >= 6 and SequenceMatcher(None, na, nb).ratio() >= 0.88:
        return 'proche'
    return ''


def clean_parties(parties):
    out, seen = [], set()
    for p in parties or []:
        name = re.sub(r'\s+', ' ', str(p.get('name', ''))).strip()[:120]
        role = str(p.get('role', ''))
        email = str(p.get('email', '')).strip().lower()[:120]
        if not name:
            continue
        if role not in ROLES:
            raise Stop('role_partie_invalide')
        if email and not re.fullmatch(r'[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+', email):
            raise Stop('adresse_invalide')
        key = (norm(name), role)
        if not key[0]:
            raise Stop('nom_partie_invalide')
        if key in seen:
            continue
        seen.add(key)
        out.append({'name': name, 'role': role, 'email': email})
    if len(out) > MAX_PARTIES:
        raise Stop('trop_de_parties')
    return out


# ----------------------------------------------------------------------------------------- parties connues
def _matter_state(desk, mid):
    try:
        row = desk.db.execute('SELECT state FROM matter_portfolio WHERE matter=?', (mid,)).fetchone()
        return row[0] if row else ''
    except sqlite3.Error:
        return ''


def known_parties(desk, exclude=''):
    """[(matter, name, role, email, basis, source_label)] pour tous les dossiers sauf ``exclude``."""
    m5.ensure_schema(desk)
    out = []
    for mid, m in m5.matter_index(desk).items():
        if mid == exclude:
            continue
        for name in [m.get('client_name', '')] + list(m.get('aliases', []) or []):
            if name and tokens(name):
                out.append((mid, name, 'client', '', 'registre', 'Registre des dossiers (client / alias)'))
        for person in m.get('correspondents', []) or []:
            if person.get('role') == 'client' and person.get('name'):
                out.append((mid, person['name'], 'client', person.get('email', ''), 'registre', 'Registre des dossiers (correspondant client)'))
    for r in desk.db.execute('SELECT matter,name,role,email FROM conflicts500_parties WHERE matter<>?', (exclude,)):
        out.append((r['matter'], r['name'], r['role'], r['email'], 'parties_enregistrees', 'Parties enregistrées lors d’une recherche de conflits'))
    try:
        for r in desk.db.execute("SELECT matter,title,content FROM legal_memory_records WHERE record_type='party' AND status IN ('validated','pinned','suggested') AND matter<>?", (exclude,)):
            role = 'adverse' if re.search(r'adverse|defendeur|assigne|intime|partie adverse', fold(r['title'] + ' ' + r['content'])) else 'inconnu'
            out.append((r['matter'], r['title'], role, '', 'memoire', 'Mémoire du dossier (partie)'))
    except sqlite3.Error:
        pass
    return out


def _excerpt(segments):
    return ''.join(s['t'] for s in segments)[:240]


def _mentions(desk, party, exclude):
    """Mentions du nom (expression exacte) dans l'index local des autres dossiers."""
    try:
        from . import search490
        words = tokens(party['name'])
        if not words:
            return [], 0
        phrase = '"%s"' % ' '.join(re.findall(r'[A-Za-zÀ-ÿ0-9]+', party['name'])[:6])
        found = search490.search(desk, phrase, limit=60)
    except Stop:
        return [], 0
    except Exception:
        return [], 0
    if found.get('relaxed'):
        return {}, 0
    by_matter = {}
    for hit in found.get('results', []):
        if not hit.get('matter') or hit['matter'] == exclude:
            continue
        by_matter.setdefault(hit['matter'], []).append({'type': hit['kind'], 'label': (hit.get('source') or {}).get('label') or hit.get('title', ''),
                                                       'title': hit.get('title', ''), 'date': hit.get('date', ''), 'excerpt': _excerpt(hit.get('segments', []))})
    return by_matter, len(found.get('results', []))


def coverage(desk):
    cov = {'matters': len(m5.matter_index(desk)),
           'register': desk.db.execute('SELECT COUNT(*) FROM conflicts500_parties').fetchone()[0], 'index': {}}
    try:
        from . import search490
        st = search490.status(desk)
        cov['index'] = {'counts': st['counts'], 'mail_indexed': st['mail_indexed'], 'backfill_done': st['backfill_done'],
                        'mail_indexing': st['mail_indexing']}
    except Exception:
        cov['index'] = {}
    return cov


def _risk(role_new, role_old, level):
    if role_old == 'inconnu' or level == 'mention':
        return 'a_verifier'
    if level == 'homonyme_possible':
        return 'a_verifier'
    if 'tiers' in (role_new, role_old):
        return 'a_verifier'
    if role_new != role_old:
        return 'conflit_potentiel'
    return 'meme_cote'


def search(desk, parties, exclude=''):
    parties = clean_parties(parties)
    known = known_parties(desk, exclude)
    matches = []
    seen = set()
    for party in parties:
        for mid, name, role_old, email, basis, label in known:
            level = 'identique' if (party['email'] and email and party['email'].lower() == email.lower()) else similarity(party['name'], name)
            if not level:
                continue
            key = (party['name'], mid, norm(name), role_old)
            if key in seen:
                continue
            seen.add(key)
            matches.append({'party': party['name'], 'role_new': party['role'], 'matter': mid, 'matter_label': m5.matter_label(desk, mid),
                            'matter_state': _matter_state(desk, mid), 'other_name': name, 'other_role': role_old, 'level': level,
                            'basis': basis, 'risk': _risk(party['role'], role_old, level),
                            'sources': [{'type': 'registre', 'label': label, 'date': '', 'excerpt': name}]})
        mentions, _ = _mentions(desk, party, exclude)
        for mid, items in (mentions or {}).items():
            if any(m['party'] == party['name'] and m['matter'] == mid for m in matches):
                for m in matches:
                    if m['party'] == party['name'] and m['matter'] == mid:
                        m['sources'] += items[:3]
                continue
            matches.append({'party': party['name'], 'role_new': party['role'], 'matter': mid, 'matter_label': m5.matter_label(desk, mid),
                            'matter_state': _matter_state(desk, mid), 'other_name': party['name'], 'other_role': 'inconnu', 'level': 'mention',
                            'basis': 'texte', 'risk': 'a_verifier', 'sources': items[:3]})
    matches.sort(key=lambda m: (RISK_ORDER[m['risk']], m['party'], m['matter']))
    return matches


def _limits(desk, parties, cov):
    limits = ['L’absence de résultat ne vaut pas garantie d’absence de conflit : seule votre analyse fait foi.',
              'Ne sont pas couverts : les dossiers et archives hors AxiorHub (papier, ancien logiciel), les noms orthographiés autrement, '
              'les liens de groupe, de parenté ou de dirigeants entre sociétés, les conflits liés à des confrères associés, les anciens dossiers non enregistrés.',
              'La recherche compare des noms : un homonyme n’est pas la même personne, et deux noms différents peuvent désigner la même.']
    if not any(p['role'] == 'adverse' for p in parties):
        limits.insert(0, 'Aucun adversaire n’est renseigné : la recherche est incomplète tant que les parties adverses ne sont pas saisies.')
    index = cov.get('index', {})
    if index and index.get('mail_indexing') and not index.get('backfill_done'):
        limits.append('L’indexation des anciens courriels n’est pas terminée (%d courriels lus) : les mentions anciennes peuvent manquer.' % index.get('mail_indexed', 0))
    if index and not index.get('mail_indexing'):
        limits.append('L’indexation du texte des courriels est désactivée : les courriels ne sont pas interrogés.')
    return limits


def check(desk, parties, matter='', trigger='manuel'):
    """Lance la recherche et conserve le rapport. N'ouvre ni ne bloque rien : c'est ``decide`` qui engage l'avocat."""
    m5.ensure_schema(desk)
    if matter:
        m5.require_matter(desk, matter)
    parties = clean_parties(parties)
    if not parties:
        raise Stop('aucune_partie')
    matches = search(desk, parties, exclude=matter)
    cov = coverage(desk)
    risks = {m['risk'] for m in matches}
    if risks & {'conflit_potentiel'}:
        status = 'a_examiner'
        summary = 'Conflit potentiel signalé : à examiner avant tout acte.'
    elif risks & {'a_verifier'}:
        status = 'a_examiner'
        summary = 'Rapprochements à vérifier avant tout acte.'
    elif matches:
        status = 'information'
        summary = 'Parties déjà connues du cabinet, du même côté : information.'
    else:
        status = 'aucun_resultat'
        summary = 'Aucun rapprochement trouvé dans les sources interrogées. Ce n’est pas une garantie d’absence de conflit.'
    result = {'summary': summary, 'status': status, 'matches': matches, 'limits': _limits(desk, parties, cov), 'coverage': cov,
              'searched_at': m5.now(), 'method': 'Comparaison de noms normalisés (civilités, formes sociales et accents ignorés) et recherche d’expressions exactes dans l’index local.'}
    cid = digest('conflicts500|%s|%s|%s' % (matter, json.dumps(parties, sort_keys=True), m5.now()))
    desk.db.execute('INSERT INTO conflicts500_checks VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (cid, matter, trigger, json.dumps(parties, ensure_ascii=False), json.dumps(result, ensure_ascii=False), status, '', '', '', m5.now()))
    if matter:
        row = desk.db.execute('SELECT baseline FROM conflicts500_matters WHERE matter=?', (matter,)).fetchone()
        if row is not None and not row[0]:
            desk.db.execute("UPDATE conflicts500_matters SET check_id=? WHERE matter=?", (cid, matter))
    desk.db.commit()
    desk.audit('conflits_500_recherche', {'matter': matter, 'parties': len(parties), 'matches': len(matches), 'status': status, 'trigger': trigger})
    m5.log_access(desk, 'conflits', 'recherche', matter)
    return get(desk, cid, log=False)


def get(desk, cid, log=True):
    m5.ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM conflicts500_checks WHERE id=?', (cid,)).fetchone()
    if not row:
        raise Stop('recherche_conflits_absente')
    out = dict(row)
    out['parties'] = json.loads(out['parties'])
    out['result'] = json.loads(out['result'])
    out['decision_label'] = DECISIONS.get(out['decision'], '')
    if log:
        m5.log_access(desk, 'conflits', 'consultation', out['matter'])
    return out


def listing(desk, matter='', limit=100):
    m5.ensure_schema(desk)
    sql, params = 'SELECT id,matter,trigger_kind,status,decision,created,parties FROM conflicts500_checks', []
    if matter:
        sql += ' WHERE matter=?'
        params.append(matter)
    sql += ' ORDER BY created DESC LIMIT ?'
    params.append(max(1, min(int(limit), 300)))
    out = []
    for r in desk.db.execute(sql, params):
        d = dict(r)
        d['parties'] = json.loads(d['parties'])
        d['matter_label'] = m5.matter_label(desk, d['matter']) if d['matter'] else '(avant ouverture)'
        d['decision_label'] = DECISIONS.get(d['decision'], '')
        out.append(d)
    m5.log_access(desk, 'conflits', 'liste', matter)
    return out


# ----------------------------------------------------------------------------------------------- décision
def decide(desk, cid, decision, note=''):
    if decision not in DECISIONS:
        raise Stop('decision_conflit_invalide')
    row = get(desk, cid, log=False)
    note = re.sub(r'\s+', ' ', str(note or '')).strip()[:500]
    needs_note = row['status'] != 'aucun_resultat' and decision in ('accepter', 'decliner')
    if needs_note and len(note) < 10:
        raise Stop('motif_decision_conflit_requis')
    desk.db.execute('UPDATE conflicts500_checks SET decision=?, decision_note=?, decided=? WHERE id=?', (decision, note, m5.now(), cid))
    matter = row['matter']
    if decision == 'accepter':
        for p in row['parties']:
            try:
                desk.db.execute('INSERT OR IGNORE INTO conflicts500_parties VALUES(?,?,?,?,?,?,?,?)',
                                (digest('c500party|%s|%s|%s' % (matter, norm(p['name']), p['role'])), matter, p['name'], norm(p['name']), p['role'],
                                 p.get('email', ''), 'decision_avocat', m5.now()))
            except sqlite3.Error:
                pass
    if matter:
        status = {'accepter': 'examine', 'decliner': 'decline', 'a_etudier': 'a_examiner'}[decision]
        base = desk.db.execute('SELECT baseline FROM conflicts500_matters WHERE matter=?', (matter,)).fetchone()
        if base is None:
            desk.db.execute('INSERT INTO conflicts500_matters VALUES(?,?,?,?,?)', (matter, m5.now(), 0, cid, status))
        else:
            desk.db.execute('UPDATE conflicts500_matters SET status=?, check_id=?, baseline=0 WHERE matter=?', (status, cid, matter))
    desk.db.commit()
    desk.audit('conflits_500_decision', {'matter': matter, 'decision': decision, 'status': row['status']})
    m5.log_access(desk, 'conflits', 'decision', matter)
    return get(desk, cid, log=False)


def add_party(desk, matter, name, role, email=''):
    """Ajoute une partie (client, adversaire, tiers) à un dossier : elle sera retrouvée par les recherches futures."""
    m5.ensure_schema(desk)
    m5.require_matter(desk, matter)
    party = clean_parties([{'name': name, 'role': role, 'email': email}])
    if not party:
        raise Stop('nom_partie_invalide')
    p = party[0]
    pid = digest('c500party|%s|%s|%s' % (matter, norm(p['name']), p['role']))
    desk.db.execute('INSERT OR IGNORE INTO conflicts500_parties VALUES(?,?,?,?,?,?,?,?)',
                    (pid, matter, p['name'], norm(p['name']), p['role'], p['email'], 'saisie', m5.now()))
    desk.db.commit()
    desk.audit('conflits_500_partie_ajoutee', {'matter': matter, 'role': p['role']})
    return {'party_id': pid}


def remove_party(desk, party_id, reason):
    reason = m5.need_reason(reason)
    row = desk.db.execute('SELECT matter FROM conflicts500_parties WHERE id=?', (party_id,)).fetchone()
    if not row:
        raise Stop('partie_absente')
    desk.db.execute('DELETE FROM conflicts500_parties WHERE id=?', (party_id,))
    desk.db.commit()
    desk.audit('conflits_500_partie_retiree', {'matter': row[0], 'reason_length': len(reason)})
    return {'removed': True}


def matter_parties(desk, matter):
    m5.ensure_schema(desk)
    m = m5.require_matter(desk, matter)
    out = []
    if m.get('client_name'):
        out.append({'id': '', 'name': m['client_name'], 'role': 'client', 'email': '', 'source': 'registre'})
    for a in m.get('aliases', []) or []:
        if a and a != m.get('client_name'):
            out.append({'id': '', 'name': a, 'role': 'client', 'email': '', 'source': 'registre (alias)'})
    for r in desk.db.execute('SELECT id,name,role,email,source FROM conflicts500_parties WHERE matter=? ORDER BY created', (matter,)):
        out.append(dict(r))
    return out


def recheck(desk, matter, trigger='manuel'):
    parties = [{'name': p['name'], 'role': p['role'], 'email': p['email']} for p in matter_parties(desk, matter)]
    # un même nom n'est contrôlé qu'une fois par rôle
    return check(desk, parties[:MAX_PARTIES], matter, trigger)


# --------------------------------------------------------------------------------------- nouveaux dossiers
def _release_stuck_501(desk):
    """5.0.1 (une seule fois) : libère les dossiers que la 5.0.0 avait bloqués sans décision.

    La 5.0.0 bloquait la production pour tout dossier apparu après la première recherche, y compris ceux découverts
    automatiquement dans Nextcloud ; l'avocat ne voyait qu'un message technique. Ces dossiers restent signalés (rapport
    conservé) mais ne bloquent plus. Un dossier « décliné » reste bloqué : c'est une décision explicite.
    """
    if desk.settings('conflicts500:released501', False):
        return
    desk.db.execute("UPDATE conflicts500_matters SET baseline=1, status='anterieur' WHERE baseline=0 AND status='a_examiner'")
    desk.setting('conflicts500:released501', m5.now())
    desk.db.commit()


def scan_new(desk, gated=True):
    """Repère les dossiers apparus depuis l'installation.

    ``gated=True`` (dossier ouvert par l'avocat dans l'interface) : la recherche de conflits est lancée et la production reste
    refusée tant que l'avocat n'a pas décidé. ``gated=False`` (passes de fond, dossier découvert, importé ou ajouté par
    l'administrateur) : le dossier est simplement enregistré, rien n'est bloqué ; la recherche reste disponible dans la page Conflits.
    """
    m5.ensure_schema(desk)
    ids = sorted(m5.matter_index(desk))
    stamp = m5.now()
    if not desk.settings('conflicts500:baseline_done', False):
        for mid in ids:
            desk.db.execute('INSERT OR IGNORE INTO conflicts500_matters VALUES(?,?,?,?,?)', (mid, stamp, 1, '', 'anterieur'))
        desk.setting('conflicts500:baseline_done', stamp)
        desk.setting('conflicts500:released501', stamp)
        desk.db.commit()
        return []
    _release_stuck_501(desk)
    known = {r[0] for r in desk.db.execute('SELECT matter FROM conflicts500_matters')}
    created = []
    for mid in ids:
        if mid in known:
            continue
        if not gated:
            desk.db.execute('INSERT INTO conflicts500_matters VALUES(?,?,?,?,?)', (mid, stamp, 1, '', 'anterieur'))
            desk.db.commit()
            continue
        desk.db.execute('INSERT INTO conflicts500_matters VALUES(?,?,?,?,?)', (mid, stamp, 0, '', 'a_examiner'))
        desk.db.commit()
        try:
            result = recheck(desk, mid, trigger='nouveau_dossier')
            from .live430 import emit
            emit(desk, 'conflit_a_examiner', 'Nouveau dossier %s : recherche de conflits à examiner avant tout acte.' % m5.matter_label(desk, mid),
                 matter=mid, dedupe='conflicts500|new|' + mid)
            created.append({'matter': mid, 'check_id': result['id'], 'status': result['status']})
        except Stop:
            continue
    return created


def scan_background(desk):
    """Passe de fond : enregistre les dossiers apparus sans jamais rien bloquer."""
    return scan_new(desk, gated=False)


def set_gate(desk, enabled):
    """Active ou désactive le refus de production pour les dossiers nouveaux non examinés (réglage de l'avocat)."""
    desk.setting('conflicts500:gate', bool(enabled))
    desk.audit('conflits_500_blocage_regle', {'enabled': bool(enabled)})
    return {'gate': bool(enabled)}


def matter_gate_state(desk, matter):
    m5.ensure_schema(desk)
    if desk.settings('conflicts500:baseline_done', False):
        _release_stuck_501(desk)
    row = desk.db.execute('SELECT status,baseline,check_id FROM conflicts500_matters WHERE matter=?', (matter,)).fetchone()
    if not row:
        return {'gated': False, 'status': ''}
    return {'gated': not row['baseline'] and row['status'] != 'examine', 'status': row['status'], 'check_id': row['check_id']}


def gate(desk, matter):
    """Refuse la production d'actes pour un dossier nouveau dont la recherche de conflits n'a pas été examinée."""
    if not matter or not desk.settings('conflicts500:gate', True):
        return
    state = matter_gate_state(desk, matter)
    if state['gated']:
        raise Stop('dossier_decline_conflit' if state['status'] == 'decline' else 'conflit_a_examiner')


def pending(desk):
    m5.ensure_schema(desk)
    out = []
    for r in desk.db.execute("SELECT matter,check_id,status FROM conflicts500_matters WHERE baseline=0 AND status<>'examine' ORDER BY first_seen"):
        out.append({'matter': r['matter'], 'label': m5.matter_label(desk, r['matter']), 'check_id': r['check_id'], 'status': r['status']})
    return out


def banner_html(desk, prefix):
    """Bandeau d'explication : pourquoi la production est suspendue pour certains dossiers (vide s'il n'y en a pas)."""
    from html import escape as e
    try:
        if not desk.settings('conflicts500:gate', True):
            return ''
        blocked = [p for p in pending(desk) if matter_gate_state(desk, p['matter'])['gated']]
    except Exception:
        return ''
    if not blocked:
        return ''
    names = ', '.join(e(p['label']) for p in blocked[:5]) + (' …' if len(blocked) > 5 else '')
    return ('<p class="notice" role="status">La production (courriers, actes, notes) est suspendue pour %d dossier(s) récemment ouvert(s) dont la recherche de conflits '
            'd’intérêts n’a pas encore été examinée : %s. <a href="%s/conflits">Examiner et décider</a>.</p>') % (len(blocked), names, e(prefix))
