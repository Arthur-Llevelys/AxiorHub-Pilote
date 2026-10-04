#!/usr/bin/env python3
"""Recette automatique d'AxiorHub (5.2.0).

Lance la suite de tests de la version installée dans une COPIE temporaire (rien n'est écrit dans le dossier de la version, pas même
de fichier .pyc), puis enregistre le résultat dans le dossier d'état ; la page « Pourquoi rien n'est produit ? » l'affiche.

  sudo python3 /opt/axiorhub-mail-agent/current/recette.py               # au premier plan
  sudo python3 /opt/axiorhub-mail-agent/current/recette.py --background  # en tâche de fond (après l'installation)
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

CONFIG = Path('/etc/axiorhub-mail-agent/config.json')
HERE = Path(__file__).resolve().parent


def state_dir(config=CONFIG):
    try:
        return Path(json.loads(config.read_text(encoding='utf-8'))['state_dir'])
    except (OSError, ValueError, KeyError):
        return Path('/var/lib/axiorhub-mail-agent')


def version():
    text = (HERE / 'agent' / '__init__.py').read_text(encoding='utf-8')
    m = re.search(r"__version__\s*=\s*'([^']+)'", text)
    return m.group(1) if m else '?'


def write(path, data):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def parse(output):
    ran = re.search(r'^Ran (\d+) tests? in ([\d.]+)s', output, re.M)
    end = re.search(r'^(OK|FAILED)(?: \(([^)]*)\))?\s*$', output, re.M)
    failures = sorted(set(re.findall(r'^(?:FAIL|ERROR): (\S+ \([^)]*\))', output, re.M)))
    counts = dict(re.findall(r'(failures|errors|skipped)=(\d+)', end.group(2) if end and end.group(2) else ''))
    status = 'ok' if end and end.group(1) == 'OK' else 'echec'
    summary = '%s test(s)' % (ran.group(1) if ran else '?')
    if counts:
        summary += ', ' + ', '.join('%s %s' % (v, {'failures': 'échec(s)', 'errors': 'erreur(s)', 'skipped': 'ignoré(s)'}[k]) for k, v in counts.items())
    return status, summary, failures


def reasons(output):
    """5.6.1 : motif de chaque échec, c'est-à-dire la dernière ligne d'exception du rapport (« AssertionError: … »)."""
    details = {}
    for block in re.split(r'^={50,}\s*$', output, flags=re.M):
        head = re.search(r'^(?:FAIL|ERROR): (\S+ \([^)]*\))', block, re.M)
        if not head:
            continue
        lines = [x.strip() for x in block.splitlines() if x.strip() and not set(x.strip()) <= set('-')]
        tail = [x for x in lines if re.match(r'^[A-Za-z_][\w.]*(?:Error|Exception|Failure|Stop)\b', x)]
        details.setdefault(head.group(1), (tail[-1] if tail else lines[-1])[:300])
    return details


def run(target, timeout=3600):
    started = time.monotonic()
    base = {'version': version(), 'at': datetime.now(timezone.utc).isoformat(), 'status': 'en_cours', 'summary': 'recette en cours', 'failures': []}
    write(target, base)
    with tempfile.TemporaryDirectory(prefix='axiorhub-recette-') as td:
        copy = Path(td) / 'release'
        shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1', 'HOME': td, 'TMPDIR': td}
        try:
            p = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'], cwd=copy, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
            output = p.stdout.decode('utf-8', 'replace')
            status, summary, failures = parse(output)
            details = reasons(output)
        except subprocess.TimeoutExpired:
            status, summary, failures, details = 'echec', 'recette interrompue (délai dépassé)', [], {}
    result = {**base, 'status': status, 'summary': summary, 'failures': failures, 'details': details,
              'duration_s': int(time.monotonic() - started),
              'finished': datetime.now(timezone.utc).isoformat()}
    write(target, result)
    return result


def main():
    parser = argparse.ArgumentParser(description='Recette automatique AxiorHub')
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--state', default='')
    args = parser.parse_args()
    target = Path(args.state or state_dir()) / 'recette520.json'
    if args.background:
        subprocess.Popen(['nice', '-n', '10', sys.executable, str(Path(__file__).resolve()), '--state', str(target.parent)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        print('Recette automatique lancée en arrière-plan ; résultat dans « Pourquoi rien n’est produit ? ».')
        return
    result = run(target)
    print('Recette %s : %s' % ('réussie' if result['status'] == 'ok' else 'avec échecs', result['summary']))
    for name in result['failures'][:40]:
        print('  - ' + name)


if __name__ == '__main__':
    main()
