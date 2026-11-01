"""5.6.88 : second emplacement des sauvegardes : WebDAV (Nextcloud, NAS…) ou stockage compatible S3 (signature AWS v4, requêtes à chemin).

L'envoi est en flux, le fichier n'est jamais chargé en mémoire. Chaque dépôt est relu entièrement (empreinte SHA-256) avant d'être tenu pour bon.
Les identifiants vivent dans le coffre (``vault567``). HTTPS est imposé par ``common.HTTP`` (sauf boucle locale) ; aucune redirection n'est suivie.
Le dépôt quotidien est un envoi automatique : il ne part que si l'administrateur l'a autorisé pour cet emplacement (``autorise_par`` dans les réglages) ;
seule une archive déjà chiffrée quitte le serveur. La rétention distante ne touche que les noms exacts des archives d'AxiorHub.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import urllib.error
from urllib.parse import quote, unquote, urlsplit
import urllib.request
import uuid

from .common import HTTP, Stop, read_secret, release_before_network, xml_bytes
from . import sauvegarde5688 as sv
from . import vault567

DELAI = 300
TRANCHE = 256 * 1024 * 1024          # S3 : au-delà, envoi en plusieurs parties (une seule requête PUT est limitée à 5 Gio)
LECTURE = 1024 * 1024


class Liaison:
    """Transport : ``common.HTTP`` pour les petites requêtes, le même ouvreur (sans proxy ni redirection) pour les flux."""

    def __init__(self, base, utilisateur=None, secret=None):
        self.http = HTTP(base, utilisateur, secret, timeout=DELAI)

    def request(self, method, url, data=None, headers=None, limit=12_000_000):
        return self.http.request(method, url, data=data, headers=headers, limit=limit)

    def _ouvrir(self, requete):
        release_before_network(urlsplit(requete.full_url).netloc)
        p = urlsplit(requete.full_url)
        if (p.scheme, p.netloc) != self.http.origin:
            raise Stop('origine_http_refusee')
        try:
            return self.http.opener.open(requete, timeout=DELAI)
        except urllib.error.HTTPError as error:
            from .erreurs5634 import http_error
            raise http_error(error.code, requete.get_method(), requete.full_url) from None
        except TimeoutError:
            raise Stop('delai_http_depasse') from None
        except urllib.error.URLError:
            raise Stop('connexion_http_indisponible') from None
        except OSError:
            raise Stop('connexion_http_indisponible') from None

    def envoyer(self, url, flux, taille, headers=None):
        requete = urllib.request.Request(url, data=flux, method='PUT', headers={**self.http.headers, 'Content-Type': 'application/octet-stream',
                                                                                'Content-Length': str(taille), **(headers or {})})
        with self._ouvrir(requete) as reponse:
            reponse.read(4096)
            return {k.lower(): v for k, v in reponse.headers.items()}

    def empreinte(self, url, headers=None):
        """Relit le fichier distant en flux : (taille, SHA-256)."""
        requete = urllib.request.Request(url, method='GET', headers={**self.http.headers, **(headers or {})})
        sha, taille = hashlib.sha256(), 0
        with self._ouvrir(requete) as reponse:
            while True:
                try:
                    data = reponse.read(LECTURE)
                except OSError:
                    raise Stop('connexion_http_indisponible') from None
                if not data:
                    break
                sha.update(data)
                taille += len(data)
        return taille, sha.hexdigest()


class _Tranche:
    """Partie d'un fichier, lue en flux (envoi S3 en plusieurs parties)."""

    def __init__(self, flux, debut, taille):
        self.flux, self.reste = flux, taille
        flux.seek(debut)

    def read(self, n=-1):
        n = self.reste if n < 0 else min(n, self.reste)
        data = self.flux.read(n)
        self.reste -= len(data)
        return data


class WebDAV:
    genre = 'WebDAV'

    def __init__(self, cfg, identifiants, liaison=None):
        self.base = str(cfg.get('url') or '').rstrip('/')
        self.liaison = liaison or Liaison(self.base, identifiants.get('identifiant') or None, identifiants.get('secret') or '')

    def _url(self, nom):
        return self.base + '/' + quote(nom)

    def preparer(self):
        try:
            self.liaison.request('MKCOL', self.base + '/')
        except Stop as error:
            if str(error) not in ('http_405', 'http_301'):     # le dossier existe déjà
                raise

    def deposer(self, chemin, taille, sha, nom):
        """Dépôt sous un nom provisoire, relecture, puis nom définitif : un envoi coupé ne ressemble jamais à une archive."""
        self.preparer()
        provisoire = nom + '.part'
        with open(chemin, 'rb') as flux:
            self.liaison.envoyer(self._url(provisoire), flux, taille)
        try:
            if self.liaison.empreinte(self._url(provisoire)) != (taille, sha):
                raise Stop('verification_distante_echouee')
            self.liaison.request('MOVE', self._url(provisoire), headers={'Destination': self._url(nom), 'Overwrite': 'F'})
        except Stop:
            self._retirer(provisoire)
            raise

    def _retirer(self, nom):
        try:
            self.supprimer(nom)
        except Stop:
            pass

    def lister(self):
        corps = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>'
        brut = self.liaison.request('PROPFIND', self.base + '/', data=corps, headers={'Depth': '1', 'Content-Type': 'application/xml'})
        noms = []
        for noeud in xml_bytes(brut).iter('{DAV:}href'):
            nom = unquote(str(noeud.text or '').rstrip('/').rsplit('/', 1)[-1])
            if nom:
                noms.append(nom)
        return noms

    def supprimer(self, nom):
        self.liaison.request('DELETE', self._url(nom))

    def essai(self):
        """Écriture, relecture et suppression d'un petit fichier."""
        self.preparer()
        nom = 'axiorhub-essai-%s.txt' % uuid.uuid4().hex
        data = b'Essai de sauvegarde AxiorHub. Ce fichier peut etre supprime.\n'
        self.liaison.request('PUT', self._url(nom), data=data, headers={'Content-Type': 'text/plain'})
        try:
            if self.liaison.empreinte(self._url(nom))[1] != hashlib.sha256(data).hexdigest():
                raise Stop('verification_distante_echouee')
        finally:
            self._retirer(nom)


def signature_s3(methode, hote, chemin, requete, entetes, empreinte_corps, date, region, acces, secret):
    """Signature AWS v4 ; rend (en-tête Authorization). ``entetes`` : noms en minuscules, sans Authorization ; ``requete`` : paires (clé, valeur) encodées."""
    jour = date[:8]
    signes = sorted(entetes)
    canon = '\n'.join((methode, chemin, '&'.join('%s=%s' % x for x in sorted(requete)), ''.join('%s:%s\n' % (k, ' '.join(str(entetes[k]).split())) for k in signes),
                       ';'.join(signes), empreinte_corps))
    portee = '%s/%s/s3/aws4_request' % (jour, region)
    a_signer = '\n'.join(('AWS4-HMAC-SHA256', date, portee, hashlib.sha256(canon.encode()).hexdigest()))
    cle = ('AWS4' + secret).encode()
    for morceau in (jour, region, 's3', 'aws4_request'):
        cle = hmac.new(cle, morceau.encode(), hashlib.sha256).digest()
    return 'AWS4-HMAC-SHA256 Credential=%s/%s,SignedHeaders=%s,Signature=%s' % (acces, portee, ';'.join(signes), hmac.new(cle, a_signer.encode(), hashlib.sha256).hexdigest())


def _encoder(valeur):
    return quote(str(valeur), safe='-_.~')


class S3:
    genre = 'S3'
    VIDE = hashlib.sha256(b'').hexdigest()

    def __init__(self, cfg, identifiants, liaison=None):
        self.base = str(cfg.get('url') or '').rstrip('/')
        self.seau, self.region = str(cfg.get('seau') or ''), str(cfg.get('region') or 'us-east-1')
        self.prefixe = str(cfg.get('prefixe') or '').strip('/')
        self.acces, self.secret = identifiants.get('identifiant') or '', identifiants.get('secret') or ''
        self.hote = urlsplit(self.base).netloc
        self.racine = urlsplit(self.base).path.rstrip('/')
        self.liaison = liaison or Liaison(self.base)

    def _cle(self, nom):
        return (self.prefixe + '/' if self.prefixe else '') + nom

    def _preparer(self, methode, cle, requete=(), empreinte=None):
        """URL et en-têtes signés d'une requête."""
        empreinte = empreinte or self.VIDE
        chemin = self.racine + '/' + quote(self.seau, safe='') + ('/' + quote(cle, safe='/-_.~') if cle else '')
        date = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        entetes = {'host': self.hote, 'x-amz-content-sha256': empreinte, 'x-amz-date': date}
        requete = [(_encoder(k), _encoder(v)) for k, v in requete]
        autorisation = signature_s3(methode, self.hote, chemin, requete, entetes, empreinte, date, self.region, self.acces, self.secret)
        url = self.base[:len(self.base) - len(self.racine)] + chemin + (('?' + '&'.join('%s=%s' % x for x in sorted(requete))) if requete else '')
        return url, {'Host': self.hote, 'X-Amz-Content-Sha256': empreinte, 'X-Amz-Date': date, 'Authorization': autorisation}

    def _relire(self, nom):
        url, entetes = self._preparer('GET', self._cle(nom))
        return self.liaison.empreinte(url, entetes)

    def deposer(self, chemin, taille, sha, nom):
        if taille > TRANCHE:
            self._plusieurs_parties(chemin, taille, nom)
        else:
            url, entetes = self._preparer('PUT', self._cle(nom), empreinte=sha)   # le service recalcule cette empreinte et refuse un corps différent
            with open(chemin, 'rb') as flux:
                self.liaison.envoyer(url, flux, taille, entetes)
        if self._relire(nom) != (taille, sha):
            self._retirer(nom)
            raise Stop('verification_distante_echouee')

    def _plusieurs_parties(self, chemin, taille, nom):
        cle = self._cle(nom)
        url, entetes = self._preparer('POST', cle, [('uploads', '')])
        reponse = xml_bytes(self.liaison.request('POST', url, data=b'', headers=entetes))
        ident = next((n.text for n in reponse.iter() if n.tag.rsplit('}', 1)[-1] == 'UploadId' and n.text), '')
        if not ident:
            raise Stop('reponse_s3_invalide')
        parties = []
        try:
            with open(chemin, 'rb') as flux:
                for numero, debut in enumerate(range(0, taille, TRANCHE), 1):
                    longueur = min(TRANCHE, taille - debut)
                    empreinte, tranche = hashlib.sha256(), _Tranche(flux, debut, longueur)
                    while True:
                        data = tranche.read(LECTURE)
                        if not data:
                            break
                        empreinte.update(data)
                    url, entetes = self._preparer('PUT', cle, [('partNumber', numero), ('uploadId', ident)], empreinte.hexdigest())
                    retour = self.liaison.envoyer(url, _Tranche(flux, debut, longueur), longueur, entetes)
                    parties.append((numero, retour.get('etag', '')))
            corps = ('<CompleteMultipartUpload>%s</CompleteMultipartUpload>' % ''.join(
                '<Part><PartNumber>%d</PartNumber><ETag>%s</ETag></Part>' % (n, e.replace('&', '&amp;').replace('<', '&lt;')) for n, e in parties)).encode()
            url, entetes = self._preparer('POST', cle, [('uploadId', ident)], hashlib.sha256(corps).hexdigest())
            fin = self.liaison.request('POST', url, data=corps, headers={**entetes, 'Content-Type': 'application/xml'})
            if b'<Error' in fin:
                raise Stop('reponse_s3_invalide')
        except Stop:
            try:     # une partie laissée sur le service y coûterait de la place indéfiniment
                url, entetes = self._preparer('DELETE', cle, [('uploadId', ident)])
                self.liaison.request('DELETE', url, headers=entetes)
            except Stop:
                pass
            raise

    def _retirer(self, nom):
        try:
            self.supprimer(nom)
        except Stop:
            pass

    def lister(self):
        noms, jeton = [], ''
        for _ in range(50):           # 50 000 objets : au-delà, le préfixe n'est pas réservé à AxiorHub
            requete = [('list-type', '2')] + ([('prefix', self.prefixe + '/')] if self.prefixe else []) + ([('continuation-token', jeton)] if jeton else [])
            url, entetes = self._preparer('GET', '', requete)
            racine = xml_bytes(self.liaison.request('GET', url, headers=entetes))
            jeton = ''
            for noeud in racine.iter():
                etiquette = noeud.tag.rsplit('}', 1)[-1]
                if etiquette == 'Key' and noeud.text:
                    noms.append(noeud.text.rsplit('/', 1)[-1])
                elif etiquette == 'NextContinuationToken':
                    jeton = noeud.text or ''
            if not jeton:
                break
        return noms

    def supprimer(self, nom):
        url, entetes = self._preparer('DELETE', self._cle(nom))
        self.liaison.request('DELETE', url, headers=entetes)

    def essai(self):
        nom = 'axiorhub-essai-%s.txt' % uuid.uuid4().hex
        data = b'Essai de sauvegarde AxiorHub. Ce fichier peut etre supprime.\n'
        empreinte = hashlib.sha256(data).hexdigest()
        url, entetes = self._preparer('PUT', self._cle(nom), empreinte=empreinte)
        self.liaison.request('PUT', url, data=data, headers=entetes)
        try:
            if self._relire(nom)[1] != empreinte:
                raise Stop('verification_distante_echouee')
        finally:
            self._retirer(nom)


GENRES = {'webdav': WebDAV, 's3': S3}


# ------------------------------------------------------------------------------------------------ réglages
def _fichier_identifiants(desk):
    return sv._coffre(desk) / 'distant.secret'


def identifiants(desk):
    path = _fichier_identifiants(desk)
    if not path.is_file():
        return {}
    try:
        return json.loads(read_secret(path))
    except ValueError:
        raise Stop('secret_invalide') from None


def _client(desk, cfg, liaison=None):
    distant = cfg.get('distant') or {}
    if distant.get('type') not in GENRES:
        raise Stop('second_emplacement_absent')
    if not distant.get('autorise_par'):
        raise Stop('second_emplacement_non_autorise')
    return GENRES[distant['type']](distant, identifiants(desk), liaison)


def enregistrer(desk, data, owner):
    """Second emplacement. Un secret laissé vide conserve celui du coffre ; aucun identifiant n'est écrit dans les réglages."""
    cfg = sv.config(desk)
    genre = str(data.get('type') or '')
    if genre not in ('', 'webdav', 's3'):
        raise Stop('second_emplacement_invalide')
    if not genre:
        cfg['distant'] = {'type': ''}
        _fichier_identifiants(desk).unlink(missing_ok=True)
    else:
        if data.get('autorise') not in (True, 'on', '1', 'true'):
            raise Stop('second_emplacement_non_autorise')
        url = str(data.get('url') or '').strip()
        Liaison(url or 'x')            # adresse contrôlée : HTTPS obligatoire, sans identifiant ni paramètre dans l'adresse
        distant = {'type': genre, 'url': url, 'autorise_par': str(owner)[:120], 'autorise_le': desk.now()}
        if genre == 's3':
            seau = str(data.get('seau') or '').strip()
            region = str(data.get('region') or '').strip() or 'us-east-1'
            prefixe = str(data.get('prefixe') or '').strip().strip('/')
            if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', seau) or not re.fullmatch(r'[a-z0-9-]{2,40}', region) or not re.fullmatch(r'[A-Za-z0-9._/-]{0,200}', prefixe) or '..' in prefixe:
                raise Stop('second_emplacement_invalide')
            distant.update(seau=seau, region=region, prefixe=prefixe)
        ancien = identifiants(desk) if (cfg['distant'] or {}).get('type') == genre else {}
        valeurs = {'identifiant': str(data.get('identifiant') or '').strip() or ancien.get('identifiant', ''), 'secret': str(data.get('secret') or '').strip() or ancien.get('secret', '')}
        if not valeurs['identifiant'] or not valeurs['secret']:
            raise Stop('identifiants_second_emplacement_requis')
        vault567.write(_fichier_identifiants(desk), json.dumps(valeurs, ensure_ascii=False, separators=(',', ':')))
        cfg['distant'] = distant
    desk.setting(sv.CONFIG, cfg)
    desk.audit('sauvegarde5688_second_emplacement', {'par': str(owner)[:120], 'type': genre, 'hote': urlsplit(str(data.get('url') or '')).hostname or ''})
    return {'ok': True, 'message': 'Second emplacement enregistré.' if genre else 'Second emplacement retiré.'}


def tester(desk, owner, liaison=None):
    cfg = sv.config(desk)
    client = _client(desk, cfg, liaison)
    client.essai()
    desk.audit('sauvegarde5688_second_emplacement_teste', {'par': str(owner)[:120], 'type': client.genre})
    return {'ok': True, 'message': 'Second emplacement joignable : écriture, relecture et suppression réussies.'}


def retention(client, cfg, courante):
    """Noms exacts seulement, et seulement si l'archive du jour figure bien dans la liste : une liste vide ou fausse ne supprime rien."""
    noms = client.lister()
    if courante not in noms:
        return []
    garde = sv.a_conserver([n for n in noms if sv.NOM.match(n)], cfg['quotidiennes'], cfg['hebdomadaires'])
    retires = []
    for nom in noms:
        if sv.PARTIE.match(nom) or (sv.NOM.match(nom) and nom not in garde and nom != courante):
            client.supprimer(nom)
            retires.append(nom)
    return retires


def deposer(desk, cfg, chemin, taille, sha, nom, liaison=None):
    """Dépôt relu puis rétention distante. Rend l'hôte de l'emplacement (pour l'état affiché), jamais d'identifiant."""
    client = _client(desk, cfg, liaison)
    client.deposer(Path(chemin), taille, sha, nom)
    retention(client, cfg, nom)
    return client.genre + ' · ' + (urlsplit(str(cfg['distant'].get('url') or '')).hostname or '')
