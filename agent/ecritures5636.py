"""5.6.36 : écritures longues de la base AxiorHub — mesurées, nommées, montrées dans « Pourquoi rien n'est produit ? ».

« Un instant… » (base occupée) apparaît quand une écriture attend plus longtemps que prévu derrière une autre. Pour corriger les
causes une à une, chaque transaction d'écriture de plus de 5 secondes est consignée avec l'endroit du code qui l'a ouverte (module,
fonction, ligne), sa durée et sa date, dans « ecritures-longues.json » à côté des données (jamais dans la base, pour ne pas ajouter
d'écriture). Aucun contenu de dossier, aucune requête SQL complète n'y figure. Les pages « base occupée » sont comptées de la même façon.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

THRESHOLD = 5.0
KEEP = 60
_LOCK = threading.Lock()


def _caller():
    """Premier cadre du code AxiorHub hors de ce module et de la connexion : « module.fonction:ligne »."""
    try:
        f = sys._getframe(2)   # noqa: SLF001
    except ValueError:
        return ''
    here = os.path.basename(__file__)
    for _ in range(14):
        if f is None:
            break
        name = os.path.basename(f.f_code.co_filename)
        if name != here and 'sqlite3' not in f.f_code.co_filename:
            if os.sep + 'agent' + os.sep in f.f_code.co_filename or '/agent/' in f.f_code.co_filename:
                return '%s.%s:%d' % (name[:-3] if name.endswith('.py') else name, f.f_code.co_name, f.f_lineno)
        f = f.f_back
    return ''


def _record(state_dir, kind, where, seconds, extra=''):
    if not state_dir:
        return
    path = Path(state_dir) / 'ecritures-longues.json'
    with _LOCK:
        try:
            data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
            data = data if isinstance(data, list) else []
        except (OSError, ValueError):
            data = []
        data.append({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'kind': kind, 'where': where[:120],
                     'seconds': round(float(seconds), 1), 'extra': str(extra)[:80]})
        tmp = path.with_name(path.name + '.tmp')
        try:
            tmp.write_text(json.dumps(data[-KEEP:], ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, path)
        except OSError:
            pass


class TimedConnection(sqlite3.Connection):
    """Connexion SQLite qui mesure la durée de chaque transaction d'écriture (début : première écriture ; fin : commit ou rollback)."""
    state_dir = ''
    _t0 = None
    _where = ''

    def _start(self, was):
        if not was and self.in_transaction and self._t0 is None:
            self._t0 = time.monotonic()
            self._where = _caller()

    _explicit = False

    def execute(self, *args, **kwargs):
        was = self.in_transaction
        if args and isinstance(args[0], str) and args[0].lstrip()[:5].upper() == 'BEGIN':
            # 5.6.56 : avant la 5.6.56, executescript validait implicitement les écritures en attente ; plusieurs opérations ouvraient
            # ensuite leur propre transaction (BEGIN IMMEDIATE) en comptant sur cette validation. Des écritures en attente d'une
            # transaction IMPLICITE sont donc validées avant un BEGIN ; un BEGIN dans une transaction EXPLICITE reste une erreur.
            if was and not self._explicit:
                self.commit()
                was = False
            cur = super().execute(*args, **kwargs)
            self._explicit = True
            self._start(was)
            return cur
        cur = super().execute(*args, **kwargs)
        if not self.in_transaction:
            self._explicit = False
        self._start(was)
        return cur

    def executemany(self, *args, **kwargs):
        was = self.in_transaction
        cur = super().executemany(*args, **kwargs)
        self._start(was)
        return cur

    def _finish(self):
        t0, self._t0 = self._t0, None
        if t0 is not None:
            spent = time.monotonic() - t0
            if spent >= THRESHOLD:
                _record(self.state_dir, 'transaction', self._where, spent)

    def executescript(self, script):
        """5.6.56 (audit 5.6.55, N27) : ``sqlite3.executescript`` VALIDE la transaction en cours avant d'exécuter son script. Une
        initialisation de schéma appelée depuis une opération métier (émission, numérotation) cassait donc son atomicité. Dans une
        transaction, les instructions sont exécutées une à une, sans validation implicite ; hors transaction, comportement inchangé."""
        if not self.in_transaction:
            return super().executescript(script)
        buf = ''
        for line in str(script).splitlines(keepends=True):
            buf += line
            if sqlite3.complete_statement(buf):
                if buf.strip():
                    super().execute(buf)
                buf = ''
        if buf.strip():
            super().execute(buf)
        return self.cursor()

    def commit(self):
        try:
            return super().commit()
        finally:
            self._explicit = False
            self._finish()

    def rollback(self):
        try:
            return super().rollback()
        finally:
            self._explicit = False
            self._finish()


def note_busy(config_path, env):
    """Une page « base occupée » a été servie : consignée (méthode et chemin, sans paramètre)."""
    try:
        state_dir = json.loads(Path(config_path).read_text(encoding='utf-8')).get('state_dir', '') if config_path else ''
    except (OSError, ValueError):
        state_dir = ''
    _record(state_dir, 'base_occupee', str(env.get('REQUEST_METHOD', ''))[:8] + ' ' + str(env.get('PATH_INFO', ''))[:100], 0)


def summary(state_dir, days=7):
    """Regroupement par origine : nombre, durée maximale, dernière occurrence."""
    path = Path(state_dir) / 'ecritures-longues.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'transactions': [], 'busy': 0}
    cutoff = time.time() - days * 86400
    groups, busy = {}, 0
    for x in data if isinstance(data, list) else []:
        try:
            when = datetime.fromisoformat(x['at']).timestamp()
        except (KeyError, ValueError, TypeError):
            continue
        if when < cutoff:
            continue
        if x.get('kind') == 'base_occupee':
            busy += 1
            continue
        g = groups.setdefault(x.get('where') or 'origine inconnue', {'where': x.get('where') or 'origine inconnue', 'count': 0, 'max': 0.0, 'last': ''})
        g['count'] += 1
        g['max'] = max(g['max'], float(x.get('seconds') or 0))
        g['last'] = max(g['last'], x['at'])
    return {'transactions': sorted(groups.values(), key=lambda g: -g['max'])[:10], 'busy': busy}


def html(state_dir):
    from html import escape as e
    s = summary(state_dir)
    if not s['transactions'] and not s['busy']:
        return ''
    rows = ''.join('<tr><td><code>%s</code></td><td>%d</td><td>%.0f s</td><td>%s</td></tr>' % (
        e(g['where']), g['count'], g['max'], e(g['last'][:16].replace('T', ' '))) for g in s['transactions'])
    return ('<section class="ax-card" id="ecritures"><h2>Écritures longues (7 jours)</h2><p class="vf-note">Pages « Un instant… » servies : %d. Chaque '
            'écriture de plus de 5 secondes retarde les autres ; son origine est indiquée pour être corrigée (joignez ce tableau à un signalement).</p>%s</section>') % (
        s['busy'], ('<table class="vf-table"><thead><tr><th>Origine</th><th>Nombre</th><th>Plus longue</th><th>Dernière</th></tr></thead><tbody>%s</tbody></table>' % rows)
        if rows else '')
