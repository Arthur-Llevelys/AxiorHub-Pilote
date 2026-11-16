#!/usr/bin/env python3
"""AxiorHub Pilote 5.6.101 — veille de sécurité du serveur (collecteur privilégié, EN LECTURE SEULE).

Pourquoi : le 12/11/2026, un programme de minage a tourné une trentaine d'heures sous le compte du serveur web sans qu'aucun outil ne
le signale. Ce collecteur observe la machine toutes les 10 minutes et chaque nuit, et écrit un rapport que l'interface d'AxiorHub
(page « Sécurité du serveur », bandeau d'Aujourd'hui, notification du téléphone) présente en français clair, avec les commandes de
correction à COPIER. Il ne corrige rien lui-même.

Ce qu'il fait — et seulement cela :
- il LIT /proc, /etc, /var/spool/cron, /var/log, les dossiers web et la configuration d'Apache ; il ne relit ni ne stocke aucun contenu de
  fichier : seulement des chemins (dossiers de données remplacés par « [données]/… »), des droits, des noms de réglages (jamais leurs
  valeurs), des versions et des lignes de commande tronquées à 200 caractères (mots de passe masqués) ;
- il envoie, pour le contrôle S08, quelques requêtes HTTP à 127.0.0.1 (en-tête Host de chaque site Apache) et n'ouvre aucune autre
  connexion ; aucune vérification en ligne des versions (table embarquée, décision D3 du cabinet) ;
- il ÉCRIT uniquement son rapport JSON dans /var/lib/axiorhub-mail-agent/securite/ (root:axiorhub-mail, 640) et supprime ses propres
  rapports de plus de 30 jours dans ce même dossier ;
- il n'arrête, ne supprime, ne bloque et ne modifie jamais rien d'autre.

Lancement (root, par les minuteurs systemd installés avec AxiorHub sur le serveur Linux) :
  python3 veille-securite.py --rapide      processus, réseau, tâches planifiées, fichiers de démarrage (S01 à S06), toutes les 10 minutes
  python3 veille-securite.py --complet     tous les contrôles (S01 à S13), chaque nuit à 4 h 40
  python3 veille-securite.py --activer | --desactiver      minuteurs (sudo) ; « Paramètres » dépose une demande appliquée par
                                                           axiorhub-veille-securite-demande.path (--appliquer-demande)
  python3 veille-securite.py --installer-auto              installation et activation (paquet Debian, install.sh)
Essais sans root : --racine <dossier> lit un faux /proc, un faux /etc et un faux /var sous ce dossier et y écrit le rapport.
Bibliothèque standard de Python seulement.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time

VERSION = '5.6.102'
FORMAT = 1
RAPPORTS = '/var/lib/axiorhub-mail-agent/securite'
DEMANDES = '/var/lib/axiorhub-mail-agent/securite-demande'
INSTALLE = '/usr/lib/axiorhub-pilote/veille-securite.py'
GROUPE = 'axiorhub-mail'
HISTORIQUE_JOURS = 30
TIMERS = ('axiorhub-veille-securite-rapide.timer', 'axiorhub-veille-securite-complete.timer')
CHEMIN_DEMANDE = 'axiorhub-veille-securite-demande.path'
UNITES = TIMERS + ('axiorhub-veille-securite-rapide.service', 'axiorhub-veille-securite-complete.service', CHEMIN_DEMANDE,
                   'axiorhub-veille-securite-demande.service')
RAPIDES = ('S01', 'S02', 'S03', 'S04', 'S05', 'S06')
COMPLETS = RAPIDES + ('S07', 'S08', 'S09', 'S10', 'S11', 'S12', 'S13')
AIDE_DURCIR = 'sudo bash /opt/axiorhub-mail-agent/current/deploy/securite/durcir-serveur.sh'

# S01 : programmes connus pour occuper longtemps le processeur (modèle local, conversions de documents, sauvegardes, AxiorHub)
CONNUS_CPU = {'ollama', 'ollama_llama_server', 'llama-server', 'llama.cpp', 'soffice', 'soffice.bin', 'oosplash', 'libreoffice', 'unoconv',
              'tesseract', 'ocrmypdf', 'pdftoppm', 'pdftotext', 'gs', 'convert', 'magick', 'ffmpeg', 'kokoro', 'borg', 'restic', 'rsync',
              'duplicity', 'rclone', 'tar', 'gzip', 'pigz', 'xz', 'zstd', 'bzip2', 'mysqldump', 'mariadb-dump', 'pg_dump', 'clamscan',
              'freshclam', 'updatedb.plocate', 'apt', 'apt-get', 'dpkg', 'unattended-upgr'}
# S04 : noms de mineurs connus, ports de minage habituels
MINEURS = {'xmrig', 'xmr-stak', 'xmrstak', 'kinsing', 'kdevtmpfsi', 'linuxsys', 'minerd', 'cpuminer', 'cpuminer-multi', 'nbminer', 't-rex',
           'lolminer', 'phoenixminer', 'ethminer', 'srbminer', 'srbminer-multi', 'teamredminer', 'gminer', 'nanominer', 'xmr-miner', 'kworkerds',
           'sysupdate', 'networkservice', 'kthreaddk', 'dbused', 'solr.sh'}
PORTS_MINAGE = {3333, 4444, 5555, 6666, 7777, 13333, 14433, 14444, 45560, 45700}
SYSTEME = ('/usr/', '/lib/', '/lib64/', '/lib32/', '/bin/', '/sbin/', '/snap/')
TEMPORAIRES = ('/tmp/', '/dev/shm/', '/var/tmp/')
WEB = ('/var/www/', '/srv/www/', '/srv/http/')
COMPTES_WEB = ('www-data', 'apache', 'nginx', 'http')
AXIORHUB_CODE = '/opt/axiorhub-mail-agent'   # 5.6.104 : code d'AxiorHub, root:root, lecture seule pour les autres (installer.py)
COQUILLES_SANS = ('nologin', 'false', 'sync', 'halt', 'shutdown')
# 5.6.102 (lot C) : Collabora intégré à Nextcloud (application « Collabora Online – Built-in CODE Server »). Sans FUSE, l'AppImage est
# lancée avec --appimage-extract-and-run : elle se décompresse dans /tmp/appimage_extracted_<empreinte>/ et y lance coolwsd, coolforkit…
# Jamais reconnu par le seul nom : l'AppImage doit être à sa place dans un Nextcloud (fichier régulier, non effacé, hors dossier
# temporaire), et un programme décompressé n'est reconnu que si ses parents remontent jusqu'à elle (voir ``_collabora``).
COLLABORA_APPIMAGE = re.compile(r'(/(?:[^/]+/)+)(?:apps|custom_apps|apps-extra)/richdocumentscode(?:_arm64)?/collabora/Collabora_Online\.AppImage')
COLLABORA_EXTRAIT = re.compile(r'/tmp/appimage_extracted_([0-9a-f]{32})/(?:[^/]+/)*[^/]+')
COLLABORA_FUSE = re.compile(r'(?:/tmp|/dev/shm|/var/tmp)/\.mount_[^/]+/.+')     # ancien mode FUSE (exception de la 5.6.101, ancrée)
PROFONDEUR_PARENTS = 64
GROUPES_SENSIBLES = ('dovecot', 'mail', 'vmail', 'postfix', 'shadow', 'adm', 'docker', 'sudo', 'wheel', 'admin', 'lxd', 'disk', GROUPE)
TERMINAUX = ('open-terminal', 'openterminal', 'ttyd', 'wetty', 'gotty', 'shellinabox', 'webssh', 'code-server', 'butterfly', 'sshwifty')
AUTOMATISATION = ('n8n', 'node-red', 'nodered', 'huginn', 'activepieces')
# S09 : versions minimales sûres connues (table embarquée, mise à jour à chaque version d'AxiorHub ; aucune vérification en ligne)
TABLE_VERSIONS_DATE = '2026-11-14'
VERSIONS_MINIMALES = {
    'roundcube': {'nom': 'Roundcube', 'minimum': '1.7.6',
                  'source': 'notes de version officielles : https://github.com/roundcube/roundcubemail/releases'},
    'snappymail': {'nom': 'SnappyMail', 'minimum': '3.0.1',
                   'source': 'notes de version officielles : https://github.com/the-djmaze/snappymail/releases'},
    'rainloop': {'nom': 'RainLoop', 'abandonne': True,
                 'source': 'projet abandonné, dépôt archivé : https://github.com/RainLoop/rainloop-webmail'},
    # Nextcloud, WordPress, Invoice Ninja, PostfixAdmin : version minimale non connue avec certitude → version affichée, rien d'affirmé
}
NOMS_APPS = {'nextcloud': 'Nextcloud', 'wordpress': 'WordPress', 'invoiceninja': 'Invoice Ninja', 'postfixadmin': 'PostfixAdmin'}
SECRETS_NOMS = re.compile(r'(?i)pass|secret|key|token|salt|pwd|auth|connect|dsn|credential|private|cookie')
SECRETS_CMD = re.compile(r'(?i)((?:--)?(?:pass(?:word)?|passwd|pwd|secret|token|api[_-]?key|key)[=:])\S+')
URL_AUTH = re.compile(r'(://[^/\s:@]+):[^@\s/]+@')

EXPLICATIONS = {
    'S01': 'Un programme occupe le processeur presque en continu depuis plus de 30 minutes sans figurer parmi les traitements connus '
           '(modèle local, conversions, sauvegardes, AxiorHub). C’est le premier signe d’un programme de minage installé par un intrus.',
    'S02': 'Ce programme tourne alors que son fichier a été effacé du disque : c’est une technique pour échapper aux recherches. Un logiciel '
           'normal mis à jour par le système est dans /usr ; ici, le programme vient d’ailleurs.',
    'S03': 'Ce programme a été lancé depuis un dossier temporaire ou un dossier web, où n’importe quel visiteur malveillant peut déposer un '
           'fichier par une faille d’application. Aucun programme légitime du serveur ne s’y trouve.',
    'S04': 'Le nom du programme ou sa connexion correspond à un programme de minage connu : la machine travaille pour un tiers, et l’intrus '
           'qui l’a installé a pu lire d’autres données.',
    'S05': 'Un compte de service (sans interpréteur de commandes, comme le serveur web) possède une tâche planifiée personnelle : c’est la façon '
           'habituelle de relancer un programme malveillant après chaque arrêt.',
    'S05d': 'Le compte du serveur web peut créer des tâches planifiées (il n’est pas dans /etc/cron.deny) : un intrus passé par une application '
            'web peut s’installer durablement.',
    'S06': 'Un fichier lu au démarrage de la machine ou à la connexion a été ajouté ou modifié depuis le passage précédent. C’est ainsi qu’un '
           'intrus revient après un redémarrage.',
    'S06p': 'Un fichier lu au démarrage a été changé lors d’une mise à jour de paquet : en principe normal, à vérifier si aucune mise à jour '
            'n’était prévue.',
    'S07': 'Un fichier qui contient des mots de passe ou des clés peut être lu par le compte du serveur web (directement, par un groupe '
           'supplémentaire, ou parce qu’il est lisible par tous). Une faille dans n’importe quel site suffit alors à voler ces secrets.',
    'S07g': 'Le compte du serveur web est membre d’un groupe sensible : tout site piraté obtient les droits de ce groupe (par exemple lire la '
            'configuration de la messagerie).',
    'S08': 'La racine d’un site contient d’autres applications : leurs fichiers de configuration (.env, .git) peuvent être téléchargés par '
           'n’importe qui depuis Internet.',
    'S08s': 'Un fichier de secrets est réellement servi par le site : n’importe qui peut le télécharger depuis Internet. Les mots de passe '
            'qu’il contient doivent être considérés comme connus et changés.',
    'S09': 'Cette application web est plus ancienne que la version minimale sûre connue : des failles publiées permettent de prendre le '
           'contrôle du serveur ou des boîtes de messagerie.',
    'S09r': 'RainLoop n’est plus maintenu depuis des années ; ses failles ne seront jamais corrigées. Remplacez-le (par exemple par SnappyMail).',
    'S10': 'Un dossier web appartient au compte du serveur web : une application piratée peut y modifier tous les fichiers, y compris ceux '
           'exécutés par d’autres comptes.',
    'S10s': 'Un script lancé par root ou par un compte administrateur se trouve dans un dossier modifiable par le serveur web : un intrus '
            'passé par un site peut le réécrire et obtenir les droits de l’administrateur au prochain lancement.',
    'S11': 'Un port de conteneur Docker est ouvert sur toutes les interfaces : Docker contourne le pare-feu ufw, le service est donc joignable '
           'depuis Internet même si le pare-feu semble le bloquer.',
    'S11t': 'Un terminal web ou un outil d’automatisation est joignable depuis Internet : quiconque y accède peut lancer des commandes sur le '
            'serveur.',
    'S12': 'Des tentatives de connexion échouent (messagerie, SSH). C’est habituel sur Internet ; un nombre élevé ou des adresses très actives '
           'justifient un blocage automatique.',
    'S12f': 'Fail2ban, qui bloque automatiquement les adresses qui multiplient les échecs de connexion, est absent ou arrêté.',
    'S13': 'Les droits d’un fichier de configuration, de secrets ou de rapports d’AxiorHub sont trop larges : d’autres comptes de la machine '
           'pourraient les lire ou les modifier.',
}


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).replace(microsecond=0).isoformat()


def _version(v):
    return tuple(int(x) for x in re.findall(r'\d+', str(v))[:4])


# Réseaux privés (RFC 1918), partagé des opérateurs (RFC 6598, écrit en entier pour ne pas ressembler à une adresse réelle) et ULA (RFC 4193)
PRIVES = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', (0x64400000, 10), 'fc00::/7'))


def _externe(adresse):
    """Adresse « extérieure » : ni boucle locale, ni lien local, ni réseau privé (RFC 1918, CGNAT, ULA)."""
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return False
    return not any(ip.version == n.version and ip in n for n in PRIVES)


class Veille:
    """Un passage du collecteur. ``racine`` : racine du système observé (« / » en production, un faux arbre pour les essais)."""

    def __init__(self, racine='/', maintenant=None, ports=None, sonde=True, passage='rapide', precedent=None):
        self.racine = Path(racine)
        self.fausse = str(racine) not in ('/', '')
        self.t = float(maintenant if maintenant is not None else time.time())
        self.ports = dict(ports or {})
        self.sonde = sonde
        self.passage = passage
        self.precedent = precedent or {}
        self.constats, self.controles, self.erreurs, self.applications = [], {}, [], []
        self.etat = {'empreintes': {}, 'cpu': {}, 'passage': self.t}
        self._meta = {}
        if self.fausse:
            try:
                self._meta = json.loads((self.racine / '.meta.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                self._meta = {}
        self.clk = 100 if self.fausse else (os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100)
        self.comptes, self.groupes = self._comptes()
        self.donnees = self._dossiers_donnees()

    # ---------------------------------------------------------------------------------------------------------- accès au système
    def p(self, chemin):
        return Path(chemin) if not self.fausse else self.racine / str(chemin).lstrip('/')

    def meta(self, chemin):
        """(mode, uid, gid) ; dans une fausse racine, ``.meta.json`` peut fixer droits et propriétaires (essais sous Windows)."""
        m = self._meta.get(str(chemin).rstrip('/') or '/') if self.fausse else None
        try:
            st = os.lstat(self.p(chemin))
        except OSError:
            if m:
                return int(str(m.get('mode', '0644')), 8), int(m.get('uid', 0)), int(m.get('gid', 0))
            return None
        mode, uid, gid = st.st_mode, getattr(st, 'st_uid', 0), getattr(st, 'st_gid', 0)
        if m and m.get('lien'):   # 5.6.104 : lien déclaré dans une fausse racine (Windows crée mal les vrais liens)
            return stat.S_IFLNK | 0o777, int(m.get('uid', 0)), int(m.get('gid', 0))
        if m:
            kind = stat.S_IFMT(mode)
            mode = kind | int(str(m.get('mode', oct(stat.S_IMODE(mode)))), 8)
            uid, gid = int(m.get('uid', uid)), int(m.get('gid', gid))
        return mode, uid, gid

    def lien(self, chemin):
        """Cible d'un lien de /proc ; dans une fausse racine, un fichier ordinaire contient la cible (pas de liens sous Windows)."""
        p = self.p(chemin)
        try:
            return os.readlink(p)
        except OSError:
            if self.fausse and p.is_file():
                try:
                    return p.read_text(encoding='utf-8').strip()
                except OSError:
                    return None
            return None

    def lire(self, chemin, limite=1_000_000):
        try:
            with open(self.p(chemin), 'rb') as f:
                return f.read(limite).decode('utf-8', 'replace')
        except OSError:
            return None

    def lignes_fin(self, chemin, limite=20_000_000):
        """Fin d'un journal (au plus ``limite`` octets), pour borner la durée du passage."""
        try:
            with open(self.p(chemin), 'rb') as f:
                f.seek(0, os.SEEK_END)
                taille = f.tell()
                f.seek(max(0, taille - limite))
                return f.read().decode('utf-8', 'replace').splitlines()
        except OSError:
            return []

    def lister(self, chemin):
        try:
            return sorted(os.listdir(self.p(chemin)))
        except OSError:
            return []

    def est_dossier(self, chemin):
        p = self.p(chemin)
        return p.is_dir() and not p.is_symlink()

    def est_fichier(self, chemin):
        p = self.p(chemin)
        return p.is_file() and not p.is_symlink()

    def existe(self, chemin):
        return os.path.lexists(self.p(chemin))

    def parcourir(self, depart, profondeur, ignorer=()):
        """(dossier, sous-dossiers, fichiers) sans suivre les liens, jusqu'à ``profondeur`` ; dossiers de données ignorés."""
        base = self.p(depart)
        if not base.is_dir():
            return
        pile = [(str(depart).rstrip('/') or '/', 0)]
        while pile:
            courant, niveau = pile.pop()
            noms = self.lister(courant)
            dossiers, fichiers = [], []
            for n in noms:
                chemin = (courant.rstrip('/') + '/' + n)
                if self.est_dossier(chemin):
                    if n in ignorer or self.masque(chemin) != chemin or self.existe(chemin + '/.ocdata'):
                        continue
                    dossiers.append(n)
                elif self.est_fichier(chemin):
                    fichiers.append(n)
            yield courant, dossiers, fichiers
            if niveau < profondeur:
                pile.extend((courant.rstrip('/') + '/' + d, niveau + 1) for d in dossiers)

    # ---------------------------------------------------------------------------------------------------------- comptes, données
    def _comptes(self):
        comptes, groupes = {}, {}
        for ligne in (self.lire('/etc/passwd') or '').splitlines():
            x = ligne.split(':')
            if len(x) >= 7 and x[2].isdigit():
                comptes[x[0]] = {'nom': x[0], 'uid': int(x[2]), 'gid': int(x[3]) if x[3].isdigit() else -1, 'home': x[5], 'shell': x[6]}
        for ligne in (self.lire('/etc/group') or '').splitlines():
            x = ligne.split(':')
            if len(x) >= 4 and x[2].isdigit():
                groupes[x[0]] = {'nom': x[0], 'gid': int(x[2]), 'membres': [m for m in x[3].split(',') if m]}
        return comptes, groupes

    def nom_uid(self, uid):
        return next((c['nom'] for c in self.comptes.values() if c['uid'] == uid), str(uid))

    def nom_gid(self, gid):
        return next((g['nom'] for g in self.groupes.values() if g['gid'] == gid), str(gid))

    def groupes_de(self, nom):
        compte = self.comptes.get(nom)
        if not compte:
            return set(), set()
        supp = {g['gid'] for g in self.groupes.values() if nom in g['membres'] and g['gid'] != compte['gid']}
        return {compte['gid']}, supp

    def compte_web(self):
        return next((self.comptes[n] for n in COMPTES_WEB if n in self.comptes), None)

    def compte_service(self, nom):
        c = self.comptes.get(nom)
        if not c:
            return nom in COMPTES_WEB
        return nom in COMPTES_WEB or os.path.basename(c['shell']) in COQUILLES_SANS

    def administrateurs(self):
        noms = {n for n, c in self.comptes.items() if c['uid'] == 0}
        for g in ('sudo', 'admin', 'wheel'):
            noms |= set(self.groupes.get(g, {}).get('membres', []))
        return noms

    def _dossiers_donnees(self):
        """S-C1 : dossiers de données (Nextcloud, messagerie) dont les chemins sont remplacés par « [données]/… »."""
        dossiers = {'/var/mail', '/var/vmail', '/srv/vmail', '/var/spool/mail', '/home/vmail'}
        for racine in ('/var/www', '/srv'):
            for nom in self.lister(racine):
                texte = self.lire('%s/%s/config/config.php' % (racine, nom), 200_000) or ''
                trouve = re.search(r"['\"]datadirectory['\"]\s*=>\s*['\"]([^'\"]+)['\"]", texte)
                if trouve:
                    dossiers.add(trouve.group(1).rstrip('/'))
                elif self.existe('%s/%s/data/.ocdata' % (racine, nom)):
                    dossiers.add('%s/%s/data' % (racine, nom))
        return sorted(dossiers, key=len, reverse=True)

    def masque(self, texte):
        texte = str(texte)
        for d in self.donnees:
            texte = re.sub(re.escape(d) + r'(?=/|$|\s)', '[données]', texte)
        return re.sub(r'/home/[^/\s]+/Maildir', '[données]/Maildir', texte)

    def commande(self, texte, limite=200):
        texte = SECRETS_CMD.sub(lambda m: m.group(1) + '•••', URL_AUTH.sub(r'\1:•••@', ' '.join(str(texte).split())))
        texte = self.masque(texte)
        return texte if len(texte) <= limite else texte[:limite - 1] + '…'

    def meta_reelle(self, chemin):
        """(mode, uid, gid, cible) du fichier réellement atteint : les liens sont suivis (huit au plus) ; ``cible`` vaut None sans lien."""
        courant, cible = str(chemin), None
        for _ in range(8):
            m = self.meta(courant)
            if not m or not stat.S_ISLNK(m[0]):
                return (m + (cible,)) if m else None
            suivant = self.cible_lien(courant)
            if not suivant:
                return None
            courant = cible = suivant
        return None

    def cible_lien(self, chemin):
        """Cible absolue d'un lien symbolique (None sinon) ; une cible relative est rattachée au dossier du lien."""
        m = self._meta.get(str(chemin).rstrip('/') or '/') if self.fausse else None
        if m and m.get('lien'):
            cible = str(m['lien'])
        else:
            try:
                cible = os.readlink(self.p(chemin))
            except OSError:
                return None
        if not cible.startswith('/'):
            cible = os.path.normpath(os.path.join(os.path.dirname(str(chemin)), cible)).replace(os.sep, '/')
        return cible

    def ecrivable_web(self, chemin):
        """Le compte web peut-il écrire ce fichier ou dossier ? (propriétaire, groupes, autres ; dossiers à bit collant exclus).

        5.6.104 : les droits d'un lien symbolique (toujours rwxrwxrwx) sont sans effet sous Linux : un lien n'est jamais « modifiable »
        par eux ; le remplacer dépend de son dossier, et sa cible est contrôlée par ``_ancetres_ecrivables``."""
        web = self.compte_web()
        if not web:
            return False
        m = self.meta(chemin)
        if not m:
            return False
        mode, uid, gid = m
        if stat.S_ISLNK(mode):
            return False
        principal, supp = self.groupes_de(web['nom'])
        if uid == web['uid'] and mode & stat.S_IWUSR:
            return True
        if gid in principal | supp and mode & stat.S_IWGRP:
            return True
        return bool(mode & stat.S_IWOTH) and not (stat.S_ISDIR(mode) and mode & stat.S_ISVTX)

    # ---------------------------------------------------------------------------------------------------------- constats
    def ajouter(self, ident, sujet, niveau, titre, explication, vu, commandes, signature):
        empreinte = hashlib.sha256(json.dumps([ident, sujet, signature], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.constats.append({'id': ident, 'cle': '%s:%s' % (ident, self.masque(sujet)[:200]), 'niveau': niveau, 'titre': titre[:200],
                              'explication': explication, 'vu': [self.masque(v)[:400] for v in vu][:40],
                              'commandes': [c[:600] for c in commandes][:12], 'aide': ident, 'empreinte': empreinte})

    def executer(self, controles):
        for ident in controles:
            fonction = getattr(self, 'controle_' + ident.lower())
            avant = len(self.constats)
            try:
                fonction()
                self.controles[ident] = 'constat' if len(self.constats) > avant else 'ok'
            except Exception as ex:   # noqa: BLE001 — un contrôle en panne n'empêche pas les autres ; il est dit tel
                del self.constats[avant:]
                self.controles[ident] = 'erreur'
                self.erreurs.append('%s : %s' % (ident, type(ex).__name__))
        if 'S01' in controles:
            self._combiner_s01()

    # ---------------------------------------------------------------------------------------------------------- processus et réseau
    def processus(self):
        if hasattr(self, '_processus'):
            return self._processus
        out = []
        for nom in self.lister('/proc'):
            if not nom.isdigit():
                continue
            base = '/proc/' + nom
            brut = self.lire(base + '/stat', 4096)
            if not brut or ')' not in brut:
                continue
            comm = brut[brut.find('(') + 1:brut.rfind(')')]
            reste = brut[brut.rfind(')') + 2:].split()
            try:
                ticks, debut = int(reste[11]) + int(reste[12]), int(reste[19])
                ppid = int(reste[1])        # 5.6.102 : parent, pour reconnaître la famille du Collabora intégré
            except (IndexError, ValueError):
                continue
            exe = self.lien(base + '/exe')
            if exe is None:          # fil du noyau, ou processus terminé entre deux lectures
                continue
            uid = 0
            for ligne in (self.lire(base + '/status', 8192) or '').splitlines():
                if ligne.startswith('Uid:'):
                    try:
                        uid = int(ligne.split()[1])
                    except (IndexError, ValueError):
                        pass
            cmd = (self.lire(base + '/cmdline', 8192) or '').replace('\x00', ' ').strip()
            cgroup = self.lire(base + '/cgroup', 8192) or ''
            sockets = set()
            for fd in self.lister(base + '/fd')[:4096]:
                cible = self.lien(base + '/fd/' + fd) or ''
                m = re.fullmatch(r'socket:\[(\d+)\]', cible)
                if m:
                    sockets.add(m.group(1))
            supprime = exe.endswith(' (deleted)')
            chemin = exe[:-len(' (deleted)')] if supprime else exe
            noms = {comm.lower(), os.path.basename(chemin).lower()}
            if cmd:
                noms.add(os.path.basename(cmd.split()[0]).lower())
            out.append({'pid': int(nom), 'ppid': ppid, 'comm': comm, 'exe': chemin, 'supprime': supprime, 'cmd': cmd, 'uid': uid, 'compte': self.nom_uid(uid),
                        'ticks': ticks, 'debut': debut, 'noms': noms, 'sockets': sockets,
                        'conteneur': bool(re.search(r'docker|containerd|kubepods|libpod|lxc|machine\.slice', cgroup))})
        self._processus = out
        return out

    def connexions(self):
        if hasattr(self, '_connexions'):
            return self._connexions
        out = []
        for fichier, v6 in (('/proc/net/tcp', False), ('/proc/net/tcp6', True)):
            for ligne in (self.lire(fichier, 5_000_000) or '').splitlines()[1:]:
                x = ligne.split()
                if len(x) < 10:
                    continue
                try:
                    (la, lp), (ra, rp) = (self._adresse(a, v6) for a in (x[1], x[2]))
                except ValueError:
                    continue
                out.append({'local': la, 'lport': lp, 'distant': ra, 'dport': rp, 'etat': x[3], 'inode': x[9]})
        self._connexions = out
        return out

    @staticmethod
    def _adresse(texte, v6):
        hexa, port = texte.split(':')
        brut = bytes.fromhex(hexa)
        if not v6:
            return socket.inet_ntop(socket.AF_INET, brut[::-1]) if hasattr(socket, 'inet_ntop') else str(ipaddress.IPv4Address(brut[::-1])), int(port, 16)
        mots = b''.join(brut[i:i + 4][::-1] for i in range(0, 16, 4))
        ip = ipaddress.IPv6Address(mots)
        return str(ip.ipv4_mapped or ip), int(port, 16)

    def _drapeaux(self):
        """Pour chaque processus : programme effacé (S02), dossier temporaire ou web (S03), mineur (S04)."""
        if hasattr(self, '_flags'):
            return self._flags
        connexions = [c for c in self.connexions() if c['etat'] == '01']
        par_inode = {c['inode']: c for c in connexions}
        web = list(WEB) + [r.rstrip('/') + '/' for r in self.racines_apache()]
        autorises = self._config().get('autorises', [])
        racines, extraits, fuse = self._collabora()
        flags = {}
        for pr in self.processus():
            f = set()
            exe = pr['exe']
            # un programme décompressé du Collabora reconnu peut être effacé par le nettoyage de /tmp pendant qu'il tourne : pas de S02
            if pr['supprime'] and not exe.startswith(SYSTEME) and not exe.startswith('/memfd:') and 'memfd:' not in exe and \
                    pr['pid'] not in extraits:
                f.add('S02')
            collabora = pr['pid'] in racines or pr['pid'] in extraits or pr['pid'] in fuse
            if (exe.startswith(TEMPORAIRES) or exe.startswith(tuple(web))) and not pr['conteneur'] and not collabora and \
                    not any(re.fullmatch(motif.replace('*', '.*'), exe) for motif in autorises if isinstance(motif, str)):
                f.add('S03')
            liens = [par_inode[i] for i in pr['sockets'] if i in par_inode]
            pr['liens'] = liens
            if pr['noms'] & MINEURS or any(c['dport'] in PORTS_MINAGE and _externe(c['distant']) for c in liens):
                f.add('S04')
            if f:
                flags[pr['pid']] = f
        self._flags = flags
        return flags

    # 5.6.102 (lot C) : Collabora intégré à Nextcloud ------------------------------------------------------------------------------
    def _collabora(self):
        """(AppImages reconnues, programmes décompressés reconnus, processus de l'ancien mode FUSE) : trois ensembles de numéros.

        - AppImage : chemin ``<racine Nextcloud>/{apps,custom_apps,apps-extra}/richdocumentscode(_arm64)/collabora/Collabora_Online.AppImage``, hors dossier
          temporaire, fichier régulier non effacé, ``version.php`` de Nextcloud présent dans la racine ;
        - programme de ``/tmp/appimage_extracted_<empreinte>/`` : reconnu seulement si la chaîne de ses parents atteint une AppImage
          reconnue sans passer par un processus d'un autre dossier (``_parents_collabora``) ; le nom du programme ne compte pas ;
        - FUSE (exception de la 5.6.101, gardée telle quelle mais ancrée) : ``/tmp/.mount_<x>/…`` avec « collabora » dans le chemin ou la
          commande. En mode FUSE, l'AppImage devient elle-même coolwsd (le serveur FUSE est son enfant) : la remontée des parents ne
          s'applique pas."""
        if hasattr(self, '_collab'):
            return self._collab
        par_pid = {pr['pid']: pr for pr in self.processus()}
        racines = {pid for pid, pr in par_pid.items() if self._appimage_collabora(pr)}
        extraits, fuse = set(), set()
        for pid, pr in par_pid.items():
            if pid in racines:
                continue
            m = COLLABORA_EXTRAIT.fullmatch(pr['exe'])
            if m and not self._chemin_douteux(pr['exe']) and self._parents_collabora(pr, par_pid, racines, m.group(1)):
                extraits.add(pid)
            elif COLLABORA_FUSE.fullmatch(pr['exe']) and not self._chemin_douteux(pr['exe']) and \
                    'collabora' in (pr['exe'] + ' ' + pr['cmd']).lower():
                fuse.add(pid)
        self._collab = (racines, extraits, fuse)
        return self._collab

    @staticmethod
    def _chemin_douteux(chemin):
        return bool(re.search(r'/\.\.?(?:/|$)|//', chemin))

    def _appimage_collabora(self, pr):
        exe = pr['exe']
        m = COLLABORA_APPIMAGE.fullmatch(exe)
        if not m or pr['supprime'] or exe.startswith(TEMPORAIRES) or self._chemin_douteux(exe):
            return False
        return self.est_fichier(exe) and self.est_fichier(m.group(1) + 'version.php')

    def _parents_collabora(self, pr, par_pid, racines, empreinte):
        """Remonte les parents (ppid) : vrai dès qu'une AppImage reconnue est atteinte, en ne traversant que des programmes du même
        dossier ``appimage_extracted_<empreinte>`` ; faux sur un parent absent, étranger (shell, autre dossier) ou une boucle."""
        vus = {pr['pid']}
        courant = par_pid.get(pr.get('ppid'))
        while courant is not None and courant['pid'] not in vus and len(vus) <= PROFONDEUR_PARENTS:
            if courant['pid'] in racines:
                return True
            m = COLLABORA_EXTRAIT.fullmatch(courant['exe'])
            if not m or m.group(1) != empreinte:
                return False
            vus.add(courant['pid'])
            courant = par_pid.get(courant.get('ppid'))
        return False

    def reconnus(self):
        """Éléments reconnus comme normaux pendant ce passage (ligne d'information du rapport, sans chemin)."""
        if self.controles.get('S03') not in ('ok', 'constat') and self.controles.get('S02') not in ('ok', 'constat'):
            return []
        try:
            racines, extraits, fuse = self._collabora()
        except Exception:   # noqa: BLE001 — une ligne d'information ne fait jamais échouer le passage
            return []
        n = len(racines) + len(extraits) + len(fuse)
        if not n:
            return []
        return [{'id': 'collabora', 'processus': n,
                 'titre': 'Collabora intégré à Nextcloud reconnu (%d processus) : non signalé comme programme d’un dossier temporaire '
                          'ou web ni pour sa charge du processeur ; le contrôle de minage (S04) lui reste appliqué' % n}]

    def _vu_processus(self, pr):
        vu = ['Processus %d (compte %s) : %s%s' % (pr['pid'], pr['compte'], pr['exe'], ' — fichier effacé du disque' if pr['supprime'] else ''),
              'Ligne de commande : ' + self.commande(pr['cmd'] or pr['comm'])]
        for c in pr.get('liens', [])[:5]:
            vu.append('Connexion établie vers %s:%d%s' % (c['distant'], c['dport'], ' (adresse extérieure)' if _externe(c['distant']) else ''))
        return vu

    def _commandes_processus(self, pr):
        pid = pr['pid']
        return ['sudo ls -l /proc/%d/exe /proc/%d/cwd' % (pid, pid),
                'sudo ss -tnp | grep "pid=%d,"' % pid,
                'sudo cp /proc/%d/exe /root/preuve-%d.bin   # copie du programme pour l’enquête, avant tout arrêt' % (pid, pid),
                'sudo kill -STOP %d   # suspend le programme sans effacer sa mémoire' % pid,
                'sudo kill -9 %d      # arrêt définitif, une fois la copie faite' % pid,
                'sudo crontab -u %s -l   # chercher la tâche qui le relance' % pr['compte']]

    def _signature_processus(self, pr):
        return [pr['exe'], pr['compte'], pr['supprime']]

    def controle_s02(self):
        for pr in self.processus():
            if 'S02' in self._drapeaux().get(pr['pid'], ()):
                self.ajouter('S02', pr['exe'] + '|' + pr['compte'], 'critique', 'Programme effacé du disque en cours d’exécution : %s' % os.path.basename(pr['exe']),
                             EXPLICATIONS['S02'], self._vu_processus(pr), self._commandes_processus(pr), self._signature_processus(pr))

    def controle_s03(self):
        for pr in self.processus():
            if 'S03' in self._drapeaux().get(pr['pid'], ()):
                self.ajouter('S03', pr['exe'] + '|' + pr['compte'], 'critique', 'Programme lancé depuis un dossier temporaire ou web : %s' % self.masque(pr['exe']),
                             EXPLICATIONS['S03'], self._vu_processus(pr), self._commandes_processus(pr), self._signature_processus(pr))

    def controle_s04(self):
        for pr in self.processus():
            if 'S04' in self._drapeaux().get(pr['pid'], ()):
                self.ajouter('S04', pr['exe'] + '|' + pr['compte'], 'critique', 'Programme de minage probable : %s' % (pr['comm'] or os.path.basename(pr['exe'])),
                             EXPLICATIONS['S04'], self._vu_processus(pr), self._commandes_processus(pr) + [
                                 'sudo ufw deny out to any port 3333,4444,5555,7777,14444,45700 proto tcp   # ports de minage habituels'],
                             self._signature_processus(pr))

    def controle_s01(self):
        uptime = float(((self.lire('/proc/uptime') or '0').split() or ['0'])[0])
        precedent = (self.precedent.get('etat') or {}).get('cpu') or {}
        dernier = float((self.precedent.get('etat') or {}).get('passage') or 0)
        connus = CONNUS_CPU | {str(x).lower() for x in self._config().get('connus', []) if isinstance(x, str)}
        self._s01 = []
        collabora = set().union(*self._collabora())   # 5.6.102 : le rendu de documents du Collabora reconnu peut occuper le processeur
        for pr in self.processus():
            age = uptime - pr['debut'] / self.clk
            moyenne = (pr['ticks'] / self.clk / age) if age > 0 else 0
            avant = precedent.get(str(pr['pid']))
            haut_depuis = None
            if avant and avant.get('debut') == pr['debut'] and dernier and self.t > dernier:
                taux = (pr['ticks'] - int(avant.get('ticks', 0))) / self.clk / (self.t - dernier)
                if taux >= 0.8:
                    haut_depuis = avant.get('haut') or dernier
            if moyenne >= 0.8 and age >= 1800:
                haut_depuis = min(haut_depuis or self.t, self.t - age)
            if moyenne >= 0.2 or haut_depuis:
                self.etat['cpu'][str(pr['pid'])] = {'debut': pr['debut'], 'ticks': pr['ticks'], 'haut': haut_depuis}
            connu = bool(pr['noms'] & connus) or '/opt/axiorhub-mail-agent/' in pr['cmd'] or 'axiorhub' in pr['cmd'].lower()
            connu = connu or pr['pid'] in collabora     # 5.6.102 : Collabora reconnu (règle de ``_collabora``), jamais par le nom
            if haut_depuis and self.t - haut_depuis >= 1800 and not (connu and not pr['noms'] & MINEURS):
                self._s01.append((pr, moyenne, self.t - haut_depuis))

    def _combiner_s01(self):
        flags = self._drapeaux() if hasattr(self, '_processus') else {}
        for pr, moyenne, duree in getattr(self, '_s01', []):
            grave = bool(flags.get(pr['pid'], set()) & {'S02', 'S03', 'S04'})
            vu = ['Processeur occupé à %d %% en moyenne depuis %d minutes' % (round(moyenne * 100), duree // 60)] + self._vu_processus(pr)
            self.ajouter('S01', pr['exe'] + '|' + pr['compte'], 'critique' if grave else 'important',
                         'Processeur occupé en continu par %s' % (pr['comm'] or os.path.basename(pr['exe'])),
                         EXPLICATIONS['S01'] + (' Ce programme présente aussi un autre signe d’intrusion.' if grave else ''), vu,
                         self._commandes_processus(pr) + ['top -b -n 1 -o %CPU | head -20'], self._signature_processus(pr))
        if 'S01' in self.controles and self.controles['S01'] == 'ok' and any(c['id'] == 'S01' for c in self.constats):
            self.controles['S01'] = 'constat'

    # ---------------------------------------------------------------------------------------------------------- tâches planifiées
    def crontabs(self):
        """{compte: [lignes actives]} des tâches personnelles (Debian : /var/spool/cron/crontabs ; autres : /var/spool/cron)."""
        out = {}
        for dossier in ('/var/spool/cron/crontabs', '/var/spool/cron'):
            for nom in self.lister(dossier):
                if self.est_fichier(dossier + '/' + nom) and nom not in out:
                    texte = self.lire(dossier + '/' + nom, 200_000) or ''
                    out[nom] = [l.strip() for l in texte.splitlines() if l.strip() and not l.strip().startswith('#') and '=' not in l.split()[0]]
        return out

    def controle_s05(self):
        for compte, lignes in sorted(self.crontabs().items()):
            if not lignes or not self.compte_service(compte):
                continue
            telecharge = any(re.search(r'\b(curl|wget)\b', l) for l in lignes)
            vu = ['Tâche planifiée du compte %s : %s' % (compte, self.commande(l)) for l in lignes[:8]]
            if telecharge:
                vu.append('La tâche télécharge un programme depuis Internet (curl ou wget) à chaque lancement.')
            self.ajouter('S05', 'crontab|' + compte, 'critique', 'Tâche planifiée du compte de service %s' % compte, EXPLICATIONS['S05'], vu,
                         ['sudo crontab -u %s -l > /root/crontab-%s.sauvegarde   # copie pour l’enquête' % (compte, compte),
                          'sudo crontab -u %s -r   # supprime la tâche' % compte,
                          'echo %s | sudo tee -a /etc/cron.deny   # interdit les tâches planifiées à ce compte' % compte], [compte] + lignes)
        web = self.compte_web()
        if not web:
            return
        autorises = self.lire('/etc/cron.allow')
        if autorises is not None:
            if web['nom'] in autorises.split():
                self.ajouter('S05', 'cron.allow|' + web['nom'], 'important', 'Le compte %s peut créer des tâches planifiées' % web['nom'],
                             EXPLICATIONS['S05d'], ['/etc/cron.allow contient %s' % web['nom']],
                             ['sudo sed -i "/^%s$/d" /etc/cron.allow' % web['nom']], ['allow', web['nom']])
            return
        refuses = (self.lire('/etc/cron.deny') or '').split()
        if web['nom'] not in refuses:
            self.ajouter('S05', 'cron.deny|' + web['nom'], 'important', 'Le compte %s n’est pas dans /etc/cron.deny' % web['nom'], EXPLICATIONS['S05d'],
                         ['/etc/cron.deny %s' % ('ne contient pas %s' % web['nom'] if self.existe('/etc/cron.deny') else 'est absent')],
                         ['echo %s | sudo tee -a /etc/cron.deny' % web['nom']], ['deny', web['nom']])

    # ---------------------------------------------------------------------------------------------------------- fichiers de démarrage
    def fichiers_demarrage(self):
        cibles = []
        for depart, profondeur in (('/etc/systemd/system', 3), ('/usr/lib/systemd/system', 1), ('/lib/systemd/system', 1), ('/etc/cron.d', 0),
                                   ('/etc/cron.daily', 0), ('/etc/cron.hourly', 0), ('/etc/cron.weekly', 0), ('/etc/cron.monthly', 0),
                                   ('/etc/profile.d', 0), ('/etc/sudoers.d', 0), ('/var/spool/cron/crontabs', 0)):
            if not self.fausse and depart == '/lib/systemd/system' and os.path.realpath('/lib') == '/usr/lib':
                continue
            for dossier, _, fichiers in self.parcourir(depart, profondeur):
                cibles += [dossier.rstrip('/') + '/' + f for f in fichiers]
        cibles += ['/etc/crontab', '/etc/ld.so.preload', '/etc/rc.local', '/etc/sudoers']
        foyers = {'/root'} | {c['home'] for c in self.comptes.values() if c['home'] and c['home'] != '/'}
        for foyer in sorted(foyers):
            cibles += [foyer.rstrip('/') + '/.ssh/authorized_keys', foyer.rstrip('/') + '/.ssh/authorized_keys2']
            for dossier, _, fichiers in self.parcourir(foyer.rstrip('/') + '/.config/systemd/user', 1):
                cibles += [dossier + '/' + f for f in fichiers]
        out = {}
        for chemin in cibles:
            if self.est_fichier(chemin):
                try:
                    with open(self.p(chemin), 'rb') as f:
                        out[chemin] = hashlib.sha256(f.read(4_000_000)).hexdigest()
                except OSError:
                    continue
        return out

    def _paquets(self):
        """Index chemin → (paquet, date de l'installation) construit seulement si un changement est constaté."""
        if hasattr(self, '_index_paquets'):
            return self._index_paquets
        index = {}
        for nom in self.lister('/var/lib/dpkg/info'):
            if nom.endswith('.list'):
                chemin = '/var/lib/dpkg/info/' + nom
                try:
                    quand = os.stat(self.p(chemin)).st_mtime
                except OSError:
                    quand = 0
                for ligne in (self.lire(chemin, 5_000_000) or '').splitlines():
                    index[ligne.strip()] = (nom[:-5].split(':')[0], quand)
        self._index_paquets = index
        return index

    def controle_s06(self):
        actuelles = self.fichiers_demarrage()
        self.etat['empreintes'] = actuelles
        avant = (self.precedent.get('etat') or {}).get('empreintes')
        dernier = float((self.precedent.get('etat') or {}).get('passage') or 0)
        if not isinstance(avant, dict) or not avant:      # premier passage : référence seulement
            if actuelles.get('/etc/ld.so.preload') and (self.lire('/etc/ld.so.preload') or '').strip():
                self.ajouter('S06', '/etc/ld.so.preload', 'important', 'Fichier /etc/ld.so.preload présent', EXPLICATIONS['S06'],
                             ['/etc/ld.so.preload existe et n’est pas vide : il fait charger une bibliothèque dans chaque programme lancé.'],
                             ['sudo ls -l /etc/ld.so.preload', 'sudo dpkg -S /etc/ld.so.preload'], [actuelles['/etc/ld.so.preload']])
            return
        for chemin, empreinte in sorted(actuelles.items()):
            if avant.get(chemin) == empreinte:
                continue
            critique = chemin == '/etc/ld.so.preload' or os.path.basename(chemin).startswith('authorized_keys')
            paquet = self._paquets().get(chemin)
            niveau = 'critique' if critique else 'important'
            vu = ['%s : %s depuis le passage précédent' % (chemin, 'ajouté' if chemin not in avant else 'modifié')]
            explication = EXPLICATIONS['S06']
            if paquet and not critique:
                if paquet[1] >= dernier - 60:
                    niveau, explication = 'surveiller', EXPLICATIONS['S06p']
                    vu.append('Changement fait par une mise à jour du paquet %s.' % paquet[0])
                else:
                    vu.append('Fichier fourni par le paquet %s, modifié en dehors d’une mise à jour.' % paquet[0])
            self.ajouter('S06', chemin, niveau, 'Fichier de démarrage %s : %s' % ('ajouté' if chemin not in avant else 'modifié', chemin), explication, vu,
                         ['sudo ls -l --time-style=full-iso %s' % chemin, 'sudo dpkg -S %s   # appartient-il à un paquet ?' % chemin,
                          'sudo less %s   # relire le contenu avant toute décision' % chemin], [empreinte])
        for chemin in sorted(set(avant) - set(actuelles)):
            if chemin.startswith(('/etc/cron', '/var/spool/cron', '/etc/systemd/system')):
                self.ajouter('S06', 'retire|' + chemin, 'surveiller', 'Fichier de démarrage retiré : %s' % chemin, EXPLICATIONS['S06'],
                             ['%s a disparu depuis le passage précédent' % chemin], ['sudo journalctl --since "-1h" | grep -i "%s"' % os.path.basename(chemin)],
                             [avant[chemin]])

    # ---------------------------------------------------------------------------------------------------------- secrets (S07)
    def _est_secret(self, dossier, nom):
        bas = nom.lower()
        if bas.endswith(('.example', '.sample', '.dist', '.template')):
            return False
        if bas == '.env' or bas.endswith('.env') or bas.startswith('.env.'):
            return True
        if bas in ('wp-config.php', 'dovecot-sql.conf.ext', 'config.inc.php', 'config.local.php'):
            return True
        if bas == 'config.php' and (os.path.basename(dossier) == 'config' or dossier.startswith('/etc/')):
            return True
        if bas == 'application.ini' and ('snappymail' in dossier.lower() or '_data_' in dossier):
            return True
        return dossier.startswith('/etc/postfix') and bas.endswith('.cf') and bas not in ('main.cf', 'master.cf')

    def noms_reglages(self, chemin):
        """Noms des réglages sensibles du fichier (jamais leurs valeurs : la lecture est jetée aussitôt)."""
        texte = self.lire(chemin, 256_000) or ''
        noms = []
        motifs = (r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=', r"['\"]([A-Za-z0-9_.-]{2,60})['\"]\s*=>",
                  r"define\(\s*['\"]([A-Za-z0-9_]{2,60})['\"]", r'^\s*([A-Za-z0-9_.-]{2,60})\s*[:=]', r'\b(password|passwd|user|dbname)=')
        for motif in motifs:
            for m in re.finditer(motif, texte, re.M):
                n = m.group(1)
                if SECRETS_NOMS.search(n) and n not in noms:
                    noms.append(n)
        del texte
        return noms[:20]

    def controle_s07(self):
        web = self.compte_web()
        if not web:
            return
        principal, supp = self.groupes_de(web['nom'])
        for g in sorted(supp):
            nom = self.nom_gid(g)
            if nom in GROUPES_SENSIBLES:
                self.ajouter('S07', 'groupe|' + nom, 'important', 'Le compte %s est membre du groupe %s' % (web['nom'], nom), EXPLICATIONS['S07g'],
                             ['%s appartient au groupe supplémentaire %s' % (web['nom'], nom)],
                             ['sudo gpasswd -d %s %s' % (web['nom'], nom), 'sudo systemctl restart apache2 php*-fpm 2>/dev/null   # pour que le retrait prenne effet'],
                             [web['nom'], nom])
        for depart, profondeur in (('/var/www', 6), ('/srv', 5), ('/etc', 3), ('/opt', 4)):
            for dossier, _, fichiers in self.parcourir(depart, profondeur, ignorer=('node_modules', 'vendor', '.git', 'releases', '__pycache__', 'cache')):
                for nom in fichiers:
                    if not self._est_secret(dossier, nom):
                        continue
                    chemin = dossier.rstrip('/') + '/' + nom
                    m = self.meta_reelle(chemin)   # 5.6.104 : un lien a toujours rwxrwxrwx ; seuls les droits de sa cible comptent
                    if not m:
                        continue
                    mode, uid, gid, _cible = m
                    if uid == web['uid']:
                        continue                  # fichier de l'application web elle-même : lisible par construction
                    raison = ''
                    if mode & stat.S_IROTH:
                        raison = 'lisible par tous les comptes de la machine (droits %s)' % oct(stat.S_IMODE(mode))[2:]
                    elif gid in supp and mode & stat.S_IRGRP:
                        raison = 'lisible par %s grâce au groupe supplémentaire %s' % (web['nom'], self.nom_gid(gid))
                    if not raison:
                        continue
                    noms = self.noms_reglages(chemin)
                    if dossier.startswith('/etc/postfix') and not noms:
                        continue                  # 5.6.104 : .cf de Postfix sans mot de passe (types de tables, etc.) : rien de secret
                    if mode & stat.S_IROTH:       # 5.6.104 : retirer la lecture par tous ; le propriétaire et le groupe actuels gardent leur accès
                        commandes = ['sudo chmod o-r %s' % chemin]
                    else:
                        commandes = ['sudo chmod g-r %s' % chemin,
                                     '# ou retirez %s du groupe %s si un autre service de ce groupe doit lire le fichier' % (web['nom'], self.nom_gid(gid))]
                    commandes.append('# puis changez les mots de passe et clés concernés : ils ont pu être lus')
                    self.ajouter('S07', chemin, 'important', 'Fichier de secrets lisible par le serveur web : %s' % self.masque(chemin), EXPLICATIONS['S07'],
                                 ['%s : %s ; propriétaire %s, groupe %s' % (chemin, raison, self.nom_uid(uid), self.nom_gid(gid)),
                                  'Réglages concernés : %s' % (', '.join(noms) if noms else 'non identifiés')],
                                 commandes,
                                 [chemin, oct(stat.S_IMODE(mode)), gid, noms])

    # ---------------------------------------------------------------------------------------------------------- Apache (S08)
    def sites_apache(self):
        if hasattr(self, '_sites'):
            return self._sites
        sites = []
        for nom in self.lister('/etc/apache2/sites-enabled'):
            texte = self.lire('/etc/apache2/sites-enabled/' + nom, 500_000) or ''
            courant = None
            for ligne in texte.splitlines():
                l = ligne.strip()
                m = re.match(r'(?i)<VirtualHost\s+([^>]+)>', l)
                if m:
                    port = re.search(r':(\d+)', m.group(1))
                    courant = {'fichier': nom, 'port': int(port.group(1)) if port else 80, 'noms': [], 'racine': ''}
                    continue
                if courant is None:
                    continue
                if re.match(r'(?i)</VirtualHost>', l):
                    if courant['noms'] or courant['racine']:
                        sites.append(courant)
                    courant = None
                    continue
                m = re.match(r'(?i)(ServerName|ServerAlias)\s+(.+)', l)
                if m:
                    courant['noms'] += [n for n in m.group(2).split() if re.fullmatch(r'[A-Za-z0-9.*-]+', n) and '*' not in n]
                m = re.match(r'(?i)DocumentRoot\s+"?([^"\s]+)"?', l)
                if m:
                    courant['racine'] = m.group(1).rstrip('/') or '/'
        self._sites = sites
        return sites

    def racines_apache(self):
        return sorted({s['racine'] for s in self.sites_apache() if s['racine']})

    def sonder(self, hote, port, tls, chemin):
        """Requête GET vers 127.0.0.1 seulement, en-tête Host du site ; renvoie (statut, servi). Le corps lu (4 Ko au plus) est jeté."""
        cible = self.ports.get(port, port if not self.fausse else None)
        if cible is None:
            return None, False
        s = socket.create_connection(('127.0.0.1', int(cible)), timeout=4)
        try:
            if tls:
                contexte = ssl.create_default_context()
                contexte.check_hostname = False
                contexte.verify_mode = ssl.CERT_NONE
                s = contexte.wrap_socket(s, server_hostname=hote)
            s.sendall(('GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: axiorhub-veille-securite\r\nAccept: */*\r\nConnection: close\r\n\r\n' % (chemin, hote)).encode('ascii'))
            brut = b''
            while len(brut) < 16384:
                bloc = s.recv(4096)
                if not bloc:
                    break
                brut += bloc
        finally:
            s.close()
        tete, _, corps = brut.partition(b'\r\n\r\n')
        m = re.match(rb'HTTP/\d(?:\.\d)?\s+(\d{3})', tete)
        statut = int(m.group(1)) if m else 0
        corps = corps[:4096].decode('utf-8', 'replace')
        signatures = {'/.env': r'(?m)^[A-Z][A-Z0-9_]{2,}\s*=', '/.git/config': r'\[core\]', '/config/config.php': r'<\?php|\$CONFIG'}
        servi = statut == 200 and bool(re.search(signatures[chemin], corps))
        del corps, brut
        return statut, servi

    def controle_s08(self):
        sites = self.sites_apache()
        racines = self.racines_apache()
        for site in sites:
            nom = site['noms'][0] if site['noms'] else site['fichier']
            r = site['racine']
            if not r:
                continue
            autres = [x for x in racines if x != r and x.startswith(r.rstrip('/') + '/')]
            if r in ('/var/www', '/var/www/html'):
                for d in self.lister(r):
                    chemin = r + '/' + d
                    if self.est_dossier(chemin) and chemin not in autres and any(self.existe(chemin + '/' + m) for m in ('index.php', 'config', '.env', 'composer.json', 'wp-config.php', '.git')):
                        autres.append(chemin)
            servis = []
            if self.sonde:
                for chemin in ('/.env', '/.git/config', '/config/config.php'):
                    try:
                        statut, servi = self.sonder(nom, site['port'], site['port'] == 443, chemin)
                    except (OSError, ssl.SSLError, ValueError):
                        continue
                    if servi:
                        servis.append('https://%s%s' % (nom, chemin) if site['port'] == 443 else 'http://%s%s' % (nom, chemin))
            if not autres and not servis:
                continue
            vu = ['Site %s (%s), racine %s' % (nom, site['fichier'], r)]
            if autres:
                vu.append('La racine contient aussi : %s' % ', '.join(sorted(autres)[:10]))
            vu += ['Fichier servi à tout visiteur : %s' % u for u in servis]
            commandes = ['# dans %s : donnez au site sa propre racine (ex. DocumentRoot /var/www/<site>/public)' % ('/etc/apache2/sites-enabled/' + site['fichier']),
                         '# et refusez les fichiers cachés : <FilesMatch "^\\.(env|git|ht)"> Require all denied </FilesMatch>',
                         'sudo apache2ctl configtest && sudo systemctl reload apache2']
            if servis:
                commandes.append('# changez tous les mots de passe et clés des fichiers servis : ils doivent être considérés comme publics')
            self.ajouter('S08', site['fichier'] + '|' + nom, 'critique' if servis else 'important',
                         ('Fichier de secrets servi par le site %s' if servis else 'Racine du site %s trop large') % nom,
                         EXPLICATIONS['S08s' if servis else 'S08'], vu, commandes, [r, sorted(autres), sorted(servis)])

    # ---------------------------------------------------------------------------------------------------------- versions (S09)
    def detecter_applications(self):
        trouvees = []
        departs = [('/var/www', 4), ('/srv', 4), ('/opt', 3), ('/usr/share/roundcube', 0), ('/usr/share/postfixadmin', 0), ('/usr/share/snappymail', 0)]
        vus = set()
        for depart, profondeur in departs:
            for dossier, sous, fichiers in self.parcourir(depart, profondeur, ignorer=('node_modules', 'vendor', '.git', 'releases', '__pycache__')):
                if dossier in vus:
                    continue
                vus.add(dossier)
                trouve = self._application(dossier, sous, fichiers)
                if trouve:
                    trouvees.append(trouve)
        return trouvees

    def _application(self, d, sous, fichiers):
        texte = lambda rel, n=200_000: self.lire(d.rstrip('/') + '/' + rel, n) or ''   # noqa: E731
        if self.est_fichier(d + '/program/include/iniset.php'):
            m = re.search(r"RCMAIL_VERSION['\"]\s*,\s*['\"]([0-9][^'\"]*)", texte('program/include/iniset.php'))
            return {'app': 'roundcube', 'chemin': d, 'version': m.group(1) if m else ''}
        for app in ('snappymail', 'rainloop'):
            if self.est_dossier(d + '/' + app + '/v'):
                versions = [v for v in self.lister(d + '/' + app + '/v') if re.fullmatch(r'\d+(\.\d+)+', v) and any(_version(v))]   # 5.6.104 : « 0.0.0 » = copie de développement
                return {'app': app, 'chemin': d, 'version': max(versions, key=_version) if versions else ''}
        if 'version.php' in fichiers and ('occ' in fichiers or 'core' in sous) and 'OC_VersionString' in texte('version.php'):
            m = re.search(r"OC_VersionString\s*=\s*['\"]([^'\"]+)", texte('version.php'))
            return {'app': 'nextcloud', 'chemin': d, 'version': m.group(1) if m else ''}
        if self.est_fichier(d + '/wp-includes/version.php'):
            m = re.search(r"wp_version\s*=\s*['\"]([^'\"]+)", texte('wp-includes/version.php'))
            return {'app': 'wordpress', 'chemin': d, 'version': m.group(1) if m else ''}
        if 'VERSION.txt' in fichiers and 'artisan' in fichiers and 'invoiceninja' in texte('composer.json', 20_000).lower():
            return {'app': 'invoiceninja', 'chemin': d, 'version': texte('VERSION.txt', 100).strip()[:20]}
        if 'postfixadmin' in d.lower() and 'config.inc.php' in fichiers:
            m = re.search(r"\$CONF\['version'\]\s*=\s*['\"]([^'\"]+)", texte('config.inc.php'))
            if m:
                return {'app': 'postfixadmin', 'chemin': d, 'version': m.group(1)}
        return None

    def controle_s09(self):
        for a in self.detecter_applications():
            regle = VERSIONS_MINIMALES.get(a['app'])
            nom = (regle or {}).get('nom') or NOMS_APPS.get(a['app'], a['app'])
            entree = {'nom': nom, 'version': a['version'] or 'inconnue', 'chemin': self.masque(a['chemin']), 'minimum': '', 'statut': 'version_affichee',
                      'source': (regle or {}).get('source', ''), 'table': TABLE_VERSIONS_DATE}
            if regle and regle.get('abandonne'):
                entree['statut'] = 'abandonne'
                self.ajouter('S09', a['app'] + '|' + a['chemin'], 'important', '%s installé : logiciel abandonné' % nom, EXPLICATIONS['S09r'],
                             ['%s %s dans %s' % (nom, a['version'] or '(version inconnue)', a['chemin']), 'Source : ' + regle['source']],
                             ['# remplacez %s par une messagerie web maintenue, puis retirez le dossier %s du site' % (nom, a['chemin'])],
                             [a['app'], a['chemin'], a['version']])
            elif regle and regle.get('minimum'):
                entree['minimum'] = regle['minimum']
                if a['version'] and _version(a['version']) < _version(regle['minimum']):
                    entree['statut'] = 'obsolete'
                    self.ajouter('S09', a['app'] + '|' + a['chemin'], 'important', '%s %s obsolète (minimum sûr : %s)' % (nom, a['version'], regle['minimum']),
                                 EXPLICATIONS['S09'],
                                 ['%s %s dans %s ; version minimale sûre connue : %s (table du %s)' % (nom, a['version'], a['chemin'], regle['minimum'],
                                                                                                     '14/11/2026'), 'Source : ' + regle['source']],
                                 ['# sauvegardez le dossier et la base, puis installez la dernière version publiée par l’éditeur (%s)' % regle['source'].split(': ', 1)[-1],
                                  '# Roundcube : bin/installto.sh <dossier> depuis l’archive officielle ; SnappyMail : mise à jour depuis son panneau d’administration'],
                                 [a['app'], a['chemin'], a['version']])
                else:
                    entree['statut'] = 'a_jour' if a['version'] else 'version_inconnue'
            self.applications.append(entree)

    # ---------------------------------------------------------------------------------------------------------- dossiers web (S10)
    def _ancetres_ecrivables(self, chemin, profondeur=0):
        """Premier élément du chemin (fichier, dossiers parents) modifiable par le compte web ; un lien rencontré fait contrôler aussi
        sa cible et les dossiers de la cible (5.6.104), sur huit liens au plus."""
        parties = chemin.rstrip('/').split('/')
        for i in range(len(parties), 1, -1):
            courant = '/'.join(parties[:i]) or '/'
            if not self.existe(courant):
                continue
            if self.ecrivable_web(courant):
                return courant
            m = self.meta(courant)
            if m and stat.S_ISLNK(m[0]) and profondeur < 8:
                cible = self.cible_lien(courant)
                trouve = self._ancetres_ecrivables(cible, profondeur + 1) if cible else None
                if trouve:
                    return trouve
        return None

    def taches_privilegiees(self):
        """(compte, source, commande) des tâches lancées par root ou un administrateur : /etc/crontab, /etc/cron.d, crontabs, unités."""
        admins = self.administrateurs()
        out = []
        for source in ['/etc/crontab'] + ['/etc/cron.d/' + n for n in self.lister('/etc/cron.d')]:
            for l in (self.lire(source, 200_000) or '').splitlines():
                x = l.split()
                if len(x) >= 7 and not l.strip().startswith('#') and '=' not in x[0]:
                    debut = 1 if x[0].startswith('@') else 5
                    if len(x) > debut + 1 and x[debut] in admins:
                        out.append((x[debut], source, ' '.join(x[debut + 1:])))
        for compte, lignes in self.crontabs().items():
            if compte in admins:
                for l in lignes:
                    x = l.split()
                    debut = 1 if x and x[0].startswith('@') else 5
                    out.append((compte, 'crontab de ' + compte, ' '.join(x[debut:])))
        for dossier, _, fichiers in self.parcourir('/etc/systemd/system', 1):
            for f in fichiers:
                if f.endswith('.service'):
                    texte = self.lire(dossier + '/' + f, 100_000) or ''
                    utilisateur = re.search(r'(?m)^\s*User\s*=\s*(\S+)', texte)
                    if not utilisateur or utilisateur.group(1) in admins:
                        for m in re.finditer(r'(?m)^\s*Exec(?:Start|StartPre|StartPost)\s*=\s*[-@:+!]*(.+)$', texte):
                            out.append(((utilisateur.group(1) if utilisateur else 'root'), 'unité ' + f, m.group(1)))
        return out

    def controle_s10(self):
        web = self.compte_web()
        if not web:
            return
        for d in ('/var/www', '/var/www/html'):
            m = self.meta(d)
            if m and stat.S_ISDIR(m[0]) and m[1] == web['uid']:
                self.ajouter('S10', 'dossier|' + d, 'important', 'Le dossier %s appartient au compte %s' % (d, web['nom']), EXPLICATIONS['S10'],
                             ['%s : propriétaire %s, droits %s' % (d, web['nom'], oct(stat.S_IMODE(m[0]))[2:])],
                             ['sudo chown root:root %s && sudo chmod 755 %s   # seuls les sous-dossiers qui le demandent restent à %s' % (d, d, web['nom'])],
                             [d, web['uid']])
        deja = set()
        for compte, source, commande in self.taches_privilegiees():
            for chemin in re.findall(r'(?<![\w.])(/[^\s;|&><"\'`$()]+)', commande):
                if chemin in deja or not self.est_fichier(chemin) or chemin.startswith(SYSTEME):
                    continue
                dossier = self._ancetres_ecrivables(chemin)
                if not dossier:
                    continue
                deja.add(chemin)
                if chemin.startswith(AXIORHUB_CODE + '/'):   # 5.6.104 : code d'AxiorHub : remettre les droits de l'installateur, ne pas déplacer
                    commandes = ['sudo chown -R root:root %s' % AXIORHUB_CODE, 'sudo chown -h root:root %s/current' % AXIORHUB_CODE,
                                 'sudo chmod -R u=rwX,go=rX %s' % AXIORHUB_CODE]
                else:
                    commandes = ['sudo install -o root -g root -m 755 %s /usr/local/sbin/%s' % (chemin, os.path.basename(chemin)),
                                 '# puis remplacez %s par /usr/local/sbin/%s dans %s' % (chemin, os.path.basename(chemin), source),
                                 'sudo chown root:root %s' % dossier]
                self.ajouter('S10', 'script|' + chemin, 'critique', 'Script de %s modifiable par le serveur web : %s' % (compte, chemin), EXPLICATIONS['S10s'],
                             ['%s lancé par %s (%s)' % (chemin, compte, source), '%s est modifiable par %s' % (dossier, web['nom'])],
                             commandes, [chemin, compte, dossier])

    # ---------------------------------------------------------------------------------------------------------- conteneurs (S11)
    def ports_conteneurs(self):
        out = []
        for pr in self.processus():
            if os.path.basename(pr['exe']) == 'docker-proxy' or pr['comm'] == 'docker-proxy':
                a = dict(re.findall(r'-(host-ip|host-port|container-ip|container-port|proto)\s+(\S+)', pr['cmd']))
                out.append({'hote': a.get('host-ip', ''), 'port': a.get('host-port', ''), 'nom': '', 'image': '',
                            'cible': '%s:%s' % (a.get('container-ip', ''), a.get('container-port', ''))})
        for ident in self.lister('/var/lib/docker/containers'):
            base = '/var/lib/docker/containers/' + ident
            try:
                config = json.loads(self.lire(base + '/config.v2.json', 2_000_000) or '{}')
                hote = json.loads(self.lire(base + '/hostconfig.json', 2_000_000) or '{}')
            except ValueError:
                continue
            if config.get('State', {}).get('Running') is False:
                continue
            nom = str(config.get('Name') or ident[:12]).lstrip('/')
            image = str((config.get('Config') or {}).get('Image') or '')
            for interne, liaisons in ((hote.get('PortBindings') or {}).items()):
                for l in liaisons or []:
                    port = str(l.get('HostPort') or '')
                    deja = next((x for x in out if x['port'] == port and not x['nom']), None)
                    if deja:
                        deja.update({'nom': nom, 'image': image, 'cible': interne})
                    else:
                        out.append({'hote': str(l.get('HostIp') or ''), 'port': port, 'nom': nom, 'image': image, 'cible': interne})
        return out

    def controle_s11(self):
        for x in self.ports_conteneurs():
            if x['hote'] not in ('', '0.0.0.0', '::', '[::]'):
                continue
            libelle = (x['nom'] + ' ' + x['image']).lower()
            terminal = any(t in libelle for t in TERMINAUX)
            auto = any(t in libelle for t in AUTOMATISATION)
            nom = x['nom'] or 'conteneur %s' % x['cible']
            vu = ['Port %s ouvert sur toutes les interfaces → %s%s' % (x['port'], nom, (' (image %s)' % x['image']) if x['image'] else '')]
            if terminal:
                vu.append('Terminal web : permet de lancer des commandes sur le serveur depuis un navigateur.')
            elif auto:
                vu.append('Outil d’automatisation : ses flux peuvent lancer des commandes et contiennent des accès à d’autres services.')
            self.ajouter('S11', '%s|%s' % (x['nom'] or x['cible'], x['port']), 'important' if terminal else 'surveiller',
                         ('Terminal web joignable depuis Internet : %s' if terminal else 'Port de conteneur ouvert sur toutes les interfaces : %s') % nom,
                         EXPLICATIONS['S11t' if (terminal or auto) else 'S11'], vu,
                         ['# dans le docker-compose.yml du conteneur : ports: - "127.0.0.1:%s:%s"' % (x['port'], str(x['cible']).split(':')[-1].split('/')[0]),
                          'docker compose up -d   # dans le dossier du conteneur', 'sudo ss -ltnp | grep ":%s "' % x['port']],
                         [x['nom'], x['image'], x['port']])

    # ---------------------------------------------------------------------------------------------------------- authentification (S12)
    def _instant_ligne(self, ligne):
        m = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?([+-]\d{2}:?\d{2}|Z)?', ligne)
        if m:
            try:
                dt = datetime.fromisoformat(m.group(1) + (m.group(2) or '+00:00').replace('Z', '+00:00'))
                return dt.timestamp()
            except ValueError:
                return None
        m = re.match(r'([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})', ligne)
        if m:
            mois = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
            if m.group(1) not in mois:
                return None
            ref = datetime.fromtimestamp(self.t, timezone.utc)
            try:
                dt = datetime(ref.year, mois.index(m.group(1)) + 1, int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), tzinfo=timezone.utc)
            except ValueError:
                return None
            if dt.timestamp() > self.t + 86400:
                dt = dt.replace(year=ref.year - 1)
            return dt.timestamp()
        return None

    def controle_s12(self):
        motifs = ((re.compile(r'sshd\[\d+\]: (?:Failed password|Invalid user|Connection closed by authenticating user|Disconnected from invalid user)'), 'SSH'),
                  (re.compile(r'(?:dovecot|imap-login|pop3-login|auth).*(?:auth failed|authentication failed|Disconnected \(auth failed)', re.I), 'messagerie'),
                  (re.compile(r'SASL \w+ authentication failed'), 'messagerie'))
        adresse = re.compile(r'(?:rip=|from |\[)(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]{3,39})(?:[\],\s]|$)')
        compteurs, adresses = {'SSH': 0, 'messagerie': 0}, {}
        for journal in ('/var/log/auth.log', '/var/log/auth.log.1', '/var/log/secure', '/var/log/mail.log', '/var/log/mail.log.1'):
            for ligne in self.lignes_fin(journal):
                for motif, service in motifs:
                    if motif.search(ligne):
                        quand = self._instant_ligne(ligne)
                        if quand is None or not (self.t - 86400 <= quand <= self.t + 300):
                            break
                        compteurs[service] += 1
                        m = adresse.search(ligne)
                        if m:
                            try:
                                ip = str(ipaddress.ip_address(m.group(1)))
                                adresses[ip] = adresses.get(ip, 0) + 1
                            except ValueError:
                                pass
                        break
        installe = any(self.existe(p) for p in ('/usr/bin/fail2ban-client', '/usr/bin/fail2ban-server', '/etc/fail2ban/jail.conf'))
        actif = self.existe('/run/fail2ban/fail2ban.sock') or self.existe('/var/run/fail2ban/fail2ban.sock')
        if not installe or not actif:
            self.ajouter('S12', 'fail2ban', 'important', 'Fail2ban %s' % ('absent' if not installe else 'arrêté'), EXPLICATIONS['S12f'],
                         ['Fail2ban %s' % ('n’est pas installé' if not installe else 'est installé mais ne tourne pas')],
                         [AIDE_DURCIR + '   # bilan, sans rien modifier', AIDE_DURCIR + ' --appliquer   # installe et règle Fail2ban (SSH, AxiorHub, Nextcloud)',
                          'sudo systemctl enable --now fail2ban'], [installe, actif])
        total = sum(compteurs.values())
        if total:
            tete = sorted(adresses.items(), key=lambda x: (-x[1], x[0]))[:5]
            vu = ['Échecs sur 24 h : %d (SSH : %d, messagerie : %d)' % (total, compteurs['SSH'], compteurs['messagerie'])]
            vu += ['Adresse %s : %d échec%s' % (ip, n, 's' if n > 1 else '') for ip, n in tete]
            self.ajouter('S12', 'echecs', 'surveiller', 'Échecs de connexion sur 24 h : %d' % total, EXPLICATIONS['S12'], vu,
                         ['sudo fail2ban-client status', 'sudo fail2ban-client status sshd'], sorted(ip for ip, _ in tete))

    # ---------------------------------------------------------------------------------------------------------- AxiorHub (S13)
    def controle_s13(self):
        attendus = [('/etc/axiorhub-mail-agent', 'dossier'), ('/etc/axiorhub-mail-agent/config.json', 'config'),
                    ('/etc/axiorhub-mail-agent/secrets', 'dossier'), ('/var/lib/axiorhub-mail-agent', 'etat'), (RAPPORTS, 'rapports')]
        attendus += [('/etc/axiorhub-mail-agent/secrets/' + n, 'secret') for n in self.lister('/etc/axiorhub-mail-agent/secrets')]
        attendus += [('/etc/axiorhub-mail-agent/' + n, 'secret') for n in self.lister('/etc/axiorhub-mail-agent') if n.endswith(('.secret', '.key', '.pem', '.token'))]
        attendus += [(RAPPORTS + '/' + n, 'rapport') for n in self.lister(RAPPORTS) if n.endswith('.json')][:5]
        service = self.comptes.get(GROUPE)
        for chemin, nature in attendus:
            m = self.meta_reelle(chemin)   # 5.6.104 : config.json peut être un lien vers l'état du service ; seuls les droits de la cible comptent
            if not m:
                continue
            mode, uid, gid, _cible = m
            droits = stat.S_IMODE(mode)
            defauts = []
            if droits & 0o007:
                defauts.append('accessible aux autres comptes (droits %s)' % oct(droits)[2:])
            if nature in ('secret', 'config', 'rapport') and droits & 0o020:
                defauts.append('modifiable par le groupe')
            if nature in ('dossier', 'rapports') and uid != 0:
                defauts.append('propriétaire %s au lieu de root' % self.nom_uid(uid))
            if nature in ('secret', 'rapport') and droits & 0o002:
                defauts.append('modifiable par tous')
            if nature == 'rapports' and service and uid == service['uid']:
                defauts.append('le compte de l’interface ne doit pas pouvoir réécrire les rapports')
            if not defauts:
                continue
            attendu = {'dossier': '750 root:%s' % GROUPE, 'config': '640 root:%s' % GROUPE, 'secret': '640 root:%s' % GROUPE,
                       'etat': '700 %s:%s' % (GROUPE, GROUPE), 'rapports': '750 root:%s' % GROUPE, 'rapport': '640 root:%s' % GROUPE}[nature]
            mode_attendu, proprietaire = attendu.split()
            self.ajouter('S13', chemin, 'important', 'Droits trop larges sur %s' % chemin, EXPLICATIONS['S13'],
                         ['%s : %s' % (chemin, ' ; '.join(defauts)), 'Attendu : %s' % attendu],
                         ['sudo chown %s %s && sudo chmod %s %s' % (proprietaire, chemin, mode_attendu, chemin)], [chemin, droits, uid, gid])

    # ---------------------------------------------------------------------------------------------------------- configuration
    def _config(self):
        """Réglages facultatifs de l'administrateur (/etc/axiorhub-mail-agent/veille-securite.json) : programmes connus (S01) et
        chemins autorisés (S03). Absent : listes intégrées seulement."""
        if not hasattr(self, '_conf'):
            try:
                self._conf = json.loads(self.lire('/etc/axiorhub-mail-agent/veille-securite.json', 100_000) or '{}')
            except ValueError:
                self._conf = {}
            if not isinstance(self._conf, dict):
                self._conf = {}
        return self._conf

    def hote(self):
        charge = (self.lire('/proc/loadavg') or '').split()[:3]
        try:
            charge = [float(x) for x in charge]
        except ValueError:
            charge = []
        coeurs = sum(1 for l in (self.lire('/proc/cpuinfo', 2_000_000) or '').splitlines() if l.startswith('processor'))
        return {'charge': charge, 'coeurs': coeurs}


# -------------------------------------------------------------------------------------------------------------- rapport
def dossier_sortie(racine):
    return Path(RAPPORTS) if str(racine) in ('/', '') else Path(racine) / RAPPORTS.lstrip('/')


def precedent(sortie):
    """Dernier rapport (rapide ou complet) : état des empreintes (S06) et du processeur (S01)."""
    meilleur = {}
    for nom in ('dernier-rapide.json', 'dernier-complet.json'):
        try:
            p = sortie / nom
            if p.is_symlink():
                continue
            data = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get('fin', '')) > str(meilleur.get('fin', '')):
            meilleur = data
    return meilleur


def _ecrire(sortie, nom, texte, groupe_gid):
    fd, tmp = tempfile.mkstemp(prefix='.tmp-', suffix='.json', dir=str(sortie))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(texte)
        os.chmod(tmp, 0o640)
        if groupe_gid is not None:
            os.chown(tmp, 0, groupe_gid)
        os.replace(tmp, str(sortie / nom))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def passer(racine='/', passage='rapide', maintenant=None, ports=None, sonde=True, sortie=None):
    """Un passage complet : contrôles, rapport écrit dans ``sortie`` (seule écriture), historique de 30 jours. Renvoie le rapport."""
    debut = time.monotonic()
    t = float(maintenant if maintenant is not None else time.time())
    sortie = Path(sortie) if sortie else dossier_sortie(racine)
    if not sortie.is_dir():
        raise SystemExit('Dossier des rapports absent : %s (installation : --installer-auto)' % sortie)
    v = Veille(racine, t, ports, sonde, passage, precedent(sortie))
    v.executer(COMPLETS if passage == 'complet' else RAPIDES)
    if 'S06' not in v.controles:
        v.etat['empreintes'] = (v.precedent.get('etat') or {}).get('empreintes') or {}
    rapport = {'format': FORMAT, 'outil': 'veille-securite', 'version': VERSION, 'passage': passage, 'debut': _iso(t),
               'fin': _iso(t + (time.monotonic() - debut)), 'duree_ms': int((time.monotonic() - debut) * 1000), 'hote': v.hote(),
               'controles': v.controles, 'erreurs': v.erreurs, 'constats': v.constats, 'applications': v.applications,
               'reconnus': v.reconnus(),
               'table_versions': TABLE_VERSIONS_DATE, 'etat': v.etat}
    texte = json.dumps(rapport, ensure_ascii=False, indent=1, sort_keys=True)
    gid = None
    if not v.fausse and hasattr(os, 'geteuid') and os.geteuid() == 0:
        try:
            import grp
            gid = grp.getgrnam(GROUPE).gr_gid
        except (KeyError, ImportError):
            gid = None
    horodatage = datetime.fromtimestamp(t, timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    nom = 'rapport-%s-%s.json' % (horodatage, passage)
    _ecrire(sortie, nom, texte, gid)
    _ecrire(sortie, 'dernier-%s.json' % passage, texte, gid)
    limite = datetime.fromtimestamp(t, timezone.utc) - timedelta(days=HISTORIQUE_JOURS)
    for p in sortie.glob('rapport-*.json'):
        m = re.match(r'rapport-(\d{8}T\d{6}Z)-', p.name)
        if m and datetime.strptime(m.group(1), '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc) < limite and not p.is_symlink():
            p.unlink()
    return rapport


# -------------------------------------------------------------------------------------------------------------- installation
def _systemctl(journal, a_blanc, *args):
    journal.append('systemctl ' + ' '.join(args))
    if not a_blanc:
        subprocess.run(['systemctl', *args], check=False)


def _ids_service():
    try:
        import grp
        import pwd
        return pwd.getpwnam(GROUPE).pw_uid, grp.getgrnam(GROUPE).gr_gid
    except (ImportError, KeyError):
        return None, None


def installer_auto(racine='/', a_blanc=False, source=None):
    """Installe le collecteur (/usr/lib/axiorhub-pilote), ses unités systemd et ses dossiers, puis active les minuteurs (décision D1),
    sauf si l'administrateur a désactivé la veille. Appelé par le paquet Debian (postinst) et par install.sh. Renvoie le journal."""
    source = Path(source or __file__).resolve()
    deploy = source.parent.parent
    r = Path(racine)
    journal = []
    cible = r / INSTALLE.lstrip('/')
    cible.parent.mkdir(parents=True, exist_ok=True)
    tmp = cible.with_name('.veille-securite.tmp')
    tmp.write_bytes(source.read_bytes())
    os.chmod(tmp, 0o755)
    os.replace(tmp, cible)
    journal.append('collecteur installé : ' + INSTALLE)
    unites = r / 'etc/systemd/system'
    unites.mkdir(parents=True, exist_ok=True)
    for unite in UNITES:
        donnees = (deploy / unite).read_bytes()
        tmp = unites / ('.' + unite + '.tmp')
        tmp.write_bytes(donnees)
        os.chmod(tmp, 0o644)
        os.replace(tmp, unites / unite)
    journal.append('unités installées : ' + ', '.join(UNITES))
    uid, gid = (None, None) if a_blanc else _ids_service()
    for chemin, mode, proprietaire in ((RAPPORTS, 0o750, (0, gid)), (DEMANDES, 0o750, (uid, gid))):
        d = r / chemin.lstrip('/')
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, mode)
        if not a_blanc and hasattr(os, 'chown') and proprietaire[1] is not None:
            os.chown(d, proprietaire[0] if proprietaire[0] is not None else 0, proprietaire[1])
    journal.append('dossiers : %s (root:%s 750), %s (%s 750)' % (RAPPORTS, GROUPE, DEMANDES, GROUPE))
    _systemctl(journal, a_blanc, 'daemon-reload')
    _systemctl(journal, a_blanc, 'enable', '--now', CHEMIN_DEMANDE)
    if (r / RAPPORTS.lstrip('/') / 'desactivee').exists():
        journal.append('veille désactivée par l’administrateur : minuteurs laissés arrêtés')
    else:
        _systemctl(journal, a_blanc, 'enable', '--now', *TIMERS)
    return journal


def basculer(racine='/', action='desactiver', a_blanc=False):
    """Active ou désactive les minuteurs (root). La marque « desactivee » est respectée par les mises à jour suivantes."""
    r = Path(racine)
    journal = []
    marque = r / RAPPORTS.lstrip('/') / 'desactivee'
    if action == 'desactiver':
        _systemctl(journal, a_blanc, 'disable', '--now', *TIMERS)
        marque.parent.mkdir(parents=True, exist_ok=True)
        marque.write_text(_iso(time.time()) + '\n', encoding='utf-8')
        os.chmod(marque, 0o640)
    else:
        try:
            marque.unlink()
        except FileNotFoundError:
            pass
        _systemctl(journal, a_blanc, 'enable', '--now', *TIMERS)
    return journal


def appliquer_demande(racine='/', a_blanc=False):
    """Lancé par axiorhub-veille-securite-demande.service : ne fait confiance qu'au champ « action » (liste fermée) de la demande."""
    r = Path(racine)
    d = r / DEMANDES.lstrip('/')
    demande = d / 'demande.json'
    if not os.path.lexists(demande):
        return {'etat': 'aucune_demande'}
    resultat = {'etat': 'refusee', 'fait': _iso(time.time())}
    try:
        st = os.lstat(demande)
        uid, _ = (None, None) if a_blanc else _ids_service()
        if not stat.S_ISREG(st.st_mode) or st.st_size > 4096 or (uid is not None and st.st_uid != uid):
            raise ValueError('demande_refusee')
        fd = os.open(str(demande), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'r', encoding='utf-8') as f:
            data = json.loads(f.read(4096))
        action = data.get('action') if isinstance(data, dict) else None
        if action not in ('desactiver', 'activer'):
            raise ValueError('action_inconnue')
        resultat = {'etat': 'appliquee', 'action': action, 'fait': _iso(time.time()), 'demande': re.sub(r'[^A-Za-z0-9_.:-]', '', str(data.get('id', '')))[:80],
                    'journal': basculer(racine, action, a_blanc)}
    except (OSError, ValueError) as ex:
        resultat['motif'] = str(ex)[:80]
    finally:
        try:
            os.unlink(demande)
        except OSError:
            pass
    _, gid = (None, None) if a_blanc else _ids_service()
    _ecrire(d, 'resultat.json', json.dumps(resultat, ensure_ascii=False), gid)
    return resultat


def main(argv=None):
    p = argparse.ArgumentParser(description='Veille de sécurité du serveur AxiorHub (lecture seule).')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--rapide', action='store_true', help='processus, réseau, tâches planifiées, fichiers de démarrage (S01-S06)')
    g.add_argument('--complet', action='store_true', help='tous les contrôles (S01-S13)')
    g.add_argument('--installer-auto', action='store_true', help='installation et activation (paquet Debian, install.sh)')
    g.add_argument('--activer', action='store_true')
    g.add_argument('--desactiver', action='store_true')
    g.add_argument('--appliquer-demande', action='store_true')
    p.add_argument('--racine', default='/', help='racine observée (essais sans root : faux /proc, /etc, /var)')
    p.add_argument('--sortie', default='', help='dossier des rapports (par défaut : <racine>/var/lib/axiorhub-mail-agent/securite)')
    p.add_argument('--maintenant', type=float, default=None, help='instant du passage (essais)')
    p.add_argument('--port-http', type=int, default=None, help='essais : port local qui remplace le port 80 des sondes')
    p.add_argument('--port-https', type=int, default=None, help='essais : port local qui remplace le port 443 des sondes')
    p.add_argument('--sans-sonde', action='store_true', help='aucune requête HTTP, même vers 127.0.0.1')
    p.add_argument('--a-blanc', action='store_true', help='installation à blanc : systemctl et propriétaires seulement consignés')
    a = p.parse_args(argv)
    os.umask(0o027)
    if a.installer_auto or a.activer or a.desactiver or a.appliquer_demande:
        if not a.a_blanc and hasattr(os, 'geteuid') and os.geteuid() != 0:
            raise SystemExit('Exécuter avec sudo.')
        if a.installer_auto:
            try:
                journal = installer_auto(a.racine, a.a_blanc)
            except Exception as ex:   # noqa: BLE001 — l'installation d'AxiorHub ne doit jamais échouer pour cette raison
                print('Veille de sécurité non installée : %s' % str(ex)[:200])
                return 0
        elif a.appliquer_demande:
            journal = [json.dumps(appliquer_demande(a.racine, a.a_blanc), ensure_ascii=False)]
        else:
            journal = basculer(a.racine, 'activer' if a.activer else 'desactiver', a.a_blanc)
        for ligne in journal:
            print('veille-securite : ' + ligne)
        return 0
    ports = {k: v for k, v in ((80, a.port_http), (443, a.port_https)) if v}
    rapport = passer(a.racine, 'complet' if a.complet else 'rapide', a.maintenant, ports, not a.sans_sonde, a.sortie or None)
    niveaux = [c['niveau'] for c in rapport['constats']]
    print('veille-securite %s : %d critique(s), %d important(s), %d à surveiller, %d contrôle(s) en erreur, %d ms' % (
        rapport['passage'], niveaux.count('critique'), niveaux.count('important'), niveaux.count('surveiller'), len(rapport['erreurs']), rapport['duree_ms']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
