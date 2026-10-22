"""5.6.70 : blocage automatique des tentatives d'accès suspectes (audit de sécurité du 22 octobre 2026).

Constats de l'audit :
- derrière le proxy (Apache ou Nginx), toutes les requêtes arrivaient de 127.0.0.1 : la limite « par adresse » des connexions
  s'appliquait au cabinet entier et un blocage par adresse aurait bloqué tout le monde. L'adresse réelle est désormais lue dans
  X-Forwarded-For, seulement quand la requête vient d'un proxy de confiance (boucle locale, ou AXIORHUB_TRUSTED_PROXIES) ;
- les robots qui sondent le serveur (« /wp-login.php », « /.env », « /.git », « phpMyAdmin »…) recevaient la page de connexion.

Désormais :
- une sonde reçoit un 404 sans traitement et compte comme indice ; les échecs de connexion aussi ;
- 5 sondes ou 10 échecs en 10 minutes : l'adresse est bloquée 15 minutes, puis 1 heure, puis 24 heures en cas de récidive sur 7 jours ;
- une adresse bloquée reçoit un 403 minimal (aucune page, aucun formulaire) ; la boucle locale et la liste d'adresses de confiance
  ne sont jamais bloquées ;
- chaque blocage est écrit dans « securite-acces.log » (ligne lisible par fail2ban : « AXIORHUB-BLOCAGE ip=… ») pour que le pare-feu du
  serveur prenne le relais ; l'administrateur voit et lève les blocages dans « Sécurité des accès ».
"""
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
import re
import time

PROBE = re.compile(r'(?:\.php\d?(?:$|/)|/wp-(?:admin|login|content|includes|json)|/xmlrpc|/\.(?:env|git|svn|hg|aws|ssh|htaccess|htpasswd|DS_Store)|'
                   r'phpmyadmin|/pma(?:/|$)|/cgi-bin/|/vendor/phpunit|/actuator|/server-status|/boaform|/HNAP1|/owa/|/autodiscover|/\.well-known/security\.txt\.php|'
                   r'/solr/|/console(?:/|$)|/manager/html|/jenkins|/telescope|/_ignition|/debug/default|/setup\.cgi|/shell|/etc/passwd)', re.I)
WINDOW = 600            # secondes
MAX_PROBES = 5
MAX_FAILURES = 10
STEPS = (900, 3600, 86400)   # 15 min, 1 h, 24 h
RECIDIVE = 7 * 86400
SCHEMA = '''CREATE TABLE IF NOT EXISTS acces5670_events(id INTEGER PRIMARY KEY, ip TEXT NOT NULL, kind TEXT NOT NULL, at REAL NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS acces5670_events_ip ON acces5670_events(ip, at);
CREATE TABLE IF NOT EXISTS acces5670_bans(id INTEGER PRIMARY KEY, ip TEXT NOT NULL, since REAL NOT NULL, until REAL NOT NULL, reason TEXT NOT NULL,
  lifted_by TEXT NOT NULL DEFAULT '', lifted_at REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS acces5670_bans_ip ON acces5670_bans(ip, until);
CREATE TABLE IF NOT EXISTS acces5670_allow(ip TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '', added_by TEXT NOT NULL DEFAULT '', at REAL NOT NULL);'''
REASONS = {'sondes': 'Sondes de failles connues', 'connexions': 'Échecs de connexion répétés', 'manuel': 'Blocage manuel'}


def _trusted_proxies():
    extra = [x.strip() for x in os.environ.get('AXIORHUB_TRUSTED_PROXIES', '').split(',') if x.strip()]
    nets = []
    for value in ['127.0.0.0/8', '::1/128'] + extra:
        try:
            nets.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return nets


def _is(addr, nets):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in nets)


def client_ip(env):
    """Adresse réelle : X-Forwarded-For n'est lu que si la connexion vient d'un proxy de confiance ; on prend la dernière adresse
    non fiable de la chaîne (celle qu'a vue notre proxy), jamais la première (qu'un client peut forger)."""
    remote = str(env.get('REMOTE_ADDR') or '').strip()
    nets = _trusted_proxies()
    if not _is(remote, nets):
        return remote[:64] or 'inconnu'
    chain = [x.strip() for x in str(env.get('HTTP_X_FORWARDED_FOR') or '').split(',') if x.strip()]
    for addr in reversed(chain):
        try:
            ipaddress.ip_address(addr)
        except ValueError:
            return remote[:64]                      # chaîne illisible : jamais d'adresse inventée
        if not _is(addr, nets):
            return addr[:64]
    return remote[:64] or 'inconnu'


def is_probe(path):
    return bool(PROBE.search(str(path or '')))


_READY = set()


def open_garde(state_dir):
    """Garde du service web (base dédiée « acces5670.sqlite3 » du dossier d'état) ; à fermer par l'appelant (``g.db.close()``)."""
    import sqlite3
    path = Path(state_dir) / 'acces5670.sqlite3'
    db = sqlite3.connect(str(path), timeout=5)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return Garde(db, state_dir)


class Garde:
    def __init__(self, db, state_dir=''):
        self.db = db
        self.state_dir = str(state_dir or '')
        try:
            key = str(self.db.execute('PRAGMA database_list').fetchone()[2] or '')
        except Exception:   # noqa: BLE001
            key = ''
        if not key or key not in _READY:   # schéma créé une fois par fichier de base (base en mémoire : à chaque fois)
            self.db.executescript(SCHEMA)
            self.db.commit()
            if key:
                _READY.add(key)
        if not self.state_dir and key:
            self.state_dir = str(Path(key).parent)   # journal fail2ban à côté de la base

    def _protected(self, ip):
        if ip in ('', 'inconnu') or _is(ip, _trusted_proxies()):
            return True
        try:   # réseau privé (proxy Docker de Nextcloud en mode ExApp, poste du cabinet) : jamais bloqué, sinon tout le cabinet le serait
            if ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_link_local:
                return True
        except ValueError:
            return True
        return bool(self.db.execute('SELECT 1 FROM acces5670_allow WHERE ip=?', (ip,)).fetchone())

    def banned(self, ip):
        if self._protected(ip):
            return None
        row = self.db.execute('SELECT until,reason FROM acces5670_bans WHERE ip=? AND until>? AND lifted_at=0 ORDER BY until DESC LIMIT 1',
                              (ip, time.time())).fetchone()
        return {'until': row[0], 'reason': row[1]} if row else None

    def record(self, ip, kind, detail=''):
        """Indice (« sonde », « echec_connexion ») ; bloque l'adresse au-delà du seuil. Renvoie le blocage éventuel."""
        if self._protected(ip):
            return None
        now = time.time()
        self.db.execute('DELETE FROM acces5670_events WHERE at<?', (now - RECIDIVE,))
        self.db.execute('INSERT INTO acces5670_events(ip,kind,at,detail) VALUES(?,?,?,?)', (ip, kind, now, str(detail)[:200]))
        n = self.db.execute('SELECT COUNT(*) FROM acces5670_events WHERE ip=? AND kind=? AND at>=?', (ip, kind, now - WINDOW)).fetchone()[0]
        self.db.commit()
        limit = MAX_PROBES if kind == 'sonde' else MAX_FAILURES
        if n >= limit and not self.banned(ip):
            return self.ban(ip, 'sondes' if kind == 'sonde' else 'connexions')
        return None

    def ban(self, ip, reason, actor='', seconds=None):
        now = time.time()
        previous = self.db.execute('SELECT COUNT(*) FROM acces5670_bans WHERE ip=? AND since>=?', (ip, now - RECIDIVE)).fetchone()[0]
        duration = int(seconds or STEPS[min(previous, len(STEPS) - 1)])
        self.db.execute('INSERT INTO acces5670_bans(ip,since,until,reason) VALUES(?,?,?,?)', (ip, now, now + duration, reason))
        self.db.commit()
        self._log('AXIORHUB-BLOCAGE ip=%s motif=%s duree=%ds%s' % (ip, reason, duration, (' par=%s' % actor) if actor else ''))
        return {'ip': ip, 'until': now + duration, 'reason': reason, 'duration': duration}

    def lift(self, ip, actor):
        self.db.execute('UPDATE acces5670_bans SET lifted_by=?,lifted_at=? WHERE ip=? AND until>? AND lifted_at=0', (str(actor)[:80], time.time(), ip, time.time()))
        self.db.execute("DELETE FROM acces5670_events WHERE ip=?", (ip,))
        self.db.commit()
        self._log('AXIORHUB-LEVEE ip=%s par=%s' % (ip, actor))

    def allow(self, ip, note, actor):
        ipaddress.ip_address(ip)   # ValueError si l'adresse est invalide
        self.db.execute('INSERT OR REPLACE INTO acces5670_allow VALUES(?,?,?,?)', (ip, str(note)[:120], str(actor)[:80], time.time()))
        self.db.commit()
        self.lift(ip, actor)

    def disallow(self, ip):
        self.db.execute('DELETE FROM acces5670_allow WHERE ip=?', (ip,))
        self.db.commit()

    def active(self):
        now = time.time()
        return [{'ip': r[0], 'since': r[1], 'until': r[2], 'reason': r[3]} for r in self.db.execute(
            'SELECT ip,since,until,reason FROM acces5670_bans WHERE until>? AND lifted_at=0 ORDER BY since DESC LIMIT 200', (now,))]

    def recent(self, hours=24):
        since = time.time() - hours * 3600
        return [{'ip': r[0], 'kind': r[1], 'n': r[2], 'last': r[3]} for r in self.db.execute(
            'SELECT ip,kind,COUNT(*),MAX(at) FROM acces5670_events WHERE at>=? GROUP BY ip,kind ORDER BY 3 DESC LIMIT 50', (since,))]

    def allowed_list(self):
        return [{'ip': r[0], 'note': r[1], 'by': r[2]} for r in self.db.execute('SELECT ip,note,added_by FROM acces5670_allow ORDER BY ip')]

    def _log(self, line):
        if not self.state_dir:
            return
        try:
            path = Path(self.state_dir) / 'securite-acces.log'
            stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            with open(path, 'a', encoding='utf-8') as f:
                f.write('%s %s\n' % (stamp, line))
        except OSError:
            pass


def when(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M')


def section_html(state_dir, client=''):
    """Paramètres › Cabinet (service web) : adresses bloquées, indices des dernières 24 heures, adresses de confiance."""
    from html import escape as e
    g = open_garde(state_dir)
    try:
        bans, recent, allowed = g.active(), g.recent(), g.allowed_list()
    finally:
        g.db.close()
    form = lambda route, ip, label, extra='': ('<form class="m5-form m5-inline m5-mini" data-api="m5670/securite/%s" data-reload>'
                                               '<input type="hidden" name="ip" value="%s">%s<button class="ax-btn ghost" type="submit">%s</button></form>') % (
        route, e(ip, quote=True), extra, e(label))
    rows_b = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(b['ip']), e(REASONS.get(b['reason'], b['reason'])), when(b['since']), when(b['until']), form('lever', b['ip'], 'Lever le blocage')) for b in bans)
    rows_r = ''.join('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td></tr>' % (
        e(r['ip']), 'Sondes' if r['kind'] == 'sonde' else 'Échecs de connexion', r['n'], when(r['last']), form('bloquer', r['ip'], 'Bloquer 24 h'))
        for r in recent)
    rows_a = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(a['ip']), e(a['note'] or '—'), form('retirer', a['ip'], 'Retirer')) for a in allowed)
    return ('<section id="garde5670"><h2>Sécurité des accès</h2><p class="ax-muted">Une adresse qui sonde des failles connues (5 fois en 10 minutes) '
            'ou échoue 10 fois à se connecter est bloquée 15 minutes, puis 1 heure, puis 24 heures en cas de récidive. Les blocages sont écrits '
            'dans « securite-acces.log » pour le pare-feu du serveur (fail2ban). Les adresses de réseau privé ne sont jamais bloquées. '
            'Votre adresse : <strong>%s</strong>.</p>'
            '<h3>Adresses bloquées</h3><div class="table"><table><thead><tr><th>Adresse</th><th>Motif</th><th>Depuis</th><th>Jusqu’au</th><th></th></tr></thead>'
            '<tbody>%s</tbody></table></div><h3>Indices des dernières 24 heures</h3><div class="table"><table><thead><tr><th>Adresse</th><th>Nature</th>'
            '<th>Nombre</th><th>Dernier</th><th></th></tr></thead><tbody>%s</tbody></table></div><h3>Adresses de confiance</h3><div class="table"><table>'
            '<thead><tr><th>Adresse</th><th>Note</th><th></th></tr></thead><tbody>%s</tbody></table></div>'
            '<form class="m5-form" data-api="m5670/securite/confiance" data-reload><label>Adresse IP de confiance<input name="ip" maxlength="64" required></label>'
            '<label>Note<input name="note" maxlength="120"></label><button class="ax-btn" type="submit">Ajouter</button><p role="status" class="m5-status"></p></form>'
            '</section>') % (e(client or '—'), rows_b or '<tr><td colspan="5" class="ax-muted">Aucune adresse bloquée.</td></tr>',
                             rows_r or '<tr><td colspan="5" class="ax-muted">Aucun indice.</td></tr>', rows_a or '<tr><td colspan="3" class="ax-muted">Aucune.</td></tr>')
