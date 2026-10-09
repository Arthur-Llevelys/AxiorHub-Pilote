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
import urllib.request
import webbrowser

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


def runtime(p):
    try:
        info = json.loads(p['runtime'].read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    try:
        os.kill(int(info['pid']), 0)
    except (OSError, ValueError, TypeError):
        return None
    return info


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

    def __init__(self, p, port=DEFAULT_PORT, python=None, interval_minutes=5, root=ROOT):
        self.p, self.port, self.python, self.root = p, int(port), python or sys.executable, Path(root)
        self.interval = max(1, int(interval_minutes)) * 60
        self.procs, self.logs, self.stop_event = {}, {}, threading.Event()
        self.timer = None

    def _spawn(self, name, args):
        log = open(self.p['logs'] / (name + '.log'), 'ab')
        self.logs[name] = log
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'}
        self.procs[name] = subprocess.Popen([self.python, *args], cwd=str(self.root), stdout=log, stderr=subprocess.STDOUT, env=env)

    def start(self):
        cfg = ['--config', str(self.p['config'])]
        self._spawn('interface', [str(self.root / 'web.py'), 'serve', *cfg, '--auth', str(self.p['auth']), '--port', str(self.port)])
        self._spawn('worker', [str(self.root / 'web.py'), 'worker', *cfg])
        self._spawn('veille', [str(self.root / 'web.py'), 'watch', *cfg])
        self.timer = threading.Thread(target=self._periodic, name='axiorhub-poste-periodique', daemon=True)
        self.timer.start()
        self.p['runtime'].parent.mkdir(parents=True, exist_ok=True)
        _write_private(self.p['runtime'], json.dumps({'pid': os.getpid(), 'port': self.port, 'started': time.strftime('%Y-%m-%dT%H:%M:%S'),
                                                      'processes': {k: v.pid for k, v in self.procs.items()}}) + '\n')
        return self

    def _periodic(self):
        while not self.stop_event.wait(self.interval):
            try:
                with open(self.p['logs'] / 'passage.log', 'ab') as log:
                    subprocess.run([self.python, str(self.root / 'manage.py'), '--config', str(self.p['config']), 'run'],
                                   cwd=str(self.root), stdout=log, stderr=subprocess.STDOUT, timeout=3600)
            except (OSError, subprocess.SubprocessError):
                pass

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

    def stop(self, timeout=15):
        self.stop_event.set()
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + timeout
        for proc in self.procs.values():
            try:
                proc.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                proc.kill()
        for log in self.logs.values():
            log.close()
        try:
            self.p['runtime'].unlink()
        except OSError:
            pass


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

    def authenticate(_view, request):
        request.authenticate(WebKit2.Credential.new(user, password, WebKit2.CredentialPersistence.FOR_SESSION))
        return True
    view.connect('authenticate', authenticate)
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
        supervisor = Supervisor(p, port, interval_minutes=args.interval).start()
        stopping = threading.Event()

        def on_signal(*_):
            stopping.set()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, on_signal)
        if not supervisor.wait_ready(90):
            supervisor.stop()
            print('L’interface locale n’a pas démarré : consultez ' + str(p['logs'] / 'interface.log'), file=sys.stderr)
            return 1
    url = origin_for(port) + PREFIX + '/'
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
