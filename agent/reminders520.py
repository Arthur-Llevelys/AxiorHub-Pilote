"""Rappels 5.2.0 : échéances de procédure et prescriptions, par notification mobile et/ou courriel adressé à vous-même.

Les rappels sont ceux déjà calculés par les modules Échéances (J-30, J-14, J-7, J-2, jour J, dépassement) et Prescription
(J-90 … jour J, alerte sans demande en justice). Ce module ne fait que les acheminer hors de l'interface :
  - notification mobile : signal SANS contenu (aucune donnée de dossier ne transite par le service de notification) ;
  - courriel : un récapitulatif DÉPOSÉ (non lu) dans la boîte de réception du cabinet par IMAP — aucun envoi SMTP : le message ne peut
    parvenir à personne d'autre (AxiorHub reste incapable d'envoyer un courriel hors du module d'envoi facultatif et confirmé).
Désactivés par défaut ; aucun rappel n'est répété.
"""
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

from .common import Stop

KINDS = ('echeance', 'echeance_alerte', 'prescription', 'prescription_alerte')


def settings(desk):
    s = desk.settings('reminders520', {}) or {}
    return {'push': bool(s.get('push')), 'email': bool(s.get('email'))}


def own_address(desk):
    return str(desk.c.get('mail', {}).get('from_address', '') or '').strip()


def save(desk, push=False, email=False):
    if email and not own_address(desk):
        raise Stop('adresse_cabinet_absente')
    value = {'push': bool(push), 'email': bool(email)}
    desk.setting('reminders520', value)
    if not desk.settings('reminders520:last_id', 0):
        row = desk.db.execute('SELECT MAX(id) FROM live_events_v430').fetchone()
        desk.setting('reminders520:last_id', int(row[0] or 0))       # pas de rattrapage de l'historique à l'activation
    desk.audit('rappels_520_reglages', value)
    return value


def pending(desk, limit=50):
    last = int(desk.settings('reminders520:last_id', 0) or 0)
    q = 'SELECT id,at,kind,message,matter FROM live_events_v430 WHERE id>? AND kind IN (%s) ORDER BY id LIMIT ?' % ','.join('?' * len(KINDS))
    return [dict(r) for r in desk.db.execute(q, (last, *KINDS, limit))]


def _push(desk):
    from . import mobile490
    if not mobile490.vapid_available():
        return 0
    subject = 'mailto:' + (own_address(desk) or 'contact@invalid.example')
    sent = 0
    for row in desk.db.execute('SELECT id,endpoint FROM push_subs490').fetchall():
        try:
            headers = {'Authorization': mobile490.vapid_header(desk, row['endpoint'], subject), 'TTL': '3600', 'Urgency': 'high'}
        except Stop:
            continue
        code = (mobile490.http_post or mobile490._post)(row['endpoint'], headers)
        if code in (200, 201, 202):
            sent += 1
    return sent


def _email(desk, items, sender=None):
    """Récapitulatif déposé (non lu) dans la boîte de réception du cabinet par IMAP : aucun envoi, aucun tiers possible."""
    to = own_address(desk)
    if not to:
        raise Stop('adresse_cabinet_absente')
    msg = EmailMessage()
    msg['From'] = to
    msg['To'] = to
    msg['Subject'] = 'AxiorHub — %d rappel(s) d’échéance' % len(items)
    msg['Date'] = format_datetime(datetime.now(timezone.utc))
    msg['Message-ID'] = make_msgid(domain=to.rsplit('@', 1)[-1] or 'localhost')
    body = ['Rappels calculés par AxiorHub (à vérifier dans le dossier) :', '']
    for it in items:
        body.append('• ' + it['message'])
    body += ['', 'Ce message a été déposé directement dans votre boîte de réception par AxiorHub (aucun envoi). Réglage : Agenda et tâches › Réglages.']
    msg.set_content('\n'.join(body))
    if sender:
        sender(msg)
        return 1
    from .mailbox import Mailbox
    box = Mailbox(desk.c['mail'])
    try:
        box.append_reminder(msg)
    finally:
        box.close()
    return 1


def run(desk, sender=None):
    """Appelé par la maintenance : achemine les nouveaux rappels une seule fois."""
    cfg = settings(desk)
    if not (cfg['push'] or cfg['email']):
        return {'sent': 0, 'reason': 'desactive'}
    items = pending(desk)
    if not items:
        return {'sent': 0, 'reason': 'rien_de_nouveau'}
    result = {'push': 0, 'email': 0}
    if cfg['push']:
        try:
            result['push'] = _push(desk)
        except Exception:
            result['push'] = 0
    if cfg['email']:
        try:
            result['email'] = _email(desk, items, sender)
        except Stop as ex:
            desk.audit('rappels_520_courriel_non_envoye', {'reason': str(ex)})
            return {'sent': 0, 'reason': str(ex)}
    desk.setting('reminders520:last_id', items[-1]['id'])
    desk.audit('rappels_520_envoyes', {'count': len(items), **result})
    return {'sent': len(items), **result}
