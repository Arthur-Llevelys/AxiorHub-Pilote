"""5.6.103 (lot M) : source OneDrive / SharePoint pour la page « Fichiers » (lot F), par l'application Microsoft intégrée.

Contrat de source (méthodes publiques, chemins relatifs à la racine de la source, ``/`` pour séparateur) :

- ``lister(chemin='')`` → ``[{'nom', 'chemin', 'dossier', 'taille', 'modifie', 'version', 'id'}]`` (``version`` = eTag ; vide pour un dossier) ;
- ``lire(chemin)`` → ``{'nom', 'donnees', 'version', 'taille'}`` ; les octets viennent de l'adresse à durée limitée
  ``@microsoft.graph.downloadUrl`` (hôte contrôlé par ``cloud5688.host_allowed``, appelée **sans** jeton) ;
- ``ecrire(chemin, donnees, version_attendue=None)`` → ``{'version', 'id', 'taille'}`` : **création exclusive** si ``version_attendue`` est
  ``None`` (``@microsoft.graph.conflictBehavior=fail`` ; existant → ``fichier_existe_deja``), sinon **remplacement conditionnel**
  ``If-Match: <eTag>`` (changé entre-temps → ``version_modifiee`` : rien n'est écrasé). Envoi simple jusqu'à ``ENVOI_MAX`` ;
- ``versions(chemin)`` → ``[{'id', 'modifie', 'taille', 'auteur'}]`` (du plus récent au plus ancien, selon Microsoft) ;
- ``lire_version(chemin, version_id)`` → octets (redirection lue sans être suivie, hôte contrôlé, sans jeton) ;
- ``restaurer_version(chemin, version_id)`` : ``restoreVersion`` côté Microsoft (une nouvelle version est créée ; rien n'est effacé).

Capacités : ``fichiers`` (``Files.ReadWrite``, OneDrive de la personne) ou, pour un lecteur SharePoint (``drive_id``), ``sharepoint``
(``Files.ReadWrite.All`` + ``Sites.Read.All``, consentement d'un administrateur dans la plupart des locataires). Chaque écriture passe
``pause5629.checkpoint`` ; une écriture n'est jamais déclenchée par un modèle (l'appelant est un geste de la personne).
"""
import json
from urllib.parse import quote, urlsplit

from .common import Stop
from . import microsoft56103 as ms

SELECT = 'id,name,eTag,size,lastModifiedDateTime,file,folder,@microsoft.graph.downloadUrl'
ENVOI_MAX = 100 * 1024 * 1024        # envoi simple (Microsoft l'accepte jusqu'à 250 Mo) ; au-delà, refus clair dans ce lot
LECTURE_MAX = 250 * 1024 * 1024
TELECHARGEMENT = None                # tests : appelable (méthode, url, en-têtes, corps) -> (statut, en-têtes, octets) pour l'adresse à durée limitée


class SourceOneDrive:
    nom = 'onedrive'

    def __init__(self, desk, owner, racine='/AxiorHub', drive_id=''):
        self.desk, self.owner = desk, str(owner)
        self.drive_id = str(drive_id or '')
        self.capacite = 'sharepoint' if self.drive_id else 'fichiers'
        self.libelle = 'SharePoint' if self.drive_id else 'OneDrive'
        self.racine = '/' + '/'.join(p for p in str(racine or '').split('/') if p)
        if self.racine == '/':
            self.racine = ''
        self.route = (ms.GRAPH + '/v1.0/drives/' + quote(self.drive_id, safe='')) if self.drive_id else ms.GRAPH + '/v1.0/me/drive'

    # ----- chemins et appels
    def _chemin(self, chemin):
        parties = [p for p in str(chemin or '').replace('\\', '/').split('/') if p]
        if any(p in ('.', '..') or p.startswith('.') or any(c in p for c in '"*:<>?|') for p in parties):
            raise Stop('chemin_invalide')
        return (self.racine + '/' + '/'.join(parties)).rstrip('/') or ''

    def _element(self, chemin, suite=''):
        distant = self._chemin(chemin)
        if not distant:
            return self.route + '/root' + suite
        return self.route + '/root:' + quote(distant, safe='/') + ':' + suite     # adressage par chemin : /root:/chemin:/suite

    def _conn(self):
        return ms.connexion_active(self.desk, self.owner, self.capacite)

    def _appel(self, methode, url, donnees=None, entetes=None, ok=(200, 201, 204)):
        row = self._conn()
        statut, rep, brut = ms.requete(self.desk, row['id'], methode, url, donnees, entetes, self.capacite)
        if statut not in ok:
            if statut == 404:
                raise Stop('fichier_absent')
            if statut == 412:
                raise Stop('version_modifiee')               # If-Match refusé : le fichier a changé chez Microsoft
            if statut == 409:
                raise Stop('fichier_existe_deja')
            raise ms.ErreurMicrosoft('microsoft56103_http_%d' % statut, statut, ms._attente(rep))
        return statut, rep, brut

    def _json(self, methode, url, **kw):
        return ms._json(self._appel(methode, url, **kw)[2] or b'{}')

    def _normal(self, x, parent):
        dossier = 'folder' in x
        return {'nom': str(x.get('name') or ''), 'chemin': (parent.rstrip('/') + '/' + str(x.get('name') or '')).lstrip('/'), 'dossier': dossier,
                'taille': 0 if dossier else int(x.get('size') or 0), 'modifie': str(x.get('lastModifiedDateTime') or ''),
                'version': '' if dossier else str(x.get('eTag') or ''), 'id': str(x.get('id') or ''), '_lien': str(x.get('@microsoft.graph.downloadUrl') or '')}

    @staticmethod
    def _public(item):
        return {k: v for k, v in item.items() if not k.startswith('_')}

    # ----- contrat
    def lister(self, chemin=''):
        url = self._element(chemin, '/children?$top=200&$select=' + SELECT)
        sortie, pages = [], 0
        while url and pages < 20:
            if not url.startswith(ms.GRAPH + '/'):
                raise Stop('microsoft56103_origine_refusee')
            page = self._json('GET', url)
            sortie += [self._public(self._normal(x, chemin)) for x in page.get('value') or [] if 'file' in x or 'folder' in x]
            url = str(page.get('@odata.nextLink') or '')
            pages += 1
        return sortie

    def _meta(self, chemin):
        return self._normal(self._json('GET', self._element(chemin, '?$select=' + SELECT)), '/'.join(str(chemin).split('/')[:-1]))

    def _telecharger(self, lien, limite):
        from .cloud5688 import host_allowed
        if not host_allowed(lien):
            raise Stop('hote_transfert_refuse')
        if TELECHARGEMENT is not None:
            statut, _, brut = TELECHARGEMENT('GET', lien, {}, None)
        else:
            from .cloud5688 import CloudHTTP
            p = urlsplit(lien)
            statut, _, brut = CloudHTTP('https://' + p.netloc, timeout=60).call('GET', lien, None, {}, limite)
        if statut != 200:
            raise ms.ErreurMicrosoft('microsoft56103_http_%d' % statut, statut)
        if len(brut) > limite:
            raise Stop('reponse_trop_volumineuse')
        return brut

    def _redirection(self, url):
        """Adresse donnée par une redirection (lue, jamais suivie à l'aveugle)."""
        statut, rep, _ = self._appel('GET', url, ok=(301, 302, 303, 307))
        return {str(k).lower(): v for k, v in (rep or {}).items()}.get('location', '')

    def lire(self, chemin):
        meta = self._meta(chemin)
        if meta['dossier']:
            raise Stop('chemin_est_un_dossier')
        lien = meta['_lien'] or self._redirection(self.route + '/items/' + quote(meta['id'], safe='') + '/content')
        donnees = self._telecharger(lien, LECTURE_MAX)
        return {'nom': meta['nom'], 'donnees': donnees, 'version': meta['version'], 'taille': len(donnees)}

    def ecrire(self, chemin, donnees, version_attendue=None):
        from .pause5629 import checkpoint
        donnees = bytes(donnees or b'')
        if len(donnees) > ENVOI_MAX:
            raise Stop('fichier_trop_volumineux_onedrive')
        if not self._chemin(chemin):
            raise Stop('chemin_invalide')
        checkpoint(self.desk)
        entetes = {'Content-Type': 'application/octet-stream'}
        if version_attendue is None:
            url = self._element(chemin, '/content?@microsoft.graph.conflictBehavior=fail')
        else:
            if not str(version_attendue).strip():
                raise Stop('version_requise')
            url = self._element(chemin, '/content')
            entetes['If-Match'] = str(version_attendue)
        x = self._json('PUT', url, donnees=donnees, entetes=entetes)
        return {'version': str(x.get('eTag') or ''), 'id': str(x.get('id') or ''), 'taille': int(x.get('size') or len(donnees))}

    def versions(self, chemin):
        meta = self._meta(chemin)
        out = self._json('GET', self.route + '/items/' + quote(meta['id'], safe='') + '/versions')
        return [{'id': str(v.get('id') or ''), 'modifie': str(v.get('lastModifiedDateTime') or ''), 'taille': int(v.get('size') or 0),
                 'auteur': str((((v.get('lastModifiedBy') or {}).get('user')) or {}).get('displayName') or '')} for v in out.get('value') or []]

    def _version(self, chemin, version_id):
        if not str(version_id or '').replace('.', '').isalnum():
            raise Stop('version_absente')
        meta = self._meta(chemin)
        return self.route + '/items/' + quote(meta['id'], safe='') + '/versions/' + quote(str(version_id), safe='')

    def lire_version(self, chemin, version_id):
        return self._telecharger(self._redirection(self._version(chemin, version_id) + '/content'), LECTURE_MAX)

    def restaurer_version(self, chemin, version_id):
        from .pause5629 import checkpoint
        url = self._version(chemin, version_id) + '/restoreVersion'
        checkpoint(self.desk)
        self._appel('POST', url)
        return {'restauree': str(version_id), 'version': self._meta(chemin)['version']}

    def creer_dossier(self, chemin):
        from .pause5629 import checkpoint
        if not self._chemin(chemin):                     # contrôle du chemin (relatif à la racine de la source)
            raise Stop('chemin_invalide')
        parties = [p for p in str(chemin).replace('\\', '/').split('/') if p]
        parent = '/'.join(parties[:-1])
        checkpoint(self.desk)
        x = self._json('POST', self._element(parent, '/children'), donnees=json.dumps(
            {'name': parties[-1], 'folder': {}, '@microsoft.graph.conflictBehavior': 'fail'}).encode(), entetes={'Content-Type': 'application/json'})
        return {'id': str(x.get('id') or '')}

    def corbeille(self, chemin, version_attendue):
        """Suppression récupérable (corbeille de OneDrive / SharePoint), conditionnée par l'eTag (``If-Match``)."""
        from .pause5629 import checkpoint
        if not self._chemin(chemin) or not str(version_attendue or '').strip():
            raise Stop('version_requise')
        checkpoint(self.desk)
        self._appel('DELETE', self._element(chemin), entetes={'If-Match': str(version_attendue)})
        return {'corbeille': True}


# ------------------------------------------------------------------------------------------------ branchement à la page « Fichiers » (lot F)
RACINE_LOGIQUE = {'fichiers': '/OneDrive', 'sharepoint': '/SharePoint'}
ERREURS_F = {'fichier_absent': 'introuvable', 'chemin_invalide': 'introuvable', 'chemin_est_un_dossier': 'introuvable', 'fichier_existe_deja': 'deja_existant',
             'version_modifiee': 'version_perimee', 'reponse_trop_volumineuse': 'fichier_trop_volumineux',
             'fichier_trop_volumineux_onedrive': 'fichier_trop_volumineux', 'delai_http_depasse': 'stockage_injoignable',
             'connexion_http_indisponible': 'stockage_injoignable', 'microsoft56103_reconnexion_requise': 'stockage_injoignable',
             'microsoft56103_non_connecte': 'stockage_injoignable', 'microsoft56103_capacite_non_accordee': 'stockage_injoignable'}
_CLASSE = None


def _erreur_f(ex):
    code = str(ex).split(':')[0]
    if code.startswith('microsoft56103_http_5') or code in ('microsoft56103_http_429',):
        return Stop('stockage_injoignable')
    return Stop(ERREURS_F[code]) if code in ERREURS_F else ex


def classe_source():
    """Classe ``fichiers56103.Source`` (contrat du lot F) adossée à ``SourceOneDrive`` ; importée à la demande (aucune dépendance au chargement)."""
    global _CLASSE
    if _CLASSE is not None:
        return _CLASSE
    from .common import clean_path
    from .fichiers56103 import Source, entree

    class SourceFichiersMicrosoft(Source):
        ident, libelle = 'onedrive', 'OneDrive'

        def __init__(self, desk, owner, drive_id='', racine='/AxiorHub'):
            self.od = SourceOneDrive(desk, owner, racine=racine, drive_id=drive_id)
            self.base = RACINE_LOGIQUE[self.od.capacite]
            if drive_id:
                self.ident, self.libelle = 'sharepoint', 'SharePoint'

        def capacites(self):
            return frozenset({'ecriture', 'corbeille', 'versions'})

        def racines(self):
            return [{'chemin': self.base, 'nom': self.libelle}]

        def _rel(self, chemin):
            chemin = clean_path(str(chemin or ''))
            if chemin != self.base and not chemin.startswith(self.base + '/'):
                raise Stop('introuvable')
            return chemin[len(self.base):].strip('/')

        def _f(self, fn, *a):
            try:
                return fn(*a)
            except Stop as ex:
                raise _erreur_f(ex) from None

        def lister(self, chemin):
            rel = self._rel(chemin)
            return [entree(self.base + '/' + x['chemin'], x['dossier'], x['taille'], x['modifie'], '', x['version'])
                    for x in self._f(self.od.lister, rel)]

        def proprietes(self, chemin):
            rel = self._rel(chemin)
            m = self._f(self.od._meta, rel)
            return {**entree(clean_path(chemin), m['dossier'], m['taille'], m['modifie'], '', m['version']), 'auteur': ''}

        def lire(self, chemin, limite):
            out = self._f(self.od.lire, self._rel(chemin))
            if len(out['donnees']) > int(limite):
                raise Stop('fichier_trop_volumineux')
            return out['donnees']

        def ecrire(self, chemin, octets, etag=None):
            return self._f(self.od.ecrire, self._rel(chemin), octets, etag)['version']

        def creer_dossier(self, chemin):
            self._f(self.od.creer_dossier, self._rel(chemin))

        def corbeille(self, chemin, etag):
            self._f(self.od.corbeille, self._rel(chemin), etag)

        def versions(self, chemin):
            return [{'id': v['id'], 'date': v['modifie'], 'auteur': v['auteur'], 'taille': v['taille']} for v in self._f(self.od.versions, self._rel(chemin))]

        def lire_version(self, chemin, ident, limite):
            donnees = self._f(self.od.lire_version, self._rel(chemin), ident)
            if len(donnees) > int(limite):
                raise Stop('fichier_trop_volumineux')
            return donnees

    _CLASSE = SourceFichiersMicrosoft
    return _CLASSE


def fabrique(desk, auth, acteur):
    """Fabrique attendue par ``fichiers56103.inscrire_source`` : la source OneDrive de cette personne si son compte Microsoft (application
    intégrée) est relié avec l'autorisation « fichiers », sinon ``None`` (source fermée). Aucun appel à Microsoft ici."""
    owner = str((acteur or ('', ''))[0] or '')
    row = ms.connexion(desk, owner) if owner else None
    if not row or 'fichiers' not in ms.capacites(row):
        return None
    return classe_source()(desk, owner)


def inscrire():
    """Branchement final (intégrateur) : ``onedrive56103.inscrire()`` au chargement de la page « Fichiers »."""
    from .fichiers56103 import inscrire_source
    inscrire_source('onedrive', 'OneDrive', fabrique)
