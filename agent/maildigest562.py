"""Résumé des courriels reçus, demandé dans la zone de saisie du poste de pilotage (AxiorHub Pilote 5.6.2).

« Fais-moi un résumé des mails reçus aujourd'hui », « quels courriels hier ? », « point sur les mails de cette semaine » : la réponse
est construite immédiatement, sur le serveur, à partir des rapports de traitement (heure, expéditeur, objet, dossier, suite donnée).
Aucun modèle d'IA n'est appelé, aucun dossier n'est deviné, rien ne passe par la file de travail.
"""
from datetime import date, datetime, time, timedelta, timezone
import re

from .common import Stop, fold

MAIL_WORDS = r'(?:e ?-?mails?|mails?|courriels?|messages?|correspondances?)'
ASK_WORDS = r'(?:resume|resumer|recap|recapitul\w*|synthese|synthetis\w*|liste|lister|point|bilan|quels?|quelles?|combien|qu ?est ?ce|montre|affiche|ai ?je|j ?ai ?recu)'
MONTHS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8, 'septembre': 9,
          'octobre': 10, 'novembre': 11, 'decembre': 12}
DONE_LABELS = (('review', 'À traiter par vous'), ('drafted', 'Brouillon de réponse prêt (dossier Brouillons)'),
               ('observed', 'Analysés en observation (aucun brouillon écrit)'), ('error', 'Traitement interrompu'),
               ('retry', 'En cours de reprise'))


def _norm(text):
    return ' ' + re.sub(r'[^a-z0-9]+', ' ', fold(str(text or ''))) + ' '


def period(text, today):
    """(début, fin, libellé) de la période demandée, en dates locales ; None si la phrase ne parle pas d'une période."""
    f = _norm(text)
    m = re.search(r' (\d{1,2}) (%s) (\d{4}) ' % '|'.join(MONTHS), f) or re.search(r' (\d{1,2}) (%s) ' % '|'.join(MONTHS), f)
    if m:
        try:
            d = date(int(m.group(3)) if m.lastindex >= 3 else today.year, MONTHS[m.group(2)], int(m.group(1)))
            return d, d, 'le %s' % d.strftime('%d/%m/%Y')
        except ValueError:
            pass
    m = re.search(r' (\d{1,2}) (\d{1,2}) (\d{4}) ', f)
    if m:
        try:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return d, d, 'le %s' % d.strftime('%d/%m/%Y')
        except ValueError:
            pass
    if ' avant hier ' in f:
        d = today - timedelta(days=2)
        return d, d, 'avant-hier'
    if ' depuis hier ' in f:
        return today - timedelta(days=1), today, 'depuis hier'
    if ' hier ' in f:
        d = today - timedelta(days=1)
        return d, d, 'hier'
    if ' semaine derniere ' in f:
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6), 'la semaine dernière'
    if ' cette semaine ' in f or ' de la semaine ' in f:
        return today - timedelta(days=today.weekday()), today, 'cette semaine'
    if re.search(r' (aujourd hui|ce jour|ce matin|cet apres midi|de la journee|du jour) ', f):
        return today, today, 'aujourd’hui'
    return None


def detect(text, today):
    """Période demandée si la phrase demande un point sur les courriels reçus ; None sinon (autre question ou demande de document)."""
    f = _norm(text)
    if not re.search(' %s ' % MAIL_WORDS, f):
        return None
    if re.search(r' (redige|rediger|prepare|preparer|reponds|repondre|envoie|envoyer|ecris|ecrire) ', f) and not re.search(r' (resume|recap|synthese|liste) ', f):
        return None                       # « prépare une réponse au mail de… » reste une demande de travail
    asked = re.search(' %s ' % ASK_WORDS, f) or re.search(r' (recus?|arrives?|entrants?) ', f)
    if not asked:
        return None
    found = period(text, today)
    if found:
        return found
    if re.search(r' (recus?|arrives?|nouveaux?) ', f):
        return today, today, 'aujourd’hui'
    return None


def collect(desk, start, end, tz):
    """Courriels reçus entre deux dates locales (incluses), d'après les rapports de traitement."""
    from .desk import report_for
    from .state import State
    lo = datetime.combine(start, time.min, tz).astimezone(timezone.utc)
    hi = datetime.combine(end + timedelta(days=1), time.min, tz).astimezone(timezone.utc)
    state = State(desk.c['state_dir'])
    rows = state.db.execute('SELECT key,status,reason,updated FROM messages WHERE updated>=? ORDER BY updated DESC LIMIT 3000',
                            ((lo - timedelta(days=1)).isoformat(),)).fetchall()
    out = []
    for key, status, reason, _ in rows:
        try:
            report = report_for(desk.c, key)
        except Stop:
            continue
        try:
            received = datetime.fromisoformat(str(report.get('received_at') or ''))
        except ValueError:
            continue
        if not received.tzinfo:
            received = received.replace(tzinfo=timezone.utc)
        if lo <= received < hi:
            out.append({'key': key, 'status': status, 'reason': reason, 'received': received.astimezone(tz), 'subject': report.get('subject') or '',
                        'sender': report.get('sender') or '', 'matter': str(report.get('matter') or '')})
    out.sort(key=lambda x: x['received'])
    return out


def _who(sender):
    m = re.match(r'\s*"?([^"<]+?)"?\s*<([^>]+)>', sender or '')
    return (m.group(1).strip() if m else (sender or 'expéditeur inconnu')).strip()[:60]


def answer(desk, text, today=None, tz=None, matter=''):
    """Texte de la réponse, ou None si la phrase n'est pas une demande de point sur les courriels (limitée au dossier choisi s'il y en a un)."""
    from .cockpit530 import _labels, _reason, tz as cabinet_tz
    tz = tz or cabinet_tz(desk)
    today = today or datetime.now(tz).date()
    found = detect(text, today)
    if not found:
        return None
    start, end, label = found
    mails = collect(desk, start, end, tz)
    if matter:
        mails = [m for m in mails if m['matter'] == matter]
        label += ' pour ce dossier'
    if not mails:
        return ('Aucun courriel analysé %s. Les messages encore en attente d’analyse n’apparaissent pas ici : voir « Activité de '
                'l’assistant » en bas de l’écran.' % label)
    labels = _labels(desk)
    multi_day = start != end
    line = lambda m: '• %s — %s — « %s »%s' % (m['received'].strftime('%d/%m %H:%M' if multi_day else '%H:%M'), _who(m['sender']),
                                                (m['subject'] or '(sans objet)')[:110], (' — ' + labels[m['matter']]) if m['matter'] in labels else '')
    parts = ['%d courriel(s) reçu(s) %s.' % (len(mails), label)]
    for status, title in DONE_LABELS:
        group = [m for m in mails if m['status'] == status]
        if not group:
            continue
        lines = []
        for m in group[:25]:
            extra = (' — ' + _reason(m['reason'])) if status in ('review', 'error') and m['reason'] else ''
            lines.append(line(m) + extra)
        more = '\n… et %d autre(s).' % (len(group) - 25) if len(group) > 25 else ''
        parts.append('%s (%d)\n%s%s' % (title, len(group), '\n'.join(lines), more))
    ignored = [m for m in mails if m['status'] == 'ignored']
    if ignored:
        counts = {}
        for m in ignored:
            counts[_reason(m['reason']) if m['reason'] else 'sans motif'] = counts.get(_reason(m['reason']) if m['reason'] else 'sans motif', 0) + 1
        top = sorted(counts.items(), key=lambda x: -x[1])[:6]
        parts.append('Sans réponse nécessaire (%d) : %s.' % (len(ignored), ' ; '.join('%d — %s' % (n, k[:80]) for k, n in top)))
    parts.append('Source : rapports de traitement du serveur ; aucun contenu n’a été envoyé à une IA pour ce résumé.')
    return '\n\n'.join(parts)
