#!/usr/bin/env python3
"""Intégration continue : résume les tests en échec en annotations GitHub (lisibles sur la page du dépôt, sans connexion).

  python3 scripts/ci-annotate.py /tmp/tests.log

GitHub n'affiche que 10 annotations d'erreur par étape : les échecs sont regroupés (plusieurs par annotation).
"""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(log_path):
    spec = importlib.util.spec_from_file_location('recette', ROOT / 'recette.py')
    recette = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recette)
    output = Path(log_path).read_text(encoding='utf-8', errors='replace')
    status, summary, failures = recette.parse(output)
    details = recette.reasons(output)
    lines = ['%s : %s' % (name, details.get(name, '')) for name in failures]
    print('::error title=Tests::%s' % summary.replace('\n', ' '))
    per = max(1, -(-len(lines) // 9))                         # 9 annotations au plus, en plus du résumé
    for i in range(0, len(lines), per):
        chunk = '%0A'.join(x.replace('%', '%25').replace('\r', '').replace('\n', ' ')[:400] for x in lines[i:i + per])
        print('::error title=Échecs %d-%d::%s' % (i + 1, min(i + per, len(lines)), chunk))
    return 1 if status != 'ok' else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
