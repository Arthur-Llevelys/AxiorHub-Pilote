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


def _lire_autorise(desk, box, cfg, folder, uid, acteur=None, validity=None, expected_key=''):
    if acteur is None:                       # appels internes historiques, jamais utilisé par une route legacy protégée
        return box.fetch(folder, uid)
    # 5.6.100 (R04) : garde commune — sélection, UIDVALIDITY confirmée, rattachements du message ET de son projet, origine d'un brouillon
    # (en-têtes seulement), puis FETCH du corps dans la même session ; absent, autre boîte, UID réutilisé ou interdit : même 404
    from .acces_courriel56100 import lire_imap
    return lire_imap(desk, acteur, box, cfg, folder, uid, validity, cle_attendue=expected_key)[0]


RECHERCHE_LOCALE = 300   # recherche accentuée : messages examinés au plus (les plus récents), couverture partielle annoncée au-delà
LOT_RECHERCHE = 50       # en-têtes lus par commande pendant une recherche accentuée


def listing(desk, page=1, query='', box=None, folder=None, acteur=None):
    """Page d'un dossier de la messagerie (boîte de réception par défaut), du plus récent au plus ancien.

    5.6.100 (lot F, R07) : sur une vraie connexion, une seule sélection (EXAMINE), un UID SEARCH, puis un UID FETCH groupé des en-têtes
    utiles de la page autorisée (``Mailbox.entetes``) ; aucun corps. Avant : une sélection et deux FETCH par message (26 EXAMINE, 1 SEARCH,
    50 FETCH pour 25 lignes). Droits appliqués avant le total et la page ; clés et contrôle d'accès calculés avec l'UIDVALIDITY de cette
    sélection. Recherche accentuée : filtrée ici sur les ``RECHERCHE_LOCALE`` messages autorisés les plus récents, lus par lots ;
    ``couverture`` dit si des messages plus anciens n'ont pas été examinés. Les doubles d'essai historiques gardent l'ancien chemin."""
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
        accent = bool(query) and not query.isascii()
        criteres = ('UNDELETED', 'OR', 'SUBJECT', mbx.imap_quote(query), 'FROM', mbx.imap_quote(query)) if query and not accent else ('UNDELETED',)
        par_lot = mbx.lot_possible(box)
        validity = None
        if par_lot:
            validity = str(box.select(folder))          # la seule sélection de la page
            uids = box.search_selection(*criteres)
        else:
            uids = box.search(folder, *criteres)
        uids = sorted(uids, key=int, reverse=True)
        if accent and not par_lot:   # recherche accentuée : filtrage des 300 plus récents côté AxiorHub
            uids = uids[:RECHERCHE_LOCALE]
        from . import acces5697 as acces, messages5697 as refs
        caches = acces.masques(desk, acteur) if acteur is not None else ()
        if caches:
            if validity is None:
                validity = str(box.select(folder))
            account = _account(cfg)
            permis = []
            for start in range(0, len(uids), 400):
                lot = [(u, refs.cle_canonique(account, folder, validity, u)) for u in uids[start:start+400]]
                assoc = refs.dossiers_par_cle(desk, [cle for _, cle in lot])
                permis.extend(u for u, cle in lot if acces.visible(assoc.get(cle, ()), caches))
            uids = permis
        partielle = False
        if accent and par_lot:       # après les droits : les messages masqués ne comptent ni dans la couverture ni dans la page
            partielle = len(uids) > RECHERCHE_LOCALE
            uids = uids[:RECHERCHE_LOCALE]
        total = len(uids)
        state = _state(desk)
        account = _account(cfg)
        items = []

        def ajouter(mail):
            subject = mail.subject or '(sans objet)'
            sender = str(mail.msg.get('From', ''))[:200]
            if accent and query.lower() not in (subject + ' ' + sender).lower():
                return
            key = mail.key(account)
            known = state.get(key)
            items.append({'uid': mail.uid, 'key': key, 'subject': subject[:200], 'from': sender, 'to': str(mail.msg.get('To', ''))[:200],
                          'date': mail.timestamp.isoformat(),
                          'seen': '\\Seen' in mail.flags, 'attachments': mail.msg.get_content_type() == 'multipart/mixed',
                          'status': known[0] if known else '', 'status_label': STATUS_LABELS.get(known[0], known[0]) if known else ''})

        chosen = uids if accent else uids[(page - 1) * PAGE:page * PAGE]
        if par_lot:
            pas = LOT_RECHERCHE if accent else PAGE
            for start in range(0, len(chosen), pas):
                lot = chosen[start:start + pas]
                try:
                    trouves = box.entetes(folder, lot, expected_validity=validity)
                except Stop as ex:
                    if str(ex) == 'uidvalidity_modifiee':
                        raise Stop('introuvable') from None
                    raise
                for uid in lot:
                    if str(uid) in trouves:      # message disparu entre SEARCH et FETCH : absent, comme avant
                        ajouter(trouves[str(uid)])
                    if len(items) >= PAGE:
                        break
                if len(items) >= PAGE:
                    break
        else:
            for uid in chosen:
                try:
                    if validity is None:
                        mail = box.fetch(folder, uid, headers_only=True)
                    else:
                        mail = box.fetch(folder, uid, headers_only=True, expected_validity=validity)
                except Stop as ex:
                    if str(ex) == 'uidvalidity_modifiee':
                        raise Stop('introuvable') from None
                    continue
                ajouter(mail)
                if len(items) >= PAGE:
                    break
        if accent:
            total = len(items)
        out = {'folder': folder, 'page': page, 'pages': max(1, (total + PAGE - 1) // PAGE), 'total': total, 'items': items, 'query': query}
        if accent:
            out['couverture'] = {'partielle': partielle, 'examines': min(len(uids), RECHERCHE_LOCALE)}
        return out
    finally:
        if own:
            box.close()


def _attachments(msg):
    out = []
    for n, part in enumerate(msg.iter_attachments()):
        payload = part.get_payload(decode=True) or b''
        out.append({'index': n, 'name': str(part.get_filename() or 'pièce %d' % (n + 1))[:200], 'type': part.get_content_type(), 'size': len(payload)})
    return out


def _classement5697(desk, mail):
    """5.6.97 (lot dossiers, § 11) : dossier choisi à la main pour ce message (registre unique), s'il est visible du compte ; sinon None."""
    try:
        from . import dossiers5697
        from .membres5650 import current_author, viewer_role
        mid = dossiers5697.dossier_du_message(desk, _account(desk.c['mail']), mail.mailbox, mail.uidvalidity, mail.uid, mail.mid)
        if not mid:
            return None
        row = desk.db.execute('SELECT version FROM message_dossier5697 WHERE dossier_id=? AND compte=? AND boite=? AND uid=? ORDER BY modifie_le DESC LIMIT 1',
                              (mid, _account(desk.c['mail']), mail.mailbox, str(mail.uid))).fetchone()
        d = dossiers5697.lire(desk, (current_author() or 'cabinet', viewer_role()), mid)
        return {'dossier': d['id'], 'titre': d['titre'], 'reference': d['reference'], 'version': row['version'] if row else 1}
    except Stop:
        return None


def _demande5697(key):
    """5.6.97 : adresse (sans préfixe) de « Créer une demande depuis ce message », telle que la construit le lot documents-mission."""
    try:
        import html
        import re as _re
        from .mission_web5697 import lien_nouvelle_demande
        trouve = _re.search(r'href="([^"]+)"', lien_nouvelle_demande('', 'courriel', key))
        return html.unescape(trouve.group(1)) if trouve else ''
    except Exception:   # noqa: BLE001 — module indisponible : pas de lien
        return ''


def _guess_matter(desk, mail, report):
    manuel = _classement5697(desk, mail)   # 5.6.97 : le choix de l'avocat prévaut sur le rapport de l'agent et sur toute proposition
    if manuel:
        return manuel['dossier'], 'classé à la main'
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


def replies(box, cfg, mail, desk=None, acteur=None):
    """Brouillons qui répondent à ce courriel (en-tête In-Reply-To), du plus récent au plus ancien.

    5.6.100 (R04) : avec un acteur, chaque brouillon est résolu séparément (sa propre clé, le courriel ouvert comme origine vérifiée, la
    liaison de l'agent) : un brouillon rattaché par ailleurs à un dossier réservé n'est pas listé. Le Message-ID vient du serveur."""
    if not mail.mid:
        return []
    out, refs = [], {}
    try:
        uids = box.search(cfg['drafts'], 'UNDELETED', 'HEADER', 'In-Reply-To', mbx.imap_quote(mail.mid))
    except Stop:
        return []
    from . import acces_courriel56100 as ac
    for uid in sorted(uids, key=int, reverse=True)[:10]:
        try:
            d = box.fetch(cfg['drafts'], uid, headers_only=True)
        except Stop:
            continue
        out.append({'uid': d.uid, 'uidvalidity': d.uidvalidity, 'subject': d.subject[:200], 'date': d.timestamp.isoformat(),
                    'agent': str(d.msg.get('Message-ID', '')).startswith('<axiorhub-')})
        if acteur is not None:
            origine = ac._origine_agent(desk, mbx.draft_key(d.msg))
            refs[d.uid] = ac._ref('imap', d.key(_account(cfg)), cles=[mail.key(_account(cfg))] + ([origine] if origine else []))
    if acteur is None:
        return out
    gardes = {r.cle for r in ac.filtrer(desk, acteur, list(refs.values()))}
    return [x for x in out if refs[x['uid']].cle in gardes]


def message(desk, uid, box=None, folder=None, acteur=None, validity=None):
    cfg = desk.c['mail']
    uid = _uid(uid)
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        folder = _folder(box, cfg, folder)
        mail = _lire_autorise(desk, box, cfg, folder, uid, acteur, validity)
        key = mail.key(_account(cfg))
        try:
            from .desk import report_for
            report = report_for(desk.c, key)
        except Stop:
            report = None
        matter, why = _guess_matter(desk, mail, report)
        if acteur is not None and matter:   # 5.6.100 (R04) : un dossier proposé réservé à d'autres n'est pas nommé
            from .acces_courriel56100 import masques as _masques100
            if matter in _masques100(desk, acteur):
                matter, why = '', ''
        known = _state(desk).get(key)
        text = mail.text
        return {'uid': mail.uid, 'key': key, 'folder': folder, 'subject': mail.subject, 'from': str(mail.msg.get('From', ''))[:300], 'to': str(mail.msg.get('To', ''))[:500],
                'cc': str(mail.msg.get('Cc', ''))[:500], 'date': mail.timestamp.isoformat(), 'text': text[:MAX_TEXT], 'truncated': len(text) > MAX_TEXT,
                'attachments': _attachments(mail.msg), 'matter': matter, 'matter_reason': why,
                'uidvalidity': mail.uidvalidity, 'message_id': mail.mid, 'compte': _account(cfg), 'classement': _classement5697(desk, mail),   # 5.6.97 (§ 11)
                'demande_url': _demande5697(key),
                'status': known[0] if known else '', 'status_label': STATUS_LABELS.get(known[0], known[0]) if known else 'Jamais traité par l’agent',
                'reason': (report or {}).get('reason', ''), 'replies': replies(box, cfg, mail, desk, acteur),
                'own': mail.sender.lower() in {a.lower() for a in mbx.own_addresses(cfg)}}
    finally:
        if own:
            box.close()


def attachment_text(desk, uid, index, box=None, folder=None, acteur=None, validity=None):
    """Texte extrait d'une pièce jointe (lecture locale bornée)."""
    from .documents import extract
    cfg = desk.c['mail']
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        mail = _lire_autorise(desk, box, cfg, _folder(box, cfg, folder), _uid(uid), acteur, validity)
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


def request_reply(desk, uid, matter, instruction='', with_attachments=True, owner='cabinet', box=None, folder=None, without_matter=False, role='', expected_key='', validity=None):
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
    if role and matter:   # 5.6.100 (R04) : le dossier choisi à l'écran ne donne aucun droit : réservé à d'autres = introuvable
        from .acces_courriel56100 import exiger_contexte
        exiger_contexte(desk, (owner, role), '', matter)
    own = box is None
    box = box or mbx.Mailbox(cfg)
    try:
        mail = _lire_autorise(desk, box, cfg, _folder(box, cfg, folder), uid, (owner, role) if role else None, validity, expected_key)
    finally:
        if own:
            box.close()
    account = _account(cfg)
    if not mail.sender:
        raise Stop('expediteur_illisible')
    own_set = {a.lower() for a in mbx.own_addresses(cfg)}
    recipients = [mail.sender]
    if mail.sender.lower() in own_set:   # 5.6.28 : message du cabinet : la réponse s'adresse à ses destinataires extérieurs
        recipients = list(dict.fromkeys(a.lower() for a in mbx.addresses(str(mail.msg.get('To', '')) + ',' + str(mail.msg.get('Cc', ''))) if a.lower() not in own_set))
        if not recipients:
            raise Stop('aucun_destinataire_exterieur')
    key = mail.key(account)
    if expected_key and key != expected_key:          # 5.6.97 (B01) : l'UID ne désigne plus le message contrôlé (boîte réindexée)
        raise Stop('message_deplace_ou_boite_reindexee')
    state = _state(desk)
    try:
        from .desk import report_for
        report = report_for(desk.c, key)
    except Stop:
        report = {'key': key, 'subject': mail.subject, 'sender': mail.sender, 'source_uid': mail.uid, 'source_mailbox': mail.mailbox,
                  'received_at': mail.timestamp.isoformat(), 'mode': desk.c.get('mode', 'drafts'), 'sources': [], 'incoming_message_id': mail.mid,
                  'account_key': digest(account), 'model': desk.c.get('ollama', {}).get('model', ''), 'started_at': datetime.now(timezone.utc).isoformat()}
    report.update({'matter': matter, 'reply_recipients': recipients, 'manual_request': {'by': owner, 'at': desk.now(), 'instruction': instruction[:500],
                                                                                         'attachments': bool(with_attachments)}})
    if not report.get('status'):
        report.update({'status': 'manual', 'reason': 'reponse_demandee_par_avocat'})
    state.report(key, report)
    known = state.get(key)
    if not known:
        state.set(key, digest(account + mail.mid), digest(account + mail.root), 'manual', 'reponse_demandee_par_avocat')
    job = desk.enqueue('prepare_reply', {'key': key, 'matter': matter, 'instruction': instruction, 'pieces_jointes': 'oui' if with_attachments else '',
                                         'sans_dossier': 'oui' if not matter else '', 'origine': 'boite5625',
                                         **({'demande_par': str(owner), 'demande_role': str(role)} if role else {})}, priority=0)
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
    if not (cfg.get('url') or cfg.get('local_path') or cfg.get('stockage_cloud')):
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


def discard_reply(desk, uid, validity, acteur=None):
    """Supprime une réponse préparée : déplacement dans la corbeille de la messagerie (récupérable).
    5.6.100 (R04) : avec un acteur, CE brouillon est résolu et contrôlé (origine comprise) avant COPY/STORE ; interdit = absent (404)."""
    from .drafts440 import discard_draft
    garde = None
    if acteur is not None:
        from .acces_courriel56100 import garde_brouillon
        garde = garde_brouillon(desk, acteur, desk.c['mail'])
    out = discard_draft(desk.c['mail'], uid, validity, 'yes', garde=garde)
    desk.audit('boite5625_reponse_supprimee', {'uid': str(uid)})
    return {**out, 'message': 'Réponse supprimée' + (' (déplacée dans la corbeille de la messagerie).' if out.get('moved_to_trash') else '.')}
