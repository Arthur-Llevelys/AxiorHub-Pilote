"""5.6.102 (lot P) : une seule voie de rangement des pièces jointes des courriels reçus.

Demande du cabinet : un client écrit avec des pièces jointes (« préparez un contrat de travail ») ; les pièces doivent arriver dans le bon
dossier client, et le projet de réponse doit en tenir compte. Ce module ne fait aucun dépôt lui-même : il confie chaque message à la voie
existante de ``correspondances5643`` (mandat ``entrust`` pour CE message, état ``waiting_matter``, dépôt exclusif, relecture et bilan par
fichier dans ``NOS PIECES/PJ courriels/…``). Rien n'appelle le modèle ; rien n'est envoyé.

- **Courriel reçu avec pièces jointes et dossier connu** (correspondant validé d'un seul dossier, dossier choisi pour ce message ou pour
  son fil, rattachement confirmé) : mandat avec le dossier, travail de rangement aussitôt en file (origine automatique : pause respectée).
- **Sans dossier** (prospect, nouveau client) : mandat ``waiting_matter`` lié au **fil** du message (``pj56102_fils``) ; rien n'est déposé.
- **Création ou rattachement d'un dossier** : ``attribuer_dossier`` donne le dossier à tous les mandats en attente du fil et les met en file ;
  les travaux partent au passage suivant des automatismes (``correspondances5643.auto_tick``, toutes les 10 minutes au plus).
- **Doublons** : quand ce réglage est actif, ``ged5636.after_mail`` ne range plus (son code reste, neutralisé) ; une pièce déjà rangée
  dans le dossier par l'une ou l'autre voie (même empreinte SHA-256) n'est jamais redéposée (``deja_range``).

Réglage ``pj56102:auto`` (activé par défaut, c'est la demande du cabinet) : page « Données des pièces », carte « Pièces jointes des
courriels » (route existante ``m5636/pieces/pj-auto``). Types et taille : ceux de ``ged5636`` (``ALLOWED``, 20 Mo).

Contrat d'interface pour les autres lots (lot K : accord du client → contrat → courriel)
------------------------------------------------------------------------------------------
``fil`` : empreinte du fil telle que l'enregistre le moteur (``state.py`` : ``digest(compte + racine)``, racine = premier identifiant de
``References``/``In-Reply-To``, sinon ``Message-ID``). Sont aussi acceptés : la clé d'un courriel (``mail_key`` de 64 caractères, rapport ou
``work_items``), qui est convertie en son fil. ``owner`` : ``(compte, rôle)`` de la personne qui agit (recommandé), un compte seul (traité
comme un avocat non administrateur), ou ``'cabinet'`` pour un automatisme.

- ``attribuer_dossier(desk, fil, matter, owner) -> {'mandats': n, 'statut': s, 'dossier': matter, 'fil': fil}``
  Aucune lecture IMAP, aucun dépôt dans l'appel : les mandats ``waiting_matter`` du fil reçoivent le dossier et passent ``pending``.
  ``statut`` : ``'en_file'`` (n > 0), ``'aucun_mandat'`` (rien n'attendait), ``'dossier_reserve'`` (dossier interdit à ``owner`` : rien
  n'est modifié), ``'dossier_absent'``, ``'fil_inconnu'``. Le dossier est retenu pour les prochains messages du même fil.
- ``ranger_fil(desk, fil, matter, owner, box=None) -> {'messages': n, 'mandats': k, 'deja_ranges': d, 'statut': s, ...}``
  Fait ``attribuer_dossier`` puis lit la boîte (lecture seule) : chaque message reçu du fil qui porte des pièces jointes est confié avec le
  dossier ; une pièce déjà rangée dans ce dossier n'est pas redéposée. Les travaux de dépôt partent en file (jamais dans l'appel).
  ``statut`` : ``'en_file'``, ``'deja_range'``, ``'aucune_piece'``, ``'dossier_reserve'``, ``'dossier_absent'``, ``'automatismes_en_pause'``,
  ``'messagerie_indisponible'``.
"""
from datetime import datetime, timezone
import json
import re
import sqlite3

from .common import Stop, digest, load_matters, matter_display

REGLAGE = 'pj56102:auto'
CURSEUR = 'pj56102:curseur:%s:%s'
PAR_PASSAGE = 30
FIL_MAX = 35
ACTION = 'file_attachments'


def actif(desk):
    """Rangement automatique des pièces jointes par la voie 5.6.102 (activé par défaut)."""
    try:
        return bool(desk.settings(REGLAGE, True))
    except Exception:   # noqa: BLE001 — réglage illisible : la voie reste celle par défaut
        return True


def regler(desk, valeur, owner='cabinet'):
    desk.setting(REGLAGE, bool(valeur))
    desk.audit('pj56102_reglage', {'actif': bool(valeur), 'par': str(owner)[:80]})
    return {'enabled': bool(valeur), 'message': 'Rangement automatique des pièces jointes des courriels ' + ('activé.' if valeur else 'désactivé.')}


def _now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = '''
CREATE TABLE IF NOT EXISTS pj56102_fils(
  mandat_id TEXT PRIMARY KEY, fil TEXT NOT NULL, mail_key TEXT NOT NULL DEFAULT '', boite TEXT NOT NULL, uidvalidity TEXT NOT NULL,
  uid TEXT NOT NULL, message_id TEXT NOT NULL DEFAULT '', racine TEXT NOT NULL DEFAULT '', expediteur TEXT NOT NULL DEFAULT '',
  pieces_json TEXT NOT NULL DEFAULT '[]', dossier TEXT NOT NULL DEFAULT '', cree TEXT NOT NULL, maj TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pj56102_fils_fil ON pj56102_fils(fil);
CREATE INDEX IF NOT EXISTS pj56102_fils_cle ON pj56102_fils(mail_key);
CREATE INDEX IF NOT EXISTS pj56102_fils_source ON pj56102_fils(boite, uid);
CREATE TABLE IF NOT EXISTS pj56102_attributions(fil TEXT PRIMARY KEY, dossier TEXT NOT NULL, par TEXT NOT NULL, quand TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pj56102_gmail(
  connexion_id TEXT NOT NULL, gmail_id TEXT NOT NULL, piece INTEGER NOT NULL, fil TEXT NOT NULL, nom TEXT NOT NULL, expediteur TEXT NOT NULL DEFAULT '',
  recu TEXT NOT NULL DEFAULT '', dossier TEXT NOT NULL DEFAULT '',
  etat TEXT NOT NULL DEFAULT 'attente' CHECK(etat IN ('attente','a_ranger','verifie','deja','ecarte','erreur')),
  sha256 TEXT NOT NULL DEFAULT '', chemin TEXT NOT NULL DEFAULT '', erreur TEXT NOT NULL DEFAULT '', cree TEXT NOT NULL, maj TEXT NOT NULL,
  PRIMARY KEY(connexion_id, gmail_id, piece));
CREATE INDEX IF NOT EXISTS pj56102_gmail_fil ON pj56102_gmail(fil);
CREATE INDEX IF NOT EXISTS pj56102_gmail_etat ON pj56102_gmail(etat, maj);
'''


def ensure_schema(desk):
    """Au démarrage (``Desk``), jamais pendant une lecture de page."""
    desk.db.executescript(SCHEMA)
    from .ged5636 import schema as ged_schema   # table des rangements 5.6.36, lue par ``deja_range``
    ged_schema(desk)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ identité du fil
def compte(cfg):
    mail = (cfg or {}).get('mail') or {}
    return '%s@%s' % (mail.get('username', ''), mail.get('host', ''))


def fil_du_message(cfg, mail):
    """Empreinte du fil d'un message IMAP (même calcul que le moteur, ``engine.py``) ; sans identifiant, le message seul."""
    account = compte(cfg)
    if mail.root:
        return digest(account + mail.root)
    return digest(account + '|sans-identifiant|' + mail.key(account))


def fil_de(desk, valeur):
    """Fil d'une valeur reçue : empreinte de fil, ou clé de courriel convertie (registre 5.6.102, puis état du moteur)."""
    valeur = str(valeur or '').strip()
    if valeur.startswith('gmailfil:'):
        return valeur
    if valeur.startswith('gmail:'):   # clé d'un message Gmail ``gmail:<connexion>:<identifiant>`` -> fil Gmail
        parts = valeur.split(':')
        if len(parts) != 3:
            return ''
        try:
            row = desk.db.execute('SELECT thread_id FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=?', (parts[1], parts[2])).fetchone()
        except sqlite3.Error:
            row = None
        return 'gmailfil:%s:%s' % (parts[1], row['thread_id']) if row else ''
    if not re.fullmatch(r'[0-9a-f]{64}', valeur):
        return ''
    row = desk.db.execute('SELECT fil FROM pj56102_fils WHERE mail_key=? LIMIT 1', (valeur,)).fetchone()
    if row:
        return row['fil']
    try:
        from .state import State
        known = State(desk.c['state_dir']).db.execute('SELECT thread FROM messages WHERE key=?', (valeur,)).fetchone()
        if known:
            return str(known[0])
    except (sqlite3.Error, OSError, KeyError):
        pass
    return valeur


# ------------------------------------------------------------------------------------------------ droits, dossiers, doublons
def _acteur(owner):
    from .acces5697 import AGENT, Acteur, acteur_de
    if owner in (None, '', 'cabinet', AGENT):
        return AGENT
    try:
        return acteur_de(owner)
    except Stop:
        return Acteur(str(owner), 'avocat')   # compte sans rôle : jamais administrateur par défaut


def _reserve_pour(desk, matter, owner):
    """Vrai si le dossier est interdit à l'auteur (réservé à d'autres) ; une panne du contrôle refuse."""
    from .acces5697 import masques
    try:
        return str(matter) in masques(desk, _acteur(owner))
    except Stop:
        return True


def _dossier(desk, matter):
    return next((m for m in load_matters(desk.c, unfiltered=True) if m['id'] == str(matter or '') and m.get('path')), None)


def deja_range(desk, matter, sha):
    """La pièce (empreinte) est déjà rangée et vérifiée dans ce dossier, par la voie 5.6.43/5.6.102 ou par la voie 5.6.36."""
    if not matter or not sha:
        return False
    try:
        if desk.db.execute("SELECT 1 FROM mail_archive_assets a JOIN mail_archive_links l ON l.id=a.link_id WHERE l.matter_id=? AND "
                           "a.kind='attachment' AND a.state='verified' AND a.sha256=? LIMIT 1", (matter, sha)).fetchone():
            return True
    except sqlite3.Error:
        pass
    try:   # pièce déjà confiée à un dépôt en cours pour ce dossier (deux courriels portant la même pièce, traités au même passage)
        if desk.db.execute("SELECT 1 FROM mail_archive_links l, json_each(l.resolution_json, '$.parts') p WHERE l.matter_id=? AND "
                           "l.attachments_requested=1 AND l.attachments_state IN ('queued','saving','verifying') AND "
                           "json_extract(p.value, '$.sha256')=? LIMIT 1", (matter, sha)).fetchone():
            return True
    except sqlite3.Error:
        pass
    try:
        if desk.db.execute('SELECT 1 FROM attachments_filed5636 WHERE matter=? AND sha256=? LIMIT 1', (matter, sha)).fetchone():
            return True
    except sqlite3.Error:
        pass
    try:
        if desk.db.execute("SELECT 1 FROM pj56102_gmail WHERE dossier=? AND sha256=? AND etat='verifie' LIMIT 1", (matter, sha)).fetchone():
            return True
    except sqlite3.Error:
        pass
    return False


def autorisee(part):
    """Pièce ordinaire rangeable : ni corps, ni image de signature, type courant (``ged5636.ALLOWED``), 20 Mo au plus."""
    from .ged5636 import ALLOWED, MAX_ATTACHMENT
    name = str(part.get('name') or '').lower()
    return bool(part.get('attachment') and not part.get('signature_like') and part.get('state') == 'extraite'
                and name.endswith(ALLOWED) and 0 < int(part.get('size') or 0) <= MAX_ATTACHMENT)


def dossier_connu(desk, source, sender, mail_key, fil, message_id=''):
    """Dossier connu sans déduction du modèle : dossier choisi pour ce message ou pour son fil, rattachement confirmé par l'avocat,
    sinon correspondant validé d'UN seul dossier. ``''`` si aucun."""
    from . import correspondances5643 as cx
    candidats = [cx.get_message_rule(desk, source).get('matter_id') or '']
    row = desk.db.execute('SELECT dossier FROM pj56102_attributions WHERE fil=?', (fil,)).fetchone() if fil else None
    candidats.append(row['dossier'] if row else '')
    try:
        from .dossiers5697 import dossier_du_message
        candidats.append(dossier_du_message(desk, boite=source['folder'], uidvalidity=source['uidvalidity'], uid=source['uid'], message_id=message_id))
    except (Stop, sqlite3.Error, KeyError):
        pass
    if mail_key:
        try:
            liens = {r[0] for r in desk.db.execute("SELECT matter FROM portfolio_mail_links WHERE mail_key=? AND status='confirmed'", (mail_key,))}
            candidats.append(next(iter(liens)) if len(liens) == 1 else '')
        except sqlite3.Error:
            pass
    candidats.append(cx.confirmed_matter(desk, sender))
    for c in candidats:
        if c and _dossier(desk, c):
            return c
    return ''


# ------------------------------------------------------------------------------------------------ mandats
def _noms(desk, pieces, matter, sender, day):
    """Nom affiché de chaque pièce : règle de nommage du cabinet (``cloud_nommage5688``), sinon nom reçu nettoyé."""
    from . import cloud_nommage5688 as nommage
    from .correspondances5643 import safe_name
    m = _dossier(desk, matter) if matter else None
    ctx = nommage.context_of(m, date=day, expediteur=sender) if m else {'date': day, 'expediteur': sender}
    out = []
    for p in pieces:
        name = safe_name(p.get('name') or '', 80)
        try:
            name = nommage.apply(desk, 'piece_jointe', name, ctx)
        except (Stop, ValueError):
            pass
        out.append({'part_key': str(p['part_key']), 'display_name': safe_name(name, 80)})
    return out


def _jour(stamp):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Paris')).date().isoformat()
    except (ValueError, TypeError):
        return _now()[:10]


def _confier(desk, owner, source, matter, pieces, box, fil, mail_key, sender, message_id='', racine='', day=''):
    """Mandat de CE message pour le seul rangement des pièces choisies (``correspondances5643.entrust``) et lien avec son fil."""
    from . import correspondances5643 as cx
    from .acces5697 import AGENT
    acteur = _acteur(owner)
    owner = 'cabinet' if acteur == AGENT else acteur.compte   # le mandat garde le compte de la personne (texte), jamais un couple
    selection = {'mode': 'selected', 'parts': _noms(desk, pieces, matter, sender, day)}
    issue = 'en_attente' if not matter else 'en_file'
    try:
        out = cx.entrust(desk, owner, {**source, 'actions': [ACTION], 'matter_id': matter, 'attachments_selection': selection}, box)
        if out.get('state') == 'en_attente_reprise':
            issue = 'en_attente_reprise'
    except Stop as ex:
        mandat = cx._mandate(desk, source)
        if str(ex) == 'aucune_piece_jointe_a_enregistrer' and mandat:   # toutes déjà rangées dans ce dossier : rien à redéposer
            desk.db.execute("UPDATE mail_filing_mandates SET state='done', error_code='deja_rangees', updated_at=? WHERE id=?", (_now(), mandat['id']))
            desk.db.commit()
            issue = 'deja_range'
        elif not mandat:
            raise
        else:
            issue = 'erreur:' + str(ex)[:60]   # le mandat reste en file : repris par ``correspondances5643._run_mandates``
    mandat = cx._mandate(desk, source)
    stamp = _now()
    desk.db.execute('INSERT INTO pj56102_fils(mandat_id,fil,mail_key,boite,uidvalidity,uid,message_id,racine,expediteur,pieces_json,dossier,cree,maj) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mandat_id) DO UPDATE SET fil=excluded.fil, mail_key=excluded.mail_key, '
                    'dossier=excluded.dossier, pieces_json=excluded.pieces_json, maj=excluded.maj',
                    (mandat['id'], fil, mail_key, source['folder'], source['uidvalidity'], source['uid'], message_id[:300], racine[:300],
                     sender[:200], json.dumps([{'part_key': p['part_key'], 'name': p.get('name', '')} for p in pieces], ensure_ascii=False),
                     matter, stamp, stamp))
    desk.db.commit()
    return issue


def _pieces_du_message(desk, source, box):
    """Lecture seule des octets (copie analysée) : pièces rangeables avec leur empreinte, en-têtes utiles."""
    from . import correspondances5643 as cx
    raw, msg, meta = cx.capture(desk, source, box)
    pieces = [{'part_key': p['key'], 'name': p.get('name') or '', 'sha256': p.get('sha256', '')} for p in cx.mime_parts(msg, desk.c) if autorisee(p)]
    return pieces, meta


def _mail_head(box, folder, uid):
    return box.fetch(folder, str(uid), headers_only=True)


def _recu_avec_pieces(desk, head):
    from .sens5643 import direction
    if head.msg.get('List-Id') or head.msg.get('List-Unsubscribe'):
        return False
    if direction(head, desk.c['mail']) != 'received':
        return False
    ctype = head.msg.get_content_type()
    return ctype.startswith('multipart/') and ctype != 'multipart/alternative'


def _deja_confie(desk, source):
    from . import correspondances5643 as cx
    if cx._mandate(desk, source):
        return True
    return any(l['attachments_requested'] for l in cx.links_for_source(desk, source['folder'], source['uid'], source['uidvalidity']))


def observer(desk, box, folder, uid, head=None, owner='cabinet'):
    """Un message reçu : mandat avec le dossier connu, sinon en attente du dossier (lié au fil). Renvoie l'issue (texte court)."""
    head = head or _mail_head(box, folder, uid)
    if not _recu_avec_pieces(desk, head):
        return 'ignore'
    source = {'folder': folder, 'uid': str(uid), 'uidvalidity': str(head.uidvalidity)}
    if _deja_confie(desk, source):
        return 'deja_confie'
    pieces, meta = _pieces_du_message(desk, source, box)
    if not pieces:
        return 'aucune_piece'
    account = compte(desk.c)
    mail_key = head.key(account)
    fil = fil_du_message(desk.c, head)
    matter = dossier_connu(desk, source, head.sender, mail_key, fil, head.mid)
    return _confier(desk, owner, source, matter, pieces, box, fil, mail_key, head.sender, head.mid, head.root,
                    _jour(meta['headers'].get('declared_date') or meta.get('internaldate') or _now()))


def _reprendre_attentes(desk, limite=200):
    """Mandats en attente dont le fil a reçu un dossier, ou dont l'expéditeur est devenu le correspondant validé d'un seul dossier."""
    from . import correspondances5643 as cx
    rows = desk.db.execute("SELECT f.fil, f.expediteur, f.mandat_id FROM pj56102_fils f JOIN mail_filing_mandates m ON m.id=f.mandat_id "
                           "WHERE m.state='waiting_matter' ORDER BY f.cree LIMIT ?", (int(limite),)).fetchall()
    repris = 0
    for r in rows:
        att = desk.db.execute('SELECT dossier, par FROM pj56102_attributions WHERE fil=?', (r['fil'],)).fetchone()
        matter = att['dossier'] if att else cx.confirmed_matter(desk, r['expediteur'])
        if matter and _dossier(desk, matter):
            repris += _attribuer_mandats(desk, [r['mandat_id']], matter)
    for r in desk.db.execute("SELECT DISTINCT connexion_id, gmail_id, fil, expediteur FROM pj56102_gmail WHERE etat='attente' LIMIT ?", (int(limite),)).fetchall():
        att = desk.db.execute('SELECT dossier FROM pj56102_attributions WHERE fil=?', (r['fil'],)).fetchone()
        matter = att['dossier'] if att else cx.confirmed_matter(desk, r['expediteur'])
        if matter and _dossier(desk, matter):
            desk.db.execute("UPDATE pj56102_gmail SET dossier=?, etat='a_ranger', maj=? WHERE connexion_id=? AND gmail_id=? AND etat='attente'",
                            (matter, _now(), r['connexion_id'], r['gmail_id']))
            repris += 1
    desk.db.commit()
    return repris


def _attribuer_mandats(desk, ids, matter):
    """Dossier donné aux mandats en attente (et à la règle de leur message) ; noms recalculés avec le dossier. Transaction courte."""
    n, stamp = 0, _now()
    for mid in ids:
        m = desk.db.execute("SELECT * FROM mail_filing_mandates WHERE id=? AND state='waiting_matter'", (mid,)).fetchone()
        if not m:
            continue
        f = desk.db.execute('SELECT * FROM pj56102_fils WHERE mandat_id=?', (mid,)).fetchone()
        selection = m['selection_json']
        if f:
            pieces = json.loads(f['pieces_json'] or '[]')
            if pieces:
                selection = json.dumps({'mode': 'selected', 'parts': _noms(desk, pieces, matter, f['expediteur'], _now()[:10])}, ensure_ascii=False)
        cur = desk.db.execute("UPDATE mail_filing_mandates SET matter_id=?, selection_json=?, state='pending', version=version+1, updated_at=? "
                              "WHERE id=? AND state='waiting_matter'", (matter, selection, stamp, mid))
        if not cur.rowcount:
            continue
        desk.db.execute('UPDATE mail_filing_message_rules SET matter_id=?, confirmed_by=?, confirmed_at=?, version=version+1, updated_at=? '
                        'WHERE scope_id=? AND account_id=? AND folder=? AND uidvalidity=? AND uid=?',
                        (matter, 'pj56102', stamp, stamp, m['scope_id'], m['account_id'], m['folder'], m['uidvalidity'], m['uid']))
        desk.db.execute('UPDATE pj56102_fils SET dossier=?, maj=? WHERE mandat_id=?', (matter, stamp, mid))
        n += 1
    desk.db.commit()
    return n


def _attribuer(desk, fil, matter, owner='cabinet'):
    """Contrat lot K (voir l'en-tête) : le dossier est donné à tous les mandats en attente du fil, qui passent en file. Aucune E/S."""
    fil = fil_de(desk, fil)
    out = {'mandats': 0, 'statut': '', 'dossier': str(matter or ''), 'fil': fil}
    if not fil:
        return {**out, 'statut': 'fil_inconnu'}
    if not _dossier(desk, matter):
        return {**out, 'statut': 'dossier_absent'}
    if _reserve_pour(desk, matter, owner):   # même réponse que « absent » pour qui n'y a pas accès : rien n'est modifié
        return {**out, 'statut': 'dossier_reserve'}
    desk.db.execute('INSERT OR REPLACE INTO pj56102_attributions(fil,dossier,par,quand) VALUES(?,?,?,?)',
                    (fil, str(matter), str(_acteur(owner).compte)[:80], _now()))
    desk.db.commit()
    if fil.startswith('gmailfil:'):   # fil Gmail : les pièces relevées en attente reçoivent le dossier (déposées au passage suivant)
        n = len({r[0] for r in desk.db.execute("SELECT gmail_id FROM pj56102_gmail WHERE fil=? AND etat='attente'", (fil,))})
        desk.db.execute("UPDATE pj56102_gmail SET dossier=?, etat='a_ranger', maj=? WHERE fil=? AND etat='attente'", (str(matter), _now(), fil))
        desk.db.commit()
    else:
        ids = [r[0] for r in desk.db.execute("SELECT f.mandat_id FROM pj56102_fils f JOIN mail_filing_mandates m ON m.id=f.mandat_id "
                                             "WHERE f.fil=? AND m.state='waiting_matter'", (fil,))]
        n = _attribuer_mandats(desk, ids, str(matter))
    if n:
        desk.audit('pj56102_dossier_attribue', {'dossier': str(matter), 'mandats': n, 'par': str(_acteur(owner).compte)[:80]})
    return {**out, 'mandats': n, 'statut': 'en_file' if n else 'aucun_mandat'}


MESSAGES = {'en_file': 'Pièces jointes du fil à ranger dans le dossier (%d message(s)) ; chaque fichier sera relu après dépôt.',
            'deja_range': 'Pièces jointes du fil déjà rangées dans le dossier.', 'aucune_piece': 'Aucune pièce jointe à ranger pour ce fil.',
            'aucun_mandat': 'Aucune pièce jointe en attente pour ce fil.', 'dossier_reserve': 'Dossier introuvable.', 'dossier_absent': 'Dossier introuvable.',
            'fil_inconnu': 'Fil de courriels introuvable.', 'automatismes_en_pause': 'IA en pause : pièces jointes rangées à la reprise.',
            'messagerie_indisponible': 'Messagerie indisponible : pièces jointes non rangées pour l’instant.'}


def _message(out):
    texte = MESSAGES.get(out.get('statut'), '')
    return {**out, 'message': (texte % out.get('mandats', 0)) if '%d' in texte else texte}


def attribuer_dossier(desk, fil, matter, owner='cabinet'):
    """Contrat lot K (voir l'en-tête) : ``{'mandats', 'statut', 'dossier', 'fil', 'message'}``."""
    return _message(_attribuer(desk, fil, matter, owner))


def ranger_fil(desk, fil, matter, owner='cabinet', box=None):
    """Contrat lot K (voir l'en-tête) : ``{'messages', 'mandats', 'deja_ranges', 'statut', 'dossier', 'fil', 'message'}``."""
    return _message(_ranger(desk, fil, matter, owner, box))


def apres_dossier(desk, matter, owner='cabinet', mail_key='', source=None, correspondant=''):
    """Accroche commune (création d'un dossier, gain d'une opportunité, conversion d'une demande, classement d'un courriel, rattachement
    confirmé) : fil du message désigné, puis mandats en attente de l'expéditeur devenu correspondant validé. Jamais bloquante."""
    out = {'mandats': 0, 'statut': 'aucun_mandat'}
    try:
        fil = ''
        if mail_key:
            fil = fil_de(desk, mail_key)
        elif source and str(source.get('uid') or '').isdigit():
            row = desk.db.execute('SELECT fil FROM pj56102_fils WHERE boite=? AND uid=? AND (?=\'\' OR uidvalidity=?) LIMIT 1',
                                  (str(source.get('folder') or 'INBOX'), str(source['uid']), str(source.get('uidvalidity') or ''),
                                   str(source.get('uidvalidity') or ''))).fetchone()
            fil = row['fil'] if row else ''
        if fil:
            out = attribuer_dossier(desk, fil, matter, owner)
            if out['statut'] == 'dossier_reserve':
                return out
        if correspondant and not _reserve_pour(desk, matter, owner):
            from .correspondances5643 import confirmed_matter
            if confirmed_matter(desk, correspondant) == str(matter):
                ids = [r[0] for r in desk.db.execute("SELECT f.mandat_id FROM pj56102_fils f JOIN mail_filing_mandates m ON m.id=f.mandat_id "
                                                     "WHERE lower(f.expediteur)=? AND m.state='waiting_matter'", (str(correspondant).strip().lower(),))]
                n = _attribuer_mandats(desk, ids, str(matter))
                gm = len({r[0] for r in desk.db.execute("SELECT gmail_id FROM pj56102_gmail WHERE lower(expediteur)=? AND etat='attente'",
                                                        (str(correspondant).strip().lower(),))})
                desk.db.execute("UPDATE pj56102_gmail SET dossier=?, etat='a_ranger', maj=? WHERE lower(expediteur)=? AND etat='attente'",
                                (str(matter), _now(), str(correspondant).strip().lower()))
                desk.db.commit()
                n += gm
                out = {**out, 'mandats': out.get('mandats', 0) + n, 'statut': 'en_file' if (n or out.get('mandats')) else out.get('statut', 'aucun_mandat')}
    except (Stop, sqlite3.Error):
        if desk.db.in_transaction:
            desk.db.rollback()
    return out


def _ranger(desk, fil, matter, owner='cabinet', box=None):
    """Contrat lot K (voir l'en-tête) : toutes les pièces jointes de tous les messages reçus du fil, sans doublon. Lecture seule de la
    boîte ; les dépôts partent en file, vérifiés après dépôt par la voie 5.6.43."""
    first = _attribuer(desk, fil, matter, owner)
    out = {**first, 'messages': 0, 'deja_ranges': 0}
    if first['statut'] in ('fil_inconnu', 'dossier_absent', 'dossier_reserve'):
        return out
    if desk.settings('missions567:pause', False):
        return {**out, 'statut': 'automatismes_en_pause'}
    fil = first['fil']
    if fil.startswith('gmailfil:'):   # Gmail : messages du fil relevés dans la copie locale, pièces déposées au passage suivant
        _, conn, thread = fil.split(':', 2)
        n = 0
        for m in desk.db.execute("SELECT * FROM gmail5697_messages WHERE connexion_id=? AND thread_id=? AND supprime=0 AND sens='recu'", (conn, thread)).fetchall():
            n += _observer_gmail(desk, m, str(matter))
        total = first['mandats'] + n
        return {**out, 'messages': n, 'mandats': total, 'statut': 'en_file' if total else 'aucune_piece'}
    racines = {r[0] for r in desk.db.execute("SELECT racine FROM pj56102_fils WHERE fil=? AND racine<>''", (fil,))}
    own = box is None
    try:
        if own:
            from .mailbox import Mailbox
            box = Mailbox(desk.c['mail'])
        folder = desk.c['mail'].get('inbox', 'INBOX')
        uids = []
        try:   # messages connus du moteur pour ce fil (rapports) : leur racine sert à chercher les autres
            from .state import State
            from .desk import report_for
            for (key,) in State(desk.c['state_dir']).db.execute('SELECT key FROM messages WHERE thread=? LIMIT ?', (fil, FIL_MAX)):
                try:
                    rep = report_for(desk.c, key)
                except Stop:
                    continue
                if str(rep.get('source_mailbox') or folder) == folder and str(rep.get('source_uid') or '').isdigit():
                    uids.append(str(rep['source_uid']))
        except (sqlite3.Error, OSError, KeyError):
            pass
        uids += [r[0] for r in desk.db.execute('SELECT uid FROM pj56102_fils WHERE fil=? AND boite=?', (fil, folder))]
        from .mailbox import imap_quote
        for racine in list(racines)[:3]:
            found = box.search(folder, 'UNDELETED', 'OR', 'HEADER', 'Message-ID', imap_quote(racine), 'OR', 'HEADER', 'References',
                               imap_quote(racine), 'HEADER', 'In-Reply-To', imap_quote(racine))
            uids += [str(u) for u in found if str(u).isdigit()]
        confies = deja = 0
        for uid in list(dict.fromkeys(uids))[:FIL_MAX]:
            try:
                head = _mail_head(box, folder, uid)
            except Stop:
                continue
            if fil_du_message(desk.c, head) != fil or not _recu_avec_pieces(desk, head):
                continue
            out['messages'] += 1
            source = {'folder': folder, 'uid': str(uid), 'uidvalidity': str(head.uidvalidity)}
            from . import correspondances5643 as cx
            mandat = cx._mandate(desk, source)
            if mandat and mandat['state'] in ('pending', 'waiting_matter', 'done'):
                continue   # déjà confié (le mandat en attente vient de recevoir le dossier)
            pieces, meta = _pieces_du_message(desk, source, box)
            if not pieces:
                continue
            issue = _confier(desk, owner if owner not in (None, '') else 'cabinet', source, str(matter), pieces, box, fil, head.key(compte(desk.c)),
                             head.sender, head.mid, head.root, _jour(meta['headers'].get('declared_date') or meta.get('internaldate') or _now()))
            if issue == 'deja_range':
                deja += 1
            elif not issue.startswith('erreur'):
                confies += 1
    except Stop as ex:
        return {**out, 'statut': 'messagerie_indisponible', 'erreur': str(ex)[:60]}
    finally:
        if own and box is not None:
            try:
                box.close()
            except Exception:   # noqa: BLE001
                pass
    total = first['mandats'] + confies
    statut = 'en_file' if total else ('deja_range' if deja else 'aucune_piece')
    return {**out, 'mandats': total, 'deja_ranges': deja, 'statut': statut}


# ------------------------------------------------------------------------------------------------ Gmail (copie locale 5.6.97)
GMAIL_EXCLUS = ('liste_diffusion', 'envoi_en_masse', 'message_automatique', 'adresse_automatique')
GMAIL_PAR_PASSAGE = 10


def _dossier_gmail(desk, m, fil):
    """Dossier connu d'un message Gmail : rattachement du serveur (unique), dossier du fil, sinon correspondant validé d'un seul dossier."""
    try:
        from .gmail5697 import dossiers_du_message
        dossiers = set(dossiers_du_message(desk, m['connexion_id'], m['gmail_id']))
    except (Stop, sqlite3.Error):
        dossiers = set()
    if len(dossiers) == 1 and _dossier(desk, next(iter(dossiers))):
        return next(iter(dossiers))
    row = desk.db.execute('SELECT dossier FROM pj56102_attributions WHERE fil=?', (fil,)).fetchone()
    if row and _dossier(desk, row['dossier']):
        return row['dossier']
    from .correspondances5643 import confirmed_matter
    return confirmed_matter(desk, m['de_adresse'])


def _observer_gmail(desk, m, matter=None):
    """Pièces d'un message Gmail reçu relevées (une ligne par pièce) : à ranger (dossier connu), en attente, ou écartées (type, taille)."""
    from .ged5636 import ALLOWED, MAX_ATTACHMENT
    fil = 'gmailfil:%s:%s' % (m['connexion_id'], m['thread_id'])
    if desk.db.execute('SELECT 1 FROM pj56102_gmail WHERE connexion_id=? AND gmail_id=? LIMIT 1', (m['connexion_id'], m['gmail_id'])).fetchone():
        if matter:   # message déjà relevé : il reçoit le dossier s'il l'attendait
            desk.db.execute("UPDATE pj56102_gmail SET dossier=?, etat='a_ranger', maj=? WHERE connexion_id=? AND gmail_id=? AND etat='attente'",
                            (matter, _now(), m['connexion_id'], m['gmail_id']))
            desk.db.commit()
        return 0
    try:
        pieces = json.loads(m['pieces_json'] or '[]')
    except ValueError:
        pieces = []
    if not pieces:
        return 0
    matter = matter if matter is not None else _dossier_gmail(desk, m, fil)
    stamp = _now()
    for p in pieces:
        nom = str(p.get('name') or '')
        ok = nom.lower().endswith(ALLOWED) and 0 < int(p.get('size') or 0) <= MAX_ATTACHMENT and p.get('attachment_id')
        desk.db.execute('INSERT OR IGNORE INTO pj56102_gmail(connexion_id,gmail_id,piece,fil,nom,expediteur,recu,dossier,etat,cree,maj) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (m['connexion_id'], m['gmail_id'], int(p.get('index') or 0), fil, nom[:200], str(m['de_adresse'] or '')[:200], str(m['recu'] or ''),
                         matter or '', ('a_ranger' if matter else 'attente') if ok else 'ecarte', stamp, stamp))
    desk.db.commit()
    return 1


def _deposer_gmail(desk, row, client=None, appeler=None):
    """Une pièce Gmail : lue chez Google (``messages.attachments.get``, lecture seule), déposée de façon exclusive dans « NOS PIECES/PJ
    courriels », relue et comparée à son empreinte. Jamais d'écrasement ; une pièce déjà rangée dans le dossier n'est pas redéposée."""
    from . import correspondances5643 as cx
    from .common import clean_path, under
    from .gmail5697 import API, _b64, PIECE_MAX
    from urllib.parse import quote
    m = _dossier(desk, row['dossier'])
    if not m:
        raise Stop('dossier_absent')
    msg = desk.db.execute('SELECT pieces_json FROM gmail5697_messages WHERE connexion_id=? AND gmail_id=?', (row['connexion_id'], row['gmail_id'])).fetchone()
    pieces = json.loads(msg['pieces_json'] or '[]') if msg else []
    piece = next((p for p in pieces if int(p.get('index') or 0) == int(row['piece'])), None)
    if not piece or not piece.get('attachment_id'):
        raise Stop('piece_jointe_absente')
    if appeler is None:
        from .google5697 import appeler
    out = appeler(desk, row['connexion_id'], 'GET', '%s/messages/%s/attachments/%s' % (API, quote(row['gmail_id'], safe=''), quote(piece['attachment_id'], safe='')),
                  capacite='gmail.lecture')
    data = _b64(out.get('data'))
    if not data or len(data) > PIECE_MAX:
        raise Stop('piece_trop_volumineuse')
    sha = cx._sha(data)
    if deja_range(desk, row['dossier'], sha):
        return {'etat': 'deja', 'sha256': sha, 'chemin': ''}
    nom = _noms(desk, [{'part_key': row['piece'], 'name': row['nom']}], row['dossier'], row['expediteur'], _jour(row['recu'] or _now()))[0]['display_name']
    dossier_pj = clean_path(cx.nos_pieces_dir(desk, m) + '/PJ courriels/%s--gmail-%s' % (_jour(row['recu'] or _now()), cx._sha(row['gmail_id'].encode())[:8]))
    racine = clean_path(m['path'])
    if not under(dossier_pj, racine):
        raise Stop('destination_hors_dossier')
    from .pause5629 import checkpoint, is_paused
    checkpoint(desk)   # frontière d'engagement : un arrêt demandé avant ce point empêche l'écriture
    if desk.settings('missions567:pause', False) or is_paused(desk, row['dossier']):
        raise Stop('automatismes_en_pause')
    from .horaires_ia5689 import code_horaire   # plage horaire de l'IA (automatismes) : dépôt repris à la fin de la plage
    code = code_horaire(desk, matter=row['dossier'], automatic=True)
    if code:
        raise Stop(code)
    client = client or cx._client(desk)
    client.ensure_folder(dossier_pj, racine)
    stem, dot, ext = nom.rpartition('.')
    for n in range(1, 30):   # création exclusive ; un fichier identique déjà présent (reprise) est rejoint, jamais remplacé
        chemin = dossier_pj + '/' + (nom if n == 1 else ('%s (%d).%s' % (stem, n, ext) if dot else '%s (%d)' % (nom, n)))
        try:
            client.put_archive(chemin, data, max(len(data), 1), piece.get('type') or 'application/octet-stream')
        except Stop as ex:
            if str(ex) not in ('http_412', 'http_409'):
                raise
            try:
                if cx._sha(client.read_archive(chemin, len(data) + 1)) == sha:
                    return {'etat': 'verifie', 'sha256': sha, 'chemin': chemin}
            except Stop:
                pass
            continue
        if cx._sha(client.read_archive(chemin, len(data) + 1)) != sha:
            raise Stop('empreinte_depot_differente')
        return {'etat': 'verifie', 'sha256': sha, 'chemin': chemin}
    raise Stop('conflit_fichier_existant')


def tick_gmail(desk, limite=GMAIL_PAR_PASSAGE, client=None, appeler=None):
    """Messages Gmail reçus depuis l'activation : pièces relevées, puis déposées (au plus ``limite`` par passage). Option Gmail du cabinet
    requise ; aucune écriture chez Google."""
    try:
        from .gmail5697 import actif as gmail_actif, ensure_schema as gmail_schema
        if not gmail_actif(desk):
            return {'gmail': 'inactif'}
        gmail_schema(desk)
    except (Stop, sqlite3.Error, ImportError):
        return {'gmail': 'indisponible'}
    depuis = desk.settings('pj56102:gmail:depuis', '') or ''
    if not depuis:
        depuis = _now()
        desk.setting('pj56102:gmail:depuis', depuis)
    releves = 0
    for m in desk.db.execute("SELECT m.* FROM gmail5697_messages m WHERE m.supprime=0 AND m.sens='recu' AND m.pieces_json<>'[]' AND m.recu>=? "
                             "AND m.motif NOT IN (%s) AND NOT EXISTS (SELECT 1 FROM pj56102_gmail g WHERE g.connexion_id=m.connexion_id AND "
                             "g.gmail_id=m.gmail_id) ORDER BY m.recu LIMIT 50" % ','.join('?' * len(GMAIL_EXCLUS)), (depuis[:19],) + GMAIL_EXCLUS).fetchall():
        releves += _observer_gmail(desk, m)
    deposes = erreurs = 0
    from .pause5629 import is_paused
    for row in desk.db.execute("SELECT * FROM pj56102_gmail WHERE etat='a_ranger' ORDER BY maj LIMIT ?", (int(limite),)).fetchall():
        if is_paused(desk, row['dossier']):
            continue
        try:
            res = _deposer_gmail(desk, row, client, appeler)
        except Stop as ex:
            if str(ex) in ('automatismes_en_pause', 'ia_en_pause', 'operation_annulee', 'ia_en_pause_horaire', 'horaires_ia_illisibles'):
                break
            desk.db.execute("UPDATE pj56102_gmail SET etat='erreur', erreur=?, maj=? WHERE connexion_id=? AND gmail_id=? AND piece=?",
                            (str(ex)[:80], _now(), row['connexion_id'], row['gmail_id'], row['piece']))
            desk.db.commit()
            erreurs += 1
            continue
        desk.db.execute('UPDATE pj56102_gmail SET etat=?, sha256=?, chemin=?, erreur=\'\', maj=? WHERE connexion_id=? AND gmail_id=? AND piece=?',
                        (res['etat'], res['sha256'], res['chemin'], _now(), row['connexion_id'], row['gmail_id'], row['piece']))
        desk.db.commit()
        deposes += res['etat'] == 'verifie'
    return {'gmail_releves': releves, 'gmail_deposes': deposes, 'gmail_erreurs': erreurs}


# ------------------------------------------------------------------------------------------------ passage périodique
def _avant(quand, depuis):
    """Message reçu avant l'activation (dates comparées avec leur fuseau ; date illisible : traitée comme nouvelle)."""
    try:
        limite = datetime.fromisoformat(str(depuis).replace('Z', '+00:00'))
        if quand.tzinfo is None:
            quand = quand.replace(tzinfo=timezone.utc)
        return quand < limite
    except (TypeError, ValueError, AttributeError):
        return False


def tick(desk, box=None, limit=PAR_PASSAGE, gmail_client=None, gmail_appeler=None):
    """Nouveaux messages reçus depuis l'activation (jamais l'historique) : chaque message IMAP avec pièces jointes est confié (dossier
    connu) ou mis en attente du dossier ; les pièces Gmail relevées sont déposées. Appelé par la maintenance (``activity431``), avant
    ``correspondances5643.auto_tick`` qui exécute les mandats en file."""
    if not actif(desk):
        return {'examines': 0, 'desactive': True}
    if desk.settings('missions567:pause', False):
        return {'examines': 0, 'pause': True}   # rien n'est consommé pendant la pause : la progression reprend ensuite
    repris = _reprendre_attentes(desk)   # fil ou expéditeur rattaché depuis : les pièces en attente reçoivent leur dossier
    out = {'examines': 0}
    if (desk.c.get('mail') or {}).get('enabled', True) is False or not (desk.c.get('mail') or {}).get('host'):
        out['messagerie'] = 'non_configuree'
    else:
        out = _tick_imap(desk, box, limit)
    try:
        out.update(tick_gmail(desk, client=gmail_client, appeler=gmail_appeler))
    except (Stop, sqlite3.Error) as ex:   # Gmail injoignable : la boîte IMAP n'en dépend pas
        out['gmail'] = 'erreur:' + str(ex)[:40]
    out['repris'] = repris
    return out


def _tick_imap(desk, box, limit):
    folder = desk.c['mail'].get('inbox', 'INBOX')
    from .correspondances5643 import account_id
    key = CURSEUR % (account_id(desk.c), folder)
    cursor = desk.settings(key, {}) or {}
    if not cursor.get('depuis'):
        cursor = {'depuis': _now(), 'last_uid': 0}
        desk.setting(key, cursor)
    before = json.dumps(cursor, sort_keys=True)
    examined, issues = 0, {}
    own = box is None
    try:
        if own:
            from .mailbox import Mailbox
            box = Mailbox(desk.c['mail'])
        day = datetime.fromisoformat(cursor['depuis'].replace('Z', '+00:00')).strftime('%d-%b-%Y')
        found = sorted({str(u) for u in box.search(folder, 'SINCE', day, 'UNDELETED') if str(u).isdigit()}, key=int)
        last = int(cursor.get('last_uid') or 0)
        for uid in [u for u in found if int(u) > last][:int(limit)]:
            try:
                head = _mail_head(box, folder, uid)
            except Stop:
                break   # lecture impossible : la progression s'arrête ici et reprendra au passage suivant
            if cursor.get('uidvalidity') and str(head.uidvalidity) != str(cursor['uidvalidity']):
                cursor.update({'uidvalidity': str(head.uidvalidity), 'last_uid': 0})   # boîte renumérotée : reprise depuis l'activation
                break
            cursor['uidvalidity'] = str(head.uidvalidity)
            examined += 1
            issue = 'ancien' if _avant(head.timestamp, cursor['depuis']) else ''
            if not issue:
                try:
                    issue = observer(desk, box, folder, uid, head)
                except Stop as ex:
                    if str(ex) in ('automatismes_en_pause', 'ia_en_pause', 'controle_pause_indisponible', 'ia_en_pause_horaire', 'horaires_ia_illisibles'):
                        break   # repris au passage suivant, message non consommé
                    issue = 'erreur:' + str(ex)[:40]
            last = max(last, int(uid))
            cursor['last_uid'] = last
            issues[issue.split(':')[0]] = issues.get(issue.split(':')[0], 0) + 1
    except Stop as ex:
        return {'examines': examined, 'erreur': str(ex)[:60]}
    finally:
        if own and box is not None:
            try:
                box.close()
            except Exception:   # noqa: BLE001
                pass
        if json.dumps(cursor, sort_keys=True) != before:
            desk.setting(key, cursor)
    return {'examines': examined, 'issues': issues}


# ------------------------------------------------------------------------------------------------ affichage (lecture seule)
def _libelle(desk, matter):
    m = _dossier(desk, matter)
    return matter_display(m) if m else str(matter or '')


def etat(desk, mail_key, acteur=None, source=None):
    """État lisible du rangement des pièces jointes d'un courriel, sans aucune écriture (page, volet). ``None`` : rien à dire.
    Le nombre affiché est celui des fichiers relus après dépôt ; une source illisible ne donne jamais un chiffre."""
    from . import correspondances5643 as cx
    if str(mail_key or '').startswith('gmail:'):
        return _etat_gmail(desk, str(mail_key), acteur)
    try:
        row = desk.db.execute('SELECT * FROM pj56102_fils WHERE mail_key=? ORDER BY maj DESC LIMIT 1', (str(mail_key or ''),)).fetchone() if mail_key else None
        if row is None and source and str(source.get('uid') or '').isdigit():
            row = desk.db.execute('SELECT * FROM pj56102_fils WHERE boite=? AND uid=? ORDER BY maj DESC LIMIT 1',
                                  (str(source.get('folder') or 'INBOX'), str(source['uid']))).fetchone()
        mandat = desk.db.execute('SELECT * FROM mail_filing_mandates WHERE id=?', (row['mandat_id'],)).fetchone() if row else None
        lien = None
        if mandat and mandat['link_id']:
            lien = desk.db.execute('SELECT * FROM mail_archive_links WHERE id=?', (mandat['link_id'],)).fetchone()
        elif source and str(source.get('uid') or '').isdigit():
            lien = desk.db.execute('SELECT l.* FROM mail_archive_links l JOIN mail_archive_sources s ON s.archive_id=l.archive_id WHERE s.account_id=? '
                                   'AND s.folder=? AND s.uid=? AND l.attachments_requested=1 ORDER BY l.updated_at DESC LIMIT 1',
                                   (cx.account_id(desk.c), str(source.get('folder') or 'INBOX'), str(source['uid']))).fetchone()
        matter = (lien['matter_id'] if lien else '') or (mandat['matter_id'] if mandat else '')
        if matter and acteur is not None and _reserve_pour(desk, matter, acteur):
            return None
        if lien is not None:
            n = desk.db.execute("SELECT COUNT(*) FROM mail_archive_assets WHERE link_id=? AND kind='attachment' AND state='verified'", (lien['id'],)).fetchone()[0]
            state = cx._projected(desk, lien, ACTION)
            dossier = _libelle(desk, matter)
            if n:
                return {'etat': 'range', 'n': n, 'dossier': matter, 'texte': 'Pièces jointes rangées dans %s › PJ courriels (%d)' % (dossier, n)}
            if state == 'verified':
                return {'etat': 'deja', 'dossier': matter, 'texte': 'Pièces jointes déjà rangées dans %s' % dossier}
            if state in cx.ACTIVE_LINK_STATES:
                return {'etat': 'en_cours', 'dossier': matter, 'texte': 'Pièces jointes : rangement en cours dans %s › PJ courriels' % dossier}
            return {'etat': 'echec', 'dossier': matter, 'texte': 'Pièces jointes non rangées dans %s : %s' % (dossier, cx.STATE_LABELS.get(state, state).lower())}
        if mandat is not None:
            if mandat['state'] == 'waiting_matter':
                return {'etat': 'attente', 'texte': 'Pièces jointes en attente du dossier'}
            if mandat['state'] == 'pending':
                return {'etat': 'en_file', 'dossier': matter, 'texte': 'Pièces jointes : rangement prévu dans %s › PJ courriels' % _libelle(desk, matter)}
            if mandat['state'] == 'done' and mandat['error_code'] == 'deja_rangees':
                return {'etat': 'deja', 'dossier': matter, 'texte': 'Pièces jointes déjà rangées dans %s' % _libelle(desk, matter)}
            if mandat['state'] in ('error', 'cancelled'):
                return {'etat': 'echec', 'dossier': matter, 'texte': 'Pièces jointes non rangées (%s)' % (mandat['error_code'] or 'annulé').replace('_', ' ')}
        if mail_key:
            rows = desk.db.execute('SELECT matter, COUNT(*) n FROM attachments_filed5636 WHERE mail_key=? GROUP BY matter', (str(mail_key),)).fetchall()
            if len(rows) == 1 and not (acteur is not None and _reserve_pour(desk, rows[0]['matter'], acteur)):
                return {'etat': 'range', 'n': rows[0]['n'], 'dossier': rows[0]['matter'],
                        'texte': 'Pièces jointes rangées dans %s › PJ courriels (%d)' % (_libelle(desk, rows[0]['matter']), rows[0]['n'])}
    except (sqlite3.Error, Stop, KeyError, TypeError, ValueError):
        return None   # source illisible : rien plutôt qu'un chiffre faux
    return None


def _etat_gmail(desk, mail_key, acteur=None):
    parts = mail_key.split(':')
    if len(parts) != 3:
        return None
    try:
        rows = desk.db.execute('SELECT etat, dossier, erreur FROM pj56102_gmail WHERE connexion_id=? AND gmail_id=?', (parts[1], parts[2])).fetchall()
        if not rows:
            return None
        matter = next((r['dossier'] for r in rows if r['dossier']), '')
        if matter and acteur is not None and _reserve_pour(desk, matter, acteur):
            return None
        n = sum(1 for r in rows if r['etat'] == 'verifie')
        if n:
            return {'etat': 'range', 'n': n, 'dossier': matter, 'texte': 'Pièces jointes rangées dans %s › PJ courriels (%d)' % (_libelle(desk, matter), n)}
        if any(r['etat'] == 'attente' for r in rows):
            return {'etat': 'attente', 'texte': 'Pièces jointes en attente du dossier'}
        if any(r['etat'] == 'a_ranger' for r in rows):
            return {'etat': 'en_file', 'dossier': matter, 'texte': 'Pièces jointes : rangement prévu dans %s › PJ courriels' % _libelle(desk, matter)}
        if any(r['etat'] == 'deja' for r in rows):
            return {'etat': 'deja', 'dossier': matter, 'texte': 'Pièces jointes déjà rangées dans %s' % _libelle(desk, matter)}
        if any(r['etat'] == 'erreur' for r in rows):
            return {'etat': 'echec', 'dossier': matter, 'texte': 'Pièces jointes non rangées (%s)' % next(r['erreur'] for r in rows if r['etat'] == 'erreur').replace('_', ' ')}
    except (sqlite3.Error, Stop, KeyError, TypeError, ValueError):
        return None
    return None


def ligne(desk, mail_key, acteur=None, source=None):
    """Texte court de ``etat`` ou ``''``."""
    st = etat(desk, mail_key, acteur, source)
    return st['texte'] if st else ''
