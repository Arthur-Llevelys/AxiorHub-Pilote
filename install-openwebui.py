#!/usr/bin/env python3
"""5.6.112 : installer ou mettre à jour l'Open WebUI du cabinet sur le serveur AxiorHub (Docker), en root.

    sudo python3 install-openwebui.py --nom ia.cabinet.example          # installation, publication HTTPS par Apache
    sudo python3 install-openwebui.py --mettre-a-jour                    # sauvegarde des données, puis image de cette version d'AxiorHub

- Image officielle FIGÉE par son empreinte (``IMAGE``, Open WebUI 0.13.5, essayée avec AxiorHub 5.6.112) ; téléchargée seulement si elle
  manque ; lancement avec ``--pull never``. Une mise à jour d'AxiorHub peut apporter une nouvelle empreinte ; ``--mettre-a-jour`` la pose.
- Conteneur ``axiorhub-openwebui`` (étiquette ``axiorhub=openwebui``), données dans le volume ``axiorhub-openwebui``, écoute sur
  127.0.0.1 seulement (port 3000, sinon le premier libre jusqu'à 3019), ``host.docker.internal`` vers ce serveur (Ollama local).
  Un conteneur du même nom sans l'étiquette n'est jamais touché.
- Fichier d'environnement privé ``/etc/axiorhub-mail-agent/openwebui.env`` (600) : secret de session (fabriqué une fois, gardé ensuite),
  ``CONTENT_SECURITY_POLICY`` autorisant l'affichage dans AxiorHub (origine de ``ui-auth.json``) et nulle part ailleurs, ``WEBUI_URL``,
  ``DEFAULT_LOCALE=fr-FR``, ``ENABLE_API_KEYS=true`` (clés d'API pour « Conversations IA » et le réglage automatique).
- Apache : site ``axiorhub-openwebui`` (HTTPS, WebSocket) pour le nom donné, avec le certificat Let's Encrypt de ce nom s'il existe ; sinon
  la commande certbot à lancer est donnée et rien n'est écrit. Un nom déjà servi par un autre site d'Apache n'est pas modifié.
- Mise à jour : sauvegarde du volume (``/var/backups/axiorhub-openwebui``), nouveau conteneur ; s'il ne répond pas, l'ancien est remis.
Installation neuve, clé en main : le compte administrateur d'Open WebUI (premier compte) et sa clé d'API sont créés par ce script
(mot de passe initial affiché une fois), puis Open WebUI est réglé d'après AxiorHub (``configure-openwebui.py``) et relié à AxiorHub, qui
crée ensuite le compte de chacun et reporte ses réglages quand ils changent.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEPOT = 'ghcr.io/open-webui/open-webui'
EMPREINTE = 'sha256:e0dc5a8bf5c71cadf339d1e7b9408b357da75fb2854f82cad57a1609fadf2a22'   # v0.13.5
VERSION_OPENWEBUI = '0.13.5'
IMAGE = DEPOT + '@' + EMPREINTE
NOM = 'axiorhub-openwebui'
ETIQUETTE = 'axiorhub=openwebui'
VOLUME = 'axiorhub-openwebui'
PORTS = tuple(range(3000, 3020))
AUTH = Path('/etc/axiorhub-mail-agent/ui-auth.json')
ENV = Path('/etc/axiorhub-mail-agent/openwebui.env')
ETAT = Path('/etc/axiorhub-mail-agent/openwebui.json')
SAUVEGARDES = Path('/var/backups/axiorhub-openwebui')
SITE = Path('/etc/apache2/sites-available/axiorhub-openwebui.conf')
MARQUE = '# AxiorHub Pilote — Open WebUI du cabinet (install-openwebui.py).'
DELAI_DEMARRAGE = 420


def lancer(args, delai=600, verifier=True):
    """Toute commande passe par ici (arguments en liste, délai) ; remplacée dans les essais."""
    r = subprocess.run(args, capture_output=True, text=True, timeout=delai)
    if verifier and r.returncode:
        raise RuntimeError('%s : %s' % (' '.join(args[:3]), (r.stderr or r.stdout).strip()[-300:]))
    return r


def conteneur():
    """(id, étiquette présente, image, port) du conteneur ``axiorhub-openwebui``, ou ``None``."""
    r = lancer(['docker', 'container', 'inspect', NOM], delai=30, verifier=False)
    if r.returncode:
        return None
    data = json.loads(r.stdout)[0]
    labels = (data.get('Config') or {}).get('Labels') or {}
    ports = ((data.get('HostConfig') or {}).get('PortBindings') or {}).get('8080/tcp') or [{}]
    return {'id': data['Id'], 'nous': labels.get('axiorhub') == 'openwebui', 'image': (data.get('Config') or {}).get('Image', ''),
            'image_id': data.get('Image', ''),
            'port': int(ports[0].get('HostPort') or 0), 'marche': bool((data.get('State') or {}).get('Running'))}


def port_libre(ports=PORTS):
    for port in ports:
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError('Aucun port libre entre %d et %d sur 127.0.0.1.' % (ports[0], ports[-1]))


def contenu_env(secret, origine_axiorhub, url_publique):
    if not re.fullmatch(r'https://[a-z0-9.\-]+(:\d+)?', origine_axiorhub or ''):
        raise RuntimeError('Origine d’AxiorHub illisible dans ui-auth.json.')
    lignes = ['WEBUI_SECRET_KEY=' + secret,
              "CONTENT_SECURITY_POLICY=frame-ancestors 'self' " + origine_axiorhub,
              'DEFAULT_LOCALE=fr-FR', 'ENABLE_API_KEYS=true']
    if url_publique:
        lignes.append('WEBUI_URL=' + url_publique)
    return '\n'.join(lignes) + '\n'


def secret_existant(path=None):
    path = path or ENV
    if path.is_file():
        for ligne in path.read_text(encoding='utf-8').splitlines():
            if ligne.startswith('WEBUI_SECRET_KEY=') and len(ligne) > 40:
                return ligne.split('=', 1)[1]
    return secrets.token_urlsafe(48)


def ecrire_prive(path, texte):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(texte)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def arguments_run(port, image=None, env=None):
    image, env = image or IMAGE, env or ENV
    return ['docker', 'run', '-d', '--name', NOM, '--label', ETIQUETTE, '--restart', 'unless-stopped', '--pull', 'never',
            '-p', '127.0.0.1:%d:8080' % port, '--add-host', 'host.docker.internal:host-gateway',
            '-v', VOLUME + ':/app/backend/data', '--env-file', str(env), image]


def sain(port, delai=DELAI_DEMARRAGE, pause=5, ouvrir=urllib.request.urlopen):
    fin = time.time() + delai
    while time.time() < fin:
        try:
            with ouvrir('http://127.0.0.1:%d/health' % port, timeout=10) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(pause)
    return False


def image_presente(image=None):
    return lancer(['docker', 'image', 'inspect', image or IMAGE], delai=30, verifier=False).returncode == 0


def image_id(image=None):
    r = lancer(['docker', 'image', 'inspect', image or IMAGE, '--format', '{{.Id}}'], delai=30, verifier=False)
    return r.stdout.strip() if r.returncode == 0 else ''


def site_apache(nom, port, certificat, cle):
    if not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}', nom or ''):
        raise RuntimeError('Nom de domaine invalide.')
    return f'''{MARQUE} Ne pas modifier : relancer le script.
<VirtualHost *:443>
    ServerName {nom}
    SSLEngine on
    SSLCertificateFile {certificat}
    SSLCertificateKeyFile {cle}
    ProxyRequests Off
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"
    # WebSocket (conversations en direct d'Open WebUI) : Apache 2.4.47 ou plus
    ProxyPass "/" "http://127.0.0.1:{port}/" upgrade=websocket connectiontimeout=5 timeout=600
    ProxyPassReverse "/" "http://127.0.0.1:{port}/"
</VirtualHost>
'''


def publier(nom, port, sortie=print):
    """Site Apache du nom donné ; rien n'est écrit si le nom est déjà servi par un autre site ou si son certificat manque."""
    certificat, cle = Path('/etc/letsencrypt/live/%s/fullchain.pem' % nom), Path('/etc/letsencrypt/live/%s/privkey.pem' % nom)
    ancien = SITE.read_text(encoding='utf-8') if SITE.exists() else None
    if ancien is not None and not ancien.startswith(MARQUE):
        raise RuntimeError('%s existe et n’a pas été écrit par AxiorHub : arrêt sans écrasement.' % SITE)
    hotes = lancer(['apache2ctl', '-S'], delai=30, verifier=False)
    if ancien is None and nom in (hotes.stdout + hotes.stderr):
        sortie('Le nom %s est déjà servi par un site d’Apache : il n’est pas modifié. Faites-y suivre « / » vers http://127.0.0.1:%d/ '
               '(avec upgrade=websocket).' % (nom, port))
        return False
    if not certificat.is_file() or not cle.is_file():
        sortie('Certificat absent pour %s. Lancez : sudo certbot certonly --apache -d %s , puis relancez ce script.' % (nom, nom))
        return False
    texte = site_apache(nom, port, certificat, cle)
    if ancien == texte:
        return True
    lancer(['a2enmod', 'ssl', 'proxy', 'proxy_http', 'proxy_wstunnel', 'headers'], delai=60)
    SITE.write_text(texte, encoding='utf-8')
    os.chmod(SITE, 0o644)
    lancer(['a2ensite', 'axiorhub-openwebui'], delai=60)
    if lancer(['apache2ctl', 'configtest'], delai=60, verifier=False).returncode:
        if ancien is None:
            lancer(['a2dissite', 'axiorhub-openwebui'], delai=60, verifier=False)
            SITE.unlink()
        else:
            SITE.write_text(ancien, encoding='utf-8')
        raise RuntimeError('Test Apache échoué : site Open WebUI retiré, Apache non rechargé.')
    lancer(['systemctl', 'reload', 'apache2'], delai=60)
    return True


def sauvegarder(c, sortie=print):
    SAUVEGARDES.mkdir(parents=True, exist_ok=True, mode=0o700)
    nom = 'openwebui-%s.tar.gz' % datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lancer(['docker', 'run', '--rm', '--pull', 'never', '-v', VOLUME + ':/donnees:ro', '-v', '%s:/sauvegarde' % SAUVEGARDES,
            '--entrypoint', 'tar', c['image_id'] or c['image'], 'czf', '/sauvegarde/' + nom, '-C', '/donnees', '.'], delai=3600)
    sortie('Données d’Open WebUI sauvegardées : %s' % (SAUVEGARDES / nom))
    return SAUVEGARDES / nom


def premier_administrateur(port, courriel, ouvrir=None):
    """Instance neuve : premier compte (administrateur d'Open WebUI) créé par l'installation, avec un mot de passe fabriqué, puis sa clé
    d'API (création réservée à une session). Rend ``{courriel, mot_de_passe, cle}`` ou ``None`` si un compte existe déjà."""
    ouvrir = ouvrir or urllib.request.urlopen
    base = 'http://127.0.0.1:%d' % port

    def appel(methode, chemin, corps=None, jeton=''):
        entetes = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if jeton:
            entetes['Authorization'] = 'Bearer ' + jeton
        req = urllib.request.Request(base + chemin, method=methode, headers=entetes,
                                     data=json.dumps(corps).encode('utf-8') if corps is not None else None)
        try:
            with ouvrir(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b'null')
        except urllib.error.HTTPError as ex:
            return ex.code, None
    mot = secrets.token_urlsafe(14)
    statut, compte = appel('POST', '/api/v1/auths/signup', {'name': 'Administrateur', 'email': courriel, 'password': mot})
    if statut != 200 or not isinstance(compte, dict) or compte.get('role') != 'admin' or not compte.get('token'):
        return None
    statut, cle = appel('POST', '/api/v1/auths/api_key', None, compte['token'])
    if statut != 200 or not isinstance(cle, dict) or not cle.get('api_key'):
        raise RuntimeError('Clé d’API de l’administrateur refusée par Open WebUI.')
    return {'courriel': courriel, 'mot_de_passe': mot, 'cle': cle['api_key']}


def main(argv=None, sortie=print, racine=None):
    p = argparse.ArgumentParser(description='Installer ou mettre à jour Open WebUI sur le serveur AxiorHub (Docker)')
    p.add_argument('--nom', default='', help='nom HTTPS d’Open WebUI (ex. ia.cabinet.example)')
    p.add_argument('--mettre-a-jour', action='store_true')
    p.add_argument('--sans-apache', action='store_true')
    p.add_argument('--courriel', default='', help='adresse du compte administrateur d’Open WebUI (défaut : admin@<nom>)')
    p.add_argument('--oui', action='store_true', help='réglage sans question (aucune clé de fournisseur transmise)')
    p.add_argument('--sans-reglage', action='store_true')
    args = p.parse_args(argv)
    if (os.geteuid() if racine is None else racine) != 0:
        raise RuntimeError('Exécuter avec sudo python3 install-openwebui.py')
    if lancer(['docker', 'version', '--format', '{{.Server.Version}}'], delai=30, verifier=False).returncode:
        raise RuntimeError('Docker absent ou arrêté. Ubuntu : sudo apt install -y docker.io , puis relancez.')
    auth = json.loads(AUTH.read_text(encoding='utf-8'))
    etat = json.loads(ETAT.read_text(encoding='utf-8')) if ETAT.exists() else {}
    nom = args.nom or etat.get('nom', '')
    c = conteneur()
    if c and not c['nous']:
        raise RuntimeError('Un conteneur « %s » existe sans l’étiquette d’AxiorHub : il n’est pas touché. Renommez-le ou retirez-le.' % NOM)
    if c and not args.mettre_a_jour:
        sortie('Open WebUI est déjà installé (port %d). Pour poser la version de cette mise à jour d’AxiorHub : --mettre-a-jour.' % c['port'])
        if nom and not args.sans_apache:
            publier(nom, c['port'], sortie)
        return 0
    if args.mettre_a_jour and not c:
        raise RuntimeError('Open WebUI n’est pas installé par AxiorHub sur ce serveur : lancez d’abord l’installation.')
    if not image_presente():
        sortie('Téléchargement de l’image Open WebUI %s (plusieurs gigaoctets)…' % VERSION_OPENWEBUI)
        lancer(['docker', 'pull', IMAGE], delai=7200)
    ecrire_prive(ENV, contenu_env(secret_existant(), auth['origin'], ('https://' + nom) if nom else ''))
    if c:
        if c['image_id'] and c['image_id'] == image_id() and c['marche']:   # même image (comparée par identifiant)
            sortie('Open WebUI est déjà à la version %s.' % VERSION_OPENWEBUI)
            return 0
        sauvegarder(c, sortie)
        port = c['port']
        lancer(['docker', 'stop', c['id']], delai=120)
        lancer(['docker', 'rename', c['id'], NOM + '-precedent'], delai=60)
    else:
        port = port_libre()
    lancer(arguments_run(port), delai=300)
    if not sain(port):
        if c:   # retour à l'ancien conteneur, données intactes (même volume)
            nouveau = conteneur()
            if nouveau:
                lancer(['docker', 'rm', '-f', nouveau['id']], delai=120, verifier=False)
            lancer(['docker', 'rename', c['id'], NOM], delai=60)
            lancer(['docker', 'start', c['id']], delai=120)
            raise RuntimeError('La nouvelle version ne répond pas : l’ancienne est remise en service.')
        raise RuntimeError('Open WebUI ne répond pas après %d s : docker logs %s' % (DELAI_DEMARRAGE, NOM))
    if c:
        lancer(['docker', 'rm', c['id']], delai=120, verifier=False)
    ETAT.write_text(json.dumps({'nom': nom, 'port': port, 'image': IMAGE, 'version': VERSION_OPENWEBUI,
                                'installe': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + '\n', encoding='utf-8')
    os.chmod(ETAT, 0o600)
    sortie('Open WebUI %s en service sur http://127.0.0.1:%d/.' % (VERSION_OPENWEBUI, port))
    publie = publier(nom, port, sortie) if (nom and not args.sans_apache) else False
    if publie:
        sortie('Adresse : https://%s/' % nom)
    if not c:   # installation neuve : administrateur et clé créés ici, puis réglage complet d'après AxiorHub (clé en main)
        url = ('https://' + nom) if publie else 'http://127.0.0.1:%d' % port
        compte = premier_administrateur(port, args.courriel or ('admin@' + nom if nom else 'admin@axiorhub.invalid'))
        if compte is None:
            sortie('Un compte existe déjà dans cette Open WebUI : lancez sudo axiorhub-mail openwebui avec la clé d’API d’un administrateur.')
            return 0
        sortie('Administrateur d’Open WebUI créé : %s, mot de passe initial %s — notez-le, il ne sera plus affiché ; changez-le dans '
               'Open WebUI (Réglages › Compte).' % (compte['courriel'], compte['mot_de_passe']))
        if args.sans_reglage:
            return 0
        import importlib.util
        spec = importlib.util.spec_from_file_location('configure_openwebui', str(Path(__file__).resolve().parent / 'configure-openwebui.py'))
        cfgmod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cfgmod)
        return cfgmod.main(['--url', url] + (['--oui'] if args.oui else []), sortie=sortie, racine=0, cle=compte['cle'])
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired) as ex:
        sys.exit(str(ex))
