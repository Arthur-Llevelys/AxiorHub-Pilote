"""5.6.103 (lot M) : Outlook courrier par l'application Microsoft intégrée (``microsoft56103``) — lecture et brouillons, jamais d'envoi.

- **Lecture** (capacité ``courrier`` = ``Mail.ReadWrite``) : ``lister`` (boîte de réception par défaut), ``lire`` (corps demandé en texte
  brut à Graph, ``Prefer: outlook.body-content-type="text"`` : aucun HTML de courriel n'arrive dans la page), ``pieces`` et ``piece``.
- **Projet local** : « Retenir » (``retenir``) enregistre le projet dans AxiorHub et **n'envoie ni ne dépose rien**. Pièces jointes copiées
  dans le dossier d'état du cabinet (``outlook56103/<projet>/``), empreinte gardée.
- **Déposer dans Brouillons** (``deposer``) est une action distincte, geste d'une personne (jamais d'un travail ni d'un automatisme), avec
  clé d'idempotence et version : création du message dans le dossier Brouillons (``POST /me/mailFolders/drafts/messages``) portant une
  propriété étendue ``MARQUE`` (retrouver le brouillon si la réponse se perd), puis chaque pièce (moins de 3 Mo : ``POST attachments`` ;
  de 3 à 150 Mo : session d'envoi, octets envoyés à l'adresse pré-authentifiée **sans** jeton). ``pause5629.checkpoint`` avant chaque effet.
- **État « Déposé » seulement après constat** chez Microsoft : message relu (``isDraft``, dossier Brouillons, objet, chaque pièce par nom
  et taille). Sinon « Dépôt à vérifier » (réponse perdue, relecture différente) ou « Dépôt incomplet » (pièce refusée) ; ``verifier``
  retrouve le brouillon par sa marque avant tout nouvel essai (zéro doublon).
- **Jamais d'envoi** : aucune fonction n'appelle ``send``/``sendMail`` ; ``microsoft56103.verifier_adresse`` refuse ces adresses et
  ``Mail.Send`` n'est jamais demandé.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote, urlencode, urlsplit
import uuid

from .common import Stop
from . import microsoft56103 as ms

API = ms.GRAPH + '/v1.0/me'
CAPACITE = 'courrier'
PETITE = 3 * 1024 * 1024               # en dessous : pièce envoyée en une fois (contentBytes)
GRANDE = 150 * 1024 * 1024             # plafond Outlook d'une pièce
MORCEAU = 10 * 327_680                 # 3,1 Mio par envoi (Microsoft conseille moins de 4 Mo)
TEXTE_MAX = 50_000
MARQUE = 'String {6f0e3b1a-2c4d-4e5f-8a9b-0c1d2e3f4a5b} Name AxiorHubBrouillon'
HOTES_ENVOI = ('outlook.office.com', 'outlook.office365.com')
IDEMPOTENCE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
ADRESSE = re.compile(r'^[^@\s,;<>"]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$')
ETATS = {'local': 'Projet retenu dans AxiorHub (rien dans Outlook)', 'depot_en_cours': 'Dépôt en cours', 'incomplet': 'Dépôt incomplet : pièce non déposée',
         'a_verifier': 'Dépôt à vérifier', 'depose': 'Déposé dans les Brouillons d’Outlook (non envoyé)', 'abandonne': 'Abandonné'}
ENVOI_TRANSPORT = None                 # tests : appelable (méthode, url, en-têtes, corps) -> (statut, en-têtes, octets) pour l'adresse d'envoi

SCHEMA = '''
CREATE TABLE IF NOT EXISTS outlook56103_brouillons(id TEXT PRIMARY KEY, owner TEXT NOT NULL, connexion_id TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 0,
  a TEXT NOT NULL DEFAULT '', cc TEXT NOT NULL DEFAULT '', objet TEXT NOT NULL DEFAULT '', texte TEXT NOT NULL DEFAULT '', pieces TEXT NOT NULL DEFAULT '[]',
  etat TEXT NOT NULL DEFAULT 'local', marque TEXT NOT NULL, distant TEXT NOT NULL DEFAULT '', change_key TEXT NOT NULL DEFAULT '', erreur TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1, cree TEXT NOT NULL, maj TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outlook56103_journal(idempotence TEXT PRIMARY KEY, brouillon_id TEXT NOT NULL, action TEXT NOT NULL, auteur TEXT NOT NULL,
  resultat TEXT NOT NULL DEFAULT '', cree TEXT NOT NULL);
'''


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ------------------------------------------------------------------------------------------------ lecture
def _conn(desk, owner):
    return ms.connexion_active(desk, owner, CAPACITE)


def lister(desk, owner, dossier='inbox', nombre=25):
    if dossier not in ('inbox', 'drafts', 'sentitems', 'archive'):
        raise Stop('dossier_outlook_inconnu')
    row = _conn(desk, owner)
    url = '%s/mailFolders/%s/messages?%s' % (API, dossier, urlencode({'$top': max(1, min(50, int(nombre))), '$orderby': 'receivedDateTime desc',
                                                                      '$select': 'id,subject,from,receivedDateTime,bodyPreview,hasAttachments,isDraft,conversationId'}))
    out = ms.appeler(desk, row['id'], 'GET', url, capacite=CAPACITE)
    return [{'id': str(m.get('id') or ''), 'objet': str(m.get('subject') or '')[:300], 'de': str(((m.get('from') or {}).get('emailAddress') or {}).get('address') or ''),
             'date': str(m.get('receivedDateTime') or ''), 'apercu': str(m.get('bodyPreview') or '')[:300], 'pieces': bool(m.get('hasAttachments')),
             'brouillon': bool(m.get('isDraft')), 'fil': str(m.get('conversationId') or '')} for m in out.get('value') or []]


def _message(mid):
    if not re.fullmatch(r'[A-Za-z0-9=_+/-]{8,400}', str(mid or '')):
        raise Stop('message_absent')
    return API + '/messages/' + quote(str(mid), safe='')


def lire(desk, owner, message_id):
    """Message en texte brut (Graph convertit le HTML) : jamais de HTML de courriel injecté dans la page."""
    row = _conn(desk, owner)
    m = ms.appeler(desk, row['id'], 'GET', _message(message_id) + '?$select=id,subject,from,toRecipients,ccRecipients,receivedDateTime,body,hasAttachments',
                   capacite=CAPACITE, entetes={'Prefer': 'outlook.body-content-type="text"'})
    adresses = lambda liste: [str((x.get('emailAddress') or {}).get('address') or '') for x in liste or []]
    return {'id': str(m.get('id') or ''), 'objet': str(m.get('subject') or ''), 'de': str(((m.get('from') or {}).get('emailAddress') or {}).get('address') or ''),
            'a': adresses(m.get('toRecipients')), 'cc': adresses(m.get('ccRecipients')), 'date': str(m.get('receivedDateTime') or ''),
            'texte': str((m.get('body') or {}).get('content') or '')[:200_000], 'pieces': bool(m.get('hasAttachments'))}


def pieces(desk, owner, message_id):
    row = _conn(desk, owner)
    out = ms.appeler(desk, row['id'], 'GET', _message(message_id) + '/attachments?$select=id,name,size,contentType,isInline', capacite=CAPACITE)
    return [{'id': str(a.get('id') or ''), 'nom': str(a.get('name') or ''), 'taille': int(a.get('size') or 0), 'type': str(a.get('contentType') or '')}
            for a in out.get('value') or [] if not a.get('isInline')]


def piece(desk, owner, message_id, piece_id):
    row = _conn(desk, owner)
    if not re.fullmatch(r'[A-Za-z0-9=_+/-]{8,400}', str(piece_id or '')):
        raise Stop('piece_absente')
    statut, _, brut = ms.requete(desk, row['id'], 'GET', _message(message_id) + '/attachments/' + quote(str(piece_id), safe='') + '/$value', capacite=CAPACITE)
    if statut >= 400:
        raise ms.ErreurMicrosoft('microsoft56103_http_%d' % statut, statut)
    if len(brut) > GRANDE:
        raise Stop('piece_trop_volumineuse')
    return brut


# ------------------------------------------------------------------------------------------------ projet local (« Retenir »)
def _adresses(valeur, limite=20):
    sortie = []
    for brut in re.split(r'[,;\n]+', str(valeur or '')):
        brut = brut.strip().strip('<>').strip()
        if not brut:
            continue
        if not ADRESSE.match(brut):
            raise Stop('adresse_invalide')
        sortie.append(brut.lower())
    sortie = list(dict.fromkeys(sortie))
    if len(sortie) > limite:
        raise Stop('trop_de_destinataires')
    return sortie


def _dossier_pieces(desk, brouillon_id):
    return Path(desk.c['state_dir']) / 'outlook56103' / re.sub(r'[^A-Za-z0-9]', '', brouillon_id)


def _nom_piece(nom):
    nom = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(nom or '').strip())[:180]
    if not nom or nom in ('.', '..'):
        raise Stop('piece_nom_invalide')
    return nom


def retenir(desk, owner, a='', cc='', objet='', texte='', pieces_jointes=()):
    """« Retenir » : projet local seulement. ``pieces_jointes`` : suite de ``(nom, octets)``. Rien n'est déposé ni envoyé."""
    ensure_schema(desk)
    a_liste, cc_liste = _adresses(a), _adresses(cc)
    objet = re.sub(r'[\r\n]+', ' ', str(objet or '')).strip()[:300]
    texte = str(texte or '')[:TEXTE_MAX]
    ident = uuid.uuid4().hex
    liste = []
    total = 0
    dossier = _dossier_pieces(desk, ident)
    for i, (nom, donnees) in enumerate(pieces_jointes or ()):
        donnees = bytes(donnees or b'')
        if not donnees or len(donnees) > GRANDE:
            raise Stop('piece_trop_volumineuse' if donnees else 'piece_vide')
        total += len(donnees)
        if total > 4 * GRANDE or i >= 20:
            raise Stop('trop_de_pieces')
        liste.append({'nom': _nom_piece(nom), 'fichier': '%02d' % i, 'taille': len(donnees), 'empreinte': hashlib.sha256(donnees).hexdigest()})
    for item, (_, donnees) in zip(liste, pieces_jointes or ()):
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / item['fichier']).write_bytes(bytes(donnees))
    maintenant = _iso()
    desk.db.execute('INSERT INTO outlook56103_brouillons(id, owner, a, cc, objet, texte, pieces, marque, cree, maj) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (ident, str(owner), ', '.join(a_liste), ', '.join(cc_liste), objet, texte, json.dumps(liste), uuid.uuid4().hex, maintenant, maintenant))
    desk.db.commit()
    desk.audit('outlook56103_retenu', {'brouillon': ident[:12], 'pieces': len(liste)})
    return vue(desk, owner, ident)


def brouillon(desk, owner, brouillon_id):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM outlook56103_brouillons WHERE id=?', (str(brouillon_id or ''),)).fetchone()
    if not row or row['owner'] != str(owner):
        raise Stop('brouillon_absent')                      # le projet d'une autre personne n'existe pas pour celle-ci
    return dict(row)


def vue(desk, owner, brouillon_id):
    p = brouillon(desk, owner, brouillon_id)
    return {'id': p['id'], 'etat': p['etat'], 'libelle': ETATS.get(p['etat'], p['etat']), 'version': p['version'], 'a': p['a'], 'cc': p['cc'],
            'objet': p['objet'], 'texte': p['texte'], 'pieces': [{k: x[k] for k in ('nom', 'taille')} for x in json.loads(p['pieces'] or '[]')],
            'erreur': p['erreur'], 'depose': p['etat'] == 'depose'}


def abandonner(desk, owner, brouillon_id, version):
    p = brouillon(desk, owner, brouillon_id)
    if p['etat'] in ('depot_en_cours',):
        raise Stop('etat_incompatible')
    cur = desk.db.execute("UPDATE outlook56103_brouillons SET etat='abandonne', version=version+1, maj=? WHERE id=? AND version=?", (_iso(), p['id'], int(version)))
    desk.db.commit()
    if not cur.rowcount:
        raise Stop('version_perimee')
    return {'message': 'Projet abandonné. Un brouillon déjà déposé reste dans Outlook, où vous pouvez le supprimer.'}


# ------------------------------------------------------------------------------------------------ « Déposer dans Brouillons »
def _exiger_personne(desk):
    """Garde-fou : le dépôt est un geste d'une personne, jamais d'un travail de l'agent ni d'un automatisme."""
    if getattr(desk, 'active_job_id', None) or getattr(desk, 'origin_context', None) == 'automatic':
        raise Stop('geste_reserve_a_la_personne')


def _deja(desk, idempotence):
    if not IDEMPOTENCE.match(str(idempotence or '')):
        raise Stop('idempotence_requise')
    row = desk.db.execute('SELECT * FROM outlook56103_journal WHERE idempotence=?', (str(idempotence),)).fetchone()
    return dict(row) if row else None


def _maj(desk, p_id, **champs):
    champs['maj'] = _iso()
    desk.db.execute('UPDATE outlook56103_brouillons SET %s WHERE id=?' % ', '.join('%s=?' % k for k in champs), (*champs.values(), p_id))
    desk.db.commit()


def _checkpoint(desk):
    from .pause5629 import checkpoint
    checkpoint(desk)


def _incertain(error):
    """Réponse perdue (délai, réseau, 5xx, 408, 429) : le résultat chez Microsoft est inconnu."""
    if str(error).split(':')[0] in ('delai_http_depasse', 'connexion_http_indisponible'):
        return True
    statut = getattr(error, 'statut', None)
    return statut is not None and (statut == 0 or statut >= 500 or statut in (408, 429))


def _corps_message(p):
    dest = lambda liste: [{'emailAddress': {'address': x.strip()}} for x in liste.split(',') if x.strip()]
    return {'subject': p['objet'], 'body': {'contentType': 'Text', 'content': p['texte']}, 'toRecipients': dest(p['a']), 'ccRecipients': dest(p['cc']),
            'singleValueExtendedProperties': [{'id': MARQUE, 'value': p['marque']}]}


def _envoyer_octets(url, entetes, donnees):
    """Octets d'une pièce vers l'adresse pré-authentifiée d'Outlook : hôte contrôlé, **aucun** jeton joint."""
    p = urlsplit(str(url or ''))
    if p.scheme != 'https' or p.hostname not in HOTES_ENVOI or p.username or p.password:
        raise Stop('adresse_envoi_refusee')
    if any(k.lower() == 'authorization' for k in entetes):
        raise Stop('adresse_envoi_refusee')
    if ENVOI_TRANSPORT is not None:
        return ENVOI_TRANSPORT('PUT', url, dict(entetes), donnees)
    from .cloud5688 import CloudHTTP
    return CloudHTTP('https://' + p.netloc, timeout=60).call('PUT', url, donnees, entetes, 2_000_000)


def _joindre(desk, conn_id, distant, item, donnees):
    _checkpoint(desk)
    if len(donnees) < PETITE:
        ms.appeler(desk, conn_id, 'POST', _message(distant) + '/attachments', {'@odata.type': '#microsoft.graph.fileAttachment', 'name': item['nom'],
                                                                              'contentBytes': base64.b64encode(donnees).decode()}, capacite=CAPACITE)
        return
    session = ms.appeler(desk, conn_id, 'POST', _message(distant) + '/attachments/createUploadSession',
                         {'AttachmentItem': {'attachmentType': 'file', 'name': item['nom'], 'size': len(donnees)}}, capacite=CAPACITE)
    url = str(session.get('uploadUrl') or '')
    debut = 0
    while debut < len(donnees):
        _checkpoint(desk)
        fin = min(len(donnees), debut + MORCEAU)
        statut, _, _ = _envoyer_octets(url, {'Content-Type': 'application/octet-stream', 'Content-Length': str(fin - debut),
                                             'Content-Range': 'bytes %d-%d/%d' % (debut, fin - 1, len(donnees))}, donnees[debut:fin])
        if statut >= 400:
            raise ms.ErreurMicrosoft('microsoft56103_http_%d' % statut, statut)
        debut = fin


def _constat(desk, conn_id, p):
    """Relecture chez Microsoft : brouillon dans le dossier Brouillons, objet, chaque pièce (nom et taille au moins égale)."""
    m = ms.appeler(desk, conn_id, 'GET', _message(p['distant']) + '?$select=id,subject,isDraft,parentFolderId,changeKey&$expand=attachments($select=id,name,size)',
                   capacite=CAPACITE)
    dossier = ms.appeler(desk, conn_id, 'GET', API + '/mailFolders/drafts?$select=id', capacite=CAPACITE)
    presentes = {}
    for x in m.get('attachments') or []:
        presentes.setdefault(str(x.get('name') or ''), []).append(int(x.get('size') or 0))
    attendues = json.loads(p['pieces'] or '[]')
    manquantes = [x for x in attendues if not any(t >= x['taille'] for t in presentes.get(x['nom'], []))]
    conforme = (m.get('isDraft') is True and str(m.get('parentFolderId') or '') == str(dossier.get('id') or '-') and str(m.get('subject') or '') == p['objet']
                and not manquantes)
    return conforme, manquantes, str(m.get('changeKey') or '')


def _retrouver(desk, conn_id, p):
    """Brouillon déjà créé lors d'un essai dont la réponse s'est perdue, retrouvé par sa marque (propriété étendue)."""
    filtre = "singleValueExtendedProperties/Any(ep: ep/id eq '%s' and ep/value eq '%s')" % (MARQUE, p['marque'])
    out = ms.appeler(desk, conn_id, 'GET', API + '/mailFolders/drafts/messages?' + urlencode({'$filter': filtre, '$select': 'id', '$top': 2}), capacite=CAPACITE)
    ids = [str(x.get('id') or '') for x in out.get('value') or [] if x.get('id')]
    return ids[0] if ids else ''


def deposer(desk, owner, brouillon_id, version, idempotence):
    """« Déposer dans Brouillons » : geste distinct d'une personne. « Déposé » seulement après constat chez Microsoft."""
    ensure_schema(desk)
    deja = _deja(desk, idempotence)
    if deja:
        if deja['auteur'] != str(owner):
            raise Stop('brouillon_absent')
        return {'deja': True, **vue(desk, owner, deja['brouillon_id'])}
    _exiger_personne(desk)
    p = brouillon(desk, owner, brouillon_id)
    row = _conn(desk, owner)
    if int(version) != p['version']:
        raise Stop('version_perimee')
    if p['etat'] == 'a_verifier':
        raise Stop('depot_a_verifier')                      # vérifier d'abord : jamais de second brouillon
    if p['etat'] not in ('local', 'incomplet'):
        raise Stop('etat_incompatible')
    if p['etat'] == 'incomplet' and (p['connexion_id'] != row['id'] or p['generation'] != row['generation']):
        raise Stop('compte_modifie')
    _checkpoint(desk)
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        cur = desk.db.execute("UPDATE outlook56103_brouillons SET etat='depot_en_cours', connexion_id=?, generation=?, erreur='', version=version+1, maj=? "
                              "WHERE id=? AND version=? AND etat IN ('local','incomplet')", (row['id'], row['generation'], _iso(), p['id'], p['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        desk.db.execute('INSERT INTO outlook56103_journal(idempotence, brouillon_id, action, auteur, cree) VALUES(?,?,?,?,?)',
                        (str(idempotence), p['id'], 'deposer', str(owner), _iso()))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    p = brouillon(desk, owner, p['id'])
    if not p['distant']:
        try:
            out = ms.appeler(desk, row['id'], 'POST', API + '/mailFolders/drafts/messages', _corps_message(p), capacite=CAPACITE)
        except Stop as error:
            if _incertain(error):
                _maj(desk, p['id'], etat='a_verifier', erreur=str(error).split(':')[0])
                return {'deja': False, 'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
            _maj(desk, p['id'], etat='local', erreur=str(error).split(':')[0])   # refus certain : rien n'a été créé, nouvel essai possible
            raise
        distant = str(out.get('id') or '')
        if not distant:
            _maj(desk, p['id'], etat='a_verifier', erreur='reponse_sans_identifiant')
            return {'deja': False, 'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
        _maj(desk, p['id'], distant=distant)
        p['distant'] = distant
    return _completer(desk, owner, row, p, idempotence)


def _completer(desk, owner, row, p, idempotence=''):
    """Pièces manquantes jointes, puis constat ; « Déposé » seulement si le constat est conforme."""
    try:
        conforme, manquantes, _ = _constat(desk, row['id'], p)
        for item in manquantes:
            donnees = (_dossier_pieces(desk, p['id']) / item['fichier']).read_bytes()
            if hashlib.sha256(donnees).hexdigest() != item['empreinte']:
                raise Stop('piece_modifiee_localement')
            _joindre(desk, row['id'], p['distant'], item, donnees)
        conforme, manquantes, cle = _constat(desk, row['id'], p)
    except Stop as error:                                    # jamais « Déposé » : à vérifier si la réponse s'est perdue, incomplet sinon
        etat = 'a_verifier' if _incertain(error) else 'incomplet'
        _maj(desk, p['id'], etat=etat, erreur=str(error).split(':')[0])
        return {'deja': False, 'resultat': etat, **vue(desk, owner, p['id'])}
    if not conforme:
        _maj(desk, p['id'], etat='incomplet' if manquantes else 'a_verifier', erreur='relecture_differente')
        return {'deja': False, 'resultat': 'incomplet' if manquantes else 'a_verifier', **vue(desk, owner, p['id'])}
    _maj(desk, p['id'], etat='depose', change_key=cle, erreur='')
    if idempotence:
        desk.db.execute("UPDATE outlook56103_journal SET resultat='depose' WHERE idempotence=?", (str(idempotence),))
        desk.db.commit()
    desk.audit('outlook56103_depose', {'brouillon': p['id'][:12]})
    return {'deja': False, 'resultat': 'depose', **vue(desk, owner, p['id'])}


def verifier(desk, owner, brouillon_id):
    """Après « Dépôt à vérifier » : retrouve le brouillon par sa marque ; constat s'il existe, retour au projet local s'il n'existe pas."""
    _exiger_personne(desk)
    p = brouillon(desk, owner, brouillon_id)
    if p['etat'] not in ('a_verifier', 'incomplet', 'depose'):
        raise Stop('etat_incompatible')
    row = _conn(desk, owner)
    if p['connexion_id'] and (p['connexion_id'] != row['id'] or p['generation'] != row['generation']):
        raise Stop('compte_modifie')
    distant = p['distant'] or _retrouver(desk, row['id'], p)
    if not distant:
        _maj(desk, p['id'], etat='local', erreur='')
        return {'resultat': 'local', **vue(desk, owner, p['id'])}
    if distant != p['distant']:
        _maj(desk, p['id'], distant=distant)
        p['distant'] = distant
    try:
        conforme, manquantes, cle = _constat(desk, row['id'], p)
    except ms.ErreurMicrosoft as error:
        if error.statut == 404:                              # supprimé dans Outlook depuis : on repart du projet local
            _maj(desk, p['id'], etat='local', distant='', erreur='brouillon_supprime_dans_outlook')
            return {'resultat': 'local', **vue(desk, owner, p['id'])}
        raise
    if conforme:
        _maj(desk, p['id'], etat='depose', change_key=cle, erreur='')
        return {'resultat': 'depose', **vue(desk, owner, p['id'])}
    _maj(desk, p['id'], etat='incomplet', erreur='relecture_differente' if not manquantes else 'piece_manquante')
    return {'resultat': 'incomplet', **vue(desk, owner, p['id'])}
