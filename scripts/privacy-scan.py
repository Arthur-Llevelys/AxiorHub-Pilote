#!/usr/bin/env python3
"""Bloque une publication lorsque le dépôt contient des données personnelles, des dossiers de clients ou des secrets.

  python3 scripts/privacy-scan.py            # tout le dépôt (code, documentation, modèles .docx/.odt et leurs métadonnées)

Contrôles :
- identité de l'auteur hors des fichiers d'attribution autorisés (NOTICE, AUTHORS.md, README.md, page « À propos »…) ;
- domaines, adresses électroniques et numéros de téléphone réels (seuls les domaines d'exemple sont admis) ;
- chemins de serveur, adresses IP écrites en dur ;
- clés privées et clés d'API ;
- liste personnelle facultative ``.privacy-denylist`` (un terme par ligne : noms de clients, adversaires, numéros de dossier),
  jamais publiée (voir .gitignore), pour vérifier qu'aucun dossier réel n'a été copié dans le code.
"""
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {'.git', 'data', '__pycache__', '.pytest_cache', 'dist', 'build', '_vendor'}  # _vendor : bibliothèques tierces (auteurs publics)
EXCLUDED_FILES = {'scripts/privacy-scan.py', '.privacy-denylist', 'MANIFEST.sha256', 'SBOM.cdx.json'}
BINARY = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.zip', '.gz', '.tgz', '.woff', '.woff2', '.ttf', '.sqlite3'}
OFFICE = {'.docx', '.xlsx', '.pptx', '.odt', '.ods'}
# L'auteur est nommé volontairement (attribution, licence AGPL §7 b) dans ces fichiers seulement.
AUTHOR_FILES = {'NOTICE', 'AUTHORS.md', 'README.md', 'CHANGELOG.md', 'LOGO-LICENSE.md', 'TRADEMARKS.md', 'SECURITY.md', 'CONTRIBUTING.md',
                'agent/about560.py', 'agent/standalone_auth.py', 'tests/test_v560.py', 'docker/Dockerfile'}
AUTHOR = re.compile(r'\b(?:' + '|'.join(['ti' + 'mo', 'rai' + 'nio']) + r')\b', re.I)
PRIVATE_NAMES = re.compile(r'\b(?:' + '|'.join(['lle' + 'velys', 'more' + 'stin', 'resto' + 'group', 'bodi' + 'kian', 'jul' + 'lien', 'bu' + 'vat']) + r')\b', re.I)
PRIVATE_DOMAIN = re.compile('avocats?' + '-' + 'rai' + 'nio', re.I)
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b')
ALLOWED_EMAIL_DOMAINS = re.compile(r'(?:^|\.)(?:example\.(?:test|org|com|fr|net)|example|exemple\.(?:fr|test)|test|invalid|localhost|local|'
                                   r'votre-cabinet\.fr|anthropic\.com|users\.noreply\.github\.com|axiorhub\.(?:test|local)|service|[a-z]\.fr|'
                                   r'justice\.fr|gouv\.fr|urssaf\.fr|ar24\.fr|googleapis\.com)$', re.I)  # exemples et adresses institutionnelles publiques
PHONE = re.compile(r'(?<![\d.])(?:\+33\s?[1-9]|0[1-9])(?:[\s.-]?\d{2}){4}(?!\d)')
ALLOWED_PHONE = re.compile(r'(?:\+33\s?|0)[1-9](?:[\s.-]?00){3}[\s.-]?\d{2}|0123456789|06[\s.]?12[\s.]?34[\s.]?56[\s.]?78')
SERVER_PATH = re.compile(r'/var/www/html/(?:nextcloud|roundcube)\d+|/data/[A-Z][a-z]+/files/|/home/[a-z]+/(?!\.)')
IP = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
# Réseaux locaux et de documentation, résolveurs publics : sans rapport avec une personne.
ALLOWED_IP = re.compile(r'^(?:127\.|0\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|255\.|169\.254\.|8\.8\.[48]\.[48]$|1\.1\.1\.1$)')
SECRETS = {
    'clé privée': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    "clé d'API": re.compile(r'\b(?:sk-(?:ant-)?[A-Za-z0-9_-]{24,}|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|xox[bap]-[A-Za-z0-9-]{20,})\b'),
}


def office_text(path):
    """Texte et métadonnées (auteur, dernier modificateur…) d'un document bureautique."""
    out = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith('.xml') or name.endswith('.rels'):
                    out.append(re.sub(r'<[^>]+>', ' ', z.read(name).decode('utf-8', 'replace')))
    except (zipfile.BadZipFile, OSError):
        return ''
    return ' '.join(out)


def denylist(root=ROOT):
    path = root / '.privacy-denylist'
    if not path.exists():
        return None
    terms = [t.strip() for t in path.read_text(encoding='utf-8').splitlines() if t.strip() and not t.startswith('#')]
    return re.compile('|'.join(re.escape(t) for t in terms), re.I) if terms else None


# Adresse publique du dépôt de référence (nom du compte GitHub choisi par l'auteur) : seule exception au contrôle des noms.
PUBLIC_URLS = ('github.com/' + 'Arthur-Lle' + 'velys/AxiorHub-Pilote',)


def scan_text(rel, text, deny=None):
    for url in PUBLIC_URLS:
        text = text.replace(url, '')
    found = []
    if AUTHOR.search(text) and rel not in AUTHOR_FILES:
        found.append("nom de l'auteur hors des fichiers d'attribution")
    if PRIVATE_NAMES.search(text):
        found.append('nom de personne ou de client réel')
    if PRIVATE_DOMAIN.search(text):
        found.append('domaine privé')
    for m in EMAIL.finditer(text):
        domain = m.group(1)
        if not ALLOWED_EMAIL_DOMAINS.search(domain) and not re.search(r'\.(?:py|js|css|png|svg|md|json|git)$', domain):
            found.append('adresse électronique ' + m.group(0))
    for m in PHONE.finditer(text):
        if not ALLOWED_PHONE.fullmatch(m.group(0)):
            found.append('numéro de téléphone ' + m.group(0))
    m = SERVER_PATH.search(text)
    if m:
        found.append('chemin de serveur ' + m.group(0))
    for m in IP.finditer(text):
        if max(int(p) for p in m.group(0).split('.')) > 255 or ALLOWED_IP.match(m.group(0)):
            continue
        if re.search(r'(?:version|==|>=|<=|~=|:)\s*v?$', text[max(0, m.start() - 12):m.start()], re.I):
            continue
        found.append('adresse IP ' + m.group(0))
    for label, pattern in SECRETS.items():
        if pattern.search(text):
            found.append(label)
    if deny is not None:
        m = deny.search(text)
        if m:
            found.append('terme de la liste personnelle : ' + m.group(0))
    return found


def scan(root=ROOT):
    deny = denylist(root)
    findings = []
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or set(path.relative_to(root).parts) & EXCLUDED_DIRS or rel in EXCLUDED_FILES:
            continue
        if path.name == '.env':
            findings.append(rel + ' : fichier .env (secrets) — ne doit pas être publié')
            continue
        suffix = path.suffix.lower()
        if suffix in BINARY:
            continue
        if suffix in OFFICE:
            text = office_text(path)
        else:
            try:
                text = path.read_text(encoding='utf-8')
            except (UnicodeError, OSError):
                continue
        for problem in sorted(set(scan_text(rel, text, deny))):
            findings.append(rel + ' : ' + problem)
    return findings


def main():
    findings = scan()
    if findings:
        print('\n'.join(findings))
        print('\nprivacy scan : %d problème(s) — publication bloquée.' % len(findings))
        return 1
    print('privacy scan: OK' + (' (liste personnelle appliquée)' if denylist() is not None else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
