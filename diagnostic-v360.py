#!/usr/bin/env python3
"""Diagnostic agrégé AxiorHub 3.6.0, en lecture seule.

Ne publie ni objet, ni expéditeur, ni contenu, ni secret des courriels.
"""
import json
from collections import Counter
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

CONFIG = Path('/etc/axiorhub-mail-agent/config.json')


def connect(path):
    # L'URI mode=ro garantit que le diagnostic ne crée ou ne modifie aucune base.
    return sqlite3.connect('file:' + quote(str(path), safe='/') + '?mode=ro', uri=True)


def main():
    state = Path(json.loads(CONFIG.read_text())['state_dir'])
    with connect(state / 'state.sqlite3') as db:
        statuses = dict(db.execute('SELECT status,COUNT(*) FROM messages GROUP BY status'))
        ignored = [{'reason': reason, 'count': count} for reason, count in db.execute(
            "SELECT reason,COUNT(*) FROM messages WHERE status='ignored' "
            'GROUP BY reason ORDER BY COUNT(*) DESC')]
        reviews = [{'reason': reason, 'count': count} for reason, count in db.execute(
            "SELECT reason,COUNT(*) FROM messages WHERE status='review' "
            'GROUP BY reason ORDER BY COUNT(*) DESC')]
    with connect(state / 'desk.sqlite3') as db:
        jobs = [{'kind': kind, 'status': status, 'count': count}
                for kind, status, count in db.execute(
                    "SELECT kind,status,COUNT(*) FROM jobs "
                    "WHERE status IN ('pending','running','error') "
                    'GROUP BY kind,status ORDER BY COUNT(*) DESC')]
        work = dict(db.execute('SELECT state,COUNT(*) FROM work_items GROUP BY state'))
        hidden_reviews = db.execute(
            "SELECT COUNT(*) FROM work_items WHERE state='handled' AND source_status='review'"
        ).fetchone()[0]
        error_counts = Counter()
        error_latest = {}
        for kind, result, finished in db.execute(
            "SELECT kind,result,finished FROM jobs WHERE status='error'"):
            try:
                code = json.loads(result or '{}').get('erreur', 'autre_resultat')
            except (ValueError, TypeError, AttributeError):
                code = 'autre_resultat'
            # No arbitrary text, paths or document names in a shareable report.
            if not isinstance(code, str) or not re.fullmatch(r'[a-z0-9_]{2,100}', code):
                code = 'erreur_non_code_masquee'
            key = kind, code
            error_counts[key] += 1
            error_latest[key] = max(error_latest.get(key, ''), finished or '')
        error_jobs = [{'kind': kind, 'code': code, 'count': count,
                       'latest': error_latest[kind, code]}
                      for (kind, code), count in error_counts.most_common(35)]
    try:
        with connect(state / 'documents.sqlite3') as db:
            pending = db.execute('SELECT COUNT(*) FROM inventory_scans').fetchone()[0]
    except (sqlite3.Error, OSError):
        pending = None
    print(json.dumps({'mail_statuses': statuses, 'ignored_reasons': ignored,
                      'review_reasons': reviews, 'work_states': work,
                      'handled_reviews_to_check': hidden_reviews,
                      'job_queue': jobs, 'error_jobs': error_jobs,
                      'incomplete_inventories': pending},
                     indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
