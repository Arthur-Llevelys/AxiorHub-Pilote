"""5.6.97 (lot proteger, B01) : résolveur canonique d'un message et de ses rattachements, lu sur le serveur.

Identité d'un message : (compte, boîte, UIDVALIDITY, UID), dont la clé ``mail_key`` est l'empreinte (``Mail.key``). Les dossiers d'un message
sont lus dans les registres du serveur, message par message :

- ``work_items.matter`` (collecteur), ``portfolio_mail_links`` (rattachements proposés, automatiques ou confirmés ; un rattachement rejeté ne
  compte pas), ``mail_archive_links`` (correspondance conservée dans un dossier, non retirée), le rapport de l'agent (``reports/<clé>.json``),
  le dossier du projet de réponse préparé pour ce message (``manual_drafts``, 5.6.100) ;
- le registre des affaires du lot dossiers, s'il expose ``dossiers5697.rattachements_messages(desk, cles)``.

Un rattachement affiché, un champ POST, le suivi d'un fil ou le dernier dossier du fil ne confèrent **aucun** droit : ils ne sont jamais lus
ici. Des rattachements contradictoires (deux dossiers) sont tous rendus : le garde exige les droits sur chacun (``acces5697``) et la ligne est
signalée « rattachement à vérifier ». Aucune connexion IMAP, aucun appel au modèle : lecture locale seulement.
"""
from collections import namedtuple
import json
from pathlib import Path
import re
import sqlite3

from .common import Stop, digest

CLE = re.compile(r'^[0-9a-f]{64}$')
LOT = 400                                  # clés par requête (borne des paramètres SQLite)
ILLISIBLE = '?rattachement_illisible'      # rapport présent mais illisible : jamais lu comme « aucun dossier » (voir acces5697.visible)
Ref = namedtuple('Ref', 'mail_key compte boite uidvalidity uid dossiers')


def compte_de(cfg):
    """Compte de la boîte, tel qu'il entre dans la clé d'un message (``utilisateur@hôte``)."""
    cfg = cfg or {}
    return str(cfg.get('username') or '') + '@' + str(cfg.get('host') or '')


def cle_canonique(compte, boite, uidvalidity, uid):
    return digest('|'.join([str(compte), str(boite), str(uidvalidity), str(uid)]))


def cle_gmail(connexion_id, gmail_id):
    """5.6.97 (lot gmail) : clé canonique d'un message Gmail, (connexion, identifiant Gmail) ; jamais fusionnée avec une clé IMAP ni entre comptes."""
    return digest('|'.join(('gmail', str(connexion_id), str(gmail_id))))


GMAIL = re.compile(r'^gmail:([A-Za-z0-9_-]{1,80}):([A-Za-z0-9_-]{1,120})$')


def _cle_de(desk, ref):
    """Clé d'un message désigné par sa clé, par son identité IMAP complète, ou les deux (qui doivent alors concorder)."""
    if isinstance(ref, Ref):
        ref = ref._asdict()
    if isinstance(ref, dict) and ref.get('gmail_id') and ref.get('connexion_id'):   # 5.6.97 (lot gmail) : (connexion, identifiant Gmail)
        return cle_gmail(ref['connexion_id'], ref['gmail_id']), ('gmail:' + str(ref['connexion_id']), 'Gmail', '', str(ref['gmail_id']))
    if isinstance(ref, str) and GMAIL.match(ref):                    # 5.6.97 (lot gmail) : « gmail:<connexion>:<identifiant Gmail> »
        connexion, gid = GMAIL.match(ref).groups()
        return cle_gmail(connexion, gid), ('gmail:' + connexion, 'Gmail', '', gid)
    if isinstance(ref, str):
        cle, identite = ref, None
    elif isinstance(ref, dict):
        cle = str(ref.get('mail_key') or ref.get('cle') or ref.get('key') or '')
        parts = [ref.get('compte') or ref.get('account'), ref.get('boite') or ref.get('dossier_imap') or ref.get('folder') or ref.get('mailbox'),
                 ref.get('uidvalidity'), ref.get('uid')]
        identite = tuple(str(p) for p in parts) if all(p not in (None, '') for p in parts) else None
    else:
        raise Stop('reference_message_invalide')
    if identite:
        calculee = cle_canonique(*identite)
        if cle and cle != calculee:
            raise Stop('reference_message_incoherente')        # clé et identité IMAP ne désignent pas le même message
        cle = calculee
    if not CLE.match(cle):
        raise Stop('reference_message_invalide')
    return cle, identite


def _lignes(desk, sql, cles):
    out = []
    for i in range(0, len(cles), LOT):
        part = cles[i:i + LOT]
        try:
            out += desk.db.execute(sql % ','.join('?' * len(part)), part).fetchall()
        except sqlite3.Error:                    # table pas encore créée sur cette installation : aucune association de cette source
            return out
    return out


def _archives(desk, cles):
    """Rattachements des correspondances conservées : la source (compte, boîte, UIDVALIDITY, UID) donne la clé du message."""
    try:
        from .correspondances5643 import account_id
        compte_id = account_id(desk.c)
    except (KeyError, TypeError):
        return {}
    compte = compte_de(desk.c.get('mail'))
    voulues, out = set(cles), {}
    try:
        rows = desk.db.execute("SELECT s.folder, s.uidvalidity, s.uid, l.matter_id FROM mail_archive_sources s JOIN mail_archive_links l ON l.archive_id=s.archive_id "
                               "WHERE s.account_id=? AND l.conservation_state<>'withdrawn'", (compte_id,)).fetchall()
    except sqlite3.Error:
        return {}
    for folder, validity, uid, matter in rows:
        cle = cle_canonique(compte, folder, validity, uid)
        if cle in voulues and matter:
            out.setdefault(cle, set()).add(str(matter))
    return out


def _projets(desk, cles):
    """5.6.100 (R04) : dossier enregistré dans le projet de réponse préparé pour chaque message (``manual_drafts``). Un projet hérite des
    restrictions de son message et le message de celles de son projet ; une valeur « sans dossier » n'efface aucun autre rattachement.
    Données du projet illisibles : ``ILLISIBLE`` (jamais « aucun dossier »)."""
    out = {}
    for i in range(0, len(cles), LOT):
        part = cles[i:i + LOT]
        try:
            rows = desk.db.execute("SELECT mail_key, CASE WHEN json_valid(data) THEN COALESCE(json_extract(data,'$.matter'),'') ELSE ? END "
                                   "FROM manual_drafts WHERE mail_key IN (%s)" % ','.join('?' * len(part)), [ILLISIBLE] + part).fetchall()
        except sqlite3.Error:                    # table pas encore créée sur cette installation : aucun projet
            return out
        for cle, matter in rows:
            if matter not in (None, ''):
                out.setdefault(str(cle), set()).add(str(matter))
    return out


def _rapport(desk, cle):
    """Dossier noté par l'agent dans son rapport ; rapport absent : rien. Rapport présent mais illisible : ``ILLISIBLE`` (jamais « aucun dossier »)."""
    path = Path(str(desk.c['state_dir'])) / 'reports' / (cle + '.json')
    if not path.is_file():
        return ''
    try:
        return str((json.loads(path.read_text(encoding='utf-8')) or {}).get('matter') or '')
    except (OSError, ValueError, UnicodeError, AttributeError):
        return ILLISIBLE


def dossiers_par_cle(desk, cles, rapports=True):
    """{clé: frozenset(dossiers)} pour chaque clé demandée (ensemble vide : message sans dossier). Lecture locale, par lots."""
    cles = list(dict.fromkeys(str(c) for c in cles if CLE.match(str(c or ''))))
    out = {c: set() for c in cles}
    if not cles:
        return {}
    for cle, matter in _lignes(desk, "SELECT mail_key, matter FROM work_items WHERE mail_key IN (%s) AND COALESCE(matter,'')<>''", cles):
        out[cle].add(str(matter))
    for cle, matter in _lignes(desk, "SELECT mail_key, matter FROM portfolio_mail_links WHERE mail_key IN (%s) AND matter<>'' AND status<>'rejected'", cles):
        out[cle].add(str(matter))
    for cle, matters in _archives(desk, cles).items():
        out[cle] |= matters
    for cle, matters in _projets(desk, cles).items():   # 5.6.100 (R04) : dossier du projet de réponse de ce message
        out[cle] |= matters
    try:
        from . import dossiers5697                       # registre unique des affaires (lot dossiers), s'il expose ses rattachements
        hook = getattr(dossiers5697, 'rattachements_messages', None)
    except ImportError:
        hook = None
    if hook:
        for cle, matters in (hook(desk, cles) or {}).items():
            if cle in out:
                out[cle] |= {str(m) for m in matters if m}
    try:   # 5.6.97 (lot gmail) : dossier enregistré sur le serveur pour le projet de réponse d'un message Gmail (choix de l'avocat)
        from .gmail5697 import rattachements_messages as _gmail5697
        for cle, matters in _gmail5697(desk, cles).items():
            if cle in out:
                out[cle] |= set(matters)
    except sqlite3.Error:
        pass
    if rapports:
        for cle in cles:
            matter = _rapport(desk, cle)
            if matter:
                out[cle].add(matter)
    return {c: frozenset(v) for c, v in out.items()}


def resoudre(desk, ref):
    """Message désigné par ``ref`` (clé, ou identité IMAP complète) et ses dossiers, lus sur le serveur. Un champ ``matter`` éventuellement
    joint à ``ref`` est ignoré : seul le registre du serveur fait foi."""
    cle, identite = _cle_de(desk, ref)
    dossiers = dossiers_par_cle(desk, [cle]).get(cle, frozenset())
    compte, boite, validite, uid = identite or ('', '', '', '')   # Gmail : ('gmail:<connexion>', 'Gmail', '', identifiant Gmail)
    return Ref(cle, compte, boite, validite, uid, dossiers)


def principal(dossiers, prefere=''):
    """Dossier affiché pour un message : celui qui est préféré s'il en fait partie, sinon le seul ; plusieurs : aucun (à vérifier)."""
    if prefere and prefere in dossiers:
        return prefere
    return next(iter(dossiers)) if len(dossiers) == 1 else ''
