"""Vérificateur de citations (AxiorHub 4.7.0).

Chaque référence reçoit un statut :
- ``verifiee``       : trouvée dans une source consultée et cohérente avec la citation ;
- ``douteuse``       : trouvée mais incohérente (date, juridiction, chambre, version abrogée, citation textuelle
                       absente) ou sources divergentes ;
- ``introuvable``    : une source a répondu sans la trouver (cela ne prouve pas qu'elle n'existe pas) ;
- ``non_verifiable`` : aucune source n'a pu répondre (désactivée, en panne, code inconnu, numéro absent).

Seul « verifiee » est présenté comme sûr. Tout autre statut impose la mention « à vérifier ».
"""
import re
from datetime import date

from . import refs470, sources470
from .common import Stop, fold

STATUS_LABELS = {'verifiee': 'Vérifiée', 'douteuse': 'Douteuse', 'introuvable': 'Introuvable dans les sources',
                 'non_verifiable': 'Non vérifiable'}
ERROR_LABELS = {
    'sources_desactivees': 'Les sources juridiques ne sont pas activées.',
    'aucune_source_pour_cette_reference': 'Aucune source configurée ne couvre ce type de référence.',
    'service_en_pause_apres_echecs': 'Service de sources momentanément suspendu après des échecs répétés.',
    'delai_http_depasse': 'Le service de sources n’a pas répondu à temps.',
    'connexion_http_indisponible': 'Le service de sources est injoignable.',
    'http_401': 'Identifiants du service de sources refusés.', 'http_403': 'Accès au service de sources refusé.',
    'http_429': 'Service de sources saturé (trop de requêtes).',
    'erreur_service_sources': 'Erreur du service de sources.',
}
RECENT_DAYS = 365


def _norm(text):
    return re.sub(r'[^a-z0-9 ]+', ' ', re.sub(r'\s+', ' ', fold(text).replace('’', "'")))


def _words(text):
    return ' '.join(_norm(text).split())


def _fmt(iso):
    if not iso:
        return ''
    d = date.fromisoformat(iso)
    return '%d/%02d/%d' % (d.day, d.month, d.year)


def _error_text(code):
    return ERROR_LABELS.get(code, 'Source indisponible (%s).' % code)


def _source_info(answer, extra=None):
    result = answer.get('result') or {}
    info = {'name': answer.get('provider', ''), 'official': bool(answer.get('official')),
            'date': result.get('source_date', ''), 'cached': bool(result.get('cached')), 'stale': bool(result.get('stale'))}
    if extra:
        info.update(extra)
    return info


def _version_at(versions, day):
    best = None
    for v in versions:
        start, end = v.get('start') or '', v.get('end') or ''
        if (not start or start <= day) and (not end or day < end):
            if best is None or (v.get('start') or '') >= (best.get('start') or ''):
                best = v
    return best


def check_article(ref, answers, at_date=''):
    ok = [a for a in answers if a['ok']]
    if not ref.get('code'):
        return _item(ref, 'non_verifiable', ['Le code n’est pas précisé : impossible de contrôler cet article.'])
    if ref['code'] not in {c for c in refs470.CODES}:
        return _item(ref, 'non_verifiable', ['Code non reconnu par le vérificateur.'])
    if not ok:
        reasons = [_error_text(a.get('error', '')) for a in answers] or ['Aucune source n’a répondu.']
        return _item(ref, 'non_verifiable', list(dict.fromkeys(reasons)))
    found = [a for a in ok if a['result'].get('found')]
    missing = [a for a in ok if not a['result'].get('found')]
    if not found:
        return _item(ref, 'introuvable', ['Article absent de %s (la source peut être incomplète ; contrôler sur Légifrance).' %
                                           ', '.join(a['provider'] for a in missing)], sources=[_source_info(a) for a in missing])
    reasons, notes = [], []
    status = 'verifiee'
    if missing:
        status = 'douteuse'
        reasons.append('Sources divergentes : trouvé dans %s, absent de %s.' % (
            ', '.join(a['provider'] for a in found), ', '.join(a['provider'] for a in missing)))
    day = at_date or date.today().isoformat()
    reference = found[0]
    best = None
    for answer in found:
        candidate = _version_at(answer['result'].get('versions', []), day)
        if candidate:
            best, reference = candidate, answer
            break
    sources = [_source_info(a) for a in found]
    if best is None:
        status = 'douteuse'
        reasons.append('Aucune version de l’article n’est en vigueur au %s%s.' % (
            _fmt(day), '' if at_date else ' (date des faits non renseignée : date du jour utilisée)'))
        link = (reference['result'].get('versions') or [{}])[-1].get('url', '')
        return _item(ref, status, reasons, sources=sources, url=link)
    state = str(best.get('state', '')).upper()
    if state in ('ABROGE', 'ABROGE_DIFF', 'PERIME', 'ANNULE') and not at_date:
        status = 'douteuse'
        reasons.append('Article abrogé depuis le %s.' % (_fmt(best.get('end')) or 'date inconnue'))
    elif state in ('ABROGE', 'ABROGE_DIFF', 'PERIME', 'ANNULE') and best.get('end') and best['end'] <= date.today().isoformat():
        notes.append('Version abrogée depuis le %s mais applicable à la date des faits (%s).' % (_fmt(best['end']), _fmt(at_date)))
    current = _version_at(reference['result'].get('versions', []), date.today().isoformat())
    if at_date and current and current.get('id') != best.get('id'):
        notes.append('La version applicable aux faits (en vigueur du %s au %s) diffère de la version actuelle : vérifier le texte cité.' % (
            _fmt(best.get('start')) or '?', _fmt(best.get('end')) or '…'))
    if ref.get('quote'):
        quote = _words(ref['quote'])
        text = _words(best.get('text', ''))
        if not text:
            notes.append('Le texte de l’article n’a pas été fourni par la source : citation textuelle non contrôlée.')
            if status == 'verifiee':
                status = 'douteuse'
                reasons.append('Citation textuelle non contrôlable.')
        elif len(quote.split()) >= 5 and quote not in text:
            status = 'douteuse'
            reasons.append('La citation textuelle ne figure pas dans la version applicable de l’article.')
    if not reference['official']:
        recent = (best.get('start') or '') >= (date.fromordinal(date.today().toordinal() - RECENT_DAYS)).isoformat() or not at_date
        notes.append('Source non officielle%s : contrôle sur Légifrance recommandé%s.' % (
            ' (jeu de références du cabinet)' if reference['provider'] == 'local' else '',
            ' (texte récent ou date des faits non précisée)' if recent else ''))
    if any(s['stale'] for s in sources):
        notes.append('Réponse issue du cache périmé (service momentanément indisponible).')
    return _item(ref, status, reasons, notes, sources=sources, url=best.get('url', ''),
                 applicable={'start': best.get('start', ''), 'end': best.get('end', ''), 'state': state,
                             'excerpt': re.sub(r'\s+', ' ', best.get('text', ''))[:300]})


def check_decision(ref, answers):
    ok = [a for a in answers if a['ok']]
    number = ref.get('pourvoi') or ref.get('rg') or ref.get('ce_number') or ref.get('cc_number')
    if not number:
        return _item(ref, 'non_verifiable', ['Numéro de pourvoi ou de RG absent : décision non identifiable.'])
    if not ok:
        reasons = [_error_text(a.get('error', '')) for a in answers] or ['Aucune source n’a répondu.']
        return _item(ref, 'non_verifiable', list(dict.fromkeys(reasons)))
    found = [(a, m) for a in ok for m in (a['result'].get('matches') or [])]
    sources = [_source_info(a) for a in ok]
    if not found:
        return _item(ref, 'introuvable', ['Décision non retrouvée dans %s (ne prouve pas son inexistence ; contrôler sur Judilibre ou Légifrance).' %
                                           ', '.join(a['provider'] for a in ok)], sources=sources)
    answer, match = found[0]
    reasons, notes = [], []
    if ref.get('date') and match.get('date') and ref['date'] != match['date']:
        reasons.append('Date citée %s ≠ date de la décision %s.' % (_fmt(ref['date']), _fmt(match['date'])))
    elif not ref.get('date'):
        notes.append('Date non indiquée dans la citation (décision du %s).' % (_fmt(match.get('date')) or 'date inconnue'))
    if ref.get('court') and match.get('court') and ref['court'] != match['court'] and not ref.get('court_presumed'):
        reasons.append('Juridiction citée (%s) différente de la source (%s).' % (ref['court'], match['court']))
    if ref.get('chamber') and match.get('chamber') and ref['chamber'] != match['chamber']:
        reasons.append('Chambre citée (%s) différente de la source (%s).' % (
            refs470.CHAMBER_LABELS.get(ref['chamber'], ref['chamber']), refs470.CHAMBER_LABELS.get(match['chamber'], match['chamber'])))
    if ref.get('court_presumed'):
        notes.append('Juridiction non indiquée dans la citation (Cour de cassation présumée).')
    if len({m.get('date') for _, m in found if m.get('date')}) > 1:
        reasons.append('Sources divergentes sur la date de la décision.')
    if not answer['official']:
        notes.append('Source non officielle : contrôle sur Judilibre ou Légifrance recommandé.')
    status = 'douteuse' if reasons else 'verifiee'
    return _item(ref, status, reasons, notes, sources=sources, url=match.get('url', ''),
                 applicable={'date': match.get('date', ''), 'court': match.get('court', ''), 'title': match.get('title', '')})


def _item(ref, status, reasons, notes=None, sources=None, url='', applicable=None):
    return {'ref_id': ref['id'], 'kind': ref['kind'], 'label': refs470.label(ref), 'raw': ref['raw'][:200],
            'start': ref['start'], 'end': ref['end'], 'status': status, 'status_label': STATUS_LABELS[status],
            'reasons': reasons, 'notes': notes or [], 'sources': sources or [], 'url': _safe_url(url),
            'applicable': applicable or {}}


def _safe_url(url):
    return url if re.match(r'^https://[a-z0-9.\-]+/[^\s"<>]*$', str(url or ''), re.I) else ''


def verify_text(desk, text, fact_date='', connector=None, limit=sources470.MAX_REFS_PER_CHECK):
    """Contrôle toutes les références d'un texte. Aucune partie du texte n'est transmise : seules les références."""
    if fact_date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', fact_date):
        raise Stop('date_des_faits_invalide')
    refs = refs470.extract(text)
    truncated = len(refs) > limit
    refs = refs[:limit]
    connector = connector or sources470.Sources(desk)
    items = []
    for ref in refs:
        try:
            answers = connector.lookup(ref, fact_date)
        except Stop as exc:
            items.append(_item(ref, 'non_verifiable', ['Référence non transmissible : %s.' % exc]))
            continue
        items.append(check_article(ref, answers, fact_date) if ref['kind'] == 'article' else check_decision(ref, answers))
    counts = {k: 0 for k in STATUS_LABELS}
    for item in items:
        counts[item['status']] += 1
    needs = any(item['status'] != 'verifiee' for item in items) or truncated
    return {'items': items, 'counts': counts, 'total': len(items), 'needs_check': needs, 'truncated': truncated,
            'fact_date': fact_date, 'sources': connector.status(),
            'headline': headline(counts, len(items), truncated)}


def headline(counts, total, truncated=False):
    if not total:
        return 'Aucune référence juridique repérée.'
    bad = total - counts['verifiee']
    if not bad and not truncated:
        return 'Toutes les références (%d) sont vérifiées.' % total
    parts = ['%d vérifiée(s)' % counts['verifiee']]
    for key, word in (('douteuse', 'douteuse(s)'), ('introuvable', 'introuvable(s)'), ('non_verifiable', 'non vérifiable(s)')):
        if counts[key]:
            parts.append('%d %s' % (counts[key], word))
    return 'À VÉRIFIER : ' + ', '.join(parts) + (' ; contrôle limité aux premières références.' if truncated else '.')


def store(desk, scope, scope_id, matter, report):
    sources470.ensure_schema(desk)
    import hashlib
    cid = hashlib.sha256((scope + scope_id + desk.now()).encode()).hexdigest()[:32]
    desk.db.execute('INSERT INTO citation_checks470 VALUES(?,?,?,?,?,?,?)',
                    (cid, scope, str(scope_id), matter or '', desk.now(), report['headline'],
                     __import__('json').dumps(report, ensure_ascii=False)))
    desk.db.commit()
    return cid


def latest(desk, scope, scope_id):
    sources470.ensure_schema(desk)
    row = desk.db.execute('SELECT report,created FROM citation_checks470 WHERE scope=? AND scope_id=? ORDER BY created DESC LIMIT 1',
                          (scope, str(scope_id))).fetchone()
    if not row:
        return None
    import json
    return {**json.loads(row[0]), 'checked_at': row[1]}


BANNER = '[À VÉRIFIER — références juridiques non confirmées : %s. Contrôler avant tout usage définitif.]'


def mark_body(body, report):
    """Bandeau en tête de texte pour un brouillon dont des références ne sont pas vérifiées."""
    if not report or not report.get('needs_check'):
        return body
    bad = [i['label'] for i in report['items'] if i['status'] != 'verifiee'][:6]
    names = '; '.join(bad) if bad else 'contrôle incomplet'
    return BANNER % names + '\n\n' + body


def mark_subject(subject, report):
    if report and report.get('needs_check') and not str(subject).startswith('[À VÉRIFIER]'):
        return '[À VÉRIFIER] ' + str(subject)
    return subject


def gate_draft(desk, body, subject='', fact_date='', matter='', scope='draft', scope_id=''):
    """Pour un dépôt automatique : renvoie (body, subject, report). Sans référence, rien n'est modifié."""
    report = verify_text(desk, body, fact_date)
    if not report['total']:
        return body, subject, report
    store(desk, scope, scope_id, matter, report)
    return mark_body(body, report), mark_subject(subject, report), report


def gate_for_config(cfg, body, matter='', scope='draft', scope_id=''):
    """Pour le circuit de dépôt de brouillons : ne lève jamais. Retourne (body, marked, report|None)."""
    from .desk import Desk
    desk = Desk(cfg)
    try:
        fact_date = str(desk.settings('sources470:fact_date:' + str(matter), '') or '')
        new_body, subject, report = gate_draft(desk, body, 'x', fact_date, matter, scope, scope_id)
        return new_body, bool(report.get('needs_check')) and bool(report.get('total')), report
    except Exception:
        if refs470.extract(body):
            return BANNER % 'contrôle des citations indisponible' + '\n\n' + body, True, None
        return body, False, None
    finally:
        try:
            desk.db.close()
        except Exception:
            pass


def mark_message(message, marked):
    if marked and not str(message['Subject']).startswith('[À VÉRIFIER]'):
        subject = '[À VÉRIFIER] ' + str(message['Subject'])
        message.replace_header('Subject', subject)
        message['X-AxiorHub-Citations'] = 'a-verifier'
    return message
