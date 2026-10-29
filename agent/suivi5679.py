"""5.6.79 : données communes à la liste des dossiers et à la page Dossier refondues.

- **travail de l'IA** par dossier : pièces lues, fiche de travail, rappels rédigés, travaux terminés de la file (réponses, projets,
  analyses), avec leur date ; sert aux cartes « Travaillés récemment par l'IA » et à la colonne « Travail de l'IA » ;
- **intervenants** d'un dossier : parties et avocats lus dans les actes (5.6.79), correspondants confirmés, expéditeurs des courriels
  rattachés (hors adresses du cabinet), avec leur origine ;
- **titre lisible** d'un document : un fichier nommé par un numéro (« 7700112233.pdf ») est présenté par sa nature et sa date relevées.
"""
from collections import Counter
from email.utils import parseaddr
import json
from pathlib import PurePosixPath
import re

# Travaux de la file qui sont un travail de l'IA sur un dossier (libellé court affiché dans la liste)
AI_JOBS = {'prepare_reply': 'Réponse préparée', 'prepare_document_project': 'Projet de document', 'prepare_hearing': 'Préparation d’audience',
           'prepare_meeting': 'Préparation de rendez-vous', 'prepare_legal_opinion': 'Consultation préparée', 'analyze_strategy': 'Analyse stratégique',
           'build_matrix': 'Matrice de preuve', 'draft_act': 'Projet d’acte', 'recit5651': 'Rappel rédigé', 'relecture5651': 'Relecture Word',
           'fiche_dossier5627': 'Fiche du dossier', 'digest_pieces5632': 'Lecture des pièces', 'monitor_matter': 'Surveillance du dossier',
           'identify_latest_writings': 'Dernières écritures repérées', 'courrier5679': 'Courrier rédigé', 'recherche5634': 'Recherche juridique',
           'orchestrate_mail': 'Courriel traité', 'talk568_prepare': 'Préparation Talk', 'followup568_prepare': 'Relance préparée'}
GENERIC_NAME = re.compile(r'^(?:[\d\s_\-.]{4,}|scan[\s_\-]*\d*|document[\s_\-]*\d*|img[\s_\-]*\d+|image[\s_\-]*\d*|doc\d*|fichier[\s_\-]*\d*|'
                          r'sans titre|untitled|piece[\s_\-]*jointe[\s_\-]*\d*|attachment[\s_\-]*\d*)$', re.I)


def _rows(db, sql, params=()):
    try:
        return db.execute(sql, params).fetchall()
    except Exception:   # noqa: BLE001 — table absente (fonction jamais utilisée)
        return []


def _later(current, when, label):
    if when and (not current or str(when) > current[0]):
        return (str(when), label)
    return current


def last_ai_work(desk, days=120):
    """{dossier: (date ISO, libellé)} : dernier travail de l'IA connu pour chaque dossier."""
    out = {}
    for mid, when in _rows(desk.db, 'SELECT matter,MAX(created) FROM piece_digests5634 GROUP BY matter'):
        out[mid] = _later(out.get(mid), when, 'Lecture des pièces')
    for mid, when in _rows(desk.db, 'SELECT matter,MAX(created) FROM fiche5627 GROUP BY matter'):
        out[mid] = _later(out.get(mid), when, 'Fiche du dossier')
    for mid, when in _rows(desk.db, 'SELECT matter,MAX(created) FROM d51_narratives GROUP BY matter'):
        out[mid] = _later(out.get(mid), when, 'Rappel rédigé')
    kinds = tuple(AI_JOBS)
    for kind, args, finished in _rows(desk.db, "SELECT kind,args,finished FROM jobs WHERE status='done' AND kind IN (%s) AND finished>=date('now',?) "
                                               "ORDER BY id DESC LIMIT 5000" % ','.join('?' * len(kinds)), (*kinds, '-%d days' % days)):
        try:
            mid = str((json.loads(args or '{}') or {}).get('matter') or '')
        except (ValueError, TypeError):
            continue
        if mid:
            out[mid] = _later(out.get(mid), finished, AI_JOBS.get(kind, 'Travail de l’IA'))
    return {k: v for k, v in out.items() if v}


def matter_ai_activity(desk, mid, limit=8):
    """Derniers travaux de l'IA sur un dossier : [{'date', 'libelle', 'detail'}], du plus récent au plus ancien."""
    items = []
    n = _rows(desk.db, "SELECT COUNT(DISTINCT path),MAX(created) FROM piece_digests5634 WHERE matter=? AND created>=date('now','-30 days')", (mid,))
    if n and n[0][0]:
        items.append({'date': str(n[0][1]), 'libelle': 'Lecture des pièces', 'detail': '%d pièce(s) lue(s) sur les 30 derniers jours' % n[0][0]})
    for version, created in _rows(desk.db, 'SELECT version,created FROM fiche5627 WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)):
        items.append({'date': str(created), 'libelle': 'Fiche du dossier', 'detail': 'version %s' % version})
    for kind, version, created in _rows(desk.db, 'SELECT kind,MAX(version),MAX(created) FROM d51_narratives WHERE matter=? GROUP BY kind', (mid,)):
        items.append({'date': str(created), 'libelle': 'Rappel des faits' if kind == 'faits' else 'Rappel de la procédure', 'detail': 'version %s' % version})
    marker = '%"matter": "' + str(mid).replace('%', '').replace('_', '\\_') + '"%'
    kinds = tuple(AI_JOBS)
    for kind, finished, result in _rows(desk.db, "SELECT kind,finished,result FROM jobs WHERE status='done' AND kind IN (%s) AND args LIKE ? ESCAPE '\\' "
                                                 "ORDER BY id DESC LIMIT 40" % ','.join('?' * len(kinds)), (*kinds, marker)):
        detail = ''
        try:
            res = json.loads(result or '{}')
            if isinstance(res, dict):
                detail = str(res.get('message') or res.get('title') or res.get('path') or '')[:160]
        except (ValueError, TypeError):
            pass
        items.append({'date': str(finished or ''), 'libelle': AI_JOBS.get(kind, 'Travail de l’IA'), 'detail': PurePosixPath(detail).name if detail.startswith('/') else detail})
    items.sort(key=lambda x: x['date'], reverse=True)
    seen, out = set(), []
    for it in items:
        key = (it['libelle'], it['detail'])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out[:limit]


def _own(desk):
    try:
        from .mailbox import own_addresses
        return {a.lower() for a in own_addresses(desk.c.get('mail', {}))}
    except Exception:   # noqa: BLE001
        return set()


def senders(desk, mid=None):
    """Expéditeurs des courriels rattachés (hors cabinet) : {dossier: Counter((nom, adresse))}."""
    own = _own(desk)
    out = {}
    sql = 'SELECT matter,sender FROM work_items WHERE sender<>\'\' AND matter IS NOT NULL AND matter<>\'\'' + (' AND matter=?' if mid else '')
    for matter, sender in _rows(desk.db, sql, (mid,) if mid else ()):
        name, addr = parseaddr(str(sender or ''))
        addr = addr.lower()
        if '@' not in addr or addr in own:
            continue
        out.setdefault(matter, Counter())[(name.strip().strip('"') or addr, addr)] += 1
    return out


def correspondents_summary(desk, matters):
    """{dossier: {'nombre', 'principal'}} : correspondants confirmés et expéditeurs repérés ; principal = le plus fréquent."""
    by_sender = senders(desk)
    out = {}
    for m in matters:
        found = by_sender.get(m['id'], Counter())
        confirmed = {str(p.get('email') or '').lower() for p in m.get('correspondents') or []}
        addresses = confirmed | {addr for _, addr in found}
        main = found.most_common(1)[0][0][0] if found else (sorted(confirmed)[0] if confirmed else '')
        out[m['id']] = {'nombre': len(addresses), 'principal': main}
    return out


def intervenants(desk, m):
    """Intervenants d'un dossier : [{'nom', 'role', 'email', 'origine', 'confirme', 'n'}] (parties et avocats, puis correspondants)."""
    from . import modele5651
    from .desk import ROLES
    mid = m['id']
    out = []
    ref = modele5651.reference_procedure(desk, mid)
    for p in modele5651.parties(desk, mid):
        roles = sorted({r['role'] for r in p['roles'] if r['role'] and (not ref or r['procedure_id'] == ref['id'])}) or \
            sorted({r['role'] for r in p['roles'] if r['role']})
        counsel = p['counsel'] or next((r['counsel'] for r in p['roles'] if r.get('counsel')), '')
        origin = 'acte de procédure' if p.get('origin') == 'entete' else {'configuration': 'configuration du dossier', 'fiche': 'fiche du dossier',
                                                                         'avocat': 'saisie du cabinet', 'releve': 'pièces'}.get(p.get('origin'), 'pièces')
        out.append({'nom': p['display_name'], 'role': ('client' + (' · ' + ', '.join(roles) if roles else '')) if p['client'] else (', '.join(roles) or 'partie'),
                    'email': '', 'origine': origin, 'confirme': bool(p['client']) or p.get('origin') == 'avocat', 'n': 0, 'type': 'partie'})
        for c in [x.strip() for x in re.split(r',\s*(?=Ma[iî]tre)', counsel) if x.strip()]:
            out.append({'nom': c, 'role': 'avocat de ' + p['display_name'], 'email': '', 'origine': origin, 'confirme': False, 'n': 0, 'type': 'avocat'})
    known = {}
    for c in m.get('correspondents') or []:
        addr = str(c.get('email') or '').lower()
        known[addr] = {'nom': addr, 'role': ROLES.get(c.get('role'), c.get('role') or 'correspondant'), 'email': addr, 'origine': 'correspondant enregistré',
                       'confirme': True, 'n': 0, 'type': 'courriel'}
    for (name, addr), n in senders(desk, mid).get(mid, Counter()).most_common(40):
        if addr in known:
            known[addr]['n'] = n
            if known[addr]['nom'] == addr and name != addr:
                known[addr]['nom'] = name
            continue
        known[addr] = {'nom': name, 'role': 'expéditeur', 'email': addr, 'origine': '%d courriel(s) rattaché(s)' % n, 'confirme': False, 'n': n,
                       'type': 'courriel'}
    out.extend(sorted(known.values(), key=lambda x: (not x['confirme'], -x['n'], x['nom'].lower())))
    return out


def digests_overview(desk, mid):
    """{chemin: {'nature', 'date'}} tirés des relevés de pièces (le plus récent par pièce)."""
    out = {}
    for path, digest in _rows(desk.db, 'SELECT path,digest FROM piece_digests5634 WHERE matter=? ORDER BY created', (mid,)):
        try:
            data = json.loads(digest or '{}')
        except (ValueError, TypeError):
            continue
        out[path] = {'nature': str(data.get('nature') or '')[:80], 'date': str(data.get('date_document') or '')[:40]}
    return out


def doc_title(doc, overview=None):
    """(titre, sous-titre) d'un document : nom du fichier, ou nature et date relevées si le nom n'apprend rien."""
    from .modele5651 import NATURES
    name = doc['name']
    stem = PurePosixPath(name).stem
    info = (overview or {}).get(doc['path']) or {}
    if GENERIC_NAME.match(stem.strip()):
        nature = info.get('nature') or (NATURES.get(doc.get('nature'), '') if doc.get('nature') not in ('a_classer', None) else '')
        when = doc.get('doc_date') or info.get('date') or ''
        if nature:
            from .vue5651 import _date_fr
            label = nature[:1].upper() + nature[1:]
            if when and len(when) == 10 and when[4] == '-':
                label += ' du ' + _date_fr(when)
            elif when:
                label += ' (' + when + ')'
            return label, name
    return re.sub(r'[_]+', ' ', stem).strip() or name, ''
