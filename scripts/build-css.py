#!/usr/bin/env python3
"""Regénère agent/static/app520.css : concaténation, dans l'ordre exact de chargement, des feuilles de style historiques.

Une seule feuille chargée par page (au lieu d'une trentaine) : affichage plus rapide, plus de conflit d'ordre entre pages.
Les fichiers sources restent la référence ; un test vérifie que le paquet est à jour. Usage : python3 scripts/build-css.py
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.shell501 import BUNDLE  # noqa: E402


def build():
    static = ROOT / 'agent' / 'static'
    parts = ['/* AxiorHub — feuille de style unique générée par scripts/build-css.py. Ne pas modifier : modifier les fichiers sources. */\n']
    for name in BUNDLE:
        parts.append('/* ==== %s ==== */\n%s\n' % (name, (static / name).read_text(encoding='utf-8').rstrip()))
    return ''.join(parts)


if __name__ == '__main__':
    (ROOT / 'agent' / 'static' / 'app520.css').write_text(build(), encoding='utf-8', newline='\n')
    print('app520.css régénéré (%d feuilles)' % len(BUNDLE))
