#!/usr/bin/env python3
"""Mesure la durée de la recherche unique 4.9.0 sur VOTRE volume réel (lecture seule, aucune donnée affichée).

Usage : sudo -u <utilisateur du service> python3 scripts/bench-search490.py /etc/axiorhub-mail-agent/config.json [nombre_de_requêtes]
Les requêtes sont tirées au hasard parmi les mots de votre propre index ; seules des durées sont imprimées.
"""
import json
from pathlib import Path
import random
import re
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import search490                     # noqa: E402
from agent.common import load_config            # noqa: E402
from agent.desk import Desk                     # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cfg = load_config(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    desk = Desk(cfg)
    rnd = random.Random(4900)
    state = Path(cfg['state_dir'])
    words = set()
    db = sqlite3.connect('file:%s?mode=ro' % (state / 'documents.sqlite3'), uri=True)
    for (text,) in db.execute('SELECT text FROM knowledge_chunks ORDER BY RANDOM() LIMIT 400'):
        words.update(w.lower() for w in re.findall(r'[A-Za-zÀ-ÿ]{5,}', text))
    words = sorted(words)
    if len(words) < 20:
        print('Index trop petit pour une mesure représentative.')
        return 1
    search490.refresh_local(desk, force=True)
    print(json.dumps(search490.status(desk)['counts'], ensure_ascii=False))
    times, partial = [], 0
    for i in range(n):
        q = ' '.join(rnd.sample(words, rnd.choice((1, 2, 2, 3))))
        t0 = time.perf_counter()
        r = search490.search(desk, q)
        times.append((time.perf_counter() - t0) * 1000)
        partial += int(r['partial'])
    times.sort()
    print('%d recherches : médiane %.0f ms · 95e centile %.0f ms · maximum %.0f ms · partielles : %d' % (
        n, statistics.median(times), times[int(n * .95) - 1], times[-1], partial))
    ok = times[-1] < 3000 and not partial
    print('OBJECTIF (< 3 s) : ' + ('ATTEINT' if ok else 'NON ATTEINT — voir le guide'))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
