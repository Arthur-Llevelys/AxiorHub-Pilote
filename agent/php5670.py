"""5.6.70 : contrôle des correctifs PHP « PHP2Shell » (CVE-2026-109618, CVE-2026-109619) du 21 octobre 2026.

AxiorHub n'utilise pas PHP, mais le serveur qui l'héberge sert souvent Nextcloud (PHP) : deux failles critiques permettent d'y exécuter
du code à distance sans authentification (exploitées dès le 21 octobre). Versions corrigées : 8.5.12, 8.4.27, 8.3.36, 8.2.35 ; les
distributions (Ubuntu, Debian) corrigent souvent sans changer la version amont : le journal du paquet fait alors foi.

Le contrôle lit seulement : version de « php » sur la machine et journal des paquets ; il n'installe rien (voir
``deploy/securite/durcir-serveur.sh`` pour appliquer). Un PHP dans un conteneur Docker (image Nextcloud) n'est pas visible d'ici : le
script de durcissement le signale.
"""
import glob
import gzip
import re
import shutil
import subprocess
import time

FIXED = {(8, 5): (8, 5, 12), (8, 4): (8, 4, 27), (8, 3): (8, 3, 36), (8, 2): (8, 2, 35)}
CVES = ('CVE-2026-109618', 'CVE-2026-109619')
_CACHE = {}
TTL = 3600


def _version(binary, runner=subprocess.run):
    try:
        out = runner([binary, '-r', 'echo PHP_VERSION;'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ''
    return (out.stdout or '').strip()[:40]


def _packaged_fix(major, minor, doc_root='/usr/share/doc'):
    """Vrai si le journal du paquet de la distribution mentionne les deux CVE (correctif rétroporté)."""
    for path in glob.glob('%s/php%d.%d-common/changelog.Debian.gz' % (doc_root, major, minor)) + \
            glob.glob('%s/php%d.%d-cli/changelog.Debian.gz' % (doc_root, major, minor)):
        try:
            with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as f:
                text = f.read(4_000_000)
        except OSError:
            continue
        if all(c in text for c in CVES):
            return True
    return False


def assess(version, packaged=False):
    """État d'une version : 'corrige', 'vulnerable' ou 'inconnu', avec la version corrigée de la branche."""
    m = re.match(r'(\d+)\.(\d+)\.(\d+)', str(version or ''))
    if not m:
        return {'etat': 'inconnu', 'corrigee': ''}
    v = tuple(int(x) for x in m.groups())
    fixed = FIXED.get(v[:2])
    if packaged:
        return {'etat': 'corrige', 'corrigee': '.'.join(map(str, fixed)) if fixed else '', 'source': 'paquet de la distribution'}
    if fixed and v >= fixed:
        return {'etat': 'corrige', 'corrigee': '.'.join(map(str, fixed)), 'source': 'version'}
    return {'etat': 'vulnerable', 'corrigee': '.'.join(map(str, fixed)) if fixed else '8.4.27 ou plus récent (branche non maintenue)'}


def host_status(runner=subprocess.run, which=shutil.which, doc_root='/usr/share/doc', now=None):
    """PHP de la machine (mis en cache une heure) : {'present', 'version', 'etat', 'corrigee', 'source'}."""
    now = now or time.time()
    key = (doc_root,)
    if key in _CACHE and now - _CACHE[key][0] < TTL:
        return _CACHE[key][1]
    binary = which('php')
    if not binary:
        candidates = sorted(glob.glob('/usr/bin/php[0-9].[0-9]*'))
        binary = candidates[-1] if candidates else ''
    if not binary:
        result = {'present': False}
    else:
        version = _version(binary, runner)
        m = re.match(r'(\d+)\.(\d+)', version)
        packaged = bool(m) and _packaged_fix(int(m.group(1)), int(m.group(2)), doc_root)
        result = {'present': True, 'version': version, **assess(version, packaged)}
    _CACHE[key] = (now, result)
    return result


def health_item(status):
    """Point à vérifier pour « Aujourd'hui » quand PHP n'est pas corrigé ; sinon None."""
    if not status.get('present') or status.get('etat') != 'vulnerable':
        return None
    return {'id': 'php2shell', 'level': 'attention',
            'text': 'PHP %s de ce serveur n’est pas corrigé contre les failles critiques « PHP2Shell » (CVE-2026-109618 et CVE-2026-109619, '
                    'exploitées depuis le 21 octobre 2026) : mettez PHP à jour (%s) puis redémarrez PHP-FPM ou Apache.' % (status.get('version'), status.get('corrigee')),
            'actions': [{'href': 'parametres?rubrique=cabinet#php5670', 'label': 'Comment corriger'}]}


def settings_html(status):
    from html import escape as e
    if not status.get('present'):
        state = ('<p>PHP n’est pas installé sur la machine d’AxiorHub. Si Nextcloud tourne dans Docker, mettez à jour son image '
                 '(<code>docker compose pull &amp;&amp; docker compose up -d</code>) : le script ci-dessous le vérifie.</p>')
    elif status.get('etat') == 'corrige':
        state = '<p class="ok">PHP %s : corrigé (%s).</p>' % (e(status.get('version', '')), e(status.get('source', '')))
    elif status.get('etat') == 'vulnerable':
        state = ('<p class="ko"><strong>PHP %s n’est pas corrigé.</strong> Version corrigée de sa branche : %s. Mettez à jour puis redémarrez '
                 'PHP-FPM ou Apache, sinon l’ancien code reste chargé.</p>') % (e(status.get('version', '')), e(status.get('corrigee', '')))
    else:
        state = '<p>Version de PHP illisible.</p>'
    return ('<section id="php5670"><h2>Correctifs PHP (PHP2Shell, 21 octobre 2026)</h2>%s<p class="ax-muted">Sur le serveur, en administrateur : '
            '<code>sudo bash deploy/securite/durcir-serveur.sh</code> établit le bilan (PHP de la machine et des conteneurs, pare-feu, '
            'fail2ban, mises à jour automatiques) sans rien changer ; <code>--appliquer</code> installe les correctifs et la protection '
            'contre les tentatives d’accès.</p></section>') % state
