"""5.6.97 (lot gestion, § 20) : relation client et pièces échangées — demander, recevoir et remettre les bonnes versions.

Pièces demandées. Le registre reste celui des demandes de pièces (``parcours5657`` : une demande regroupée, ses éléments). Ce module y
ajoute le destinataire **vérifié** (correspondant client du dossier, ou confirmation d'un valideur), la date de recontrôle, la réception
contrôlée (type, taille, droit d'accès) et les états par élément : À recevoir / Reçu à vérifier / Accepté / À remplacer / Dispensé.
Un fichier reçu est déposé sous un nom nouveau (création exclusive) : il n'écrase ni l'original ni une autre pièce ; le classement valide
seulement son lien avec l'élément demandé.

Remis au client. Les versions exactes choisies sont relues, contrôlées (aucune note interne, aucune correspondance confidentielle) et
**copiées** dans une copie de remise du dossier (``REMISES/…``) qui ne contient qu'elles. Le partage Nextcloud porte sur cette copie, en
**lecture seule**, jamais sur le dossier parent ; un destinataire authentifié est préféré ; un lien public n'existe que si le cabinet
l'autorise, avec mot de passe et expiration. Changer la version interne ne change pas la copie remise.
États distincts : « Lien créé » (partage actif) ≠ « Transmission déclarée » (preuve de remise) ≠ « Accès constaté ». Révoquer supprime le
partage par son identifiant puis **relit** l'état distant : « Révocation demandée » tant que Nextcloud n'a pas confirmé, « Accès révoqué »
ensuite. Une panne de Nextcloud laisse un état honnête (« à vérifier »), jamais un succès supposé.

Le partage passe par l'API OCS de partage de fichiers (``PartagesOCS``) ; le réglage est **désactivé par défaut**. Le lien et son jeton ne
sont jamais écrits au journal ; la colonne qui les garde est retirée des exports (``export5688`` : nom de nature secrète).
"""
import base64
from datetime import date, timedelta
import hashlib
import json
from pathlib import PurePosixPath
import secrets
import urllib.parse

from .common import Stop, fold
from . import gestion5697 as g

REGLAGE = 'remises5697:reglages'
DEFAUTS = {'actif': False, 'lien_public': False, 'expiration_jours': 30}
ETATS_ELEMENT = {'a_recevoir': 'À recevoir', 'recu_a_verifier': 'Reçu à vérifier', 'accepte': 'Accepté', 'a_remplacer': 'À remplacer', 'dispense': 'Dispensé'}
ETATS_REMISE = {'reservee': 'Remise en préparation', 'copie_prete': 'Copie de remise prête (aucun partage)', 'partage_actif': 'Lien créé : transmission non déclarée',
                'partage_a_verifier': 'Partage à vérifier : réponse de Nextcloud inconnue', 'transmise': 'Transmission déclarée', 'acces_constate': 'Accès constaté',
                'revocation_demandee': 'Révocation demandée : non confirmée', 'acces_revoque': 'Accès révoqué (vérifié)', 'expire': 'Partage expiré ou retiré (vérifié)',
                'echec': 'Échec : rien n’a été partagé'}
ACTIFS = ('partage_actif', 'partage_a_verifier', 'transmise', 'acces_constate', 'revocation_demandee')
CANAUX = {'nextcloud': 'Partage Nextcloud en lecture seule', 'courriel': 'Courriel', 'main_propre': 'Remise en main propre', 'autre': 'Autre', 'depot_seul': 'Espace de dépôt (réception)'}
NATURES = {'document': 'Document', 'compte_rendu': 'Compte rendu', 'bordereau': 'Bordereau', 'piece': 'Pièce'}
TYPES_ADMIS = {'.pdf', '.docx', '.doc', '.odt', '.rtf', '.txt', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.heic', '.xlsx', '.xls', '.ods', '.csv', '.eml', '.msg', '.zip'}
MAX_RECU = 20 * 1024 * 1024
DOSSIERS_INTERNES = frozenset(('notes internes', 'note interne', 'interne', 'internes', 'notes'))   # le doute retient le fichier
LECTURE, CREATION = 1, 4                        # permissions OCS : lecture seule ; dépôt seul (création, sans lecture)


def reglages(desk):
    valeur = desk.settings(REGLAGE, {}) or {}
    out = dict(DEFAUTS)
    out.update({k: valeur[k] for k in DEFAUTS if k in valeur})
    return out


def enregistrer_reglages(desk, acteur, data):
    g.droit(desk, acteur, 'reglages')
    out = reglages(desk)
    for k in ('actif', 'lien_public'):
        if k in data:
            out[k] = data[k] is True
    if 'expiration_jours' in data:
        n = int(data['expiration_jours'])
        if not 1 <= n <= 365:
            raise Stop('expiration_invalide')
        out['expiration_jours'] = n
    desk.setting(REGLAGE, out)
    return out


# ------------------------------------------------------------------------------------------------ OCS
class PartagesOCS:
    """API OCS de partage de fichiers de Nextcloud (``/ocs/v2.php/apps/files_sharing/api/v1/shares``), au format JSON. ``http`` : client
    ``common.HTTP`` du stockage (ou faux transport dans les essais). Les erreurs 404 sont rendues comme « absent », jamais comme un succès."""
    BASE = '/ocs/v2.php/apps/files_sharing/api/v1/shares'

    def __init__(self, http):
        self.http = http

    def _appel(self, methode, chemin, donnees=None):
        corps = urllib.parse.urlencode(donnees).encode() if donnees is not None else None
        entetes = {'OCS-APIRequest': 'true', 'Accept': 'application/json'}
        if corps is not None:
            entetes['Content-Type'] = 'application/x-www-form-urlencoded'
        raw = self.http.request(methode, self.http.base + self.BASE + chemin + ('&' if '?' in chemin else '?') + 'format=json', corps, entetes)
        try:
            out = json.loads(raw or b'{}')
        except ValueError:
            raise Stop('reponse_ocs_illisible') from None
        meta = (out.get('ocs') or {}).get('meta') or {}
        if int(meta.get('statuscode') or 0) == 404:
            raise Stop('http_404')
        if str(meta.get('status')) != 'ok':
            raise Stop('ocs_refuse:%s' % meta.get('statuscode'))
        return (out.get('ocs') or {}).get('data')

    def creer(self, path, type_partage, destinataire='', permissions=LECTURE, mot_de_passe='', expiration=''):
        donnees = {'path': path, 'shareType': int(type_partage), 'permissions': int(permissions)}
        if destinataire:
            donnees['shareWith'] = destinataire
        if mot_de_passe:
            donnees['password'] = mot_de_passe
        if expiration:
            donnees['expireDate'] = expiration
        return self._appel('POST', '', donnees)

    def lire(self, ident):
        """Le partage, ou None s'il n'existe plus (404)."""
        try:
            data = self._appel('GET', '/' + urllib.parse.quote(str(ident), safe=''))
        except Stop as ex:
            if str(ex) == 'http_404':
                return None
            raise
        return (data[0] if data else None) if isinstance(data, list) else data

    def chercher(self, path):
        data = self._appel('GET', '?path=' + urllib.parse.quote(path, safe='/') + '&reshares=false')
        return data if isinstance(data, list) else []

    def supprimer(self, ident):
        try:
            self._appel('DELETE', '/' + urllib.parse.quote(str(ident), safe=''))
        except Stop as ex:
            if str(ex) != 'http_404':
                raise


def ocs_client(desk):
    """Client OCS du stockage des documents. Un dossier local ou un serveur WebDAV autre que Nextcloud n'a pas d'API de partage."""
    from .exapp5642 import nextcloud_http
    cfg = desk.c.get('nextcloud_documents') or desk.c['nextcloud']
    if cfg.get('local_path') or cfg.get('webdav_url') or cfg.get('stockage_cloud') or not cfg.get('url'):
        raise Stop('partage_nextcloud_indisponible')
    return PartagesOCS(nextcloud_http(cfg['url'], cfg))


# ------------------------------------------------------------------------------------------------ pièces demandées
def _adresses_client(desk, m):
    """Adresses du client connues du serveur : correspondants « client » du registre, et parties « client » du registre unique (lot dossiers)."""
    out = {str(c.get('email') or '').strip().lower() for c in m.get('correspondents') or [] if c.get('role') == 'client' and '@' in str(c.get('email') or '')}
    try:
        if desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dossier_parties5697'").fetchone():
            out |= {str(r[0]).strip().lower() for r in desk.db.execute("SELECT courriel FROM dossier_parties5697 WHERE dossier_id=? AND role='client' AND courriel LIKE '%@%'",
                                                                        (str(m.get('id') or ''),))}
    except Exception:   # noqa: BLE001 — registre unique illisible : seule la vérification expresse reste possible
        pass
    return out


def demander(desk, acteur, mid, data):
    """Liste d'éléments, destinataire vérifié, date de recontrôle. La demande rejoint le registre existant ; rien n'est envoyé ici."""
    from . import parcours5657
    m = g.dossier(desk, acteur, mid)
    g.schema(desk)
    elements = [g.texte(x, 300) for x in (data.get('elements') or []) if g.texte(x, 300)]
    if not elements:
        raise Stop('demande_pieces_vide')
    destinataire = str(data.get('destinataire') or '').strip().lower()
    if '@' not in destinataire:
        raise Stop('destinataire_requis')
    verifie_par = 'correspondant client du dossier' if destinataire in _adresses_client(desk, m) else ''
    if not verifie_par:
        if data.get('confirmer_destinataire') is not True:
            raise Stop('destinataire_non_verifie')
        g.valideur(acteur)
        verifie_par = 'confirmé par ' + g.compte(acteur)
    revoir = g.jour(data.get('revoir_le'))
    a = g.acteur_de(acteur)
    demande = parcours5657.prepare_request(desk, a.compte, str(mid), (), '\n'.join(elements))
    with g.transaction(desk):
        desk.db.execute('UPDATE p57_requests SET recipient=?,updated=? WHERE id=?', (destinataire, g.maintenant(), demande['id']))
        desk.db.execute('INSERT INTO pieces_suivi5697(request,matter,destinataire,verifie_par,revoir_le,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(request) DO UPDATE SET '
                        'destinataire=excluded.destinataire,verifie_par=excluded.verifie_par,revoir_le=excluded.revoir_le,updated=excluded.updated',
                        (demande['id'], str(mid), destinataire, verifie_par, revoir, g.maintenant()))
        g.journal(desk, mid, 'demande_pieces', demande['id'], 'demande', acteur, '', {}, {'elements': len(elements), 'revoir_le': revoir})
    return vue_demande(desk, acteur, demande['id'])


def _etat_element(item, reception):
    if item['state'] == 'abandonnee':
        return 'dispense'
    if item['state'] == 'rapprochee':
        return 'accepte'
    if item['state'] == 'recue':
        return 'recu_a_verifier'
    if reception and reception['etat'] == 'a_remplacer':
        return 'a_remplacer'
    return 'a_recevoir'


def vue_demande(desk, acteur, rid, aujourdhui=None):
    from . import parcours5657
    g.schema(desk)
    parcours5657.ensure_schema(desk)
    row = desk.db.execute('SELECT matter FROM p57_requests WHERE id=?', (str(rid),)).fetchone()
    if not row:
        raise Stop('introuvable')
    g.dossier(desk, acteur, row['matter'])
    r = parcours5657.request(desk, rid)
    suivi = desk.db.execute('SELECT * FROM pieces_suivi5697 WHERE request=?', (r['id'],)).fetchone()
    elements = []
    for it in r['items']:
        rec = desk.db.execute('SELECT * FROM pieces_receptions5697 WHERE request=? AND item=? ORDER BY recu_le DESC LIMIT 1', (r['id'], it['item'])).fetchone()
        etat = _etat_element(it, rec)
        elements.append({'item': it['item'], 'libelle': it['label'], 'etat': etat, 'etat_libelle': ETATS_ELEMENT[etat], 'document': it['document'],
                         'reception': dict(rec) if rec else None})
    revoir = suivi['revoir_le'] if suivi else ''
    jour = (aujourdhui or date.today()).isoformat()
    attendus = [e for e in elements if e['etat'] in ('a_recevoir', 'a_remplacer')]
    return {'id': r['id'], 'matter': r['matter'], 'etat': r['state'], 'destinataire': suivi['destinataire'] if suivi else r['recipient'],
            'destinataire_verifie': suivi['verifie_par'] if suivi else '', 'revoir_le': revoir, 'a_relancer': bool(revoir and revoir <= jour and attendus),
            'elements': elements, 'a_verifier': [e for e in elements if e['etat'] == 'recu_a_verifier'], 'cree_le': r['created']}


def pieces_demandees(desk, acteur, mid, aujourdhui=None):
    from . import parcours5657
    g.dossier(desk, acteur, mid)
    parcours5657.ensure_schema(desk)
    return [vue_demande(desk, acteur, r['id'], aujourdhui) for r in desk.db.execute('SELECT id FROM p57_requests WHERE matter=? ORDER BY created DESC LIMIT 20', (str(mid),))]


def _nom_libre(client, dossier, nom):
    """Nom nouveau dans le dossier : un fichier existant n'est jamais remplacé."""
    base, ext = PurePosixPath(nom).stem[:120], PurePosixPath(nom).suffix[:10]
    for n in range(0, 50):
        candidat = '%s/%s%s%s' % (dossier, base, '' if not n else ' (%d)' % (n + 1), ext)
        try:
            client.stat(candidat)
        except Stop:
            return candidat
    raise Stop('nom_de_fichier_indisponible')


def recevoir(desk, acteur, rid, item, data, client=None):
    """Lier un fichier déposé ou un courriel à l'élément demandé. Contrôles avant classement : droit d'accès, type, taille. Reste « à vérifier »."""
    from . import acces5697, pause5629
    from .office440 import dav_client
    v = vue_demande(desk, acteur, rid)
    m = g.dossier(desk, acteur, v['matter'])
    el = next((e for e in v['elements'] if e['item'] == item), None)
    if not el:
        raise Stop('introuvable')
    if el['etat'] in ('accepte', 'dispense'):
        raise Stop('element_deja_traite')
    cle = g.cle_de(data.get('cle'))
    origine = str(data.get('origine') or 'fichier')
    if origine == 'courriel':
        ref = g.texte(data.get('ref'), 300)
        if not ref or not acces5697.peut_lire_message(desk, acteur, ref):
            raise Stop(acces5697.NEUTRE)                     # message inconnu ou non lisible : refus neutre
        params = g.empreinte([rid, item, 'courriel', ref])
        path, sha, taille, kind = '', '', 0, 'message/rfc822'
    elif origine == 'fichier':
        nom = PurePosixPath(str(data.get('nom') or '').replace('\\', '/')).name
        ext = PurePosixPath(nom).suffix.lower()
        if not nom or ext not in TYPES_ADMIS:
            raise Stop('type_fichier_refuse')
        try:
            raw = base64.b64decode(str(data.get('contenu_b64') or ''), validate=True)
        except ValueError:
            raise Stop('fichier_invalide') from None
        if not raw:
            raise Stop('fichier_requis')
        if len(raw) > MAX_RECU:
            raise Stop('fichier_trop_volumineux')
        if raw[:2] == b'MZ' or raw[:4] == b'\x7fELF':
            raise Stop('type_fichier_refuse')                # exécutable déguisé
        sha, taille, kind, ref = hashlib.sha256(raw).hexdigest(), len(raw), ext.lstrip('.'), ''
        params = g.empreinte([rid, item, 'fichier', sha])
    else:
        raise Stop('origine_invalide')
    old = desk.db.execute('SELECT * FROM pieces_receptions5697 WHERE cle=?', (cle,)).fetchone()
    if old:
        if old['parametres'] != params:
            raise Stop('cle_idempotence_deja_utilisee')
        return vue_demande(desk, acteur, rid)
    if origine == 'fichier':
        client = client or dav_client(desk)
        racine = str(m.get('path') or '').rstrip('/')
        dossier = racine + '/PIECES RECUES'
        client.ensure_folder(dossier, racine)
        path = _nom_libre(client, dossier, '%s %s' % (date.today().isoformat(), nom))
        pause5629.checkpoint(desk)
        client.put_file(path, raw)
        if hashlib.sha256(client.download(client.stat(path))).hexdigest() != sha:
            raise Stop('depot_fichier_non_verifie')
    rec = g.nouvel_id('rec')
    with g.transaction(desk):
        desk.db.execute('INSERT INTO pieces_receptions5697(id,parametres,request,item,matter,cle,origine,ref,path,sha256,taille,type,etat,recu_par,recu_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (rec, params, rid, item, v['matter'], cle, origine, ref, path, sha, taille, kind, 'a_verifier', g.compte(acteur), g.maintenant()))
        desk.db.execute("UPDATE p57_request_items SET state='recue',document=?,updated=? WHERE request=? AND item=? AND state IN ('demandee','recue')",
                        (path or ref, g.maintenant(), rid, item))
        g.journal(desk, v['matter'], 'demande_pieces', rid, 'reception', acteur, '', {}, {'item': item, 'origine': origine, 'sha256': sha})
    return vue_demande(desk, acteur, rid)


def qualifier(desk, acteur, rid, item, decision):
    """Accepter (le lien est validé), demander un remplacement (l'élément redevient attendu, le fichier reçu reste), ou dispenser."""
    from . import parcours5657
    v = vue_demande(desk, acteur, rid)
    g.droit(desk, acteur, 'valider')
    el = next((e for e in v['elements'] if e['item'] == item), None)
    if not el:
        raise Stop('introuvable')
    cible = {'accepter': 'rapprochee', 'a_remplacer': 'demandee', 'dispenser': 'abandonnee'}.get(decision)
    if not cible:
        raise Stop('decision_invalide')
    if decision == 'accepter' and el['etat'] != 'recu_a_verifier':
        raise Stop('aucune_reception_a_verifier')
    parcours5657.set_item(desk, g.compte(acteur), rid, item, cible)
    with g.transaction(desk):
        if el['reception']:
            etat = {'accepter': 'accepte', 'a_remplacer': 'a_remplacer', 'dispenser': el['reception']['etat']}[decision]
            desk.db.execute('UPDATE pieces_receptions5697 SET etat=?,decide_par=?,decide_le=? WHERE id=?', (etat, g.compte(acteur), g.maintenant(), el['reception']['id']))
        g.journal(desk, v['matter'], 'demande_pieces', rid, 'element_' + decision, acteur, '', {'etat': el['etat']}, {'item': item})
    return vue_demande(desk, acteur, rid)


# ------------------------------------------------------------------------------------------------ remises
def _remise(desk, acteur, ident):
    g.schema(desk)
    row = desk.db.execute('SELECT * FROM remises5697 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    g.dossier(desk, acteur, row['matter'])
    return row


def _destinataire(desk, acteur, m, data):
    d = data.get('destinataire') if isinstance(data.get('destinataire'), dict) else {}
    genre = str(d.get('type') or '')
    ident = str(d.get('id') or '').strip()
    if genre not in ('utilisateur', 'lien', 'courriel', 'personne'):
        raise Stop('destinataire_requis')
    if not ident:
        raise Stop('destinataire_requis')
    if genre in ('lien', 'courriel') and ident.lower() in _adresses_client(desk, m):
        return genre, ident.lower(), 'correspondant client du dossier'
    if data.get('identite_verifiee') is not True:
        raise Stop('identite_destinataire_a_verifier')     # compte Nextcloud, personne ou adresse hors du dossier : vérification expresse
    return genre, ident[:160], 'identité vérifiée par ' + g.compte(acteur)


def remettre(desk, acteur, mid, data, client=None, ocs=None):
    """Remise des versions exactes choisies. Réserver (transaction) → copier → partager (Nextcloud) → constater. Rien ne part sans cette action."""
    from . import contentieux5689, contentieux_pieces5689 as pieces, pause5629
    from .office440 import dav_client
    m = g.dossier(desk, acteur, mid)
    g.droit(desk, acteur, 'envoyer')
    g.valideur(acteur)
    g.schema(desk)
    cle = g.cle_de(data.get('cle'))
    canal = str(data.get('canal') or '')
    if canal not in CANAUX or canal == 'depot_seul':
        raise Stop('canal_invalide')
    regl = reglages(desk)
    if canal == 'nextcloud' and not regl['actif']:
        raise Stop('partage_nextcloud_desactive')
    genre, destinataire, verifie = _destinataire(desk, acteur, m, data)
    if canal == 'nextcloud' and genre == 'lien' and not regl['lien_public']:
        raise Stop('lien_public_non_autorise')
    if canal == 'nextcloud' and genre not in ('utilisateur', 'lien'):
        raise Stop('destinataire_requis')
    choix = [f for f in (data.get('fichiers') or []) if isinstance(f, dict) and f.get('path')]
    if not choix:
        raise Stop('remise_vide')
    params = g.empreinte([str(mid), canal, genre, destinataire, [(f['path'], str(f.get('version_id') or '')) for f in choix]])
    old = g.deja_fait(desk, 'remises5697', cle, params)
    if old and old['etat'] not in ('reservee', 'partage_a_verifier'):
        return vue_remise(desk, acteur, old['id'])
    client = client or dav_client(desk)
    racine = str(m.get('path') or '').rstrip('/')
    contentieux5689.schema(desk)
    if not old:
        fichiers = []
        for rang, f in enumerate(choix, 1):
            path = str(f['path'])
            if not path.startswith(racine + '/'):
                raise Stop('fichier_hors_dossier')
            if pieces.est_interne(desk, str(mid), path) or any(fold(p) in DOSSIERS_INTERNES for p in PurePosixPath(path).parts[:-1]):
                raise Stop('document_interne_non_remissible')
            info = client.stat(path)
            if f.get('version_id') and str(f['version_id']) != str(info.get('etag') or ''):
                raise Stop('version_modifiee_depuis_le_choix')
            raw = client.download(info)
            nature = str(f.get('nature') or 'document')
            fichiers.append({'rang': rang, 'path': path, 'nature': nature if nature in NATURES else 'document', 'version_id': str(info.get('etag') or ''),
                             'sha256': hashlib.sha256(raw).hexdigest(), 'taille': len(raw), 'copie': ''})
        ident = g.nouvel_id('rem')
        copie = '%s/REMISES/%s %s' % (racine, date.today().isoformat(), ident[-8:])
        expiration = ''
        if canal == 'nextcloud':
            expiration = g.jour(data.get('expiration'), False) or (date.today() + timedelta(days=int(regl['expiration_jours']))).isoformat()
        with g.transaction(desk):                      # réservation : l'objet et sa commande, avant tout effet
            desk.db.execute('INSERT INTO remises5697(id,matter,cle,parametres,destinataire,destinataire_type,canal,fichiers,copie_dossier,expiration,etat,preuve,cree_par,created,updated) '
                            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, str(mid), cle, params, destinataire, genre, canal, json.dumps(fichiers, ensure_ascii=False), copie,
                                                                    expiration, 'reservee', json.dumps({'destinataire_verifie': verifie}, ensure_ascii=False), g.compte(acteur),
                                                                    g.maintenant(), g.maintenant()))
            g.journal(desk, mid, 'remise', ident, 'reservee', acteur, '', {}, {'canal': canal, 'fichiers': [(f['path'], f['sha256']) for f in fichiers]})
        row = desk.db.execute('SELECT * FROM remises5697 WHERE id=?', (ident,)).fetchone()
    else:
        row = old
    return _executer(desk, acteur, row, client, ocs)


def _copier(desk, row, client):
    """Copie de remise : seules les versions choisies, relues et vérifiées. Une copie déjà faite (reprise) est relue, jamais refaite."""
    from . import pause5629
    racine = row['copie_dossier'].rsplit('/REMISES/', 1)[0]
    client.ensure_folder(row['copie_dossier'], racine)
    fichiers = json.loads(row['fichiers'])
    for f in fichiers:
        cible = '%s/%02d %s' % (row['copie_dossier'], f['rang'], PurePosixPath(f['path']).name)
        try:
            present = client.download(client.stat(cible))
        except Stop:
            present = None
        if present is None:
            info = client.stat(f['path'])
            raw = client.download(info)
            if hashlib.sha256(raw).hexdigest() != f['sha256']:
                raise Stop('version_modifiee_depuis_le_choix')
            pause5629.checkpoint(desk)
            client.put_file(cible, raw)
            present = client.download(client.stat(cible))
        if hashlib.sha256(present).hexdigest() != f['sha256']:
            raise Stop('copie_de_remise_non_conforme')
        f['copie'] = cible
    return fichiers


def _executer(desk, acteur, row, client, ocs):
    from . import pause5629
    try:
        fichiers = _copier(desk, row, client)
    except Stop as ex:
        with g.transaction(desk):
            desk.db.execute("UPDATE remises5697 SET etat='echec',erreur=?,updated=? WHERE id=? AND etat='reservee'", (str(ex).split(':')[0][:200], g.maintenant(), row['id']))
            g.journal(desk, row['matter'], 'remise', row['id'], 'echec', acteur, str(ex).split(':')[0])
        raise
    desk.db.execute('UPDATE remises5697 SET fichiers=?,updated=? WHERE id=?', (json.dumps(fichiers, ensure_ascii=False), g.maintenant(), row['id']))
    desk.db.commit()
    if row['canal'] != 'nextcloud':
        with g.transaction(desk):
            desk.db.execute("UPDATE remises5697 SET etat='copie_prete',updated=? WHERE id=? AND etat='reservee'", (g.maintenant(), row['id']))
            g.journal(desk, row['matter'], 'remise', row['id'], 'copie_prete', acteur)
        return vue_remise(desk, acteur, row['id'])
    ocs = ocs or ocs_client(desk)
    existant = None
    if row['etat'] == 'partage_a_verifier' or row['share_id']:
        try:                                              # reprise après une réponse perdue : relire avant tout nouvel appel
            existant = next((s for s in ocs.chercher(row['copie_dossier']) if str(s.get('path', '')).rstrip('/') == row['copie_dossier'].rstrip('/')), None)
        except Stop:
            return vue_remise(desk, acteur, row['id']) | {'message': 'Nextcloud ne répond pas : le partage n’a pas pu être relu ; rien n’est recréé.'}
    mot_de_passe = ''
    if existant is None:
        type_partage = 3 if row['destinataire_type'] == 'lien' else 0
        mot_de_passe = secrets.token_urlsafe(12) if type_partage == 3 else ''
        pause5629.checkpoint(desk)
        try:
            existant = ocs.creer(row['copie_dossier'], type_partage, '' if type_partage == 3 else row['destinataire'], LECTURE, mot_de_passe, row['expiration'])
        except Stop as ex:
            code = str(ex).split(':')[0]
            etat = 'partage_a_verifier' if code in ('delai_http_depasse', 'connexion_http_indisponible') else 'echec'
            with g.transaction(desk):
                desk.db.execute('UPDATE remises5697 SET etat=?,erreur=?,updated=? WHERE id=?', (etat, code[:200], g.maintenant(), row['id']))
                g.journal(desk, row['matter'], 'remise', row['id'], etat, acteur, code)
            return vue_remise(desk, acteur, row['id'])
    share_id = str((existant or {}).get('id') or '')
    try:
        relu = ocs.lire(share_id) if share_id else None   # constat : le partage existe, en lecture seule, sur la seule copie
    except Stop:
        relu = None
    etat = 'partage_actif' if relu and int(relu.get('permissions') or 0) == LECTURE and str(relu.get('path', '')).rstrip('/') == row['copie_dossier'].rstrip('/') else 'partage_a_verifier'
    with g.transaction(desk):
        desk.db.execute('UPDATE remises5697 SET share_id=?,etat=?,etat_distant=?,url_jeton=?,verifie_le=?,erreur=\'\',updated=? WHERE id=?',
                        (share_id, etat, 'present' if relu else 'non_relu', str((existant or {}).get('url') or '')[:500], g.maintenant(), g.maintenant(), row['id']))
        g.journal(desk, row['matter'], 'remise', row['id'], etat, acteur, '', {}, {'share_id': share_id})
    out = vue_remise(desk, acteur, row['id'])
    if mot_de_passe:
        out['mot_de_passe_a_communiquer'] = mot_de_passe     # montré une seule fois, jamais conservé : à transmettre par un autre canal
    return out


def declarer_transmission(desk, acteur, ident, data):
    """Preuve de remise (courriel envoyé, remise en main propre, bordereau signé…) : distincte de la création du lien."""
    row = _remise(desk, acteur, ident)
    g.valideur(acteur)
    g.version_attendue(row, data.get('version'))
    preuve = g.texte(data.get('preuve'), 500)
    if len(preuve) < 3:
        raise Stop('preuve_requise')
    if row['etat'] not in ('copie_prete', 'partage_actif', 'transmise'):
        raise Stop('remise_non_transmissible')
    p = json.loads(row['preuve'] or '{}')
    p['transmission'] = {'preuve': preuve, 'date': g.jour(data.get('date'), False) or date.today().isoformat(), 'par': g.compte(acteur)}
    with g.transaction(desk):
        desk.db.execute("UPDATE remises5697 SET etat='transmise',preuve=?,revision=revision+1,updated=? WHERE id=?", (json.dumps(p, ensure_ascii=False), g.maintenant(), row['id']))
        g.journal(desk, row['matter'], 'remise', row['id'], 'transmission_declaree', acteur, preuve)
    return vue_remise(desk, acteur, row['id'])


def constater_acces(desk, acteur, ident, data):
    row = _remise(desk, acteur, ident)
    g.valideur(acteur)
    g.version_attendue(row, data.get('version'))
    constat = g.texte(data.get('constat'), 500)
    if len(constat) < 3:
        raise Stop('preuve_requise')
    if row['etat'] not in ('partage_actif', 'transmise', 'copie_prete'):
        raise Stop('remise_non_transmissible')
    p = json.loads(row['preuve'] or '{}')
    p['acces'] = {'constat': constat, 'date': date.today().isoformat(), 'par': g.compte(acteur)}
    with g.transaction(desk):
        desk.db.execute("UPDATE remises5697 SET etat='acces_constate',preuve=?,revision=revision+1,updated=? WHERE id=?", (json.dumps(p, ensure_ascii=False), g.maintenant(), row['id']))
        g.journal(desk, row['matter'], 'remise', row['id'], 'acces_constate', acteur, constat)
    return vue_remise(desk, acteur, row['id'])


def revoquer(desk, acteur, ident, ocs=None):
    """Révocation par identifiant de partage, puis relecture : « demandée » tant que Nextcloud n'a pas confirmé l'absence du partage."""
    from . import pause5629
    row = _remise(desk, acteur, ident)
    g.valideur(acteur)
    if not row['share_id']:
        raise Stop('aucun_partage_a_revoquer')
    if row['etat'] in ('acces_revoque', 'expire'):
        return vue_remise(desk, acteur, row['id'])
    with g.transaction(desk):
        desk.db.execute("UPDATE remises5697 SET etat='revocation_demandee',revision=revision+1,updated=? WHERE id=?", (g.maintenant(), row['id']))
        g.journal(desk, row['matter'], 'remise', row['id'], 'revocation_demandee', acteur)
    ocs = ocs or ocs_client(desk)
    erreur = ''
    try:
        pause5629.checkpoint(desk)
        ocs.supprimer(row['share_id'])
    except Stop as ex:
        erreur = str(ex).split(':')[0]
    return verifier(desk, acteur, row['id'], ocs, erreur)


def verifier(desk, acteur, ident, ocs=None, erreur=''):
    """Relit l'état distant du partage. Absent : révoqué (si une révocation était demandée) ou expiré/retiré. Panne : état inchangé, signalé."""
    row = _remise(desk, acteur, ident)
    if not row['share_id']:
        return vue_remise(desk, acteur, row['id'])
    ocs = ocs or ocs_client(desk)
    try:
        distant = ocs.lire(row['share_id'])
    except Stop as ex:
        erreur = erreur or str(ex).split(':')[0]
        desk.db.execute("UPDATE remises5697 SET etat_distant='illisible',erreur=?,updated=? WHERE id=?", (erreur[:200], g.maintenant(), row['id']))
        desk.db.commit()
        return vue_remise(desk, acteur, row['id'])
    if distant is None:
        etat = 'acces_revoque' if row['etat'] == 'revocation_demandee' else 'expire'
        with g.transaction(desk):
            desk.db.execute("UPDATE remises5697 SET etat=?,etat_distant='absent',url_jeton='',erreur='',verifie_le=?,updated=? WHERE id=?",
                            (etat, g.maintenant(), g.maintenant(), row['id']))
            g.journal(desk, row['matter'], 'remise', row['id'], etat, acteur)
    else:
        desk.db.execute("UPDATE remises5697 SET etat_distant='present',erreur=?,verifie_le=?,updated=? WHERE id=?", (erreur[:200], g.maintenant(), g.maintenant(), row['id']))
        desk.db.commit()
    return vue_remise(desk, acteur, row['id'])


def ouvrir_depot_client(desk, acteur, mid, data, client=None, ocs=None):
    """Espace de réception en dépôt seul : dossier nouveau et vide, partagé en création seule (aucune lecture), expiration obligatoire."""
    from . import pause5629
    from .office440 import dav_client
    m = g.dossier(desk, acteur, mid)
    g.droit(desk, acteur, 'envoyer')
    g.valideur(acteur)
    g.schema(desk)
    regl = reglages(desk)
    if not regl['actif']:
        raise Stop('partage_nextcloud_desactive')
    if not regl['lien_public']:
        raise Stop('lien_public_non_autorise')
    cle = g.cle_de(data.get('cle'))
    genre, destinataire, verifie = _destinataire(desk, acteur, m, {**data, 'destinataire': {'type': 'lien', 'id': (data.get('destinataire') or {}).get('id', '')}})
    params = g.empreinte([str(mid), 'depot_seul', destinataire])
    old = g.deja_fait(desk, 'remises5697', cle, params)
    if old:
        return vue_remise(desk, acteur, old['id'])
    racine = str(m.get('path') or '').rstrip('/')
    ident = g.nouvel_id('rem')
    dossier = '%s/DEPOT CLIENT/%s %s' % (racine, date.today().isoformat(), ident[-8:])
    expiration = g.jour(data.get('expiration'), False) or (date.today() + timedelta(days=int(regl['expiration_jours']))).isoformat()
    with g.transaction(desk):
        desk.db.execute('INSERT INTO remises5697(id,matter,cle,parametres,destinataire,destinataire_type,canal,copie_dossier,expiration,etat,preuve,cree_par,created,updated) '
                        "VALUES(?,?,?,?,?,?,'depot_seul',?,?,'reservee',?,?,?,?)", (ident, str(mid), cle, params, destinataire, genre, dossier, expiration,
                                                                                   json.dumps({'destinataire_verifie': verifie}, ensure_ascii=False), g.compte(acteur), g.maintenant(), g.maintenant()))
        g.journal(desk, mid, 'remise', ident, 'depot_seul_reserve', acteur)
    client = client or dav_client(desk)
    client.ensure_folder(dossier, racine)
    ocs = ocs or ocs_client(desk)
    mot_de_passe = secrets.token_urlsafe(12)
    pause5629.checkpoint(desk)
    try:
        cree = ocs.creer(dossier, 3, '', CREATION, mot_de_passe, expiration)
        relu = ocs.lire(str(cree.get('id')))
    except Stop as ex:
        desk.db.execute("UPDATE remises5697 SET etat='partage_a_verifier',erreur=?,updated=? WHERE id=?", (str(ex).split(':')[0][:200], g.maintenant(), ident))
        desk.db.commit()
        return vue_remise(desk, acteur, ident)
    ok = relu and int(relu.get('permissions') or 0) == CREATION
    with g.transaction(desk):
        desk.db.execute('UPDATE remises5697 SET share_id=?,etat=?,etat_distant=?,url_jeton=?,verifie_le=?,updated=? WHERE id=?',
                        (str(cree.get('id')), 'partage_actif' if ok else 'partage_a_verifier', 'present' if relu else 'non_relu', str(cree.get('url') or '')[:500],
                         g.maintenant(), g.maintenant(), ident))
        g.journal(desk, mid, 'remise', ident, 'depot_seul_ouvert', acteur)
    return vue_remise(desk, acteur, ident) | {'mot_de_passe_a_communiquer': mot_de_passe}


def vue_remise(desk, acteur, ident):
    row = _remise(desk, acteur, ident)
    out = {k: row[k] for k in row.keys() if k not in ('parametres', 'cle', 'url_jeton')}
    out['fichiers'] = json.loads(row['fichiers'] or '[]')
    out['preuve'] = json.loads(row['preuve'] or '{}')
    out['etat_libelle'], out['canal_libelle'] = ETATS_REMISE.get(row['etat'], row['etat']), CANAUX.get(row['canal'], row['canal'])
    out['partage_actif'] = bool(row['share_id']) and row['etat'] in ACTIFS
    if row['url_jeton'] and g.acteur_de(acteur).role in g.VALIDEURS:
        out['lien'] = row['url_jeton']                    # pour la personne qui transmet ; jamais au journal ni dans un export
    return out


def remises(desk, acteur, mid):
    g.dossier(desk, acteur, mid)
    g.schema(desk)
    return [vue_remise(desk, acteur, r['id']) for r in desk.db.execute('SELECT id FROM remises5697 WHERE matter=? ORDER BY created DESC LIMIT 50', (str(mid),))]


def ouvrir_version_remise(desk, acteur, ident, rang, client=None):
    """La fiche ouvre la version remise (la copie), revérifiée contre l'empreinte enregistrée."""
    from .office440 import dav_client
    row = _remise(desk, acteur, ident)
    f = next((x for x in json.loads(row['fichiers'] or '[]') if int(x['rang']) == int(rang)), None)
    if not f or not f.get('copie'):
        raise Stop('introuvable')
    client = client or dav_client(desk)
    raw = client.download(client.stat(f['copie']))
    if hashlib.sha256(raw).hexdigest() != f['sha256']:
        raise Stop('copie_de_remise_alteree')
    return raw, PurePosixPath(f['path']).name

