"""Préparation et compte rendu des rendez-vous (AxiorHub 5.0.0).

AVANT : pour chaque rendez-vous de l'agenda rattaché à un dossier, une fiche prête à lire — historique récent, points
ouverts, pièces, faits retenus, échéances proches — où chaque ligne indique sa source. Elle est assemblée à partir de
ce que le cabinet possède déjà (fiche de dossier 4.6, chronologie, échéances, prescriptions) ; aucun modèle n'est
appelé et rien n'est inventé.

APRÈS : un PROJET de compte rendu à partir de vos notes ou de la dictée. Les notes sont réorganisées (points abordés,
décisions, actions, points à confirmer) sans réécriture ni ajout : une action sans responsable ou sans date complète
est signalée « à préciser », jamais complétée. Le projet reste un brouillon jusqu'à votre validation.
"""
from .common import matter_display
from datetime import date, datetime, timedelta, timezone
import json
import re
import unicodedata

from .common import Stop, digest
from . import metier500 as m5

MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']
SECTION_TITLES = {'points': 'Points abordés', 'decisions': 'Décisions', 'actions': 'Actions à mener', 'a_confirmer': 'Points à confirmer'}
MARKERS = (('decisions', r'^(?:decisions?|conclusions?|accords?)\s*[:\-]'), ('actions', r'^(?:actions?|a faire|taches?|suites? a donner|prochaines? etapes?)\s*[:\-]'),
           ('a_confirmer', r'^(?:a confirmer|questions?(?: ouvertes?)?|points? ouverts?|en attente|a verifier)\s*[:\-]'),
           ('points', r'^(?:points? abordes?|sujets?|notes?|ordre du jour)\s*[:\-]'))
DECISION_RE = re.compile(r'\b(decid\w*|decision|convenu\w*|convient|accord(?:e|ons|ent)?|valid(?:e|ons|ent)|retenu\w*|choisi\w*|arrete\w*|il est decide|on part sur|nous partons sur)\b')
ACTION_RE = re.compile(r'\b(a faire|doit|doivent|devra|devront|prevoir|prevoyons|envoyer|relancer|preparer|rediger|transmettre|verifier|appeler|contacter|assigner|deposer|fournir|communiquer|programmer|planifier|reserver|demander)\b')
CONFIRM_RE = re.compile(r'(\?|\ba confirmer\b|\ba verifier\b|\ben attente\b|\breste a\b|\ba clarifier\b|\bpoint ouvert\b|\bincertain\b)')
DUE_FULL = re.compile(r'(?:avant|pour|d.ici|au plus tard|le|jusqu.au)\s+(?:le\s+)?(\d{1,2})(?:er)?\s+(janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)(?:\s+(\d{4}))?')
DUE_NUM = re.compile(r'(?:avant|pour|d.ici|au plus tard|le|jusqu.au)\s+(?:le\s+)?(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?')
WHO_RE = re.compile(r'\b(le client|la cliente|la cliente|client|nous|je|le cabinet|l.adversaire|le confrere|la partie adverse|le greffe|l.expert|le huissier|le commissaire de justice)\b')
NAME_BEFORE = re.compile(r'\b([A-ZÉÈÀ][\wéèêàçïëô-]{2,})\s+(?:doit|doivent|devra|devront|va|vont)\b')


def fold(text):
    text = unicodedata.normalize('NFKD', str(text or '').replace('’', "'"))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def fr_long(d):
    return '%d %s %d' % (d.day, MONTHS[d.month - 1], d.year)


def _parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------ agenda
def _event(desk, event_id):
    row = desk.db.execute('SELECT * FROM calendar_cache WHERE id=?', (event_id,)).fetchone()
    if not row:
        raise Stop('evenement_absent')
    return dict(row)


def upcoming(desk, days=7, past_days=2, today=None):
    """Rendez-vous de l'agenda autour d'aujourd'hui, avec l'état de leur fiche."""
    m5.ensure_schema(desk)
    today = today or date.today()
    start = (today - timedelta(days=past_days)).isoformat()
    end = (today + timedelta(days=days)).isoformat()
    try:
        rows = desk.db.execute("SELECT id,title,starts,ends,location,matter,matter_candidates FROM calendar_cache "
                               "WHERE substr(starts,1,10)>=? AND substr(starts,1,10)<=? AND busy<>0 ORDER BY starts LIMIT 120", (start, end)).fetchall()
    except Exception:
        rows = []
    out = []
    labels = m5.matter_index(desk)
    for r in rows:
        stored = desk.db.execute('SELECT generated FROM meeting500_fiches WHERE event_id=?', (r['id'],)).fetchone()
        report = desk.db.execute('SELECT status FROM meeting500_reports WHERE event_id=?', (r['id'],)).fetchone()
        m = labels.get(r['matter'])
        out.append({'id': r['id'], 'title': r['title'] or 'Sans titre', 'starts': r['starts'], 'ends': r['ends'], 'location': r['location'],
                    'matter': r['matter'], 'matter_label': (matter_display(m) if m else ''),
                    'past': str(r['starts'])[:10] < today.isoformat(), 'has_time': 'T' in str(r['starts']),
                    'fiche': 'prete' if stored else ('sans_dossier' if not r['matter'] else 'a_preparer'),
                    'fiche_at': stored[0] if stored else '', 'report': report[0] if report else ''})
    return out


# -------------------------------------------------------------------------------------------------- fiche
def build(desk, event_id, today=None):
    from . import fiche460, echeances450 as ech, limitation500 as lim, time500
    m5.ensure_schema(desk)
    today = today or date.today()
    ev = _event(desk, event_id)
    start, end = _parse_dt(ev['starts']), _parse_dt(ev['ends'])
    duration = int((end - start).total_seconds() // 60) if (start and end and 'T' in str(ev['ends']) and end > start) else 0
    content = {'event': {'id': ev['id'], 'title': ev['title'], 'starts': ev['starts'], 'ends': ev['ends'], 'location': ev['location'],
                         'description': (ev['description'] or '')[:800], 'duration_minutes': duration},
               'generated': m5.now(), 'matter': None, 'sections': {}, 'warnings': []}
    mid = ev['matter']
    if not mid:
        candidates = []
        try:
            candidates = json.loads(ev['matter_candidates'] or '[]')
        except ValueError:
            pass
        content['warnings'].append('Ce rendez-vous n’est rattaché à aucun dossier : aucune fiche de dossier ne peut être préparée. Rattachez-le depuis l’agenda%s.' % (
            ' (dossiers possibles : %s)' % ', '.join(str(c.get('id', c) if isinstance(c, dict) else c) for c in candidates[:4]) if candidates else ''))
        return content
    try:
        fiche = fiche460.build_fiche(desk, mid, today)
        history = fiche460.chronologie(desk, mid, 14)
    except Stop:
        content['warnings'].append('Le dossier de ce rendez-vous n’est plus configuré.')
        return content
    content['matter'] = {'id': mid, 'label': m5.matter_label(desk, mid)}
    s = content['sections']
    s['parties'] = [{'name': p['value'] or p['title'], 'status': p['status_label'], 'source': (p['sources'][0]['label'] if p['sources'] else '')} for p in fiche['parties']][:10]
    s['historique'] = [{'at': h['at'][:10], 'kind': h['kind_label'], 'title': h['title'], 'source': h['source'], 'validated': h['validated']}
                       for h in history if h['at'][:10] <= today.isoformat()][:8]
    s['dernier_echange'] = {'recu': fiche.get('last_received'), 'envoye': fiche.get('last_sent')}
    points = [{'kind': p['kind'], 'label': p['label']} for p in fiche['pending']]
    nd = fiche.get('next_deadline')
    s['echeances'] = ([{'label': nd['label'], 'due': nd['due'], 'days': nd['days'], 'validated': nd['validated'], 'status': nd['status_label'], 'source': nd['source']}] if nd else [])
    s['prescriptions'] = [{'label': r['rule_label'], 'due': r['due'], 'status': r['status_label'], 'start': r['start_label'], 'articles': r['articles']}
                          for r in lim.listing(desk, mid) if r['due']][:5]
    summ = time500.summary(desk, mid)
    if summ['level'] in time500.ALERT_LEVELS:
        points.append({'kind': 'honoraires', 'label': '%s (%s %% du budget convenu).' % (time500.ALERT_LEVELS[summ['level']], str(summ['ratio_pct']).replace('.', ','))})
    if summ['pending_count']:
        points.append({'kind': 'temps', 'label': '%d proposition(s) de temps à valider.' % summ['pending_count']})
    s['points_ouverts'] = points
    s['pieces'] = [{'name': d['name'], 'modified': str(d['modified'])[:10], 'path': d['path']} for d in fiche['documents']]
    s['faits'] = [{'text': f['value'] or f['title'], 'status': f['status_label'], 'validated': f['validated'],
                   'source': (f['sources'][0]['label'] if f['sources'] else '')} for f in fiche['facts'] if f['validated']][:8]
    s['faits_a_valider'] = fiche['facts_to_validate']
    conflicts = _conflict_note(desk, mid)
    if conflicts:
        content['warnings'].append(conflicts)
    if not s['historique'] and not s['pieces']:
        content['warnings'].append('Peu d’éléments sont connus pour ce dossier : la fiche est courte parce que l’information manque, pas parce qu’il n’y a rien à dire.')
    return content


def _conflict_note(desk, mid):
    try:
        from . import conflicts500
        state = conflicts500.matter_gate_state(desk, mid)
        if state.get('gated'):
            return 'La recherche de conflits d’intérêts de ce dossier n’a pas encore été examinée.'
    except Exception:
        pass
    return ''


def signature(content):
    keep = {k: v for k, v in content.items() if k != 'generated'}
    return digest(json.dumps(keep, sort_keys=True, ensure_ascii=False))


def fiche(desk, event_id, refresh=False, today=None):
    m5.ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM meeting500_fiches WHERE event_id=?', (event_id,)).fetchone()
    if row and not refresh:
        out = json.loads(row['content'])
        out['stored'] = True
        m5.log_access(desk, 'rendez-vous', 'fiche', row['matter'])
        return out
    content = build(desk, event_id, today)
    mid = (content.get('matter') or {}).get('id', '')
    if mid:
        desk.db.execute('INSERT OR REPLACE INTO meeting500_fiches VALUES(?,?,?,?,?)',
                        (event_id, mid, content['generated'], signature(content), json.dumps(content, ensure_ascii=False)))
        desk.db.commit()
        desk.audit('rendez_vous_500_fiche', {'matter': mid})
    m5.log_access(desk, 'rendez-vous', 'fiche', mid)
    content['stored'] = bool(mid)
    return content


def prepare_ahead(desk, hours=48, limit=8, today=None, stale_hours=6):
    """Prépare (ou actualise) les fiches des rendez-vous à venir. Appelé par la maintenance, sans intervention."""
    m5.ensure_schema(desk)
    today = today or date.today()
    done = 0
    for e in upcoming(desk, days=max(1, hours // 24), past_days=0, today=today):
        if done >= limit:
            break
        if not e['matter'] or e['past']:
            continue
        stored = desk.db.execute('SELECT generated FROM meeting500_fiches WHERE event_id=?', (e['id'],)).fetchone()
        if stored:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(stored[0])).total_seconds() / 3600
            except ValueError:
                age = 99
            if age < stale_hours:
                continue
        try:
            fiche(desk, e['id'], refresh=True, today=today)
            done += 1
        except Stop:
            continue
    return done


def markdown(content):
    ev, s = content['event'], content.get('sections', {})
    lines = ['# Fiche de préparation — %s' % ev['title'], '']
    start = _parse_dt(ev['starts'])
    lines.append('- Date : %s' % (fr_long(start.date()) + (' à %02d h %02d' % (start.hour, start.minute) if 'T' in str(ev['starts']) else '') if start else ev['starts']))
    if ev.get('location'):
        lines.append('- Lieu : %s' % ev['location'])
    if content.get('matter'):
        lines.append('- Dossier : %s' % content['matter']['label'])
    lines.append('')
    for w in content.get('warnings', []):
        lines.append('> ' + w)
    def block(title, rows):
        if rows:
            lines.extend(['## ' + title, ''] + rows + [''])
    block('Parties', ['- %s (%s) — source : %s' % (p['name'], p['status'], p['source']) for p in s.get('parties', [])])
    block('Historique récent', ['- %s — %s : %s (source : %s)%s' % (h['at'], h['kind'], h['title'], h['source'], '' if h['validated'] else ' [non validé]') for h in s.get('historique', [])])
    block('Points ouverts', ['- ' + p['label'] for p in s.get('points_ouverts', [])])
    block('Échéances', ['- %s : %s (%s, dans %d j)' % (e['label'], e['due'], e['status'], e['days']) for e in s.get('echeances', [])])
    block('Prescriptions', ['- %s : dernier jour utile %s (%s)' % (p['label'], p['due'], p['status']) for p in s.get('prescriptions', [])])
    block('Pièces récentes', ['- %s (modifiée le %s)' % (d['name'], d['modified']) for d in s.get('pieces', [])])
    block('Faits retenus', ['- %s (source : %s)' % (f['text'], f['source']) for f in s.get('faits', [])])
    return '\n'.join(lines)


# ------------------------------------------------------------------------------------- compte rendu
def _clean(text):
    text = re.sub(r'^[\s\-–•*>\d.)]+', '', text.strip())
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return ''
    text = text[0].upper() + text[1:]
    return text if text[-1] in '.!?…' else text + '.'


def _segments(notes):
    out = []
    for raw in str(notes or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        explicit = bool(re.match(r'^\s*(?:[-–•*]|\d+[.)])\s+', raw))
        parts = [line] if explicit else [p for p in re.split(r'(?<=[.!?])\s+(?=[A-ZÉÈÀ])', line) if p.strip()]
        out.extend(parts)
    return out


DUE_DAY = re.compile(r'\b(?:avant|pour|d.ici|au plus tard)(?: le)? (\d{1,2})(?:er)?\b(?!\s*(?:h\b|heure|:|/|\d|euro|%|jours|j\b|mois|semaine))')


def _due(folded, original):
    m = DUE_FULL.search(folded)
    if m:
        day, month = int(m.group(1)), ['janvier', 'fevrier', 'mars', 'avril', 'mai', 'juin', 'juillet', 'aout', 'septembre', 'octobre', 'novembre', 'decembre'].index(m.group(2)) + 1
        if m.group(3):
            try:
                d = date(int(m.group(3)), month, day)
                return {'text': fr_long(d), 'iso': d.isoformat(), 'complete': True}
            except ValueError:
                return {'text': m.group(0).strip(), 'iso': '', 'complete': False}
        return {'text': '%d %s (année à préciser)' % (day, MONTHS[month - 1]), 'iso': '', 'complete': False}
    m = DUE_NUM.search(folded)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            if m.group(3):
                year = int(m.group(3))
                year += 2000 if year < 100 else 0
                try:
                    d = date(year, month, day)
                    return {'text': fr_long(d), 'iso': d.isoformat(), 'complete': True}
                except ValueError:
                    pass
            return {'text': '%d %s (année à préciser)' % (day, MONTHS[month - 1]), 'iso': '', 'complete': False}
    m = DUE_DAY.search(folded)
    if m and 1 <= int(m.group(1)) <= 31:
        return {'text': 'le %s (mois et année à préciser)' % m.group(1), 'iso': '', 'complete': False}
    if re.search(r'sous huitaine|d.ici la fin du mois|rapidement|des que possible|au plus vite', folded):
        return {'text': re.search(r'sous huitaine|d.ici la fin du mois|rapidement|des que possible|au plus vite', folded).group(0), 'iso': '', 'complete': False}
    return None


def _who(folded, original):
    m = NAME_BEFORE.search(original)
    if m and fold(m.group(1)) not in ('nous', 'client'):
        return m.group(1)
    m = WHO_RE.search(folded)
    if m:
        who = m.group(1)
        return {'nous': 'le cabinet', 'je': 'le cabinet'}.get(who, who)
    return ''


def structure_notes(notes):
    notes = str(notes or '')[:20000]
    if not notes.strip():
        raise Stop('notes_vides')
    sections = {k: [] for k in SECTION_TITLES}
    current = ''
    for seg in _segments(notes):
        folded = fold(seg)
        marker = next((key for key, pattern in MARKERS if re.match(pattern, folded)), '')
        if marker:
            current = marker
            rest = re.sub(r'^[^:\-–]+[:\-–]\s*', '', seg).strip()
            if not rest:
                continue
            seg, folded = rest, fold(rest)
        key = current
        if not key:
            if CONFIRM_RE.search(folded):
                key = 'a_confirmer'
            elif DECISION_RE.search(folded):
                key = 'decisions'
            elif ACTION_RE.search(folded):
                key = 'actions'
            else:
                key = 'points'
        text = _clean(seg)
        if not text:
            continue
        item = {'text': text}
        if key == 'actions':
            item['who'] = _who(folded, seg)
            due = _due(folded, seg)
            item['due'] = due
        sections[key].append(item)
    return sections


def draft(desk, event_id, notes):
    ev = _event(desk, event_id)
    sections = structure_notes(notes)
    start = _parse_dt(ev['starts'])
    when = fr_long(start.date()) if start else str(ev['starts'])[:10]
    lines = ['COMPTE RENDU DE RENDEZ-VOUS (PROJET)', '',
             'Rendez-vous : %s' % (ev['title'] or 'sans titre'), 'Date : %s' % when]
    if ev['location']:
        lines.append('Lieu : %s' % ev['location'])
    if ev['matter']:
        lines.append('Dossier : %s' % m5.matter_label(desk, ev['matter']))
    lines += ['Participants : à compléter', '']
    warnings = []
    for key in ('points', 'decisions', 'actions', 'a_confirmer'):
        items = sections[key]
        if not items:
            continue
        lines.append(SECTION_TITLES[key].upper())
        for it in items:
            extra = ''
            if key == 'actions':
                who = it.get('who') or 'responsable à préciser'
                due = it.get('due')
                extra = ' (%s ; %s)' % (who, ('échéance : ' + due['text']) if due else 'échéance à préciser')
                if not it.get('who'):
                    warnings.append('Action sans responsable : « %s »' % it['text'][:80])
                if not due or not due['complete']:
                    warnings.append('Action sans date complète : « %s »' % it['text'][:80])
            lines.append('- ' + it['text'] + extra)
        lines.append('')
    lines.append('Projet établi à partir de vos notes, sans ajout ni réécriture : à relire et à valider avant tout usage.')
    return {'event_id': event_id, 'matter': ev['matter'], 'text': '\n'.join(lines), 'sections': sections, 'warnings': warnings[:20]}


def save_report(desk, event_id, text, status='brouillon'):
    m5.ensure_schema(desk)
    ev = _event(desk, event_id)
    if status not in ('brouillon', 'valide'):
        raise Stop('etat_compte_rendu_invalide')
    text = str(text or '').strip()[:30000]
    if not text:
        raise Stop('notes_vides')
    stamp = m5.now()
    old = desk.db.execute('SELECT created FROM meeting500_reports WHERE event_id=?', (event_id,)).fetchone()
    desk.db.execute('INSERT OR REPLACE INTO meeting500_reports VALUES(?,?,?,?,?,?)', (event_id, ev['matter'], text, status, old[0] if old else stamp, stamp))
    desk.db.commit()
    desk.audit('rendez_vous_500_compte_rendu', {'matter': ev['matter'], 'status': status})
    m5.log_access(desk, 'rendez-vous', 'compte_rendu_' + status, ev['matter'])
    return {'saved': True, 'status': status}


def report(desk, event_id):
    m5.ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM meeting500_reports WHERE event_id=?', (event_id,)).fetchone()
    if row:
        m5.log_access(desk, 'rendez-vous', 'compte_rendu_lu', row['matter'])
    return dict(row) if row else None
