"""5.6.107 (lot H) : démo en ligne — une instance isolée par visiteur, servie derrière le mandataire HTTPS du cabinet.

Demande du cabinet (16/11/2026, décidée avec l'avocat en connaissance du coût et du risque d'un serveur exposé) : une démonstration
publique (par exemple ``demo.example.test``) que l'avocat installe lui-même sur son serveur. Ce module est le **répartiteur** :

- « Commencer la démo » crée une instance neuve dans un dossier temporaire (données fictives créées par l'API du lot M,
  ``demo56107.creer``, appelée dans le processus de l'instance : ``deploy/demo/instance.py``), servie par **un processus dédié** qui
  n'écoute que sur un socket Unix de son dossier, et pose un cookie de session aléatoire (``__Host-``, ``HttpOnly``, ``Secure``,
  ``SameSite=Lax``). Le répartiteur ne garde que l'empreinte SHA-256 du jeton ; le dossier de l'instance porte un autre identifiant.
- API publique du lot M : ``creer`` et ``actif`` dans l'instance ; ici, ``jeton_de_session`` (la première page d'une instance prête
  redirige vers l'ouverture de session à usage unique, sous le préfixe ``/agent-courriel``, sans mot de passe) et ``quitter_demande``
  (« Quitter » dans le bandeau : instance arrêtée et effacée). « Recommencer » du bandeau reste dans l'instance (``reinitialiser``).
- Un cookie ne mène qu'à son instance ; un cookie inconnu, altéré ou expiré ne mène à aucune (page d'accueil ou page de fin).
- Durées et plafonds réglables (``REGLAGES``) : 2 h d'inactivité, 6 h au total, 20 instances simultanées (au-delà : « Démo complète,
  réessayez dans quelques minutes », 503 + Retry-After), créations par adresse et par heure, taille du dossier, corps des requêtes ;
  mémoire, processeur et nombre de processus bornés par instance (sous-groupe cgroup v2 délégué par systemd, à défaut limites
  ``setrlimit`` posées par l'instance, plus le plafond global du conteneur ou du service).
- Nettoyage automatique (fil ``nettoyage``) : instance expirée, processus arrêté, démarrage trop long ou dossier trop gros → processus
  arrêté et fichiers effacés. Au démarrage du service, tout dossier d'instance restant d'une exécution précédente est effacé : aucune
  donnée de visiteur ne survit à un redémarrage.
- Chemins d'administration (du serveur ou du produit : installation, comptes, mises à jour, intégrations, connecteurs, API) refusés
  avant toute recherche d'instance, par le **même 404 neutre** qu'une adresse inconnue. Le répartiteur n'expose aucune page
  d'administration : son état se lit sur le serveur (``python3 -m agent.demo_hote56107 etat``).
- En-têtes de sécurité sur toute réponse (HSTS, nosniff, X-Frame-Options, Referrer-Policy, COOP, X-Robots-Tag ; CSP stricte et
  ``Cache-Control: private, no-store`` sur les pages du répartiteur). Le cookie du répartiteur n'est jamais transmis à l'instance, et
  une instance ne peut pas le remplacer (``Set-Cookie`` de même nom retiré).
- Journal minimal : un événement par ligne (date, nature, 8 premiers caractères de l'identifiant d'instance) ; jamais d'adresse IP, de
  chemin, d'en-tête ni de saisie ; fichiers du jour effacés après ``journal_jours`` (7 par défaut). L'adresse du visiteur ne sert qu'en
  mémoire, sous forme d'empreinte salée (sel aléatoire du processus), pour la limite de créations.

Isolement réseau : il ne dépend pas de ce module. Le service systemd (``deploy/demo/axiorhub-demo.service``) tourne avec
``PrivateNetwork=yes``, ``RestrictAddressFamilies=AF_UNIX`` et ``IPAddressDeny=any`` ; la variante Docker
(``deploy/demo/compose.yml``) avec ``network_mode: none``. Le répartiteur et ses instances n'ont donc aucun accès réseau ; seul le
mandataire HTTPS du cabinet joint le répartiteur, par son socket Unix. L'instance pose en plus une garde dans son propre processus
(défense en profondeur, pas une barrière).
"""
from collections import deque
from datetime import datetime, timezone
from html import escape as e
import base64
import hashlib
import hmac
import http.client
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit

NOM_COOKIE = '__Host-axhdemo'            # préfixe __Host- : Secure, Path=/, sans Domain, imposés par le navigateur
NOM_PRE = '__Host-axhdemo-pre'           # jeton du formulaire « Commencer » (double soumission)
FORME_JETON = re.compile(r'[A-Za-z0-9_-]{43}')
RACINE = Path(__file__).resolve().parents[1]

# nom : (défaut, minimum, maximum) — tous réglables dans /etc/axiorhub-demo/demo.json ou par AXIORHUB_DEMO_<NOM>
REGLAGES = {
    'inactivite_minutes': (120, 5, 1440),
    'duree_max_minutes': (360, 10, 2880),
    'instances_max': (20, 1, 500),
    'creations_par_ip_heure': (5, 1, 1000),
    'memoire_mo': (512, 128, 16384),          # par instance
    'memoire_totale_mo': (6144, 512, 262144), # toute la démo (service ou conteneur) : protège les services du cabinet voisins
    'cpu_pourcent': (50, 5, 800),             # par instance (100 = un cœur)
    'cpu_total_pourcent': (200, 10, 6400),    # toute la démo
    'cpu_secondes': (1800, 60, 86400),
    'processus_max': (64, 8, 4096),
    'taille_max_mo': (200, 10, 20480),
    'corps_max_mo': (10, 1, 100),
    'delai_demarrage_secondes': (90, 5, 600),
    'delai_reponse_secondes': (600, 10, 3600),
    'journal_jours': (7, 1, 30),
    'nettoyage_secondes': (30, 5, 600),
    'fils_instance': (8, 2, 64),
    'fils': (0, 0, 2000),                # 0 : calculé (trois par instance, au moins 32)
    'socket_gid': (-1, -1, 2 ** 31 - 1),
}
TEXTES = {
    'origine': 'https://demo.example.test',
    'ecoute': 'unix:/run/axiorhub-demo/demo.sock',
    'dossier_instances': '/var/lib/axiorhub-demo/instances',
    'dossier_journal': '/var/lib/axiorhub-demo/journal',
    'prefixe': '/agent-courriel',            # préfixe de l'interface dans l'instance (demo56107.PREFIXE_WEB)
}
LISTES = {
    'proxy_de_confiance': ['localhost'],   # waitress donne REMOTE_ADDR = « localhost » à une connexion reçue sur socket Unix
    'chemins_refuses': [],                 # ajouts du cabinet à CHEMINS_REFUSES
    'commande': [],                        # vide : [python3, deploy/demo/instance.py]
}

# Administration du serveur et du produit, intégrations, connecteurs et API : jamais servis par la démo, quel que soit le cookie.
CHEMINS_REFUSES = (
    '/administration', '/administrateur', '/admin', '/installation', '/guide-installation', '/mise-en-service', '/init', '/setup',
    '/comptes', '/signup', '/reset-password', '/mot-de-passe-oublie', '/parametres/journal-audit', '/parametres/connexions',
    '/parametres/droits', '/mcp', '/integration', '/liaison-ia', '/oauth', '/microsoft-retour', '/reception567', '/office',
    '/navigateur', '/nextcloud-session', '/securite-serveur', '/securite-acces', '/ia-externe', '/routage-hybride',
    '/extensions', '/atelier/reglages', '/api', '/openapi', '/server-status', '/server-info', '/phpmyadmin', '/.env', '/.git',
    '/.well-known/security.txt.bak',
)
API440_REFUSEES = ('m5661/', 'm56101/securite/', 'm5689/gestion/restauration/', 'm5688cloud/', 'm56103/liaison/', 'm56103/gmail/',
                   'm56106/avocatfr/', 'm5632/admin/', 'm567/config/', 'm540/', 'm56107/demo/lancer')
SAUT = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'trailers', 'transfer-encoding',
        'upgrade', 'proxy-connection'}
ENTREES_RETIREES = SAUT | {'cookie', 'authorization', 'forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto',
                           'x-forwarded-port', 'x-real-ip', 'host', 'content-length', 'content-type'}
EVENEMENTS = {'service', 'creation', 'fin-inactivite', 'fin-duree', 'fin-visiteur', 'recommencement', 'arret-processus',
              'echec-demarrage', 'depassement-taille', 'refus-complet', 'refus-ip', 'orphelin-efface'}

STYLE = ('body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f6f7f9;color:#161616}'
         'main{max-width:42rem;margin:0 auto;padding:2rem 1rem}h1{font-size:1.6rem;margin:0 0 1rem}'
         '.carte{background:#fff;border:1px solid #c6c6c6;border-radius:6px;padding:1.25rem;margin:0 0 1rem}'
         'button{font:inherit;background:#0f62fe;color:#fff;border:0;border-radius:4px;padding:.7rem 1.2rem;cursor:pointer}'
         'button.second{background:#fff;color:#0f62fe;border:1px solid #0f62fe}'
         'button:focus-visible,a:focus-visible{outline:3px solid #ffb000;outline-offset:2px}a{color:#0043ce}'
         '.mention{font-size:.9rem;color:#393939}form{display:inline-block;margin:0 .5rem .5rem 0}'
         '@media (prefers-color-scheme:dark){body{background:#161616;color:#f4f4f4}.carte{background:#262626;border-color:#525252}'
         '.mention{color:#c6c6c6}a{color:#78a9ff}button.second{background:#262626;color:#78a9ff;border-color:#78a9ff}}')
CSP = ("default-src 'none'; style-src 'sha256-%s'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
       % base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode())


class Refus(Exception):
    """Réglage invalide : le service ne démarre pas (jamais une valeur corrigée en silence)."""


def charger_reglages(chemin=None, environ=None):
    """Réglages du répartiteur : défauts, puis fichier JSON, puis variables AXIORHUB_DEMO_<NOM>. Clé inconnue ou valeur hors bornes :
    refus (une faute de frappe ne doit pas laisser un plafond à sa valeur par défaut sans que l'avocat le sache)."""
    environ = os.environ if environ is None else environ
    r = {k: v[0] for k, v in REGLAGES.items()}
    r.update(TEXTES)
    r.update({k: list(v) for k, v in LISTES.items()})
    lu = {}
    if chemin:
        try:
            lu = json.loads(Path(chemin).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise Refus('réglages illisibles (%s) : %s' % (chemin, exc.__class__.__name__))
        if not isinstance(lu, dict):
            raise Refus('réglages : un objet JSON est attendu')
    for nom in REGLAGES.keys() | TEXTES.keys() | LISTES.keys():
        cle = 'AXIORHUB_DEMO_' + nom.upper()
        if cle in environ:
            brut = environ[cle]
            lu[nom] = [x for x in brut.split(',') if x.strip()] if nom in LISTES else brut
    inconnus = sorted(set(lu) - set(r) - {'_commentaire'})
    if inconnus:
        raise Refus('réglage inconnu : ' + ', '.join(inconnus))
    for nom, valeur in lu.items():
        if nom == '_commentaire':
            continue
        if nom in REGLAGES:
            _, mini, maxi = REGLAGES[nom]
            try:
                valeur = int(valeur)
            except (TypeError, ValueError):
                raise Refus('%s : nombre entier attendu' % nom)
            if not mini <= valeur <= maxi:
                raise Refus('%s : entre %d et %d' % (nom, mini, maxi))
        elif nom in LISTES:
            if not isinstance(valeur, list) or not all(isinstance(x, str) for x in valeur):
                raise Refus('%s : liste de textes attendue' % nom)
            valeur = [x.strip() for x in valeur]
        elif not isinstance(valeur, str) or not valeur.strip():
            raise Refus('%s : texte attendu' % nom)
        r[nom] = valeur
    origine = urlsplit(r['origine'])
    if origine.scheme != 'https' or not origine.hostname or origine.path not in ('', '/') or origine.query:
        raise Refus('origine : adresse https sans chemin attendue (par exemple https://demo.example.test)')
    r['origine'] = 'https://' + origine.netloc.lower()
    if not r['prefixe'].startswith('/') or r['prefixe'].endswith('/') or normaliser(r['prefixe']) != r['prefixe']:
        raise Refus('prefixe : chemin absolu sans barre finale attendu (/agent-courriel)')
    if not r['ecoute'].startswith('unix:/'):
        raise Refus('ecoute : socket Unix attendu (unix:/run/axiorhub-demo/demo.sock) ; le service n’a pas de réseau IP')
    if r['memoire_totale_mo'] < r['memoire_mo'] or r['cpu_total_pourcent'] < r['cpu_pourcent']:
        raise Refus('memoire_totale_mo et cpu_total_pourcent doivent couvrir au moins une instance')
    if r['duree_max_minutes'] < r['inactivite_minutes']:
        raise Refus('duree_max_minutes doit être au moins égale à inactivite_minutes')
    if not r['fils']:
        r['fils'] = max(32, 3 * r['instances_max'])
    return r


def maintenant_iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Journal:
    """Journal minimal : date, nature de l'événement, début de l'identifiant d'instance. Rien d'autre, par construction."""

    def __init__(self, dossier, jours, horloge=time.time):
        self.dossier, self.jours, self.horloge = Path(dossier), jours, horloge
        self._verrou = threading.Lock()

    def ecrire(self, evenement, ident=''):
        if evenement not in EVENEMENTS:
            raise ValueError(evenement)
        t = self.horloge()
        ligne = '%s %s %s\n' % (maintenant_iso(t), evenement, re.sub(r'[^0-9a-f]', '', ident or '')[:8] or '-')
        try:
            with self._verrou:
                self.dossier.mkdir(parents=True, exist_ok=True, mode=0o700)
                chemin = self.dossier / ('demo-%s.log' % maintenant_iso(t)[:10])
                fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, 'a', encoding='utf-8') as f:
                    f.write(ligne)
        except OSError:
            pass                              # un journal plein ou absent n'arrête pas la démo

    def purger(self):
        limite = maintenant_iso(self.horloge() - self.jours * 86400)[:10]
        try:
            for f in self.dossier.glob('demo-*.log'):
                if f.name[5:15] < limite:
                    f.unlink()
        except OSError:
            pass


class Instance:
    __slots__ = ('ident', 'empreinte', 'dossier', 'cree', 'vu', 'etat', 'poignee', 'csrf', 'session')

    def __init__(self, ident, empreinte, dossier, t):
        self.ident, self.empreinte, self.dossier = ident, empreinte, dossier
        self.cree = self.vu = t
        self.etat, self.poignee, self.csrf = 'demarrage', None, secrets.token_urlsafe(24)
        self.session = False                  # session de l'instance ouverte par jeton de lancement (une fois)


def empreinte(jeton):
    return hashlib.sha256(jeton.encode('ascii')).hexdigest()


def taille_dossier(dossier, plafond):
    """Octets occupés par le dossier (liens non suivis) ; s'arrête dès que le plafond est dépassé."""
    total = 0
    for racine, dossiers, fichiers in os.walk(dossier, followlinks=False):
        for nom in fichiers + dossiers:
            try:
                total += os.lstat(os.path.join(racine, nom)).st_size
            except OSError:
                continue
            if total > plafond:
                return total
    return total


def effacer(dossier):
    def _droits(fonction, chemin, _exc):
        try:
            os.chmod(chemin, 0o700)
            fonction(chemin)
        except OSError:
            pass
    shutil.rmtree(dossier, onerror=_droits)


def _retirer(chemin):
    if chemin.is_dir() and not chemin.is_symlink():
        effacer(chemin)
    else:
        chemin.unlink()


def normaliser(chemin):
    """Chemin reçu → chemin canonique, ou None (refus neutre) : segments « . » / « .. », barre inverse, NUL, « ; »."""
    if not chemin.startswith('/') or any(c in chemin for c in '\\\x00;') or len(chemin) > 4096:
        return None
    morceaux = [m for m in chemin.split('/') if m]
    if any(m in ('.', '..') for m in morceaux):
        return None
    return '/' + '/'.join(morceaux) + ('/' if chemin.endswith('/') and morceaux else '')


def chemin_refuse(chemin, supplementaires=(), prefixe=''):
    """Refus avec ou sans le préfixe de l'interface : l'instance sert la même page sous « /x » et « <préfixe>/x »."""
    bas = chemin.lower().rstrip('/') or '/'
    prefixe = (prefixe or '').lower().rstrip('/')
    if prefixe and (bas == prefixe or bas.startswith(prefixe + '/')):
        if chemin_refuse(bas[len(prefixe):] or '/', supplementaires):
            return True
    for p in CHEMINS_REFUSES + tuple(x.lower().rstrip('/') for x in supplementaires if x.startswith('/')):
        if bas == p or bas.startswith(p + '/'):
            return True
    if bas.startswith('/api440/'):
        api = bas[len('/api440/'):]
        return any(api.startswith(p) or api + '/' == p for p in API440_REFUSEES)
    return False


def lire_cookies(environ):
    vus = {}
    for morceau in environ.get('HTTP_COOKIE', '').split(';'):
        nom, _, valeur = morceau.strip().partition('=')
        if nom and nom not in vus:
            vus[nom] = valeur.strip()
    return vus


class Repartiteur:
    """Application WSGI du répartiteur. ``lanceur`` crée, sonde, arrête et joint les instances (``LanceurLocal`` en service, un faux
    dans les essais) ; ``horloge`` est remplaçable par les essais."""

    def __init__(self, reglages, lanceur, horloge=time.time):
        self.r, self.lanceur, self.horloge = reglages, lanceur, horloge
        self.instances = {}                   # empreinte du jeton → Instance
        self.verrou = threading.RLock()
        self.creations = {}                   # empreinte salée de l'adresse → dates des créations de la dernière heure
        self.sel = secrets.token_bytes(32)    # jamais écrit : les empreintes d'adresses meurent avec le processus
        self.racine = Path(reglages['dossier_instances'])
        self.journal = Journal(reglages['dossier_journal'], reglages['journal_jours'], horloge)
        self.refus = {'complet': deque(), 'ip': deque()}
        self._arret = threading.Event()

    # ------------------------------------------------------------------ cycle de vie
    def preparer(self):
        """Au démarrage du service : dossiers en 0700, restes d'une exécution précédente effacés."""
        self.racine.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.racine, 0o700)
        for reste in self.racine.iterdir():
            _retirer(reste)
        self.journal.ecrire('service')

    def nettoyer(self):
        t = self.horloge()
        with self.verrou:
            toutes = list(self.instances.values())
        for inst in toutes:
            raison = self._raison_fin(inst, t)
            if raison is None and inst.etat == 'demarrage' and t - inst.cree > self.r['delai_demarrage_secondes']:
                raison = 'echec-demarrage'
            if raison is None and inst.poignee is not None and not self.lanceur.vivant(inst.poignee):
                raison = 'arret-processus'
            if raison is None and inst.poignee is not None and self.lanceur.quitter_demande(inst.poignee):
                raison = 'fin-visiteur'           # « Quitter » dans le bandeau, sans nouvelle visite
            if raison is None and taille_dossier(inst.dossier, self.r['taille_max_mo'] << 20) > self.r['taille_max_mo'] << 20:
                raison = 'depassement-taille'
            if raison:
                self.detruire(inst, raison)
        with self.verrou:                     # liste prise sous le verrou : une instance s'inscrit avant de créer son dossier
            connus = {i.ident for i in self.instances.values()}
            try:
                orphelins = [x for x in self.racine.iterdir() if x.name not in connus]
            except OSError:
                orphelins = []
            for cle in [k for k, v in self.creations.items() if not v or v[-1] < t - 3600]:
                del self.creations[cle]
            for d in self.refus.values():
                while d and d[0] < t - 3600:
                    d.popleft()
        for reste in orphelins:
            try:
                _retirer(reste)
                self.journal.ecrire('orphelin-efface')
            except OSError:
                pass
        self.journal.purger()
        self._ecrire_etat(t)

    def _ecrire_etat(self, t):
        with self.verrou:
            etat = {'a': maintenant_iso(t), 'instances': len(self.instances),
                    'en_demarrage': sum(1 for i in self.instances.values() if i.etat == 'demarrage'),
                    'instances_max': self.r['instances_max'], 'refus_complet_derniere_heure': len(self.refus['complet']),
                    'refus_limite_adresse_derniere_heure': len(self.refus['ip'])}
        try:
            chemin = self.racine.parent / 'etat.json'
            temp = chemin.with_suffix('.tmp')
            temp.write_text(json.dumps(etat, ensure_ascii=False) + '\n', encoding='utf-8')
            os.replace(temp, chemin)
        except OSError:
            pass

    def boucle_nettoyage(self):
        while not self._arret.wait(self.r['nettoyage_secondes']):
            try:
                self.nettoyer()
            except Exception:                 # le nettoyage ne doit jamais s'arrêter sur une erreur ponctuelle
                pass

    def demarrer_nettoyage(self):
        fil = threading.Thread(target=self.boucle_nettoyage, name='nettoyage-demo', daemon=True)
        fil.start()
        return fil

    def arreter_tout(self):
        self._arret.set()
        with self.verrou:
            toutes = list(self.instances.values())
        for inst in toutes:
            self.detruire(inst, 'arret-processus')

    def _raison_fin(self, inst, t):
        if t - inst.cree > self.r['duree_max_minutes'] * 60:
            return 'fin-duree'
        if t - inst.vu > self.r['inactivite_minutes'] * 60:
            return 'fin-inactivite'
        return None

    def detruire(self, inst, raison):
        with self.verrou:
            if self.instances.get(inst.empreinte) is not inst:
                return False
            del self.instances[inst.empreinte]
        try:
            if inst.poignee is not None:
                self.lanceur.arreter(inst.poignee)
        finally:
            effacer(inst.dossier)
            self.journal.ecrire(raison, inst.ident)
        return True

    # ------------------------------------------------------------------ création
    def adresse(self, environ):
        """Adresse du visiteur, pour la seule limite de créations : X-Forwarded-For n'est lu que d'un mandataire de confiance."""
        distante = environ.get('REMOTE_ADDR', '')
        if distante in self.r['proxy_de_confiance']:
            relais = [x.strip() for x in environ.get('HTTP_X_FORWARDED_FOR', '').split(',') if x.strip()]
            if relais:
                return relais[-1]              # ajoutée par le mandataire du cabinet ; les valeurs précédentes viennent du client
        return distante

    def _cle_adresse(self, environ):
        return hmac.new(self.sel, self.adresse(environ).encode('utf-8', 'replace'), hashlib.sha256).hexdigest()[:32]

    def creer(self, environ):
        """Retourne (jeton, instance) ou lève Plein / LimiteAdresse / EchecCreation."""
        t = self.horloge()
        cle = self._cle_adresse(environ)
        plafond_disque = self.r['instances_max'] * (self.r['taille_max_mo'] << 20)
        disque_plein = taille_dossier(self.racine, plafond_disque) >= plafond_disque   # mesuré hors verrou
        with self.verrou:
            dates = self.creations.setdefault(cle, deque())
            while dates and dates[0] < t - 3600:
                dates.popleft()
            if len(dates) >= self.r['creations_par_ip_heure']:
                self.refus['ip'].append(t)
                self.journal.ecrire('refus-ip')
                raise LimiteAdresse(max(1, math.ceil(dates[0] + 3600 - t)))   # secondes restantes, arrondies au-dessus
            if disque_plein or len(self.instances) >= self.r['instances_max']:
                self.refus['complet'].append(t)
                self.journal.ecrire('refus-complet')
                raise Plein()
            jeton, ident = secrets.token_urlsafe(32), secrets.token_hex(8)   # le dossier ne dérive pas du jeton
            inst = Instance(ident, empreinte(jeton), self.racine / ident, t)
            self.instances[inst.empreinte] = inst  # place réservée avant tout effet : le plafond tient sous la concurrence
            dates.append(t)
        try:
            inst.dossier.mkdir(mode=0o700)
            inst.poignee = self.lanceur.creer(inst.ident, inst.dossier)
        except Exception:
            self.detruire(inst, 'echec-demarrage')
            raise EchecCreation()
        self.journal.ecrire('creation', inst.ident)
        return jeton, inst

    def _instance_de(self, environ):
        jeton = lire_cookies(environ).get(NOM_COOKIE, '')
        if not FORME_JETON.fullmatch(jeton):
            return None
        with self.verrou:
            return self.instances.get(empreinte(jeton))

    # ------------------------------------------------------------------ WSGI
    def __call__(self, environ, start):
        chemin = normaliser(environ.get('PATH_INFO', '/') or '/')
        if chemin is None or chemin_refuse(chemin, self.r['chemins_refuses'], self.r['prefixe']):
            return self._introuvable(start)     # avant toute recherche d'instance : même réponse qu'une adresse inconnue
        methode = environ.get('REQUEST_METHOD', 'GET').upper()
        if chemin == '/healthz' and methode in ('GET', 'HEAD'):
            return self._reponse(start, '200 OK', b'ok\n', 'text/plain; charset=utf-8')
        if chemin == '/robots.txt' and methode in ('GET', 'HEAD'):
            return self._reponse(start, '200 OK', b'User-agent: *\nDisallow: /\n', 'text/plain; charset=utf-8')
        if chemin.startswith('/_demo/') or chemin == '/_demo':
            return self._propre(environ, start, chemin, methode)
        cookies = lire_cookies(environ)
        inst = self._instance_de(environ)
        if inst is None:
            if NOM_COOKIE in cookies:          # jeton inconnu, altéré ou d'une démo terminée : aucune instance, cookie effacé
                return self._page(start, '200 OK', 'Démonstration terminée', self._texte_fin(), [self._cookie_efface()])
            if methode in ('GET', 'HEAD'):
                if chemin == '/':
                    return self._accueil(start, cookies)
                return self._reponse(start, '303 See Other', b'', 'text/plain', [('Location', '/')])
            return self._introuvable(start)
        t = self.horloge()
        raison = self._raison_fin(inst, t)
        if raison is None and inst.poignee is not None and self.lanceur.quitter_demande(inst.poignee):
            raison = 'fin-visiteur'               # « Quitter » dans le bandeau du mode Démo : instance arrêtée et effacée
        if raison:
            self.detruire(inst, raison)
            return self._page(start, '200 OK', 'Démonstration terminée', self._texte_fin(), [self._cookie_efface()])
        if inst.etat == 'demarrage':
            if not self.lanceur.vivant(inst.poignee):
                self.detruire(inst, 'echec-demarrage')
                return self._page(start, '503 Service Unavailable', 'Démarrage impossible',
                                  '<p>Votre démonstration n’a pas pu démarrer. <a href="/">Réessayer</a></p>', [self._cookie_efface()])
            if not self.lanceur.pret(inst.poignee):
                if methode not in ('GET', 'HEAD'):
                    return self._reponse(start, '503 Service Unavailable', b'', 'text/plain', [('Retry-After', '2')])
                return self._page(start, '200 OK', 'Préparation de votre démonstration',
                                  '<p>Votre cabinet fictif se prépare ; cette page se recharge toute seule.</p>',
                                  [], rafraichir=2)
            inst.etat = 'pret'
        inst.vu = t
        if not inst.session and methode in ('GET', 'HEAD'):
            return self._ouvrir(start, inst)        # première page : session de l'instance par jeton à usage unique
        return self._transmettre(environ, start, inst, methode, chemin)

    # ------------------------------------------------------------------ pages du répartiteur
    def _propre(self, environ, start, chemin, methode):
        if chemin == '/_demo/session' and methode in ('GET', 'HEAD'):
            inst = self._instance_de(environ)
            if inst is None or self._raison_fin(inst, self.horloge()):
                return self._reponse(start, '303 See Other', b'', 'text/plain', [('Location', '/')])
            return self._session(start, inst)
        if methode != 'POST' or chemin not in ('/_demo/commencer', '/_demo/terminer', '/_demo/recommencer', '/_demo/ouvrir'):
            return self._introuvable(start)
        if environ.get('HTTP_ORIGIN', '') != self.r['origine']:
            return self._page(start, '403 Forbidden', 'Demande refusée', '<p>Demande refusée. <a href="/">Retour à l’accueil</a></p>')
        try:
            longueur = int(environ.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = -1
        if not 0 <= longueur <= 4096:
            return self._page(start, '413 Payload Too Large', 'Demande refusée', '<p>Demande refusée.</p>')
        champs = {k: v[0] for k, v in parse_qs(environ['wsgi.input'].read(longueur).decode('utf-8', 'replace')).items()}
        inst = self._instance_de(environ)
        if chemin == '/_demo/commencer':
            pre = lire_cookies(environ).get(NOM_PRE, '')
            if not (FORME_JETON.fullmatch(pre) and hmac.compare_digest(pre, champs.get('pre', ''))):
                return self._page(start, '403 Forbidden', 'Formulaire expiré',
                                  '<p>Le formulaire a expiré. <a href="/">Recharger la page d’accueil</a></p>')
            if inst is not None and not self._raison_fin(inst, self.horloge()):
                return self._reponse(start, '303 See Other', b'', 'text/plain', [('Location', '/')])   # une seule instance par visiteur
            return self._nouvelle(environ, start)
        if inst is None or not hmac.compare_digest(inst.csrf, champs.get('csrf', '')):
            return self._page(start, '403 Forbidden', 'Demande refusée', '<p>Demande refusée. <a href="/">Retour à l’accueil</a></p>')
        if chemin == '/_demo/ouvrir':
            if inst.etat != 'pret':
                return self._reponse(start, '303 See Other', b'', 'text/plain', [('Location', '/')])
            inst.vu = self.horloge()
            return self._ouvrir(start, inst)
        if chemin == '/_demo/terminer':
            self.detruire(inst, 'fin-visiteur')
            return self._page(start, '200 OK', 'Démonstration terminée',
                              '<p>Votre démonstration est terminée : tout ce que vous y aviez saisi a été effacé.</p>'
                              '<p><a href="/">Revenir à l’accueil</a></p>', [self._cookie_efface()])
        self.detruire(inst, 'recommencement')
        return self._nouvelle(environ, start)

    def _nouvelle(self, environ, start):
        try:
            jeton, _inst = self.creer(environ)
        except Plein:
            return self._page(start, '503 Service Unavailable', 'Démo complète',
                              '<p><strong>Démo complète, réessayez dans quelques minutes.</strong></p>'
                              '<p><a href="/">Revenir à l’accueil</a></p>', [('Retry-After', '300')])
        except LimiteAdresse as exc:
            minutes = max(1, (exc.secondes + 59) // 60)
            return self._page(start, '429 Too Many Requests', 'Trop de démarrages',
                              '<p>Trop de démonstrations ont été démarrées depuis votre connexion. Réessayez dans %d minute%s.</p>'
                              % (minutes, 's' if minutes > 1 else ''), [('Retry-After', str(exc.secondes))])
        except EchecCreation:
            return self._page(start, '503 Service Unavailable', 'Démarrage impossible',
                              '<p>La démonstration n’a pas pu démarrer. <a href="/">Réessayer</a></p>', [('Retry-After', '60')])
        cookie = '%s=%s; Path=/; Max-Age=%d; Secure; HttpOnly; SameSite=Lax' % (NOM_COOKIE, jeton, self.r['duree_max_minutes'] * 60)
        return self._reponse(start, '303 See Other', b'', 'text/plain', [('Location', '/'), ('Set-Cookie', cookie)])

    def _accueil(self, start, cookies):
        pre = cookies.get(NOM_PRE, '')
        entetes = []
        if not FORME_JETON.fullmatch(pre):
            pre = secrets.token_urlsafe(32)
            entetes.append(('Set-Cookie', '%s=%s; Path=/; Max-Age=3600; Secure; HttpOnly; SameSite=Lax' % (NOM_PRE, pre)))
        corps = ('<p>Découvrez AxiorHub Pilote dans un cabinet fictif, le « Cabinet DURAND Avocats » : dossiers, courriels, '
                 'échéances et brouillons préparés par une IA de démonstration.</p>'
                 '<form method="post" action="/_demo/commencer"><input type="hidden" name="pre" value="%s">'
                 '<button type="submit">Commencer la démo</button></form>'
                 '<p class="mention"><strong>Vie privée.</strong> Données fictives uniquement ; aucun compte à créer. Ce que vous saisissez '
                 'reste dans votre instance, isolée de celles des autres visiteurs et sans accès à Internet, et est effacé à la fin de la '
                 'session (au plus tard après %s d’inactivité ou %s au total). Un cookie technique vous relie à votre instance, rien '
                 'd’autre. Votre adresse IP sert seulement, en mémoire et sans être enregistrée, à limiter le nombre de démarrages ; le '
                 'journal technique ne contient ni adresse ni saisie et est effacé après %d jours. N’y saisissez aucune donnée réelle.</p>'
                 % (e(pre, quote=True), _duree(self.r['inactivite_minutes']), _duree(self.r['duree_max_minutes']), self.r['journal_jours']))
        return self._page(start, '200 OK', 'AxiorHub Pilote — démonstration', corps, entetes)

    def _session(self, start, inst):
        t = self.horloge()
        reste = min(inst.cree + self.r['duree_max_minutes'] * 60, inst.vu + self.r['inactivite_minutes'] * 60) - t
        corps = ('<p>Votre démonstration est ouverte. Elle sera effacée dans %s au plus tard (ou après %s sans activité).</p>'
                 '<form method="post" action="/_demo/ouvrir"><input type="hidden" name="csrf" value="%s">'
                 '<button type="submit">Revenir à la démonstration</button></form>'
                 '<form method="post" action="/_demo/recommencer"><input type="hidden" name="csrf" value="%s">'
                 '<button type="submit" class="second">Recommencer à zéro</button></form>'
                 '<form method="post" action="/_demo/terminer"><input type="hidden" name="csrf" value="%s">'
                 '<button type="submit">Terminer et tout effacer</button></form>'
                 % (_duree(max(1, int(reste // 60))), _duree(self.r['inactivite_minutes']), e(inst.csrf, quote=True), e(inst.csrf, quote=True),
                    e(inst.csrf, quote=True)))
        return self._page(start, '200 OK', 'Votre démonstration', corps)

    def _ouvrir(self, start, inst):
        """Redirection vers l'adresse d'ouverture de session de l'instance (jeton à usage unique, deux minutes)."""
        try:
            lien = self.lanceur.lien_session(inst.poignee)
        except Exception:
            return self._page(start, '503 Service Unavailable', 'Instance occupée',
                              '<p>Votre démonstration ne répond pas encore ; <a href="/">réessayez</a>.</p>', [('Retry-After', '2')])
        if not (lien.startswith('/') and not lien.startswith('//')):
            return self._introuvable(start)        # jamais une redirection hors du domaine de la démo
        inst.session = True
        return self._reponse(start, '303 See Other', b'', 'text/plain', [('Location', lien)])

    def _texte_fin(self):
        return ('<p>Cette démonstration est terminée ou a expiré : tout ce qui y avait été saisi a été effacé.</p>'
                '<p><a href="/">Commencer une nouvelle démonstration</a></p>')

    def _cookie_efface(self):
        return ('Set-Cookie', '%s=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax' % NOM_COOKIE)

    def _introuvable(self, start):
        return self._page(start, '404 Not Found', 'Page introuvable', '<p>Page introuvable. <a href="/">Accueil de la démonstration</a></p>')

    def _page(self, start, statut, titre, corps, entetes=(), rafraichir=0):
        meta = '<meta http-equiv="refresh" content="%d">' % rafraichir if rafraichir else ''
        html = ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
                'initial-scale=1">%s<meta name="robots" content="noindex,nofollow"><title>%s</title><style>%s</style></head>'
                '<body><main><h1>%s</h1><div class="carte">%s</div></main></body></html>'
                % (meta, e(titre), STYLE, e(titre), corps))
        return self._reponse(start, statut, html.encode('utf-8'), 'text/html; charset=utf-8',
                             list(entetes) + [('Content-Security-Policy', CSP)])

    def _reponse(self, start, statut, corps, type_, entetes=()):
        liste = [('Content-Type', type_), ('Content-Length', str(len(corps))), ('Cache-Control', 'private, no-store')] + list(entetes)
        start(statut, securiser(liste))
        return [corps]

    # ------------------------------------------------------------------ transmission à l'instance
    def _transmettre(self, environ, start, inst, methode, chemin):
        if 'chunked' in environ.get('HTTP_TRANSFER_ENCODING', '').lower():
            return self._page(start, '411 Length Required', 'Demande refusée', '<p>Demande refusée.</p>')
        try:
            longueur = int(environ.get('CONTENT_LENGTH') or 0)
        except ValueError:
            return self._page(start, '400 Bad Request', 'Demande refusée', '<p>Demande refusée.</p>')
        if longueur < 0 or longueur > self.r['corps_max_mo'] << 20:
            return self._page(start, '413 Payload Too Large', 'Envoi trop volumineux',
                              '<p>Envoi trop volumineux pour la démonstration (%d Mo au plus).</p>' % self.r['corps_max_mo'])
        corps = environ['wsgi.input'].read(longueur) if longueur else b''
        entetes = []
        for cle, valeur in environ.items():
            if cle.startswith('HTTP_'):
                nom = cle[5:].replace('_', '-').lower()
                if nom not in ENTREES_RETIREES and not nom.startswith('x-axiorhub-'):
                    entetes.append((nom, valeur))
        cookies = [m.strip() for m in environ.get('HTTP_COOKIE', '').split(';')
                   if m.strip() and not m.strip().split('=', 1)[0].startswith(NOM_COOKIE)]
        if cookies:
            entetes.append(('cookie', '; '.join(cookies)))
        if environ.get('CONTENT_TYPE'):
            entetes.append(('content-type', environ['CONTENT_TYPE']))
        if longueur or methode in ('POST', 'PUT', 'PATCH'):
            entetes.append(('content-length', str(len(corps))))
        entetes += [('host', urlsplit(self.r['origine']).netloc), ('x-forwarded-proto', 'https')]
        cible = quote(chemin.encode('latin-1', 'replace'), safe="/:@!$&'()*+,=-._~")   # le chemin contrôlé, pas le chemin reçu
        if environ.get('QUERY_STRING'):
            cible += '?' + environ['QUERY_STRING']
        try:
            statut, retour, flux = self.lanceur.transmettre(inst.poignee, methode, cible, entetes, corps, self.r['delai_reponse_secondes'])
        except (OSError, http.client.HTTPException):
            if not self.lanceur.vivant(inst.poignee):
                self.detruire(inst, 'arret-processus')
                return self._page(start, '503 Service Unavailable', 'Démonstration interrompue',
                                  '<p>Votre démonstration s’est arrêtée ; elle a été effacée. <a href="/">Recommencer</a></p>',
                                  [self._cookie_efface()])
            return self._page(start, '502 Bad Gateway', 'Instance occupée', '<p>Votre démonstration ne répond pas ; réessayez.</p>')
        propres = []
        for nom, valeur in retour:
            bas = nom.lower()
            if bas in SAUT or bas in ('server', 'x-powered-by'):
                continue
            if bas == 'set-cookie' and valeur.split('=', 1)[0].strip().startswith(NOM_COOKIE):
                continue                       # une instance ne remplace jamais le cookie qui la désigne
            propres.append((nom, valeur))
        start(statut, securiser(propres, propre=False))
        return flux


def securiser(entetes, propre=True):
    """En-têtes de sécurité : HSTS et X-Robots-Tag toujours ; les autres seulement s'ils manquent (l'instance garde sa CSP). Pages
    du répartiteur : aucun cadre ; réponses de l'instance : cadres de la même origine (aperçus), sauf si sa CSP en décide."""
    presents = {n.lower() for n, _ in entetes}
    if not propre and any(n.lower() == 'content-security-policy' and 'frame-ancestors' in v for n, v in entetes):
        presents.add('x-frame-options')
    sortie = [(n, v) for n, v in entetes if n.lower() not in ('strict-transport-security', 'x-robots-tag')]
    sortie += [('Strict-Transport-Security', 'max-age=31536000'), ('X-Robots-Tag', 'noindex, nofollow')]
    for nom, valeur in (('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY' if propre else 'SAMEORIGIN'),
                        ('Referrer-Policy', 'no-referrer' if propre else 'same-origin'),
                        ('Cross-Origin-Opener-Policy', 'same-origin'), ('Cross-Origin-Resource-Policy', 'same-origin'),
                        ('Permissions-Policy', 'camera=(), geolocation=(), payment=(), usb=()')):
        if nom.lower() not in presents:
            sortie.append((nom, valeur))
    return sortie


def _duree(minutes):
    if minutes >= 60 and minutes % 60 == 0:
        return '%d h' % (minutes // 60)
    if minutes >= 60:
        return '%d h %02d' % (minutes // 60, minutes % 60)
    return '%d min' % minutes


class Plein(Exception):
    pass


class LimiteAdresse(Exception):
    def __init__(self, secondes):
        super().__init__(secondes)
        self.secondes = secondes


class EchecCreation(Exception):
    pass


# ---------------------------------------------------------------------- lanceur réel (Linux)
class Cgroups:
    """Sous-groupes cgroup v2 par instance, dans le groupe délégué au service (``Delegate=yes``). Indisponible (conteneur Docker,
    système sans délégation) : l'instance se borne alors par ``setrlimit`` et le plafond global du conteneur ou du service."""

    def __init__(self, base=None):
        self.base = Path(base) if base else self._detecter()

    @staticmethod
    def _detecter():
        try:
            for ligne in Path('/proc/self/cgroup').read_text().splitlines():
                if ligne.startswith('0::'):
                    base = Path('/sys/fs/cgroup' + ligne[3:].strip())
                    if os.access(base / 'cgroup.subtree_control', os.W_OK) and \
                            {'memory', 'cpu', 'pids'} <= set((base / 'cgroup.controllers').read_text().split()):
                        return base
        except OSError:
            pass
        return None

    def preparer(self):
        """Règle « pas de processus internes » : le répartiteur passe dans une feuille, puis les contrôleurs sont ouverts aux instances."""
        if self.base is None:
            return False
        try:
            feuille = self.base / 'repartiteur'
            feuille.mkdir(exist_ok=True)
            for pid in (self.base / 'cgroup.procs').read_text().split():
                try:
                    (feuille / 'cgroup.procs').write_text(pid)
                except OSError:
                    pass
            (self.base / 'cgroup.subtree_control').write_text('+memory +cpu +pids')
            return True
        except OSError:
            self.base = None
            return False

    def creer(self, ident, memoire_mo, cpu_pourcent, processus_max):
        if self.base is None:
            return None
        groupe = self.base / ('instance-' + ident)
        groupe.mkdir()
        (groupe / 'memory.max').write_text(str(memoire_mo << 20))
        try:
            (groupe / 'memory.swap.max').write_text('0')
        except OSError:
            pass
        (groupe / 'cpu.max').write_text('%d 100000' % (cpu_pourcent * 1000))
        (groupe / 'pids.max').write_text(str(processus_max))
        return str(groupe)

    @staticmethod
    def supprimer(groupe):
        if not groupe:
            return
        g = Path(groupe)
        try:
            if (g / 'cgroup.kill').exists():
                (g / 'cgroup.kill').write_text('1')
            for _ in range(50):
                if not (g / 'cgroup.procs').read_text().strip():
                    break
                time.sleep(0.05)
            g.rmdir()
        except OSError:
            pass


class _ConnexionUnix(http.client.HTTPConnection):
    def __init__(self, chemin, delai):
        super().__init__('localhost', timeout=delai)
        self._chemin = chemin

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self._chemin)
        except OSError:
            s.close()
            raise
        self.sock = s


def _flux(reponse, connexion):
    try:
        while True:
            bloc = reponse.read1(65536)
            if not bloc:
                break
            yield bloc
    finally:
        connexion.close()


class LanceurLocal:
    """Un processus par instance (``deploy/demo/instance.py``), sans réseau : il n'écoute que sur ``<dossier>/instance.sock``."""

    def __init__(self, reglages, racine=RACINE, cgroups=None):
        if not hasattr(socket, 'AF_UNIX') or os.name != 'posix':
            raise Refus('la démo hébergée exige Linux (sockets Unix)')
        self.r, self.racine = reglages, Path(racine)
        self.cgroups = cgroups if cgroups is not None else Cgroups()
        self.cgroups.preparer()

    def creer(self, ident, dossier):
        dossier = Path(dossier)
        (dossier / 'tmp').mkdir(mode=0o700, exist_ok=True)
        groupe = self.cgroups.creer(ident, self.r['memoire_mo'], self.r['cpu_pourcent'], self.r['processus_max'])
        commande = list(self.r['commande']) or [sys.executable, str(self.racine / 'deploy' / 'demo' / 'instance.py')]
        commande += ['--dossier', str(dossier), '--socket', str(dossier / 'instance.sock'), '--origine', self.r['origine'],
                     '--memoire-mo', str(self.r['memoire_mo']), '--cpu-secondes', str(self.r['cpu_secondes']),
                     '--taille-max-mo', str(self.r['taille_max_mo']), '--fils', str(self.r['fils_instance']),
                     '--corps-max-mo', str(self.r['corps_max_mo']), '--prefixe', self.r['prefixe']] + (['--cgroup', groupe] if groupe else [])
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'TZ': 'Europe/Paris', 'HOME': str(dossier),
               'TMPDIR': str(dossier / 'tmp'), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1', 'PYTHONPATH': str(self.racine),
               'AXIORHUB_DEMO': '1', 'AXIORHUB_DEMO_HOTE': '1'}
        erreurs = open(dossier / 'instance-erreurs.log', 'ab')   # effacé avec l'instance : rien ne survit à la session
        try:
            proc = subprocess.Popen(commande, cwd=str(self.racine), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=erreurs, close_fds=True, start_new_session=True)
        except OSError:
            Cgroups.supprimer(groupe)
            raise
        finally:
            erreurs.close()
        return {'proc': proc, 'socket': str(dossier / 'instance.sock'), 'cgroup': groupe,
                'config': str(dossier / 'donnees' / 'config.json')}   # disposition fixée par instance.py (demo56107.creer)

    def vivant(self, poignee):
        return poignee['proc'].poll() is None

    def lien_session(self, poignee):
        """API du lot M : jeton de lancement écrit dans le dossier de l'instance ; adresse sous le préfixe de l'interface."""
        from . import demo56107
        return self.r['prefixe'] + demo56107.jeton_de_session(poignee['config'])

    def quitter_demande(self, poignee):
        try:
            from . import demo56107
            return bool(demo56107.quitter_demande(poignee['config']))
        except Exception:
            return False

    def pret(self, poignee):
        if not os.path.exists(poignee['socket']):
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(poignee['socket'])
            return True
        except OSError:
            return False
        finally:
            s.close()

    def arreter(self, poignee):
        proc = poignee['proc']
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, 15)
            except OSError:
                proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, 9)
                except OSError:
                    proc.kill()
                proc.wait(5)
        Cgroups.supprimer(poignee.get('cgroup'))

    def transmettre(self, poignee, methode, cible, entetes, corps, delai):
        connexion = _ConnexionUnix(poignee['socket'], delai)
        try:
            connexion.putrequest(methode, cible, skip_host=True, skip_accept_encoding=True)
            for nom, valeur in entetes:
                connexion.putheader(nom, valeur)
            connexion.endheaders(corps or None)
            reponse = connexion.getresponse()
        except BaseException:
            connexion.close()
            raise
        statut = '%d %s' % (reponse.status, reponse.reason or 'OK')
        return statut, reponse.getheaders(), _flux(reponse, connexion)


# ---------------------------------------------------------------------- service
def servir(reglages):
    """Service : préparation, nettoyage périodique, écoute sur le socket Unix lu par le mandataire HTTPS du cabinet."""
    import signal
    from waitress import create_server
    repartiteur = Repartiteur(reglages, LanceurLocal(reglages))
    repartiteur.preparer()
    repartiteur.demarrer_nettoyage()
    chemin = reglages['ecoute'][len('unix:'):]
    Path(chemin).parent.mkdir(parents=True, exist_ok=True)
    serveur = create_server(repartiteur, unix_socket=chemin, unix_socket_perms='660', threads=reglages['fils'], ident='',
                            max_request_body_size=(reglages['corps_max_mo'] << 20) + 65536, send_bytes=1,
                            channel_timeout=reglages['delai_reponse_secondes'])
    if reglages['socket_gid'] >= 0:
        os.chown(chemin, -1, reglages['socket_gid'])   # le groupe du mandataire (www-data) peut joindre le socket, personne d'autre

    def _fin(*_):
        repartiteur.arreter_tout()
        serveur.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _fin)
    try:
        serveur.run()
    finally:
        repartiteur.arreter_tout()


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog='python3 -m agent.demo_hote56107', description='Démo en ligne d’AxiorHub Pilote (répartiteur).')
    p.add_argument('commande', choices=('servir', 'etat', 'reglages'))
    p.add_argument('--reglages', default=os.environ.get('AXIORHUB_DEMO_REGLAGES', '/etc/axiorhub-demo/demo.json'))
    a = p.parse_args(argv)
    try:
        r = charger_reglages(a.reglages if Path(a.reglages).exists() else None)
    except Refus as exc:
        print('Réglages refusés : %s' % exc, file=sys.stderr)
        return 2
    if a.commande == 'reglages':
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if a.commande == 'etat':
        try:
            print((Path(r['dossier_instances']).parent / 'etat.json').read_text(encoding='utf-8').strip())
            return 0
        except OSError:
            print('État indisponible : le service est-il démarré ?', file=sys.stderr)
            return 1
    servir(r)
    return 0


if __name__ == '__main__':
    sys.exit(main())
