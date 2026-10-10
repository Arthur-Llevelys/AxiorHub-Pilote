"""5.6.25 : boîte de réception dans « Courriels » — voir tous les courriels, en choisir un et demander à l'agent d'y répondre.

- Liste : les courriels du dossier de réception IMAP, du plus récent au plus ancien, par pages (lecture seule, sans marquer
  « lu »). Pour chacun : expéditeur, objet, date, pièces jointes probables, état connu de l'agent (traité, ignoré, brouillon…).
- Courriel : texte, pièces jointes (nom, type, taille, texte extrait à la demande), dossier rattaché ou proposé, réponses déjà
  préparées (brouillons qui y répondent, avec leurs actions : modifier, supprimer, relancer).
- Réponse demandée par l'avocat : même chemin que la réponse automatique (``prepare_reply`` : sources du dossier, contrôle,
  dépôt dans Brouillons, relecture IMAP), même pour un courriel que l'agent avait ignoré ou jamais lu. Le dossier est choisi
  par l'avocat ; son instruction est transmise ; le texte des pièces jointes peut être joint aux sources. Jamais d'envoi.
"""
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import PurePosixPath
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


ROLE_LABELS = (('inbox', 'Boîte de réception'), ('drafts', 'Brouillons'), ('sent', 'Envoyés'), ('outbox', 'Boîte d’envoi'),
               ('archive', 'Archives'), ('junk', 'Indésirables'), ('trash', 'Corbeille'))
NAME_ROLES = {'inbox': 'inbox', 'sent': 'sent', 'sent items': 'sent', 'sent messages': 'sent', 'sent mail': 'sent', 'envoyés': 'sent',
              'éléments envoyés': 'sent', 'messages envoyés': 'sent', 'drafts': 'drafts', 'brouillons': 'drafts', 'trash': 'trash',
              'deleted items': 'trash', 'deleted messages': 'trash', 'corbeille': 'trash', 'éléments supprimés': 'trash',
              'messages supprimés': 'trash', 'junk': 'junk', 'spam': 'junk', 'indésirables': 'junk', 'courrier indésirable': 'junk',
              'archive': 'archive', 'archives': 'archive', 'outbox': 'outbox', 'boîte d’envoi': 'outbox', "boîte d'envoi": 'outbox'}


def _folder_rows(box, cfg):
    """Dossiers sélectionnables de la messagerie : (nom IMAP, libellé, rôle), la boîte de réception d'abord."""
    from .setup import mailbox_names
    rows = []
    for name, flags in mailbox_names(box):
        if '\\noselect' in flags or '\\nonexistent' in flags:
            continue
        short = re.split(r'[./]', name)[-1] if name.upper() != 'INBOX' else 'INBOX'
        role = next((r for r in ('drafts', 'sent', 'trash', 'junk', 'archive') if '\\' + r in flags), '')
        role = role or NAME_ROLES.get(short.casefold(), '')
        if name == cfg.get('inbox'):
            role = 'inbox'
        elif name == cfg.get('drafts'):
            role = 'drafts'
        elif name == cfg.get('sent'):
            role = 'sent'
        label = dict(ROLE_LABELS).get(role) or (name[len('INBOX') + 1:] if name.upper().startswith(('INBOX.', 'INBOX/')) else name)
        rows.append({'name': name, 'label': label[:120], 'role': role})
    order = {r: n for n, (r, _) in enumerate(ROLE_LABELS)}
    seen, out = set(), []
    for row in sorted(rows, key=lambda r: (order.get(r['role'], len(order)), r['label'].casefold())):
        if row['role'] and row['role'] in seen and row['role'] != 'archive':
            row['label'] = row['label'] + ' (' + row['name'] + ')'
        seen.add(row['role'])
        out.append(row)
    return out


def folders(desk, box=None):
    """5.6.25 : dossiers de la messagerie (Envoyés, Brouillons, Corbeille, sous-dossiers…), sans aucun contenu."""
    cfg = desk.c['mail']
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        return {'folders': _folder_rows(box, cfg), 'inbox': cfg['inbox']}
    finally:
        if own:
            box.close()


def _folder(box, cfg, folder):
    """Dossier demandé : la boîte de réception par défaut, sinon un dossier existant de la messagerie (aucun nom inventé)."""
    folder = str(folder or '')
    if not folder or folder == cfg['inbox']:
        return cfg['inbox']
    if len(folder) > 300 or any(ord(ch) < 32 for ch in folder):
        raise Stop('dossier_messagerie_inconnu')
    if folder not in {row['name'] for row in _folder_rows(box, cfg)}:
        raise Stop('dossier_messagerie_inconnu')
    return folder


STATUS_LABELS = {'drafted': 'Brouillon préparé', 'ignored': 'Ignoré par l’agent', 'observed': 'Analysé (observation)', 'review': 'À examiner',
                 'error': 'Erreur', 'retry': 'À reprendre', 'appending': 'Dépôt en cours', 'append_uncertain': 'Dépôt à vérifier', 'manual': 'Réponse demandée'}


def listing(desk, page=1, query='', box=None, folder=None):
    """Page d'un dossier de la messagerie (boîte de réception par défaut), du plus récent au plus ancien."""
    cfg = desk.c['mail']
    try:
        page = max(1, int(page or 1))
    except ValueError:
        page = 1
    query = re.sub(r'\s+', ' ', str(query or '')).strip()[:120]
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        folder = _folder(box, cfg, folder)
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
            items.append({'uid': mail.uid, 'key': key, 'subject': subject[:200], 'from': sender, 'to': str(mail.msg.get('To', ''))[:200],
                          'date': mail.timestamp.isoformat(),
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


def message(desk, uid, box=None, folder=None):
    cfg = desk.c['mail']
    uid = _uid(uid)
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        folder = _folder(box, cfg, folder)
        mail = box.fetch(folder, uid)
        key = mail.key(_account(cfg))
        try:
            from .desk import report_for
            report = report_for(desk.c, key)
        except Stop:
            report = None
        matter, why = _guess_matter(desk, mail, report)
        known = _state(desk).get(key)
        text = mail.text
        return {'uid': mail.uid, 'key': key, 'folder': folder, 'subject': mail.subject, 'from': str(mail.msg.get('From', ''))[:300], 'to': str(mail.msg.get('To', ''))[:500],
                'cc': str(mail.msg.get('Cc', ''))[:500], 'date': mail.timestamp.isoformat(), 'text': text[:MAX_TEXT], 'truncated': len(text) > MAX_TEXT,
                'attachments': _attachments(mail.msg), 'matter': matter, 'matter_reason': why,
                'status': known[0] if known else '', 'status_label': STATUS_LABELS.get(known[0], known[0]) if known else 'Jamais traité par l’agent',
                'reason': (report or {}).get('reason', ''), 'replies': replies(box, cfg, mail),
                'own': mail.sender.lower() in {a.lower() for a in mbx.own_addresses(cfg)}}
    finally:
        if own:
            box.close()


def attachment_text(desk, uid, index, box=None, folder=None):
    """Texte extrait d'une pièce jointe (lecture locale bornée)."""
    from .documents import extract
    cfg = desk.c['mail']
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        mail = box.fetch(_folder(box, cfg, folder), _uid(uid))
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


def request_reply(desk, uid, matter, instruction='', with_attachments=True, owner='cabinet', box=None, folder=None, without_matter=False):
    """Réponse demandée par l'avocat pour un courriel choisi : rapport créé ou complété (dossier choisi), puis travail
    « prepare_reply » (sources du dossier, contrôle, dépôt vérifié dans Brouillons). Rien n'est envoyé."""
    cfg = desk.c['mail']
    uid = _uid(uid)
    instruction = re.sub(r'[ \t]+', ' ', str(instruction or '')).strip()
    if len(instruction) > 4000:
        raise Stop('instruction_trop_longue_4000_maximum')
    matters = {m['id']: m for m in load_matters(desk.c)}
    if without_matter and not matter:   # 5.6.25 : réponse sans dossier, à partir du courriel, des pièces jointes et de l'instruction
        matter = ''
    elif matter not in matters:
        raise Stop('dossier_a_choisir')
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        mail = box.fetch(_folder(box, cfg, folder), uid)
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
                                         'sans_dossier': 'oui' if not matter else '', 'origine': 'boite5625'}, priority=0)
    desk.audit('boite5625_reponse_demandee', {'key': key[:16], 'matter': matter, 'job': job, 'instruction': bool(instruction), 'attachments': bool(with_attachments)})
    return {'job_id': job, 'key': key, 'message': 'Réponse demandée à l’agent (travail n° %d). Elle arrivera dans « À relire » (dossier Brouillons) ; rien n’est envoyé.' % job}


def matter_proposal(desk):
    """5.6.25 : emplacement et référence proposés pour un nouveau dossier (à côté des dossiers existants, référence AAAAMMJJnnn libre)."""
    matters = load_matters(desk.c)
    parents = Counter(str(PurePosixPath(m['path']).parent) for m in matters if m.get('path'))
    roots = [str(r).rstrip('/') for r in desk.c.get('nextcloud', {}).get('roots', []) if str(r).strip('/')]
    parent = parents.most_common(1)[0][0] if parents else (roots[0] if roots else '')
    today = datetime.now().strftime('%Y%m%d')
    used = {m['id'] for m in matters}
    reference = next((today + '%03d' % n for n in range(1, 1000) if today + '%03d' % n not in used), today + '999')
    return {'parent': parent, 'reference': reference}


def _dav(desk):
    """Accès aux fichiers du cabinet (Nextcloud ou dossier local) ; message clair s'il n'est pas configuré."""
    from .dav import DAV
    cfg = desk.c.get('nextcloud') or {}
    if not (cfg.get('url') or cfg.get('local_path')):
        raise Stop('nextcloud_ou_dossier_local_a_configurer')
    return DAV(cfg)


def create_matter(desk, data, owner='cabinet'):
    """5.6.25 : crée le répertoire du nouveau dossier (« CLIENT - Affaire - référence ») sous une racine autorisée, l'enregistre et y
    rattache l'expéditeur du courriel comme correspondant. Rien n'est déplacé ni supprimé."""
    from .desk import save_matter
    from .workspace import create_matter as create
    clean = lambda v, n: re.sub(r'\s+', ' ', str(v or '')).strip()[:n]
    client, title, reference = clean(data.get('client_name'), 120), clean(data.get('title'), 120), clean(data.get('reference'), 80)
    parent = str(data.get('parent') or '').strip().rstrip('/')
    if len(client) < 2:
        raise Stop('nom_client_invalide')
    if any(ch in client + title for ch in '/\\') or any(ord(ch) < 32 for ch in client + title):
        raise Stop('nom_dossier_invalide')
    if not parent.startswith('/'):
        raise Stop('emplacement_du_dossier_a_choisir')
    name = ' - '.join(x for x in (client, title, reference) if x)
    path = parent + '/' + name
    out = create(desk, {'confirm': 'yes', 'path': path, 'reference': reference, 'client_name': client,
                        'references': clean(data.get('references'), 500)}, _dav(desk))
    email = clean(data.get('correspondent'), 200).lower()
    role = str(data.get('role') or 'client')
    from .desk import ROLES
    own = {a.lower() for a in mbx.own_addresses(desk.c['mail'])}
    if email and re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) and email not in own and role in ROLES:
        matter = next((m for m in load_matters(desk.c) if m['id'] == out['dossier']), None)
        if matter is not None:
            matter['correspondents'] = [p for p in matter.get('correspondents', []) if p['email'].lower() != email] + [{'email': email, 'role': role}]
            save_matter(desk.c, matter)
    desk.audit('boite5625_dossier_cree', {'id': out['dossier'], 'owner': owner})
    return {'id': out['dossier'], 'label': name, 'path': path,
            'message': 'Dossier « %s » créé dans Nextcloud et enregistré ; son indexation a démarré.' % name}


def discard_reply(desk, uid, validity):
    """Supprime une réponse préparée : déplacement dans la corbeille de la messagerie (récupérable)."""
    from .drafts440 import discard_draft
    out = discard_draft(desk.c['mail'], uid, validity, 'yes')
    desk.audit('boite5625_reponse_supprimee', {'uid': str(uid)})
    return {**out, 'message': 'Réponse supprimée' + (' (déplacée dans la corbeille de la messagerie).' if out.get('moved_to_trash') else '.')}
