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
- **Reprise d'un dépôt interrompu** (5.6.106, audit M4) : avant la création distante, une tentative et un bail (échéance, ``BAIL_DEPOT``,
  prolongé avant chaque effet) ; toutes les écritures du dépôt sont conditionnelles à cette tentative. Bail expiré → « À vérifier » (mise à
  jour conditionnelle), puis recherche chez Microsoft de la marque du projet : un résultat unique est repris (contrôle et pièces, sans
  nouvelle création) ; recherche en panne ou plusieurs résultats → on reste « À vérifier » ; aucun résultat → nouvel essai seulement après
  un rapprochement explicite de la personne (``rapprocher``). Clé d'idempotence liée au compte Microsoft, au projet et à la version
  approuvée ; jamais de reprise sur un autre compte Microsoft ; chaque appel revérifie la génération de la connexion.
- **Constat complet** (5.6.106, audit M5) : l'instantané approuvé est gardé ; « Déposé » seulement si ``toRecipients``, ``ccRecipients``,
  ``bccRecipients`` et le corps (fins de ligne et représentation normalisées, aucune réécriture du fond acceptée) correspondent, et si
  chaque pièce est reconnue par son identifiant Outlook ou par l'empreinte de son contenu (un même nom et une même taille ne suffisent
  pas). Sinon « Brouillon distant différent : à vérifier », écarts lisibles et lien vers Outlook ; rien n'est jamais réécrit dans Outlook.
"""
import base64
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import unicodedata
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
         'a_verifier': 'Dépôt à vérifier', 'depose': 'Déposé dans les Brouillons d’Outlook (non envoyé)', 'abandonne': 'Abandonné',
         'different': 'Brouillon distant différent : à vérifier'}                     # 5.6.106 (audit M5)
BAIL_DEPOT = 120                       # 5.6.106 (audit M4) : secondes ; prolongé avant chaque effet du dépôt (création, pièce, morceau)
LIENS_OUTLOOK = ('outlook.office.com', 'outlook.office365.com', 'outlook.live.com')
LIEN_BROUILLONS = 'https://outlook.office.com/mail/drafts'
ERREURS = {   # 5.6.106 : explication lisible de l'état (vue)
    'depot_interrompu': 'Le dépôt a été interrompu : AxiorHub cherche ce brouillon dans Outlook avant tout nouvel essai.',
    'aucun_brouillon_trouve': 'Aucun brouillon de ce projet n’a été trouvé dans Outlook. Regardez vos Brouillons : s’il n’y est pas, choisissez '
                              '« Je ne le trouve pas : autoriser un nouveau dépôt ».',
    'recherche_impossible': 'Outlook n’a pas pu être interrogé : relancez la vérification plus tard (aucun nouveau dépôt d’ici là).',
    'plusieurs_brouillons': 'Plusieurs brouillons de ce projet existent dans Outlook : gardez-en un, supprimez les autres, puis vérifiez à nouveau.',
    'piece_non_verifiable': 'Une pièce présente dans Outlook n’a pas pu être relue pour comparer son contenu : vérifiez-la dans Outlook.',
    'brouillon_distant_different': 'Le brouillon dans Outlook ne correspond pas au projet approuvé (détails ci-dessous) : rien n’a été modifié dans Outlook.',
    'connexion_modifiee': 'La connexion Microsoft a changé pendant le dépôt : vérifiez le brouillon.',
    'relecture_differente': 'Le message relu dans Outlook n’est plus un brouillon du dossier Brouillons.',
    'piece_manquante': 'Une pièce jointe manque encore dans Outlook : « Déposer » la joint sans créer de second brouillon.',
    'brouillon_supprime_dans_outlook': 'Le brouillon a été supprimé dans Outlook : le projet peut être déposé à nouveau.',
}
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
    # 5.6.106 (audit M4, M5) : tentative et bail du dépôt, compte Microsoft, instantané approuvé, écarts constatés, lien Outlook, pièces
    # reconnues chez Microsoft (fichier local → identifiant de la pièce), date du premier constat ; empreinte de la clé d'idempotence
    for table, colonnes in (('outlook56103_brouillons', (('tentative', "TEXT NOT NULL DEFAULT ''"), ('bail', 'REAL NOT NULL DEFAULT 0'),
                                                         ('compte_id', "TEXT NOT NULL DEFAULT ''"), ('approuve', "TEXT NOT NULL DEFAULT ''"),
                                                         ('ecarts', "TEXT NOT NULL DEFAULT ''"), ('lien', "TEXT NOT NULL DEFAULT ''"),
                                                         ('pieces_distantes', "TEXT NOT NULL DEFAULT '{}'"), ('constate_le', "TEXT NOT NULL DEFAULT ''"))),
                            ('outlook56103_journal', (('empreinte', "TEXT NOT NULL DEFAULT ''"),))):
        presentes = {r[1] for r in desk.db.execute('PRAGMA table_info(%s)' % table)}
        for nom, definition in colonnes:
            if nom not in presentes:
                desk.db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, nom, definition))
    desk.db.commit()


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


def _json_liste(valeur):
    try:
        out = json.loads(valeur or '[]')
    except ValueError:
        return []
    return [str(x) for x in out] if isinstance(out, list) else []


def _json_dict(valeur):
    try:
        out = json.loads(valeur or '{}')
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in out.items()} if isinstance(out, dict) else {}


def _lien_outlook(p):
    """Lien vers le brouillon (``webLink`` relu chez Microsoft, hôte Outlook contrôlé), à défaut vers le dossier Brouillons."""
    lien = str(p.get('lien') or '')
    hote = urlsplit(lien).hostname or ''
    if lien.startswith('https://') and hote in LIENS_OUTLOOK:
        return lien
    return LIEN_BROUILLONS


def vue(desk, owner, brouillon_id):
    p = brouillon(desk, owner, brouillon_id)
    distant_connu = p['etat'] in ('incomplet', 'a_verifier', 'depose', 'different') and bool(p['distant'])
    return {'id': p['id'], 'etat': p['etat'], 'libelle': ETATS.get(p['etat'], p['etat']), 'version': p['version'], 'a': p['a'], 'cc': p['cc'],
            'objet': p['objet'], 'texte': p['texte'], 'pieces': [{k: x[k] for k in ('nom', 'taille')} for x in json.loads(p['pieces'] or '[]')],
            'erreur': p['erreur'], 'depose': p['etat'] == 'depose',
            # 5.6.106 (audit M4, M5) : écarts lisibles, explication de l'état, lien vers Outlook, rapprochement possible (aucun brouillon trouvé)
            'ecarts': _json_liste(p.get('ecarts')) if p['etat'] == 'different' else [], 'message_etat': ERREURS.get(p['erreur'], ''),
            'lien_outlook': _lien_outlook(p) if distant_connu or p['etat'] == 'a_verifier' else '',
            'rapprochement_possible': p['etat'] == 'a_verifier' and not p['distant'] and p['erreur'] == 'aucun_brouillon_trouve'}


def abandonner(desk, owner, brouillon_id, version):
    p = _expirer_bail(desk, brouillon(desk, owner, brouillon_id))
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


def _empreinte_depot(owner, compte_id, brouillon_id, version):
    """5.6.106 (audit M4) : la clé d'idempotence d'un dépôt est liée à la personne, au compte Microsoft, au projet et à la version approuvée."""
    return hashlib.sha256(json.dumps([str(owner), str(compte_id), str(brouillon_id), int(version)]).encode('utf-8')).hexdigest()


def _maj(desk, p_id, **champs):
    champs['maj'] = _iso()
    desk.db.execute('UPDATE outlook56103_brouillons SET %s WHERE id=?' % ', '.join('%s=?' % k for k in champs), (*champs.values(), p_id))
    desk.db.commit()


def _maj_si(desk, p_id, tentative, **champs):
    """Écriture d'un dépôt en cours : seulement si cette tentative détient encore le dépôt (bail non repris par une vérification)."""
    champs['maj'] = _iso()
    cur = desk.db.execute("UPDATE outlook56103_brouillons SET %s WHERE id=? AND tentative=? AND etat='depot_en_cours'" % ', '.join('%s=?' % k for k in champs),
                          (*champs.values(), p_id, tentative))
    desk.db.commit()
    return bool(cur.rowcount)


def _maj_v(desk, p, **champs):
    """Écriture d'une vérification : conditionnelle à la version lue (deux vérifications simultanées : une seule écrit) ; version + 1."""
    champs['maj'] = _iso()
    cur = desk.db.execute('UPDATE outlook56103_brouillons SET %s, version=version+1 WHERE id=? AND version=?' % ', '.join('%s=?' % k for k in champs),
                          (*champs.values(), p['id'], p['version']))
    desk.db.commit()
    return bool(cur.rowcount)


def _checkpoint(desk):
    from .pause5629 import checkpoint
    checkpoint(desk)


def _incertain(error):
    """Réponse perdue (délai, réseau, 5xx, 408, 429) : le résultat chez Microsoft est inconnu."""
    if str(error).split(':')[0] in ('delai_http_depasse', 'connexion_http_indisponible'):
        return True
    statut = getattr(error, 'statut', None)
    return statut is not None and (statut == 0 or statut >= 500 or statut in (408, 429))


def _avant_effet(desk, p_id, tentative, row):
    """Avant chaque effet du dépôt (création, pièce, morceau) : pause commune, bail prolongé si cette tentative le détient encore, génération
    de la connexion inchangée. Sinon l'effet n'a pas lieu."""
    _checkpoint(desk)
    cur = desk.db.execute("UPDATE outlook56103_brouillons SET bail=? WHERE id=? AND tentative=? AND etat='depot_en_cours'",
                          (ms.HORLOGE() + BAIL_DEPOT, p_id, tentative))
    desk.db.commit()
    if not cur.rowcount:
        raise Stop('depot_repris')                          # bail expiré et repris par une vérification : on s'arrête
    ms._exiger_generation(ms.lire(desk, row['id']), row['generation'])


def _expirer_bail(desk, p):
    """5.6.106 (audit M4) : un « Dépôt en cours » dont le bail est échu (processus arrêté après la création distante, par exemple) passe
    « À vérifier » par mise à jour conditionnelle ; la vérification cherche ensuite la marque chez Microsoft avant tout nouvel essai."""
    if p['etat'] != 'depot_en_cours' or float(p.get('bail') or 0) > ms.HORLOGE():
        return p
    cur = desk.db.execute("UPDATE outlook56103_brouillons SET etat='a_verifier', erreur='depot_interrompu', version=version+1, maj=? "
                          "WHERE id=? AND etat='depot_en_cours' AND tentative=? AND bail<=?", (_iso(), p['id'], p.get('tentative') or '', ms.HORLOGE()))
    desk.db.commit()
    if cur.rowcount:
        desk.audit('outlook56103_depot_interrompu', {'brouillon': p['id'][:12]})
    return brouillon(desk, p['owner'], p['id'])


def _meme_compte(p, row):
    """Jamais de reprise sur un autre compte Microsoft : compte enregistré au dépôt (à défaut, connexion et génération)."""
    if p.get('compte_id'):
        return p['compte_id'] == row['compte_id']
    if p.get('connexion_id'):
        return p['connexion_id'] == row['id'] and p['generation'] == row['generation']
    return True


def _liste_adresses(texte):
    return [x.strip().lower() for x in str(texte or '').split(',') if x.strip()]


def _instantane(p):
    """Instantané approuvé par la personne au moment du dépôt (destinataires, copie, copie cachée, objet, texte, pièces et empreintes)."""
    return {'version': p['version'], 'a': _liste_adresses(p['a']), 'cc': _liste_adresses(p['cc']), 'cci': [], 'objet': p['objet'], 'texte': p['texte'],
            'pieces': [{k: x[k] for k in ('nom', 'taille', 'empreinte', 'fichier')} for x in json.loads(p['pieces'] or '[]')]}


def _approuve(p):
    try:
        out = json.loads(p.get('approuve') or '')
        if isinstance(out, dict) and 'texte' in out:
            return out
    except ValueError:
        pass
    return _instantane(p)                                    # projet déposé avant la 5.6.106 : son contenu local, qui n'est pas modifiable


def _corps_message(p):
    dest = lambda liste: [{'emailAddress': {'address': x.strip()}} for x in liste.split(',') if x.strip()]
    return {'subject': p['objet'], 'body': {'contentType': 'Text', 'content': p['texte']}, 'toRecipients': dest(p['a']), 'ccRecipients': dest(p['cc']),
            'singleValueExtendedProperties': [{'id': MARQUE, 'value': p['marque']}]}


def _texte_normalise(contenu, representation='text'):
    """Corps comparable : fins de ligne, espaces de fin de ligne, espace insécable, forme Unicode ; un corps rendu en HTML par Outlook est
    ramené à son texte (sauts de ligne des balises de bloc). Aucune autre réécriture n'est tenue pour équivalente."""
    t = str(contenu or '')
    if str(representation or '').lower() == 'html':
        t = re.sub(r'(?is)<(script|style|head)\b.*?</\1\s*>', '', t)
        t = re.sub(r'(?i)<br\s*/?>', '\n', t)
        t = re.sub(r'(?i)</(p|div|li|tr|h[1-6])\s*>', '\n', t)
        t = html.unescape(re.sub(r'<[^>]*>', '', t))
    t = unicodedata.normalize('NFC', t.replace('\r\n', '\n').replace('\r', '\n').replace('\xa0', ' '))
    return '\n'.join(ligne.rstrip() for ligne in t.split('\n')).strip('\n')


def _ecarts(attendu, m):
    """Écarts lisibles entre l'instantané approuvé et le message relu chez Microsoft (destinataires, copie, copie cachée, objet, texte)."""
    out = []
    lues = lambda cle: sorted(str((x.get('emailAddress') or {}).get('address') or '').strip().lower() for x in (m.get(cle) or []))
    for cle, champ, libelle in (('a', 'toRecipients', 'Destinataires'), ('cc', 'ccRecipients', 'Copie'), ('cci', 'bccRecipients', 'Copie cachée')):
        voulues, trouvees = sorted(attendu.get(cle) or []), lues(champ)
        if voulues != trouvees:
            out.append('%s : approuvé « %s », dans Outlook « %s ».' % (libelle, ', '.join(voulues) or 'aucun', ', '.join(trouvees) or 'aucun'))
    if str(m.get('subject') or '') != str(attendu.get('objet') or ''):
        out.append('Objet : approuvé « %s », dans Outlook « %s ».' % (attendu.get('objet') or '', str(m.get('subject') or '')[:300]))
    corps = m.get('body') or {}
    if _texte_normalise(corps.get('content'), corps.get('contentType')) != _texte_normalise(attendu.get('texte')):
        out.append('Texte : le corps du brouillon dans Outlook diffère du texte approuvé.')
    return out


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


IDENTIFIANT_PIECE = re.compile(r'^[A-Za-z0-9=_+/-]{1,400}$')


def _joindre(desk, row, distant, item, donnees, avant=None):
    """Joint une pièce et rend son identifiant Outlook ('' s'il n'est pas connu) ; ``avant()`` est appelé avant chaque effet."""
    avant = avant or (lambda: _checkpoint(desk))
    avant()
    if len(donnees) < PETITE:
        out = ms.appeler(desk, row['id'], 'POST', _message(distant) + '/attachments', {'@odata.type': '#microsoft.graph.fileAttachment', 'name': item['nom'],
                                                                                    'contentBytes': base64.b64encode(donnees).decode()},
                         capacite=CAPACITE, generation=row['generation'])
        aid = str(out.get('id') or '')
        return aid if IDENTIFIANT_PIECE.match(aid) else ''
    session = ms.appeler(desk, row['id'], 'POST', _message(distant) + '/attachments/createUploadSession',
                         {'AttachmentItem': {'attachmentType': 'file', 'name': item['nom'], 'size': len(donnees)}}, capacite=CAPACITE, generation=row['generation'])
    url = str(session.get('uploadUrl') or '')
    debut, aid = 0, ''
    while debut < len(donnees):
        avant()
        fin = min(len(donnees), debut + MORCEAU)
        statut, entetes, _ = _envoyer_octets(url, {'Content-Type': 'application/octet-stream', 'Content-Length': str(fin - debut),
                                                   'Content-Range': 'bytes %d-%d/%d' % (debut, fin - 1, len(donnees))}, donnees[debut:fin])
        if statut >= 400:
            raise ms.ErreurMicrosoft('microsoft56103_http_%d' % statut, statut)
        if fin >= len(donnees):                              # 201 : « Location » désigne la pièce créée (…/Attachments('<id>'))
            lieu = {k.lower(): v for k, v in (entetes or {}).items()}.get('location') or ''
            trouve = re.search(r"Attachments\('([^']+)'\)", str(lieu))
            aid = trouve.group(1) if trouve and IDENTIFIANT_PIECE.match(trouve.group(1)) else ''
        debut = fin
    return aid


def _contenu_piece(desk, row, distant, aid):
    if not IDENTIFIANT_PIECE.match(str(aid or '')):
        raise Stop('piece_absente')
    statut, _, brut = ms.requete(desk, row['id'], 'GET', _message(distant) + '/attachments/' + quote(str(aid), safe='') + '/$value', capacite=CAPACITE,
                                 generation=row['generation'])
    if statut >= 400:
        raise ms.ErreurMicrosoft('microsoft56103_http_%d' % statut, statut)
    if len(brut) > GRANDE:
        raise Stop('piece_trop_volumineuse')
    return brut


def _constat(desk, row, p, connues=None):
    """Relecture chez Microsoft et comparaison avec l'instantané approuvé. Rend ``{'brouillon_ok', 'ecarts', 'manquantes', 'non_verifiables',
    'pieces' (fichier → identifiant reconnu), 'cle', 'lien'}``. Pièces : identifiant connu, sinon empreinte du contenu relu ; jamais par le
    seul nom et la seule taille. Rien n'est écrit chez Microsoft ici."""
    m = ms.appeler(desk, row['id'], 'GET', _message(p['distant']) + '?$select=id,subject,isDraft,parentFolderId,changeKey,webLink,toRecipients,ccRecipients,'
                   'bccRecipients,body&$expand=attachments($select=id,name,size)', capacite=CAPACITE, entetes={'Prefer': 'outlook.body-content-type="text"'},
                   generation=row['generation'])
    dossier = ms.appeler(desk, row['id'], 'GET', API + '/mailFolders/drafts?$select=id', capacite=CAPACITE, generation=row['generation'])
    attendu = _approuve(p)
    ecarts = _ecarts(attendu, m)
    distantes = [{'id': str(x.get('id') or ''), 'nom': str(x.get('name') or ''), 'taille': int(x.get('size') or 0)} for x in m.get('attachments') or []]
    par_id = {x['id']: x for x in distantes}
    connues = dict(_json_dict(p.get('pieces_distantes')) if connues is None else connues)
    reconnues, utilisees, manquantes, non_verifiables = {}, set(), [], []
    for item in attendu['pieces']:
        aid = connues.get(item['fichier'], '')
        if aid and aid in par_id:
            utilisees.add(aid)
            if par_id[aid]['nom'] != item['nom']:
                ecarts.append('Pièce « %s » : renommée « %s » dans Outlook.' % (item['nom'], par_id[aid]['nom']))
            reconnues[item['fichier']] = aid
            continue
        candidats = [x for x in distantes if x['nom'] == item['nom'] and x['id'] not in utilisees and x['id'] not in connues.values()]
        trouvee, illisible = None, False
        for x in candidats:
            try:
                contenu = _contenu_piece(desk, row, p['distant'], x['id'])
            except Stop as error:
                if _incertain(error):
                    raise
                illisible = True
                continue
            if hashlib.sha256(contenu).hexdigest() == item['empreinte']:
                trouvee = x
                break
        if trouvee is not None:
            utilisees.add(trouvee['id'])
            reconnues[item['fichier']] = trouvee['id']
        elif illisible:
            utilisees.update(x['id'] for x in candidats)
            non_verifiables.append(item['nom'])
        elif candidats:
            utilisees.update(x['id'] for x in candidats)
            ecarts.append('Pièce « %s » : le contenu dans Outlook diffère de la pièce approuvée (même nom).' % item['nom'])
        elif aid or p.get('constate_le'):
            ecarts.append('Pièce « %s » : retirée du brouillon dans Outlook.' % item['nom'])
        else:
            manquantes.append(item)
    for x in distantes:
        if x['id'] not in utilisees and x['id'] not in reconnues.values():
            ecarts.append('Pièce « %s » : ajoutée dans Outlook, absente du projet approuvé.' % x['nom'])
    brouillon_ok = m.get('isDraft') is True and str(m.get('parentFolderId') or '') == str(dossier.get('id') or '-')
    return {'brouillon_ok': brouillon_ok, 'ecarts': ecarts, 'manquantes': manquantes, 'non_verifiables': non_verifiables, 'pieces': reconnues,
            'cle': str(m.get('changeKey') or ''), 'lien': str(m.get('webLink') or '')[:2000]}


def _issue(constat):
    """État après constat : « Déposé » seulement si tout correspond à l'instantané approuvé."""
    if constat['ecarts']:
        return 'different', 'brouillon_distant_different'
    if not constat['brouillon_ok']:
        return 'a_verifier', 'relecture_differente'
    if constat['non_verifiables']:
        return 'a_verifier', 'piece_non_verifiable'
    if constat['manquantes']:
        return 'incomplet', 'piece_manquante'
    return 'depose', ''


def _retrouver(desk, row, p):
    """Brouillons portant la marque du projet (essai dont la réponse s'est perdue) : liste des identifiants trouvés (0, 1 ou plusieurs)."""
    filtre = "singleValueExtendedProperties/Any(ep: ep/id eq '%s' and ep/value eq '%s')" % (MARQUE, p['marque'])
    out = ms.appeler(desk, row['id'], 'GET', API + '/mailFolders/drafts/messages?' + urlencode({'$filter': filtre, '$select': 'id', '$top': 3}),
                     capacite=CAPACITE, generation=row['generation'])
    return [str(x.get('id') or '') for x in out.get('value') or [] if x.get('id')]


def deposer(desk, owner, brouillon_id, version, idempotence):
    """« Déposer dans Brouillons » : geste distinct d'une personne. « Déposé » seulement après constat chez Microsoft."""
    ensure_schema(desk)
    _exiger_personne(desk)
    deja = _deja(desk, idempotence)
    if deja:
        if deja['auteur'] != str(owner):
            raise Stop('brouillon_absent')
        if deja['brouillon_id'] != str(brouillon_id or ''):
            raise Stop('cle_idempotence_deja_utilisee')     # 5.6.106 (M4) : même clé pour un autre projet = conflit
    p = brouillon(desk, owner, brouillon_id)
    row = _conn(desk, owner)
    empreinte = _empreinte_depot(owner, row['compte_id'], p['id'], version)
    if deja:
        if deja.get('empreinte') and deja['empreinte'] != empreinte:
            raise Stop('cle_idempotence_deja_utilisee')     # autre version approuvée ou autre compte Microsoft
        p = _expirer_bail(desk, p)
        if p['etat'] == 'a_verifier' and p['erreur'] == 'depot_interrompu' and _meme_compte(p, row):
            return {'deja': True, **_reprendre(desk, owner, row, p)}   # rejeu après un arrêt : reprise, jamais une nouvelle création
        return {'deja': True, **vue(desk, owner, p['id'])}
    p = _expirer_bail(desk, p)
    if p['etat'] == 'depot_en_cours':
        raise Stop('depot_en_cours')                        # bail en cours : un seul dépôt à la fois
    if p['etat'] == 'a_verifier' and p['erreur'] == 'depot_interrompu':
        if not _meme_compte(p, row):
            raise Stop('compte_modifie')
        return {'deja': False, **_reprendre(desk, owner, row, p)}
    if int(version) != p['version']:
        raise Stop('version_perimee')
    if p['etat'] in ('a_verifier', 'incomplet', 'different', 'depose') and not _meme_compte(p, row):
        raise Stop('compte_modifie')
    if p['etat'] == 'a_verifier':
        raise Stop('depot_a_verifier')                      # vérifier d'abord : jamais de second brouillon
    if p['etat'] == 'different':
        raise Stop('brouillon_distant_different')           # jamais d'écrasement d'une modification faite dans Outlook
    if p['etat'] not in ('local', 'incomplet'):
        raise Stop('etat_incompatible')
    _checkpoint(desk)
    tentative = uuid.uuid4().hex
    desk.db.execute('BEGIN IMMEDIATE')
    try:   # 5.6.106 (M4) : tentative, bail et instantané approuvé enregistrés AVANT la création distante
        cur = desk.db.execute("UPDATE outlook56103_brouillons SET etat='depot_en_cours', connexion_id=?, generation=?, compte_id=?, tentative=?, bail=?, approuve=?, "
                              "ecarts='', erreur='', version=version+1, maj=? WHERE id=? AND version=? AND etat IN ('local','incomplet')",
                              (row['id'], row['generation'], row['compte_id'], tentative, ms.HORLOGE() + BAIL_DEPOT, json.dumps(_instantane(p), ensure_ascii=False),
                               _iso(), p['id'], p['version']))
        if not cur.rowcount:
            raise Stop('version_perimee')
        desk.db.execute('INSERT INTO outlook56103_journal(idempotence, brouillon_id, action, auteur, cree, empreinte) VALUES(?,?,?,?,?,?)',
                        (str(idempotence), p['id'], 'deposer', str(owner), _iso(), empreinte))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    p = brouillon(desk, owner, p['id'])
    if not p['distant']:
        try:
            _avant_effet(desk, p['id'], tentative, row)
            out = ms.appeler(desk, row['id'], 'POST', API + '/mailFolders/drafts/messages', _corps_message(p), capacite=CAPACITE, generation=row['generation'])
        except Stop as error:
            code = str(error).split(':')[0]
            if code == 'depot_repris':
                return {'deja': False, 'resultat': brouillon(desk, owner, p['id'])['etat'], **vue(desk, owner, p['id'])}
            if _incertain(error):
                _maj_si(desk, p['id'], tentative, etat='a_verifier', erreur=code)
                return {'deja': False, 'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
            _maj_si(desk, p['id'], tentative, etat='local', erreur=code)   # refus certain (rien n'a été envoyé ou créé) : nouvel essai possible
            raise
        distant = str(out.get('id') or '')
        if not distant:
            _maj_si(desk, p['id'], tentative, etat='a_verifier', erreur='reponse_sans_identifiant')
            return {'deja': False, 'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
        if not _maj_si(desk, p['id'], tentative, distant=distant):   # repris entre-temps par une vérification : elle le retrouvera par sa marque
            return {'deja': False, 'resultat': brouillon(desk, owner, p['id'])['etat'], **vue(desk, owner, p['id'])}
        p['distant'] = distant
    return _completer(desk, owner, row, p, idempotence, tentative)


def _completer(desk, owner, row, p, idempotence, tentative):
    """Constat, pièces manquantes jointes (seulement si le brouillon distant est bien celui approuvé), nouveau constat ; « Déposé » seulement si
    tout correspond. Toutes les écritures sont conditionnelles à la tentative (bail)."""
    connues = _json_dict(p.get('pieces_distantes'))
    try:
        c = _constat(desk, row, p, connues)
        connues.update(c['pieces'])
        _maj_si(desk, p['id'], tentative, pieces_distantes=json.dumps(connues))
        if c['manquantes'] and not c['ecarts'] and c['brouillon_ok']:
            for item in c['manquantes']:
                donnees = (_dossier_pieces(desk, p['id']) / item['fichier']).read_bytes()
                if hashlib.sha256(donnees).hexdigest() != item['empreinte']:
                    raise Stop('piece_modifiee_localement')
                aid = _joindre(desk, row, p['distant'], item, donnees, lambda: _avant_effet(desk, p['id'], tentative, row))
                if aid:
                    connues[item['fichier']] = aid
                    _maj_si(desk, p['id'], tentative, pieces_distantes=json.dumps(connues))
            c = _constat(desk, row, p, connues)
            connues.update(c['pieces'])
    except Stop as error:                                    # jamais « Déposé » : à vérifier si la réponse s'est perdue, incomplet sinon
        code = str(error).split(':')[0]
        if code != 'depot_repris':
            etat = 'a_verifier' if _incertain(error) or code == 'microsoft56103_connexion_modifiee' else 'incomplet'
            _maj_si(desk, p['id'], tentative, etat=etat, erreur='connexion_modifiee' if code == 'microsoft56103_connexion_modifiee' else code)
        actuel = brouillon(desk, owner, p['id'])
        return {'deja': False, 'resultat': actuel['etat'], **vue(desk, owner, p['id'])}
    etat, erreur = _issue(c)
    champs = {'etat': etat, 'erreur': erreur, 'ecarts': json.dumps(c['ecarts'], ensure_ascii=False), 'lien': c['lien'], 'pieces_distantes': json.dumps(connues)}
    if etat == 'depose':
        champs.update(change_key=c['cle'], constate_le=_iso())
    _maj_si(desk, p['id'], tentative, **champs)
    if etat == 'depose':
        if idempotence:
            desk.db.execute("UPDATE outlook56103_journal SET resultat='depose' WHERE idempotence=?", (str(idempotence),))
            desk.db.commit()
        desk.audit('outlook56103_depose', {'brouillon': p['id'][:12]})
    elif etat == 'different':
        desk.audit('outlook56103_brouillon_different', {'brouillon': p['id'][:12], 'ecarts': len(c['ecarts'])})
    return {'deja': False, 'resultat': etat, **vue(desk, owner, p['id'])}


def _reprendre(desk, owner, row, p):
    """Vérification (geste de la personne) : retrouve le brouillon par sa marque si son identifiant est inconnu, puis constat. Ne crée rien,
    ne joint rien, ne modifie rien chez Microsoft. Écritures conditionnelles à la version lue."""
    distant = p['distant']
    if not distant:
        try:
            trouves = _retrouver(desk, row, p)
        except ms.ErreurMicrosoft:
            _maj_v(desk, p, etat='a_verifier', erreur='recherche_impossible')
            return {'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
        except Stop as error:
            if not _incertain(error):
                raise
            _maj_v(desk, p, etat='a_verifier', erreur='recherche_impossible')
            return {'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
        if len(trouves) != 1:                                # aucun résultat fiable, ou plusieurs : on reste « À vérifier »
            _maj_v(desk, p, etat='a_verifier', erreur='plusieurs_brouillons' if trouves else 'aucun_brouillon_trouve')
            return {'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
        distant = trouves[0]
    connues = _json_dict(p.get('pieces_distantes'))
    try:
        c = _constat(desk, row, {**p, 'distant': distant}, connues)
    except ms.ErreurMicrosoft as error:
        if error.statut == 404:                              # supprimé dans Outlook depuis : on repart du projet local
            _maj_v(desk, p, etat='local', distant='', erreur='brouillon_supprime_dans_outlook', pieces_distantes='{}', constate_le='', tentative='')
            return {'resultat': 'local', **vue(desk, owner, p['id'])}
        _maj_v(desk, p, etat='a_verifier', distant=distant, erreur='recherche_impossible')
        if _incertain(error):
            return {'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
        raise
    except Stop as error:
        if not _incertain(error):
            raise
        _maj_v(desk, p, etat='a_verifier', distant=distant, erreur='recherche_impossible')
        return {'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
    connues.update(c['pieces'])
    etat, erreur = _issue(c)
    champs = {'etat': etat, 'erreur': erreur, 'distant': distant, 'ecarts': json.dumps(c['ecarts'], ensure_ascii=False), 'lien': c['lien'],
              'pieces_distantes': json.dumps(connues), 'tentative': ''}
    if etat == 'depose':
        champs.update(change_key=c['cle'], constate_le=p.get('constate_le') or _iso())
    _maj_v(desk, p, **champs)
    return {'resultat': brouillon(desk, owner, p['id'])['etat'], **vue(desk, owner, p['id'])}


def verifier(desk, owner, brouillon_id):
    """Après « Dépôt à vérifier », « Dépôt incomplet », « Déposé » ou « Brouillon distant différent » : retrouve le brouillon par sa marque et
    compare le brouillon distant à l'instantané approuvé. Aucune écriture chez Microsoft."""
    _exiger_personne(desk)
    p = brouillon(desk, owner, brouillon_id)
    row = _conn(desk, owner)
    p = _expirer_bail(desk, p)
    if p['etat'] == 'depot_en_cours':
        raise Stop('depot_en_cours')
    if p['etat'] not in ('a_verifier', 'incomplet', 'depose', 'different'):
        raise Stop('etat_incompatible')
    if not _meme_compte(p, row):
        raise Stop('compte_modifie')                         # jamais de reprise sur un autre compte Microsoft
    return _reprendre(desk, owner, row, p)


def rapprocher(desk, owner, brouillon_id, version):
    """5.6.106 (audit M4) : « Je ne trouve pas ce brouillon dans Outlook : autoriser un nouveau dépôt ». Seulement après une recherche qui n'a
    rien trouvé ; une nouvelle recherche est faite d'abord (un brouillon trouvé cette fois est repris, sans nouveau dépôt)."""
    _exiger_personne(desk)
    p = brouillon(desk, owner, brouillon_id)
    row = _conn(desk, owner)
    p = _expirer_bail(desk, p)
    if p['etat'] != 'a_verifier' or p['distant'] or p['erreur'] != 'aucun_brouillon_trouve':
        raise Stop('etat_incompatible')
    if int(version) != p['version']:
        raise Stop('version_perimee')
    if not _meme_compte(p, row):
        raise Stop('compte_modifie')
    try:
        trouves = _retrouver(desk, row, p)
    except ms.ErreurMicrosoft:
        _maj_v(desk, p, etat='a_verifier', erreur='recherche_impossible')
        return {'resultat': 'a_verifier', **vue(desk, owner, p['id'])}
    if trouves:
        return _reprendre(desk, owner, row, p)
    cur = desk.db.execute("UPDATE outlook56103_brouillons SET etat='local', erreur='', tentative='', bail=0, compte_id='', connexion_id='', generation=0, "
                          "pieces_distantes='{}', constate_le='', ecarts='', version=version+1, maj=? WHERE id=? AND version=? AND etat='a_verifier' AND distant=''",
                          (_iso(), p['id'], p['version']))
    desk.db.commit()
    if not cur.rowcount:
        raise Stop('version_perimee')
    desk.audit('outlook56103_rapproche', {'brouillon': p['id'][:12]})
    return {'resultat': 'local', **vue(desk, owner, p['id'])}
