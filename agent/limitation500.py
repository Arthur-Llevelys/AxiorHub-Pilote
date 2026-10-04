"""Prescription et forclusion (AxiorHub 5.0.0).

Même logique que les délais de procédure de la 4.5 : aucune date n'est fixée par un modèle ; les règles sont des
DONNÉES versionnées (agent/data/limitation_rules500.json, articles vérifiés), le calcul est déterministe et détaillé
pas à pas, toute date est proposée « à confirmer », toute correction est motivée et journalisée, les rappels sont
émis en cascade et l'agenda reçoit un événement unique à identifiant stable.

Chaque alerte indique : la règle appliquée (articles), la date de départ retenue (et d'où elle vient) et ce qui reste
à confirmer. Le calcul est volontairement PRUDENT : la date affichée est le dernier jour utile calculé sans
prorogation éventuelle (un jour férié ou un week-end ne reporte pas automatiquement une prescription), et toute
suspension non terminée est signalée sans être comptée.
"""
from .common import matter_display
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .common import Stop, digest
from . import deadlines450 as engine
from . import metier500 as m5

DATA = Path(__file__).with_name('data') / 'limitation_rules500.json'
ACTIVE = ('a_confirmer', 'confirmee', 'manuel')
STATUS_LABELS = {'a_confirmer': 'À confirmer', 'confirmee': 'Confirmée', 'manuel': 'Corrigée à la main', 'a_completer': 'À compléter',
                 'terminee': 'Terminée', 'annulee': 'Annulée'}
NATURE_LABELS = {'prescription': 'Prescription', 'forclusion': 'Délai de forclusion / de garantie'}
OFFSETS = (90, 60, 30, 14, 7, 2, 0)
OFFSET_LABELS = {90: 'J-90', 60: 'J-60', 30: 'J-30', 14: 'J-14', 7: 'J-7', 2: 'J-2', 0: 'jour J'}
OUTCOMES = {'action_engagee': 'Action engagée (demande en justice)', 'sans_suite': 'Sans suite', 'prescrite': 'Prescription acquise',
            'erreur': 'Saisie erronée'}
_CACHE = {}


class LimitationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def load():
    if 'data' not in _CACHE:
        _CACHE['data'] = json.loads(DATA.read_text(encoding='utf-8'))
    return _CACHE['data']


def rules():
    return load()['rules']


def rule(rule_id):
    for item in rules():
        if item['id'] == rule_id:
            return item
    raise LimitationError('regle_inconnue', 'Règle de prescription inconnue : %s.' % rule_id)


def rules_for_ui():
    return [{'id': r['id'], 'label': r['label'], 'domain': r['domain'], 'articles': r['articles'], 'amount': r['amount'], 'unit': r['unit'],
             'nature': r['nature'], 'nature_label': NATURE_LABELS[r['nature']], 'start_events': r['start_events'], 'note': r['note'],
             'source_url': r['source_url']} for r in rules()]


def _d(value, field):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise LimitationError('date_invalide', 'Date invalide (%s) : %s.' % (field, value)) from None


def _add(d, amount, unit):
    return engine.add_months(d, amount * 12 if unit == 'ans' else amount)


def _normalize_events(events):
    out = []
    for ev in events or []:
        kind = str(ev.get('type', ''))
        if kind not in load()['general']['event_types']:
            raise LimitationError('evenement_inconnu', 'Type d’événement inconnu : %s.' % kind)
        item = {'type': kind, 'date': _d(ev.get('date'), 'événement').isoformat(), 'note': str(ev.get('note', ''))[:200]}
        if ev.get('end'):
            item['end'] = _d(ev['end'], 'fin').isoformat()
            if item['end'] < item['date']:
                raise LimitationError('periode_invalide', 'La fin précède le début.')
        out.append(item)
    return sorted(out, key=lambda e: (e['date'], e['type']))


def compute(rule_id, start, start_event='', events=None, alsace=False):
    spec = rule(rule_id)
    general = load()['general']
    start_d = _d(start, 'départ')
    keys = [s['key'] for s in spec['start_events']]
    start_event = start_event or keys[0]
    if start_event not in keys:
        raise LimitationError('depart_non_prevu', 'Ce point de départ n’est pas prévu pour cette règle.')
    labels = {s['key']: s['label'] for s in spec['start_events']}
    if spec.get('valid_from') and start_d < date.fromisoformat(spec['valid_from']):
        raise LimitationError('regle_non_applicable', 'La règle « %s » ne s’applique qu’à partir du %s ; pour un départ antérieur (%s), le régime applicable est à examiner.' % (
            spec['label'], engine.fr_date(date.fromisoformat(spec['valid_from'])), engine.fr_date(start_d)))
    if spec.get('valid_to') and start_d > date.fromisoformat(spec['valid_to']):
        raise LimitationError('regle_non_applicable', 'La règle « %s » a cessé de s’appliquer le %s.' % (spec['label'], spec['valid_to']))
    nature = spec['nature']
    events = _normalize_events(events)
    steps = ['Règle : %s (%s).' % (spec['label'], ', '.join(spec['articles'])),
             '%s : délai de %d %s.' % (NATURE_LABELS[nature], spec['amount'], spec['unit']),
             'Point de départ retenu : %s — %s.' % (engine.fr_date(start_d), labels[start_event]),
             'La prescription se compte par jours (C. civ. art. 2228) ; le jour de départ n’est pas compté et la prescription est acquise lorsque le dernier jour du terme est accompli (art. 2229).']
    warnings = []
    cur_start = start_d
    due = _add(start_d, spec['amount'], spec['unit'])
    steps.append('Dernier jour du terme : %s.' % engine.fr_date(due))
    for ev in events:
        info = general['event_types'][ev['type']]
        when = date.fromisoformat(ev['date'])
        if nature not in info['applies']:
            warnings.append('« %s » ne s’applique pas à un délai de %s : événement ignoré (%s).' % (info['label'], 'forclusion' if nature == 'forclusion' else 'prescription', info['article']))
            continue
        if when < cur_start:
            warnings.append('« %s » du %s précède le départ du délai : ignoré.' % (info['label'], engine.fr_date(when)))
            continue
        if when > due:
            warnings.append('« %s » du %s est postérieur au terme (%s) : il ne fait pas renaître un délai déjà expiré.' % (info['label'], engine.fr_date(when), engine.fr_date(due)))
            continue
        if info['effect'] == 'interruption':
            cur_start = when
            due = _add(when, spec['amount'], spec['unit'])
            steps.append('%s (%s) le %s : le délai acquis est effacé et un nouveau délai de même durée court (C. civ. art. 2231) → nouveau terme %s.' % (
                info['label'], info['article'], engine.fr_date(when), engine.fr_date(due)))
            warnings.append('Interruption à confirmer : elle est non avenue en cas de désistement, de péremption ou de rejet définitif de la demande (art. 2243).')
        else:
            end = date.fromisoformat(ev['end']) if ev.get('end') else None
            if end is None:
                warnings.append('Suspension commencée le %s et non terminée : la date affichée ignore la suspension (elle est donc la plus prudente). Saisir la fin dès qu’elle est connue.' % engine.fr_date(when))
                steps.append('%s (%s) depuis le %s, sans fin saisie : non comptée dans le calcul.' % (info['label'], info['article'], engine.fr_date(when)))
            else:
                shifted = due + timedelta(days=(end - when).days)
                if ev['type'] == 'mediation':
                    floor = engine.add_months(end, 6)
                    if shifted < floor:
                        shifted = floor
                steps.append('%s (%s) du %s au %s : le délai ne court pas pendant la suspension → nouveau terme %s.' % (
                    info['label'], info['article'], engine.fr_date(when), engine.fr_date(end), engine.fr_date(shifted)))
                due = shifted
    if spec.get('butoir') and nature == 'prescription':
        cap = engine.add_months(start_d, general['butoir']['years'] * 12)
        if due > cap:
            steps.append('Plafond de vingt ans (C. civ. art. 2232) calculé depuis la date de départ retenue : terme ramené au %s.' % engine.fr_date(cap))
            due = cap
            warnings.append('Le plafond de vingt ans court de la NAISSANCE DU DROIT, éventuellement antérieure à la date de départ retenue : vérifier.')
    to_confirm = list(spec['to_confirm'])
    to_confirm.append('Absence d’autre cause d’interruption ou de suspension non encore saisie (reconnaissance, mesure d’exécution, médiation, impossibilité d’agir).')
    if spec.get('butoir') and nature == 'prescription':
        to_confirm.append('Plafond de vingt ans à compter de la naissance du droit (C. civ. art. 2232).')
    if start_d < date.fromisoformat(general['transition_date']):
        to_confirm.insert(0, general['transition_text'])
    if start_event == 'mise_en_demeure':
        to_confirm.insert(0, general['mise_en_demeure_note'])
    reason = engine.non_working_reason(due, alsace)
    if reason:
        warnings.append('Le dernier jour (%s) tombe un jour non ouvré (%s). Aucune prorogation n’est supposée pour une prescription : agir au plus tard la veille ouvrée.' % (engine.fr_date(due), reason))
    return {'rule_id': spec['id'], 'rule_label': spec['label'], 'articles': spec['articles'], 'nature': nature, 'start': start_d.isoformat(),
            'start_event': start_event, 'start_label': labels[start_event], 'due': due.isoformat(), 'due_label': engine.fr_date(due),
            'steps': steps, 'warnings': warnings, 'to_confirm': to_confirm, 'events': events, 'source_url': spec['source_url'],
            'prudence': 'Dernier jour utile calculé sans prorogation. Aucune date n’est présumée plus favorable.'}


# ------------------------------------------------------------------------------------------------- stockage
def _row(desk, ident):
    m5.ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM limitation500 WHERE id=?', (ident,)).fetchone()
    return dict(row) if row else None


def public(row):
    out = dict(row)
    for key, default in (('calc', {}), ('events', [])):
        try:
            out[key] = json.loads(out.get(key) or json.dumps(default))
        except ValueError:
            out[key] = default
    spec = None
    try:
        spec = rule(out['rule_id'])
    except LimitationError:
        pass
    out['rule_label'] = spec['label'] if spec else out['rule_id']
    out['articles'] = spec['articles'] if spec else []
    out['status_label'] = STATUS_LABELS.get(out['status'], out['status'])
    out['due_label'] = engine.fr_date(date.fromisoformat(out['due'])) if out['due'] else ''
    out['start_label'] = engine.fr_date(date.fromisoformat(out['start_date']))
    out['matter_label'] = out['matter']
    return out


def _journal(desk, ident, action, reason='', before=None, after=None):
    desk.db.execute('INSERT INTO limitation500_journal(limitation_id,at,action,reason,before,after) VALUES(?,?,?,?,?,?)',
                    (ident, m5.now(), action, reason[:400], json.dumps(before or {}, ensure_ascii=False), json.dumps(after or {}, ensure_ascii=False)))


def _recompute(desk, row):
    try:
        calc = compute(row['rule_id'], row['start_date'], row['start_event'], json.loads(row.get('events') or '[]'))
        return calc['due'], json.dumps(calc, ensure_ascii=False), ''
    except LimitationError as ex:
        return '', '{}', ex.message


def create(desk, matter, rule_id, start, start_event='', note='', origin='manuel', source='', events=None, status='a_confirmer'):
    m5.ensure_schema(desk)
    m5.require_matter(desk, matter)
    spec = rule(rule_id)
    start = _d(start, 'départ').isoformat()
    start_event = start_event or spec['start_events'][0]['key']
    ident = digest('limitation500|%s|%s|%s|%s' % (matter, rule_id, start_event, start))
    existing = _row(desk, ident)
    if existing:
        return public(existing) | {'created_now': False}
    row = {'rule_id': rule_id, 'start_date': start, 'start_event': start_event, 'events': json.dumps(_normalize_events(events))}
    due, calc, error = _recompute(desk, row)
    if error:
        status = 'a_completer'
    stamp = m5.now()
    desk.db.execute('INSERT INTO limitation500 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, matter, rule_id, start_event, start, str(note or '')[:300], due, calc, error, row['events'], status, origin,
                     str(source or '')[:300], '', '', '', stamp, stamp))
    _journal(desk, ident, 'creation', origin, {}, {'rule': rule_id, 'start': start, 'due': due, 'status': status})
    desk.db.commit()
    desk.audit('prescription_500_creee', {'matter': matter, 'rule': rule_id, 'origin': origin, 'due': due})
    return public(_row(desk, ident)) | {'created_now': True}


def confirm(desk, ident):
    row = _row(desk, ident)
    if not row:
        raise Stop('prescription_absente')
    if row['status'] == 'a_completer':
        raise Stop('prescription_a_completer')
    if row['status'] in ('terminee', 'annulee'):
        raise Stop('prescription_close')
    before = {'status': row['status']}
    desk.db.execute("UPDATE limitation500 SET status='confirmee', updated=? WHERE id=?", (m5.now(), ident))
    _journal(desk, ident, 'confirmation', '', before, {'status': 'confirmee'})
    desk.db.commit()
    desk.audit('prescription_500_confirmee', {'matter': row['matter']})
    return public(_row(desk, ident))


def correct(desk, ident, reason, start_date='', rule_id='', start_event=''):
    reason = m5.need_reason(reason)
    row = _row(desk, ident)
    if not row or row['status'] in ('terminee', 'annulee'):
        raise Stop('prescription_absente')
    new = dict(row)
    if start_date:
        new['start_date'] = _d(start_date, 'départ').isoformat()
    if rule_id:
        rule(rule_id)
        new['rule_id'] = rule_id
        if not start_event:
            new['start_event'] = rule(rule_id)['start_events'][0]['key']
    if start_event:
        new['start_event'] = start_event
    due, calc, error = _recompute(desk, new)
    status = 'a_completer' if error else 'manuel'
    desk.db.execute('UPDATE limitation500 SET rule_id=?,start_date=?,start_event=?,due=?,calc=?,error=?,status=?,updated=? WHERE id=?',
                    (new['rule_id'], new['start_date'], new['start_event'], due, calc, error, status, m5.now(), ident))
    desk.db.execute('DELETE FROM limitation500_reminders WHERE limitation_id=?', (ident,))
    _journal(desk, ident, 'correction', reason, {k: row[k] for k in ('rule_id', 'start_date', 'start_event', 'due')},
             {'rule_id': new['rule_id'], 'start_date': new['start_date'], 'start_event': new['start_event'], 'due': due})
    desk.db.commit()
    desk.audit('prescription_500_corrigee', {'matter': row['matter']})
    return public(_row(desk, ident))


def add_event(desk, ident, kind, day, end='', note=''):
    row = _row(desk, ident)
    if not row or row['status'] in ('terminee', 'annulee'):
        raise Stop('prescription_absente')
    try:
        events = json.loads(row['events'] or '[]')
        events.append({'type': kind, 'date': day, **({'end': end} if end else {}), 'note': note})
        events = _normalize_events(events)
    except LimitationError as ex:
        raise Stop('evenement_invalide') from ex
    new = dict(row, events=json.dumps(events))
    due, calc, error = _recompute(desk, new)
    desk.db.execute("UPDATE limitation500 SET events=?,due=?,calc=?,error=?,status=?,updated=? WHERE id=?",
                    (new['events'], due, calc, error, 'a_completer' if error else 'manuel', m5.now(), ident))
    desk.db.execute('DELETE FROM limitation500_reminders WHERE limitation_id=?', (ident,))
    _journal(desk, ident, 'evenement', kind, {'due': row['due']}, {'due': due, 'event': kind, 'date': day})
    desk.db.commit()
    desk.audit('prescription_500_evenement', {'matter': row['matter'], 'type': kind})
    return public(_row(desk, ident))


def remove_event(desk, ident, index, reason):
    reason = m5.need_reason(reason)
    row = _row(desk, ident)
    if not row:
        raise Stop('prescription_absente')
    events = json.loads(row['events'] or '[]')
    try:
        events.pop(int(index))
    except (IndexError, ValueError):
        raise Stop('evenement_invalide') from None
    new = dict(row, events=json.dumps(events))
    due, calc, error = _recompute(desk, new)
    desk.db.execute("UPDATE limitation500 SET events=?,due=?,calc=?,error=?,status=?,updated=? WHERE id=?",
                    (new['events'], due, calc, error, 'a_completer' if error else 'manuel', m5.now(), ident))
    _journal(desk, ident, 'evenement_retire', reason, {'due': row['due']}, {'due': due})
    desk.db.commit()
    return public(_row(desk, ident))


def close(desk, ident, outcome, reason=''):
    if outcome not in OUTCOMES:
        raise Stop('issue_invalide')
    row = _row(desk, ident)
    if not row:
        raise Stop('prescription_absente')
    if outcome in ('erreur', 'sans_suite'):
        reason = m5.need_reason(reason)
    status = 'annulee' if outcome == 'erreur' else 'terminee'
    desk.db.execute('UPDATE limitation500 SET status=?, note=?, updated=? WHERE id=?', (status, (row['note'] + ' ' + OUTCOMES[outcome]).strip()[:300], m5.now(), ident))
    _journal(desk, ident, 'cloture', outcome + (' : ' + reason if reason else ''), {'status': row['status']}, {'status': status})
    desk.db.commit()
    desk.audit('prescription_500_close', {'matter': row['matter'], 'outcome': outcome})
    return public(_row(desk, ident))


def journal(desk, ident, limit=50):
    m5.ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT at,action,reason,before,after FROM limitation500_journal WHERE limitation_id=? ORDER BY id DESC LIMIT ?', (ident, limit))]


def listing(desk, matter='', include_closed=False):
    m5.ensure_schema(desk)
    sql, params = 'SELECT * FROM limitation500', []
    where = []
    if matter:
        where.append('matter=?')
        params.append(matter)
    if not include_closed:
        where.append("status NOT IN ('terminee','annulee')")
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += " ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due"
    rows = [public(r) for r in desk.db.execute(sql, params)]
    labels = m5.matter_index(desk)
    for r in rows:
        m = labels.get(r['matter'])
        r['matter_label'] = matter_display(m) if m else r['matter']
    return rows


# ---------------------------------------------------------------------------------------------- suggestions
KEYWORDS = (
    ('mise_en_demeure', r'mise en demeure'), ('reception', r'reception des travaux|proces-verbal de reception|pv de reception'),
    ('notification_rupture', r'licenciement|rupture du contrat|demission|rupture conventionnelle'), ('evenement', r'sinistre'),
    ('exigibilite', r'facture.*impay|impaye|echeance impayee|non[- ]paiement'), ('consolidation', r'consolidation'),
    ('fait_generateur', r'livraison|dommage|desordre|faute|manquement|inexecution'))


def _fold(text):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text or '').lower()) if not unicodedata.combining(c))


def suggestions(desk, limit=40):
    """Dates de départ POSSIBLES repérées dans la mémoire de dossier validée ou proposée. Rien n'est créé."""
    m5.ensure_schema(desk)
    try:
        rows = desk.db.execute("SELECT id,matter,record_type,title,content,event_date,status FROM legal_memory_records "
                               "WHERE event_date<>'' AND status IN ('validated','pinned','suggested') ORDER BY event_date DESC LIMIT 400").fetchall()
    except Exception:
        return []
    have = {(r[0], r[1]) for r in desk.db.execute("SELECT matter,start_date FROM limitation500 WHERE status NOT IN ('annulee')")}
    out = []
    for r in rows:
        day = str(r['event_date'])[:10]
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or (r['matter'], day) in have:
            continue
        text = _fold(r['title'] + ' ' + r['content'])
        for key, pattern in KEYWORDS:
            if re.search(pattern, text):
                out.append({'matter': r['matter'], 'date': day, 'start_event': key, 'start_event_label': load()['general']['start_events'][key],
                            'title': r['title'][:120], 'record_id': r['id'], 'validated': r['status'] in ('validated', 'pinned'),
                            'rules': [x['id'] for x in rules() if key in [s['key'] for s in x['start_events']]][:6]})
                break
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------------------------------------- rappels
def run_reminders(desk, today=None):
    m5.ensure_schema(desk)
    from .live430 import emit
    today = today or date.today()
    sent = 0
    for r in desk.db.execute("SELECT * FROM limitation500 WHERE status IN ('a_confirmer','confirmee','manuel') AND due<>''").fetchall():
        row = public(r)
        days = (date.fromisoformat(row['due']) - today).days
        label = m5.matter_label(desk, row['matter'])
        calc = row['calc'] or {}
        reached = [o for o in OFFSETS if days <= o] if days >= 0 else []
        for offset in reached:
            kind = 'J%s' % offset
            if desk.db.execute('SELECT 1 FROM limitation500_reminders WHERE limitation_id=? AND kind=?', (row['id'], kind)).fetchone():
                continue
            desk.db.execute('INSERT INTO limitation500_reminders VALUES(?,?,?)', (row['id'], kind, m5.now()))
            if offset == min(reached):
                emit(desk, 'prescription', '%s : %s — %s. Règle : %s. Départ retenu : %s (%s). Dernier jour utile : %s%s. Reste à confirmer : %d point(s).' % (
                    OFFSET_LABELS[offset], row['rule_label'], label, ', '.join(row['articles']), row['start_label'], calc.get('start_label', ''),
                    row['due_label'], ' (à confirmer)' if row['status'] == 'a_confirmer' else '', len(calc.get('to_confirm', []))),
                    matter=row['matter'], dedupe='limitation500|%s|%s' % (row['id'], kind))
                sent += 1
        has_action = any(e['type'] == 'demande_en_justice' for e in row['events'])
        if 0 <= days <= 30 and not has_action:
            if not desk.db.execute("SELECT 1 FROM limitation500_reminders WHERE limitation_id=? AND kind='sans_action'", (row['id'],)).fetchone():
                desk.db.execute("INSERT INTO limitation500_reminders VALUES(?,?,?)", (row['id'], 'sans_action', m5.now()))
                emit(desk, 'prescription_alerte', 'ALERTE : %s — %s, dernier jour utile le %s dans %d jour(s), sans demande en justice enregistrée.' % (
                    row['rule_label'], label, row['due_label'], days), matter=row['matter'], dedupe='limitation500|%s|sans_action' % row['id'])
                sent += 1
        if days < 0:
            if not desk.db.execute("SELECT 1 FROM limitation500_reminders WHERE limitation_id=? AND kind='depassee'", (row['id'],)).fetchone():
                desk.db.execute("INSERT INTO limitation500_reminders VALUES(?,?,?)", (row['id'], 'depassee', m5.now()))
                emit(desk, 'prescription_alerte', 'DÉLAI DÉPASSÉ : %s — %s (%s). Vérifier une interruption ou une suspension non saisie, puis clôturer.' % (
                    row['rule_label'], label, row['due']), matter=row['matter'], dedupe='limitation500|%s|depassee' % row['id'])
                sent += 1
    desk.db.commit()
    return sent


def sync_calendar(desk, dav, ident):
    row = _row(desk, ident)
    if not row or not row['due']:
        return ''
    cfg = desk.c.get('calendar', {})
    urls, tz_name = cfg.get('urls', []), cfg.get('timezone', 'Europe/Paris')
    if not urls or not desk.settings('automation:limitation_calendar500', True):
        state = 'non_configure'
    else:
        pub = public(row)
        when = date.fromisoformat(row['due'])
        start = datetime(when.year, when.month, when.day, 9, 0, tzinfo=ZoneInfo(tz_name))
        title = ('Prescription%s – %s – %s' % (' À CONFIRMER' if row['status'] == 'a_confirmer' else '', pub['rule_label'], m5.matter_label(desk, row['matter'])))[:200]
        description = '\n'.join(['Créée par AxiorHub (règle versionnée, aucune date déduite par un modèle). Dernier jour utile, sans prorogation.'] +
                                pub['calc'].get('steps', []) + ['À confirmer :'] + ['- ' + x for x in pub['calc'].get('to_confirm', [])])[:1900]
        from . import autonomy480
        if not autonomy480.allows(desk, 'agenda_echeance', 'agir'):
            autonomy480.pending_add(desk, 'agenda_echeance', row['matter'], title, {'uid': ident, 'title': title, 'start': start.isoformat(),
                'end': (start + timedelta(minutes=30)).isoformat(), 'description': description, 'deadline_id': ident})
            desk.db.execute('UPDATE limitation500 SET calendar_state=?,updated=? WHERE id=?', ('a_valider', m5.now(), ident))
            desk.db.commit()
            return 'a_valider'
        state = ''
        for replace in ((True, False) if row['calendar_uid'] else (False, True)):
            try:
                uid = dav.put_event(urls[0], ident, title, start, start + timedelta(minutes=30), description, replace=replace)
                state = 'mis_a_jour' if replace else 'cree'
                desk.db.execute('UPDATE limitation500 SET calendar_uid=? WHERE id=?', (uid, ident))
                break
            except Stop as ex:
                state = 'erreur:' + str(ex)[:60]
                if str(ex) not in ('http_412', 'http_404'):
                    break
    desk.db.execute('UPDATE limitation500 SET calendar_state=?,updated=? WHERE id=?', (state, m5.now(), ident))
    desk.db.commit()
    return state


def daily_check(desk, dav=None, today=None):
    sent = run_reminders(desk, today)
    synced = 0
    if dav is not None:
        for r in desk.db.execute("SELECT id FROM limitation500 WHERE status IN ('a_confirmer','confirmee','manuel') AND due<>'' AND calendar_state IN ('','erreur:http_404')"):
            try:
                sync_calendar(desk, dav, r[0])
                synced += 1
            except Exception:
                pass
    return {'reminders': sent, 'calendar': synced}


def counts(desk, today=None, horizon=60):
    today = today or date.today()
    near = overdue = to_confirm = 0
    for r in listing(desk):
        if r['status'] == 'a_confirmer':
            to_confirm += 1
        if r['due']:
            days = (date.fromisoformat(r['due']) - today).days
            if days < 0:
                overdue += 1
            elif days <= horizon:
                near += 1
    return {'near': near, 'overdue': overdue, 'to_confirm': to_confirm}
