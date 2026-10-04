"""Cockpit « Aujourd'hui » (AxiorHub 4.6.0) : cinq cartes au plus, classées par urgence."""
from datetime import date, datetime, timedelta, timezone
import json
from urllib.parse import urlencode

from .common import load_matters
from .improvements36 import matter_option

MAX_CARDS = 5
FOLDED_LIMIT = 40


def _parse(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d
    except ValueError:
        return None


def _age_days(value, now):
    d = _parse(value)
    if not d:
        return 0
    if not d.tzinfo:
        d = d.replace(tzinfo=timezone.utc)
    return max(0, (now - d).days)


def _when(days):
    if days < 0:
        return 'dépassée de %d j' % -days
    if days == 0:
        return 'aujourd’hui'
    if days == 1:
        return 'demain'
    return 'dans %d j' % days


def candidates(desk, today=None, now=None):
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    matters = {m['id']: matter_option(m) for m in load_matters(desk.c)}
    name = lambda mid: matters.get(mid, mid or 'Cabinet')
    cards = []

    def add(kind, score, title, detail, mid, label, href, urgency, secondary=None, key=''):
        cards.append({'kind': kind, 'score': score, 'title': title, 'detail': detail, 'matter': mid, 'matter_name': name(mid),
                      'action_label': label, 'href': href, 'urgency': urgency, 'secondary': secondary or [], 'key': key or kind + title})

    # 1. Délais
    try:
        from . import echeances450 as ech
        ech.ensure_schema(desk)
        for d in ech.listing(desk):
            if d['status'] == 'a_completer':
                add('delai', 600, 'Échéance à compléter : ' + d['rule_label'], d['error'] or 'Information manquante.', d['matter'],
                    'Compléter l’échéance', '/echeances#e-' + d['id'], 'À compléter', key='d' + d['id'])
                continue
            if not d['due']:
                continue
            days = (date.fromisoformat(d['due']) - today).days
            if days > 14:
                continue
            score = 1000 if days < 0 else 900 - min(days, 14) * 10 if days <= 14 else 400
            confirm = d['status'] == 'a_confirmer'
            add('delai', score, '%s – %s' % (d['rule_label'], d['due_label']),
                'Échéance %s%s. Source : %s.' % (_when(days), ', à confirmer' if confirm else '',
                                                 d['source_path'].rsplit('/', 1)[-1] if d['source_path'] else 'saisie à la main'),
                d['matter'], 'Confirmer l’échéance' if confirm else 'Voir le calcul', '/echeances#e-' + d['id'], _when(days),
                [('Ouvrir la fiche du dossier', '/fiche?' + urlencode({'matter': d['matter']}))], 'd' + d['id'])
    except Exception:
        pass
    # 2. Audiences et rendez-vous des 7 prochains jours (agenda déjà synchronisé)
    try:
        horizon = today + timedelta(days=7)
        for r in desk.db.execute("SELECT * FROM timeline_events WHERE source_kind='calendar' AND status<>'stale' ORDER BY event_at"):
            d = _parse(r['event_at'])
            if not d:
                continue
            day = d.date()
            if day < today or day > horizon:
                continue
            days = (day - today).days
            add('audience', 800 - days * 15, r['title'], 'Agenda %s à %s.' % (_when(days), d.strftime('%H:%M') if d.hour or d.minute else 'heure non précisée'),
                r['matter'], 'Ouvrir la fiche du dossier', '/fiche?' + urlencode({'matter': r['matter']}), _when(days), key='a' + r['id'])
    except Exception:
        pass
    # 3 et 4. Clients en attente et brouillons à valider
    try:
        for r in desk.db.execute("SELECT mail_key,state,matter,subject,sender,received,updated FROM work_items WHERE state IN ('needs_action','draft_ready') ORDER BY updated DESC LIMIT 60"):
            age = _age_days(r['received'] or r['updated'], now)
            subject = r['subject'] or 'Courriel sans objet'
            if r['state'] == 'needs_action':
                add('attente', 550 + min(age, 10) * 10, subject, 'Courriel de %s en attente de votre réponse depuis %d jour(s).' % (r['sender'] or 'un correspondant', age),
                    r['matter'], 'Ouvrir le courriel', '/mail?' + urlencode({'key': r['mail_key']}), 'En attente depuis %d j' % age, key='m' + r['mail_key'])
            else:
                add('brouillon', 350 + min(age, 10) * 5, subject, 'Brouillon préparé par l’agent, à relire avant envoi depuis votre messagerie.',
                    r['matter'], 'Relire le brouillon', '/courriels', 'À valider', key='b' + r['mail_key'])
    except Exception:
        pass
    # 5. Faits à valider
    try:
        for r in desk.db.execute("SELECT matter,COUNT(*) n FROM legal_memory_records WHERE status='suggested' GROUP BY matter"):
            add('faits', 200 + min(r['n'], 20), '%d fait(s) à valider' % r['n'], 'Proposés par l’agent à partir des pièces ; aucun n’est utilisé avant votre validation.',
                r['matter'], 'Valider les faits', '/fiche?' + urlencode({'matter': r['matter']}) + '#faits', 'À valider', key='f' + r['matter'])
    except Exception:
        pass
    # 6. Incidents de traitement
    try:
        n = desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='error'").fetchone()[0]
        if n:
            add('incident', 150, '%d traitement(s) en échec' % n, 'Un traitement de l’agent s’est interrompu.', '', 'Voir le diagnostic', '/etat-systeme', 'À examiner', key='incident')
    except Exception:
        pass
    cards.sort(key=lambda c: (-c['score'], c['title']))
    return cards


def build(desk, today=None, now=None):
    """Cinq cartes au plus ; le reste est compté et replié."""
    every = candidates(desk, today, now)
    shown, folded = every[:MAX_CARDS], every[MAX_CARDS:]
    return {'cards': shown, 'folded': folded[:FOLDED_LIMIT], 'folded_count': len(folded), 'total': len(every)}
