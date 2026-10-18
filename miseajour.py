#!/usr/bin/env python3
"""5.6.61 : mise à jour depuis la page des versions GitHub, à la demande de l'administrateur (version serveur).

Partage des rôles :
- l'interface (compte non privilégié ``axiorhub-mail``) vérifie la page des versions, affiche l'avis et dépose une DEMANDE
  (``/var/lib/axiorhub-mail-agent/maj/demande.json``) quand l'administrateur clique « Mettre à jour » ;
- ce script, lancé en root par ``axiorhub-mail-agent-maj.service`` (déclenché par l'apparition de la demande), ne fait confiance à
  aucun contenu de la demande sauf le numéro de version, qu'il revérifie lui-même sur GitHub ; il télécharge l'archive, son
  empreinte et sa SIGNATURE dans un dossier réservé à root, vérifie l'empreinte puis la signature SSH avec la clé publique du
  cabinet (``/etc/axiorhub-mail-agent/maj-signataires``), extrait l'archive sans lien ni chemin sortant, lance la mise à jour
  habituelle (``install.sh`` → ``upgrade.py``), contrôle que les services repartent, et revient à la version précédente sinon.
- Une archive non signée par la clé du cabinet est refusée : un compte GitHub piraté ne suffit pas à installer du code.

Commandes (root) :
  python3 miseajour.py --activer --cle "ssh-ed25519 AAAA… commentaire"   # installe le service et enregistre la clé publique
  python3 miseajour.py --desactiver                                         # retire le service (la clé reste)
  python3 miseajour.py --verifier                                           # dernière version publiée, sans rien installer
  python3 miseajour.py --executer                                           # traite la demande (lancé par systemd)
Bibliothèque standard seulement.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

FEED = 'https://github.com/Arthur-Llevelys/AxiorHub-Pilote/releases.atom'
DOWNLOAD = 'https://github.com/Arthur-Llevelys/AxiorHub-Pilote/releases/download/v{v}/{name}'
NAMESPACE = 'axiorhub-release'
PRINCIPAL = 'axiorhub-pilote'
STATE = Path('/var/lib/axiorhub-mail-agent')
REQUEST_DIR = STATE / 'maj'
REQUEST = REQUEST_DIR / 'demande.json'
RESULT = REQUEST_DIR / 'resultat.json'
SIGNERS = Path('/etc/axiorhub-mail-agent/maj-signataires')
WORK = Path('/var/cache/axiorhub-mail-agent-maj')
BASE = Path('/opt/axiorhub-mail-agent')
SYSTEMD = Path('/etc/systemd/system')
UNITS = ('axiorhub-mail-agent-maj.path', 'axiorhub-mail-agent-maj.service')
CHECK_UNITS = ('axiorhub-mail-ui.service', 'axiorhub-mail-desk-worker.service')
VERSION_RX = re.compile(r'^\d+\.\d+\.\d+$')
KEY_RX = re.compile(r'^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]{40,} ?[^\n]{0,80}$')
MAX_BYTES = 200 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def version_tuple(v):
    return tuple(int(x) for x in v.split('.'))


def current_version(base=BASE):
    try:
        return (base / 'current').resolve().name
    except OSError:
        return ''


def fetch(url, limit=MAX_BYTES, timeout=60):
    if not url.startswith('https://github.com/') and not url.startswith('https://objects.githubusercontent.com/'):
        raise RuntimeError('adresse_refusee')
    req = urllib.request.Request(url, headers={'User-Agent': 'AxiorHub-Pilote-maj'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # redirections GitHub vers son stockage : https uniquement
        if not resp.geturl().startswith('https://'):
            raise RuntimeError('redirection_non_https')
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError('fichier_trop_volumineux')
    return data


def latest(feed_bytes):
    """Dernière version publiée d'après le flux Atom des versions (balises ``vX.Y.Z``)."""
    tags = re.findall(r'<title>v(\d+\.\d+\.\d+)</title>', feed_bytes.decode('utf-8', 'replace'))
    return max(tags, key=version_tuple) if tags else ''


def verify_signature(path, sig, signers=None):
    signers = signers or SIGNERS
    if not signers.is_file() or not signers.read_text(encoding='utf-8').strip():
        raise RuntimeError('cle_de_signature_absente')
    with open(path, 'rb') as data:
        out = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(signers), '-I', PRINCIPAL, '-n', NAMESPACE, '-s', str(sig)],
                             stdin=data, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError('signature_invalide')


def safe_extract(archive, dest):
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        for m in members:
            name = Path(m.name)
            if name.is_absolute() or '..' in name.parts or not (m.isfile() or m.isdir()):
                raise RuntimeError('archive_refusee:' + m.name[:80])
        if hasattr(tarfile, 'data_filter'):
            tar.extractall(dest, members=members, filter='data')
        else:
            tar.extractall(dest, members=members)
    roots = [p for p in Path(dest).iterdir() if p.is_dir()]
    if len(roots) != 1 or not (roots[0] / 'install.sh').is_file():
        raise RuntimeError('archive_inattendue')
    return roots[0]


def write_result(data):
    """Le dossier de la demande appartient au compte de service : fichier temporaire créé de façon exclusive (jamais un lien préparé
    à l'avance), puis renommé ; le renommage remplace un éventuel lien sans le suivre."""
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.resultat-', dir=str(REQUEST_DIR))
    try:
        st = REQUEST_DIR.stat()
        os.fchown(fd, st.st_uid, st.st_gid)
        os.fchmod(fd, 0o640)
    except (OSError, AttributeError):   # AttributeError : plateforme sans fchown (essais sous Windows)
        pass
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, RESULT)


def services_ok(wait=25):
    time.sleep(wait)
    return all(subprocess.run(['systemctl', 'is-active', '--quiet', u]).returncode == 0 for u in CHECK_UNITS)


def execute():
    """Traite la demande déposée par l'interface. Ne renvoie jamais d'exception non rapportée."""
    if not REQUEST.is_file() or REQUEST.is_symlink():
        try:
            REQUEST.unlink()
        except OSError:
            pass
        return 0
    started = now()
    before = current_version()
    try:
        fd = os.open(str(REQUEST), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'r', encoding='utf-8') as f:
            raw = json.loads(f.read(2000))
        wanted = str(raw.get('version') or '')
        by = re.sub(r'[^\w@.\-]', '', str(raw.get('par') or ''))[:80]
    except (OSError, ValueError, AttributeError):
        wanted, by = '', ''
    finally:
        try:
            REQUEST.unlink()
        except OSError:
            pass
    report = {'demande_par': by, 'debut': started, 'depuis': before, 'vers': wanted, 'etat': 'echec', 'etapes': []}

    def step(text):
        report['etapes'].append('%s %s' % (now()[11:19], text))
        write_result({**report, 'etat': 'en_cours'})
    try:
        if not VERSION_RX.match(wanted):
            raise RuntimeError('version_demandee_invalide')
        step('Vérification de la dernière version publiée')
        published = latest(fetch(FEED, 2_000_000))
        if wanted != published:
            raise RuntimeError('version_differente_de_la_derniere_publiee:%s' % published)
        if before and VERSION_RX.match(before) and version_tuple(wanted) <= version_tuple(before):
            raise RuntimeError('version_deja_installee_ou_plus_ancienne')
        if WORK.exists():
            shutil.rmtree(WORK)
        WORK.mkdir(mode=0o700, parents=True)
        name = 'axiorhub-mail-agent-%s.tar.gz' % wanted
        step('Téléchargement de l’archive, de son empreinte et de sa signature')
        archive, sha, sig = WORK / name, WORK / (name + '.sha256'), WORK / (name + '.sig')
        archive.write_bytes(fetch(DOWNLOAD.format(v=wanted, name=name)))
        sha.write_bytes(fetch(DOWNLOAD.format(v=wanted, name=name + '.sha256'), 10_000))
        sig.write_bytes(fetch(DOWNLOAD.format(v=wanted, name=name + '.sig'), 20_000))
        expected = sha.read_text(encoding='utf-8').split()[0].lower()
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError('empreinte_differente')
        step('Empreinte SHA-256 conforme ; vérification de la signature')
        verify_signature(archive, sig)
        step('Signature du cabinet vérifiée ; extraction')
        source = safe_extract(archive, WORK / 'source')
        step('Installation (arrêt des services, migration, redémarrage)')
        out = subprocess.run(['bash', 'install.sh'], cwd=str(source), capture_output=True, text=True, timeout=2700)
        report['journal'] = (out.stdout + out.stderr)[-3000:]
        if out.returncode != 0:
            raise RuntimeError('installation_echouee')
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        for unit in ('axiorhub-mail-ui.service', 'axiorhub-mail-desk-worker.service', 'axiorhub-mail-watch.service'):
            subprocess.run(['systemctl', 'restart', unit], check=False)
        step('Contrôle des services')
        if not services_ok():
            step('Services arrêtés après la mise à jour : retour à la version précédente')
            rb = subprocess.run(['python3', str(BASE / 'current' / 'upgrade.py'), '--rollback'], capture_output=True, text=True, timeout=1800)
            report['retour_arriere'] = (rb.stdout + rb.stderr)[-1500:]
            raise RuntimeError('services_arretes_retour_arriere_effectue' if rb.returncode == 0 else 'services_arretes_retour_arriere_echoue')
        report.update({'etat': 'reussie', 'fin': now(), 'apres': current_version()})
        step('Mise à jour terminée')
        write_result(report)
        return 0
    except Exception as exc:   # noqa: BLE001 — tout échec est rapporté à l'interface, sans secret
        report.update({'etat': 'echec', 'fin': now(), 'motif': str(exc)[:200], 'apres': current_version()})
        write_result(report)
        return 1
    finally:
        if WORK.exists():
            shutil.rmtree(WORK, ignore_errors=True)


MANAGED = '# géré par AxiorHub : clé livrée avec la version installée (remplacée à chaque mise à jour)\n'


def _shipped_key(release):
    path = release / 'agent' / 'data' / 'maj-signataires'
    try:
        lines = [l for l in path.read_text(encoding='utf-8').splitlines() if l.strip() and not l.lstrip().startswith('#')]
    except OSError:
        return ''
    return '\n'.join(lines) + '\n' if lines else ''


def activate(key, release, auto=False):
    """Installe et active le service de mise à jour. Clé : celle donnée (--cle), sinon celle livrée avec AxiorHub ; une clé posée par
    l'administrateur n'est jamais remplacée par la clé livrée. ``auto`` : appel par install.sh, silencieux et jamais bloquant."""
    if os.geteuid() != 0:
        raise SystemExit('Exécuter avec sudo.')
    key = ' '.join(str(key or '').split())
    if key:
        if not KEY_RX.match(key):
            raise SystemExit('Clé publique SSH invalide (attendu : « ssh-ed25519 AAAA… »).')
        SIGNERS.write_text('%s namespaces="%s" %s\n' % (PRINCIPAL, NAMESPACE, key), encoding='utf-8')
        os.chmod(SIGNERS, 0o644)
    else:
        managed = not SIGNERS.is_file() or SIGNERS.read_text(encoding='utf-8').startswith(MANAGED)
        shipped = _shipped_key(release)
        if managed and shipped:
            SIGNERS.write_text(MANAGED + shipped, encoding='utf-8')
            os.chmod(SIGNERS, 0o644)
        elif not SIGNERS.is_file() and not auto:
            raise SystemExit('Aucune clé de signature : relancer avec --cle "ssh-ed25519 AAAA…".')
    for unit in UNITS:
        shutil.copyfile(release / 'deploy' / unit, SYSTEMD / unit)
        os.chmod(SYSTEMD / unit, 0o644)
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    import grp
    import pwd
    os.chown(REQUEST_DIR, pwd.getpwnam('axiorhub-mail').pw_uid, grp.getgrnam('axiorhub-mail').gr_gid)
    os.chmod(REQUEST_DIR, 0o750)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', UNITS[0]], check=True)
    if not auto:
        print('Mises à jour depuis l’interface activées. Clé de signature : %s' % SIGNERS)


def deactivate():
    if os.geteuid() != 0:
        raise SystemExit('Exécuter avec sudo.')
    subprocess.run(['systemctl', 'disable', '--now', UNITS[0]], check=False)
    for unit in UNITS:
        try:
            (SYSTEMD / unit).unlink()
        except OSError:
            pass
    subprocess.run(['systemctl', 'daemon-reload'], check=False)
    print('Mises à jour depuis l’interface désactivées (la clé de signature est conservée).')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Mise à jour d’AxiorHub Pilote depuis la page des versions GitHub (signée).')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--activer', action='store_true')
    g.add_argument('--desactiver', action='store_true')
    g.add_argument('--verifier', action='store_true')
    g.add_argument('--executer', action='store_true')
    g.add_argument('--installer-auto', action='store_true')   # appelé par install.sh : activation sans ligne de commande
    p.add_argument('--cle', default='')
    a = p.parse_args()
    os.umask(0o077)
    if a.activer:
        activate(a.cle, Path(__file__).resolve().parent)
    elif a.installer_auto:
        try:
            activate('', Path(__file__).resolve().parent, auto=True)
        except Exception as exc:   # noqa: BLE001 — l'installation d'AxiorHub ne doit jamais échouer pour cette raison
            print('Service de mise à jour non activé : %s' % str(exc)[:200])
    elif a.desactiver:
        deactivate()
    elif a.verifier:
        print('Version installée : %s ; dernière version publiée : %s' % (current_version(), latest(fetch(FEED, 2_000_000)) or 'inconnue'))
    else:
        sys.exit(execute())
