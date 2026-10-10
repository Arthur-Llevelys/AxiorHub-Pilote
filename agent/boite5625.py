"""5.6.25 : boîte de réception dans « Courriels » — voir tous les courriels, en choisir un et demander à l'agent d'y répondre.

- Liste : les courriels du dossier de réception IMAP, du plus récent au plus ancien, par pages (lecture seule, sans marquer
  « lu »). Pour chacun : expéditeur, objet, date, pièces jointes probables, état connu de l'agent (traité, ignoré, brouillon…).
- Courriel : texte, pièces jointes (nom, type, taille, texte extrait à la demande), dossier rattaché ou proposé, réponses déjà
  préparées (brouillons qui y répondent, avec leurs actions : modifier, supprimer, relancer).
- Réponse demandée par l'avocat : même chemin que la réponse automatique (``prepare_reply`` : sources du dossier, contrôle,
  dépôt dans Brouillons, relecture IMAP), même pour un courriel que l'agent avait ignoré ou jamais lu. Le dossier est choisi
  par l'avocat ; son instruction est transmise ; le texte des pièces jointes peut être joint aux sources. Jamais d'envoi.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re

from .common import Stop, digest, load_matters
from . import mailbox as mbx   # référence au module : remplaçable par les essais

PAGE = 25
MAX_TEXT = 30000
ATTACHMENT_TEXT = 6000


def _account(cfg):
    return cfg['username'] + '@' + cfg['host']


def _uid(value):
    value = str(value or '')
    if not re.fullmatch(r'\d{1,12}', value):
        raise Stop('courriel_invalide')
    return value


def _state(desk):
    from .state import State
    return State(desk.c['state_dir'])


STATUS_LABELS = {'drafted': 'Brouillon préparé', 'ignored': 'Ignoré par l’agent', 'observed': 'Analysé (observation)', 'review': 'À examiner',
                 'error': 'Erreur', 'retry': 'À reprendre', 'appending': 'Dépôt en cours', 'append_uncertain': 'Dépôt à vérifier', 'manual': 'Réponse demandée'}


def listing(desk, page=1, query='', box=None):
    """Page de la boîte de réception (du plus récent au plus ancien)."""
    cfg = desk.c['mail']
    folder = cfg['inbox']
    page = max(1, int(page or 1))
    query = re.sub(r'\s+', ' ', str(query or '')).strip()[:120]
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        if query and query.isascii():
            uids = box.search(folder, 'UNDELETED', 'OR', 'SUBJECT', mbx.imap_quote(query), 'FROM', mbx.imap_quote(query))
        else:
            uids = box.search(folder, 'UNDELETED')
        uids = sorted(uids, key=int, reverse=True)
        if query and not query.isascii():   # recherche accentuée : filtrage des 300 plus récents côté AxiorHub
            uids = uids[:300]
        total = len(uids)
        state = _state(desk)
        account = _account(cfg)
        items = []
        chosen = uids if (query and not query.isascii()) else uids[(page - 1) * PAGE:page * PAGE]
        for uid in chosen:
            try:
                mail = box.fetch(folder, uid, headers_only=True)
            except Stop:
                continue
            subject = mail.subject or '(sans objet)'
            sender = str(mail.msg.get('From', ''))[:200]
            if query and not query.isascii() and query.lower() not in (subject + ' ' + sender).lower():
                continue
            key = mail.key(account)
            known = state.get(key)
            items.append({'uid': mail.uid, 'key': key, 'subject': subject[:200], 'from': sender, 'date': mail.timestamp.isoformat(),
                          'seen': '\\Seen' in mail.flags, 'attachments': mail.msg.get_content_type() == 'multipart/mixed',
                          'status': known[0] if known else '', 'status_label': STATUS_LABELS.get(known[0], known[0]) if known else ''})
            if len(items) >= PAGE:
                break
        if query and not query.isascii():
            total = len(items)
        return {'folder': folder, 'page': page, 'pages': max(1, (total + PAGE - 1) // PAGE), 'total': total, 'items': items, 'query': query}
    finally:
        if own:
            box.close()


def _attachments(msg):
    out = []
    for n, part in enumerate(msg.iter_attachments()):
        payload = part.get_payload(decode=True) or b''
        out.append({'index': n, 'name': str(part.get_filename() or 'pièce %d' % (n + 1))[:200], 'type': part.get_content_type(), 'size': len(payload)})
    return out


def _guess_matter(desk, mail, report):
    if report and report.get('matter'):
        return str(report['matter']), 'rapport de l’agent'
    try:
        from .cockpit530 import guess
        g = guess(desk, (mail.subject + ' ' + str(mail.msg.get('From', '')) + ' ' + mail.text[:1500]))
        if g and g.get('matter'):
            return str(g['matter']), 'proposé d’après l’objet et l’expéditeur'
    except Exception:
        pass
    return '', ''


def replies(box, cfg, mail):
    """Brouillons qui répondent à ce courriel (en-tête In-Reply-To), du plus récent au plus ancien."""
    if not mail.mid:
        return []
    out = []
    try:
        uids = box.search(cfg['drafts'], 'UNDELETED', 'HEADER', 'In-Reply-To', mbx.imap_quote(mail.mid))
    except Stop:
        return []
    for uid in sorted(uids, key=int, reverse=True)[:10]:
        try:
            d = box.fetch(cfg['drafts'], uid, headers_only=True)
        except Stop:
            continue
        out.append({'uid': d.uid, 'uidvalidity': d.uidvalidity, 'subject': d.subject[:200], 'date': d.timestamp.isoformat(),
                    'agent': str(d.msg.get('Message-ID', '')).startswith('<axiorhub-')})
    return out


def message(desk, uid, box=None):
    cfg = desk.c['mail']
    uid = _uid(uid)
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        mail = box.fetch(cfg['inbox'], uid)
        key = mail.key(_account(cfg))
        try:
            from .desk import report_for
            report = report_for(desk.c, key)
        except Stop:
            report = None
        matter, why = _guess_matter(desk, mail, report)
        known = _state(desk).get(key)
        text = mail.text
        return {'uid': mail.uid, 'key': key, 'subject': mail.subject, 'from': str(mail.msg.get('From', ''))[:300], 'to': str(mail.msg.get('To', ''))[:500],
                'cc': str(mail.msg.get('Cc', ''))[:500], 'date': mail.timestamp.isoformat(), 'text': text[:MAX_TEXT], 'truncated': len(text) > MAX_TEXT,
                'attachments': _attachments(mail.msg), 'matter': matter, 'matter_reason': why,
                'status': known[0] if known else '', 'status_label': STATUS_LABELS.get(known[0], known[0]) if known else 'Jamais traité par l’agent',
                'reason': (report or {}).get('reason', ''), 'replies': replies(box, cfg, mail),
                'own': mail.sender.lower() in {a.lower() for a in mbx.own_addresses(cfg)}}
    finally:
        if own:
            box.close()


def attachment_text(desk, uid, index, box=None):
    """Texte extrait d'une pièce jointe (lecture locale bornée)."""
    from .documents import extract
    cfg = desk.c['mail']
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        mail = box.fetch(cfg['inbox'], _uid(uid))
        parts = list(mail.msg.iter_attachments())
        try:
            part = parts[int(index)]
        except (ValueError, IndexError):
            raise Stop('piece_jointe_absente') from None
        name = str(part.get_filename() or 'piece')
        raw = part.get_payload(decode=True) or b''
        text = extract(raw, name, {**desk.c.get('documents', {}), 'max_document_chars': 200000})
        return {'name': name, 'text': text[:60000], 'truncated': len(text) > 60000}
    finally:
        if own:
            box.close()


def attachment_sources(desk, mail):
    """Sources « pièce jointe » pour la réponse demandée : texte extrait de chaque pièce lisible (borné), erreurs nommées."""
    from .documents import extract
    sources, notes = [], []
    for n, part in enumerate(mail.msg.iter_attachments()):
        name = str(part.get_filename() or 'pièce %d' % (n + 1))
        raw = part.get_payload(decode=True) or b''
        try:
            text = extract(raw, name, {**desk.c.get('documents', {}), 'max_document_chars': 200000})
        except Stop as ex:
            notes.append('%s : %s' % (name, str(ex)))
            continue
        sources.append({'id': 'piece-jointe-%d' % (n + 1), 'kind': 'email_attachment', 'path': name, 'excerpt': text[:ATTACHMENT_TEXT],
                        'partial': len(text) > ATTACHMENT_TEXT, 'modified': mail.timestamp.isoformat()})
        if len(sources) >= 5:
            break
    return sources, notes


def request_reply(desk, uid, matter, instruction='', with_attachments=True, owner='cabinet', box=None):
    """Réponse demandée par l'avocat pour un courriel choisi : rapport créé ou complété (dossier choisi), puis travail
    « prepare_reply » (sources du dossier, contrôle, dépôt vérifié dans Brouillons). Rien n'est envoyé."""
    cfg = desk.c['mail']
    uid = _uid(uid)
    instruction = re.sub(r'[ \t]+', ' ', str(instruction or '')).strip()
    if len(instruction) > 4000:
        raise Stop('instruction_trop_longue_4000_maximum')
    matters = {m['id']: m for m in load_matters(desk.c)}
    if matter not in matters:
        raise Stop('dossier_a_choisir')
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        mail = box.fetch(cfg['inbox'], uid)
    finally:
        if own:
            box.close()
    account = _account(cfg)
    if not mail.sender:
        raise Stop('expediteur_illisible')
    if mail.sender.lower() in {a.lower() for a in mbx.own_addresses(cfg)}:
        raise Stop('courriel_envoye_par_le_cabinet')
    key = mail.key(account)
    state = _state(desk)
    try:
        from .desk import report_for
        report = report_for(desk.c, key)
    except Stop:
        report = {'key': key, 'subject': mail.subject, 'sender': mail.sender, 'source_uid': mail.uid, 'source_mailbox': mail.mailbox,
                  'received_at': mail.timestamp.isoformat(), 'mode': desk.c.get('mode', 'drafts'), 'sources': [], 'incoming_message_id': mail.mid,
                  'account_key': digest(account), 'model': desk.c.get('ollama', {}).get('model', ''), 'started_at': datetime.now(timezone.utc).isoformat()}
    report.update({'matter': matter, 'reply_recipients': [mail.sender], 'manual_request': {'by': owner, 'at': desk.now(), 'instruction': instruction[:500],
                                                                                         'attachments': bool(with_attachments)}})
    if not report.get('status'):
        report.update({'status': 'manual', 'reason': 'reponse_demandee_par_avocat'})
    state.report(key, report)
    known = state.get(key)
    if not known:
        state.set(key, digest(account + mail.mid), digest(account + mail.root), 'manual', 'reponse_demandee_par_avocat')
    job = desk.enqueue('prepare_reply', {'key': key, 'matter': matter, 'instruction': instruction, 'pieces_jointes': 'oui' if with_attachments else '',
                                         'origine': 'boite5625'}, priority=0)
    desk.audit('boite5625_reponse_demandee', {'key': key[:16], 'matter': matter, 'job': job, 'instruction': bool(instruction), 'attachments': bool(with_attachments)})
    return {'job_id': job, 'key': key, 'message': 'Réponse demandée à l’agent (travail n° %d). Elle arrivera dans « À relire » (dossier Brouillons) ; rien n’est envoyé.' % job}


def discard_reply(desk, uid, validity):
    """Supprime une réponse préparée : déplacement dans la corbeille de la messagerie (récupérable)."""
    from .drafts440 import discard_draft
    out = discard_draft(desk.c['mail'], uid, validity, 'yes')
    desk.audit('boite5625_reponse_supprimee', {'uid': str(uid)})
    return {**out, 'message': 'Réponse supprimée' + (' (déplacée dans la corbeille de la messagerie).' if out.get('moved_to_trash') else '.')}
