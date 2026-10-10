#!/usr/bin/env python3
"""AxiorHub Pilote — mode poste (5.6.20) : l'agent sur un poste de travail Ubuntu, sans root, sans Apache, dans une fenêtre.

  python3 poste.py                 démarre les services s'ils ne tournent pas, puis ouvre la fenêtre (WebKitGTK)
  python3 poste.py --no-window     services seulement (unité systemd utilisateur « axiorhub-pilote »)
  python3 poste.py --stop          arrête les services lancés par ce poste
  python3 poste.py --status        état (port, processus) et emplacement des dossiers
  python3 poste.py --print-password  affiche l'identifiant et le mot de passe locaux (pour un navigateur ordinaire)

Dossiers (XDG) : données, configuration et secrets dans ~/.local/share/axiorhub-pilote (config.json, ui-auth.json,
secrets/, state/, matters.json, mot-de-passe-local) ; journaux dans ~/.local/state/axiorhub-pilote.
Premier lancement : configuration sans secret (même modèle que le conteneur Docker, adresses locales pour Ollama et Kokoro),
compte local « admin » avec un mot de passe aléatoire enregistré en 0600 ; l'assistant d'installation de l'interface prend ensuite
le relais (messagerie, Nextcloud, Ollama). Les services sont ceux de l'installation serveur, lancés comme sous-processus :
interface (127.0.0.1 seulement), worker, veille IMAP, passage périodique de l'agent.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

try:
    import fcntl
except ImportError:   # Windows (tests) : pas de verrou d'instance, contrôle du PID seulement
    fcntl = None
RESTART_LIMIT = (5, 600)   # 5.6.24 (F24) : au plus 5 reprises par service en 10 minutes, puis défaut visible

ROOT = Path(__file__).resolve().parent
APP = 'axiorhub-pilote'
DEFAULT_PORT = 8769
PREFIX = '/agent-courriel'


def paths(home=None):
    home = Path(home or os.environ.get('AXIORHUB_POSTE_HOME') or Path.home())
    data = home / '.local' / 'share' / APP
    logs = home / '.local' / 'state' / APP
    return {'home': home, 'data': data, 'logs': logs, 'config': data / 'config.json', 'auth': data / 'ui-auth.json',
            'password': data / 'mot-de-passe-local', 'runtime': logs / 'runtime.json', 'state': data / 'state'}


def _write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(text)


def _scrypt(password, salt):
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1).hex()


def origin_for(port):
    return 'http://127.0.0.1:%d' % int(port)


def bootstrap(p, port=DEFAULT_PORT, python=None):
    """Configuration sans secret et compte local au premier lancement ; ensuite, seule l'origine (port) est mise à jour."""
    p['data'].mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p['data'], 0o700)
    except OSError:
        pass
    p['logs'].mkdir(parents=True, exist_ok=True)
    if not p['config'].exists():
        env = {**os.environ, 'AXIORHUB_DATA_DIR': str(p['data']),
               'AXIORHUB_OLLAMA_URL': os.environ.get('AXIORHUB_OLLAMA_URL', 'http://127.0.0.1:11434'),
               'AXIORHUB_SPEECH_URL': os.environ.get('AXIORHUB_SPEECH_URL', 'http://127.0.0.1:8880'),
               'AXIORHUB_PUBLIC_URL': origin_for(port), 'PYTHONDONTWRITEBYTECODE': '1'}
        subprocess.run([python or sys.executable, str(ROOT / 'docker' / 'bootstrap.py')], env=env, check=True,
                       stdout=subprocess.DEVNULL, cwd=str(ROOT))
    origin = origin_for(port)
    if p['auth'].exists():
        auth = json.loads(p['auth'].read_text(encoding='utf-8'))
        if auth.get('origin') != origin:
            auth['origin'] = origin
            _write_private(p['auth'], json.dumps(auth, ensure_ascii=False) + '\n')
        return auth
    password = secrets.token_urlsafe(18)
    salt = secrets.token_bytes(16)
    auth = {'username': 'admin', 'salt': salt.hex(), 'hash': _scrypt(password, salt), 'csrf': secrets.token_urlsafe(32),
            'origin': origin, 'prefix': PREFIX}
    internal = p['data'] / 'internal-auth.json'
    if internal.exists():
        try:
            token_hash = json.loads(internal.read_text(encoding='utf-8')).get('api_token_sha256')
            if token_hash:
                auth['api_token_sha256'] = token_hash
        except (OSError, ValueError):
            pass
    _write_private(p['password'], password + '\n')
    _write_private(p['auth'], json.dumps(auth, ensure_ascii=False) + '\n')
    return auth


def credentials(p):
    auth = json.loads(p['auth'].read_text(encoding='utf-8'))
    password = p['password'].read_text(encoding='utf-8').strip() if p['password'].exists() else ''
    return auth.get('username', 'admin'), password


def _lock_path(p):
    return p['logs'] / 'poste.lock'


def _lock_held(p):
    """5.6.24 (F24) : l'instance est vivante si son verrou est tenu ; un ancien PID réutilisé par un autre programme ne compte pas."""
    if fcntl is None:
        return None
    try:
        with open(_lock_path(p), 'a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
            return False
    except OSError:
        return None


def runtime(p):
    try:
        info = json.loads(p['runtime'].read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    try:
        os.kill(int(info['pid']), 0)
    except (OSError, ValueError, TypeError):
        return None
    if _lock_held(p) is False:
        return None   # fichier périmé : PID vivant mais ce n'est plus l'instance AxiorHub
    return info


def first_run(p):
    """5.6.24 (F19) : profil neuf = ni dossier de travail local ni Nextcloud renseigné."""
    try:
        cfg = json.loads(p['config'].read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return True
    nc = cfg.get('nextcloud') or {}
    return not nc.get('local_path') and 'example.com' in str(nc.get('url', 'example.com'))


def probe(port, timeout=2):
    """Code HTTP de l'interface locale (401 = service prêt, authentification attendue) ou None."""
    try:
        with urllib.request.urlopen(origin_for(port) + PREFIX + '/', timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError):
        return None


class Supervisor:
    """Interface, worker, veille IMAP et passage périodique de l'agent, comme les unités systemd du serveur."""

    SERVICES = ('interface', 'worker', 'veille')

    def __init__(self, p, port=DEFAULT_PORT, python=None, interval_minutes=5, root=ROOT):
        self.p, self.port, self.python, self.root = p, int(port), python or sys.executable, Path(root)
        self.interval = max(1, int(interval_minutes)) * 60
        self.procs, self.logs, self.stop_event = {}, {}, threading.Event()
        self.timer = self.monitor = None
        self.args, self.restarts, self.faults, self.lock = {}, {}, {}, None
        self.registry_lock = threading.Lock()

    def _spawn(self, name, args):
        log = self.logs.get(name)
        if log is None or log.closed:
            log = open(self.p['logs'] / (name + '.log'), 'ab')
            self.logs[name] = log
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'}
        extra = {'start_new_session': True} if os.name == 'posix' else {}   # groupe de processus : les descendants s'arrêtent avec lui
        self.args[name] = args
        self.procs[name] = subprocess.Popen([self.python, *args], cwd=str(self.root), stdout=log, stderr=subprocess.STDOUT, env=env, **extra)
        return self.procs[name]

    def _write_runtime(self):
        _write_private(self.p['runtime'], json.dumps({'pid': os.getpid(), 'port': self.port, 'started': getattr(self, 'started', ''),
                                                      'processes': {k: self.procs[k].pid for k in self.SERVICES if k in self.procs},
                                                      'restarts': {k: len(v) for k, v in self.restarts.items()}, 'defauts': self.faults}) + '\n')

    def start(self):
        self.p['logs'].mkdir(parents=True, exist_ok=True)
        if fcntl is not None:   # 5.6.24 (F24) : une seule instance par profil, verrou du système tenu pendant toute la vie du superviseur
            self.lock = open(_lock_path(self.p), 'a')
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.lock.close(); self.lock = None
                raise RuntimeError('instance_axiorhub_deja_active')
        cfg = ['--config', str(self.p['config'])]
        self._spawn('interface', [str(self.root / 'web.py'), 'serve', *cfg, '--auth', str(self.p['auth']), '--port', str(self.port)])
        self._spawn('worker', [str(self.root / 'web.py'), 'worker', *cfg])
        self._spawn('veille', [str(self.root / 'web.py'), 'watch', *cfg])
        self.started = time.strftime('%Y-%m-%dT%H:%M:%S')
        self.timer = threading.Thread(target=self._periodic, name='axiorhub-poste-periodique', daemon=True)
        self.timer.start()
        self.monitor = threading.Thread(target=self._watch, name='axiorhub-poste-surveillance', daemon=True)
        self.monitor.start()
        self.p['runtime'].parent.mkdir(parents=True, exist_ok=True)
        self._write_runtime()
        return self

    def _watch(self):
        """5.6.24 (F24) : surveillance bornée — un service arrêté est relancé avec une temporisation progressive ; au-delà de
        RESTART_LIMIT, la boucle de plantage devient un défaut visible (runtime.json, journal) au lieu d'une relance infinie."""
        while not self.stop_event.wait(2):
            for name in self.SERVICES:
                proc = self.procs.get(name)
                if proc is None or proc.poll() is None or name in self.faults:
                    continue
                now = time.time()
                history = [t for t in self.restarts.get(name, []) if now - t < RESTART_LIMIT[1]]
                if len(history) >= RESTART_LIMIT[0]:
                    self.faults[name] = 'arrets_repetes_code_%s' % proc.returncode
                    self._log('Service %s arrêté %d fois en 10 minutes : relance suspendue (voir %s.log).' % (name, len(history), name))
                    self._write_runtime()
                    continue
                if self.stop_event.wait(min(60, 2 ** len(history))):
                    return
                with self.registry_lock:
                    if self.stop_event.is_set():
                        return
                    history.append(time.time())
                    self.restarts[name] = history
                    self._log('Service %s arrêté (code %s) : relance n° %d.' % (name, proc.returncode, len(history)))
                    self._spawn(name, self.args[name])
                    self._write_runtime()

    def _log(self, message):
        try:
            with open(self.p['logs'] / 'superviseur.log', 'a', encoding='utf-8') as handle:
                handle.write(time.strftime('%Y-%m-%dT%H:%M:%S ') + message + '\n')
        except OSError:
            pass

    def _periodic(self):
        """Passage périodique de l'agent : processus enregistré (arrêté avec les autres), jamais un subprocess.run non suivi (F24)."""
        while not self.stop_event.wait(self.interval):
            with self.registry_lock:
                if self.stop_event.is_set():
                    return
                try:
                    proc = self._spawn('passage', [str(self.root / 'manage.py'), '--config', str(self.p['config']), 'run'])
                except OSError:
                    continue
            deadline = time.time() + 3600
            while proc.poll() is None and not self.stop_event.is_set() and time.time() < deadline:
                time.sleep(1)
            if proc.poll() is None and not self.stop_event.is_set():
                self._log('Passage périodique interrompu après une heure.')
                self._terminate(proc)

    def wait_ready(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if probe(self.port) in (200, 401):
                return True
            if self.procs['interface'].poll() is not None:
                return False
            time.sleep(0.5)
        return False

    def alive(self):
        return {name: proc.poll() is None for name, proc in self.procs.items()}

    @staticmethod
    def _terminate(proc, sig=signal.SIGTERM):
        if proc.poll() is not None:
            return
        try:
            if os.name == 'posix':
                os.killpg(proc.pid, sig)   # le groupe entier : aucun descendant oublié
            elif sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass

    def stop(self, timeout=15):
        self.stop_event.set()
        with self.registry_lock:
            procs = list(self.procs.values())
        for proc in procs:
            self._terminate(proc)
        deadline = time.time() + timeout
        for proc in procs:
            try:
                proc.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                self._terminate(proc, getattr(signal, 'SIGKILL', signal.SIGTERM))
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    pass
        for log in self.logs.values():
            log.close()
        try:
            self.p['runtime'].unlink()
        except OSError:
            pass
        if self.lock is not None:
            try:
                fcntl.flock(self.lock, fcntl.LOCK_UN)
            except OSError:
                pass
            self.lock.close()
            self.lock = None


def open_window(url, user, password, title='AxiorHub Pilote'):
    """Fenêtre WebKitGTK (Ubuntu 24.04 : gir1.2-webkit2-4.1) avec authentification locale automatique ; False si GTK manque."""
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('WebKit2', '4.1')
        from gi.repository import Gtk, WebKit2
    except (ImportError, ValueError):
        return False
    window = Gtk.Window(title=title)
    window.set_default_size(1280, 860)
    view = WebKit2.WebView()
    parsed = urllib.parse.urlsplit(url)
    origin = '%s://%s' % (parsed.scheme, parsed.netloc)

    def authenticate(_view, request):
        # 5.6.24 (F24) : les identifiants locaux ne répondent qu'à l'origine locale attendue (hôte et port) ; toute autre demande est annulée
        try:
            ok = request.get_host() in ('127.0.0.1', 'localhost') and int(request.get_port()) == int(parsed.port or 80)
        except (AttributeError, TypeError, ValueError):
            ok = False
        if not ok:
            request.cancel()
            return True
        request.authenticate(WebKit2.Credential.new(user, password, WebKit2.CredentialPersistence.FOR_SESSION))
        return True

    def policy(_view, decision, kind):
        # liens externes : navigateur du système ; la fenêtre AxiorHub ne quitte jamais l'origine locale
        if kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            try:
                target = decision.get_navigation_action().get_request().get_uri() or ''
            except AttributeError:
                return False
            if target and not (target == origin or target.startswith(origin + '/') or target.startswith('about:')):
                webbrowser.open(target)
                decision.ignore()
                return True
        return False
    view.connect('authenticate', authenticate)
    view.connect('decide-policy', policy)
    window.add(view)
    view.load_uri(url)
    window.connect('destroy', Gtk.main_quit)
    window.show_all()
    Gtk.main()
    return True


def basic_header(user, password):
    return 'Basic ' + base64.b64encode((user + ':' + password).encode('utf-8')).decode('ascii')


def main(argv=None):
    parser = argparse.ArgumentParser(description='AxiorHub Pilote — mode poste')
    parser.add_argument('--no-window', action='store_true', help='services seulement (unité systemd utilisateur)')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--print-password', action='store_true')
    parser.add_argument('--port', type=int, default=int(os.environ.get('AXIORHUB_POSTE_PORT', DEFAULT_PORT)))
    parser.add_argument('--home', default=None)
    parser.add_argument('--interval', type=int, default=5, help='passage périodique de l’agent (minutes)')
    parser.add_argument('--keep-running', action='store_true', help='laisser les services tourner après la fermeture de la fenêtre')
    args = parser.parse_args(argv)
    p = paths(args.home)
    if args.status:
        info = runtime(p)
        print(json.dumps({'actif': bool(info), 'runtime': info, 'interface': probe(info['port']) if info else None,
                          'dossiers': {'donnees': str(p['data']), 'journaux': str(p['logs'])}}, ensure_ascii=False, indent=2))
        return 0
    if args.stop:
        info = runtime(p)
        if not info:
            print('Aucun service AxiorHub lancé par ce poste.')
            return 0
        os.kill(int(info['pid']), signal.SIGTERM)
        print('Arrêt demandé (pid %s).' % info['pid'])
        return 0
    auth = bootstrap(p, args.port)
    user, password = credentials(p)
    if args.print_password:
        print('Adresse : ' + auth['origin'] + PREFIX + '/\nIdentifiant : ' + user + '\nMot de passe : ' + password)
        return 0
    existing = runtime(p)
    supervisor = None
    if existing and probe(existing['port']) in (200, 401):
        port = int(existing['port'])
    else:
        port = args.port
        try:
            supervisor = Supervisor(p, port, interval_minutes=args.interval).start()
        except RuntimeError as error:
            print('Une instance AxiorHub est déjà active pour ce profil (' + str(error) + ').', file=sys.stderr)
            return 1
        stopping = threading.Event()

        def on_signal(*_):
            stopping.set()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, on_signal)
        if not supervisor.wait_ready(90):
            supervisor.stop()
            print('L’interface locale n’a pas démarré : consultez ' + str(p['logs'] / 'interface.log'), file=sys.stderr)
            return 1
    url = origin_for(port) + PREFIX + ('/parametres?rubrique=connexions&premier=1' if first_run(p) else '/')   # 5.6.24 (F19) : premier lancement guidé
    if args.no_window:
        print('Services AxiorHub actifs sur ' + url + ' (identifiants : python3 poste.py --print-password)')
        if supervisor:
            while not stopping.wait(1):
                pass
            supervisor.stop()
        return 0
    if not open_window(url, user, password):
        print('Fenêtre indisponible (python3-gi, gir1.2-gtk-3.0 et gir1.2-webkit2-4.1 requis) : ouverture du navigateur.\n'
              'Identifiant : ' + user + ' — mot de passe dans ' + str(p['password']))
        webbrowser.open(url)
        if supervisor:
            while not stopping.wait(1):
                pass
    if supervisor and not args.keep_running:
        supervisor.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
