"""5.6.73 : pilotage d'un navigateur Chromium (Edge, Chrome, Chromium) installé sur le poste, par le protocole DevTools.

Aucune dépendance ajoutée : un petit client WebSocket (RFC 6455, côté client) et le protocole DevTools en JSON. Le navigateur est
lancé VISIBLE, avec un profil propre à AxiorHub (connexions conservées d'une séance à l'autre, jamais le profil personnel de
l'avocat), sur un port de débogage choisi par le navigateur et lié à 127.0.0.1 seulement.

Fonctionne seulement sur le poste (application Windows ou Ubuntu) : jamais sur le serveur du cabinet.
"""
import base64
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.request

from .common import Stop

WINDOWS_CANDIDATES = (
    r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe',
    r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe',
    r'%ProgramFiles%\Google\Chrome\Application\chrome.exe',
    r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe',
    r'%LocalAppData%\Google\Chrome\Application\chrome.exe',
    r'%LocalAppData%\Chromium\Application\chrome.exe',
)
UNIX_NAMES = ('microsoft-edge', 'microsoft-edge-stable', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')


def find_browser(env=None):
    """Chemin du navigateur Chromium du poste ('' s'il n'y en a pas). ``AXIORHUB_NAVIGATEUR`` force un chemin."""
    env = os.environ if env is None else env
    forced = str(env.get('AXIORHUB_NAVIGATEUR') or '').strip()
    if forced:
        return forced if Path(forced).is_file() else ''
    if os.name == 'nt':
        for raw in WINDOWS_CANDIDATES:
            path = os.path.expandvars(raw)
            if '%' not in path and Path(path).is_file():
                return path
        return ''
    for name in UNIX_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return ''


# ------------------------------------------------------------------------------------------------------- WebSocket (client)
class WebSocket:
    """Client WebSocket minimal : trames texte, fragmentation, ping/pong, fermeture. Pas de TLS (127.0.0.1 seulement)."""

    def __init__(self, url, timeout=15):
        if not url.startswith('ws://127.0.0.1:') and not url.startswith('ws://localhost:'):
            raise Stop('navigateur_adresse_devtools_invalide')
        rest = url[len('ws://'):]
        hostport, _, path = rest.partition('/')
        host, _, port = hostport.partition(':')
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = ('GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
                   'Sec-WebSocket-Version: 13\r\n\r\n') % (path, hostport, key)
        self.sock.sendall(request.encode('ascii'))
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise Stop('navigateur_devtools_ferme')
            head += chunk
            if len(head) > 65536:
                raise Stop('navigateur_devtools_reponse_invalide')
        status, _, self._buffer = head.partition(b'\r\n\r\n')
        if b' 101 ' not in status.split(b'\r\n', 1)[0]:
            raise Stop('navigateur_devtools_refuse')
        self.sock.settimeout(None)
        self._lock = threading.Lock()

    def _recv_exact(self, n):
        while len(self._buffer) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buffer)))
            if not chunk:
                raise ConnectionError('websocket_ferme')
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def send_text(self, text):
        payload = text.encode('utf-8')
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack('!H', n)
        else:
            header.append(0x80 | 127)
            header += struct.pack('!Q', n)
        mask = secrets.token_bytes(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(bytes(header) + masked)

    def _send_control(self, opcode, payload=b''):
        mask = secrets.token_bytes(4)
        frame = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(frame)

    def recv_text(self):
        """Message texte suivant (fragments réassemblés) ; ``None`` à la fermeture."""
        parts = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack('!H', self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack('!Q', self._recv_exact(8))[0]
            if b2 & 0x80:   # le serveur ne masque pas ; par prudence
                mask = self._recv_exact(4)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._recv_exact(n)))
            else:
                data = self._recv_exact(n)
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                self._send_control(0xA, data[:125])
                continue
            if opcode == 0xA:
                continue
            parts.append(data)
            if fin:
                return b''.join(parts).decode('utf-8', 'replace')

    def close(self):
        try:
            self._send_control(0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------------- client DevTools
class CDP:
    """Connexion DevTools : commandes avec réponse, événements en file ; sessions « à plat » (flatten) par onglet."""

    def __init__(self, ws):
        self.ws = ws
        self._next = 0
        self._waiting = {}
        self._lock = threading.Lock()
        self.events = queue.Queue()
        self.closed = False
        self._reader = threading.Thread(target=self._read, name='cdp5673', daemon=True)
        self._reader.start()

    def _read(self):
        try:
            while True:
                raw = self.ws.recv_text()
                if raw is None:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if 'id' in msg:
                    box = self._waiting.pop(msg['id'], None)
                    if box is not None:
                        box.put(msg)
                else:
                    self.events.put(msg)
        except (OSError, ConnectionError, ValueError):
            pass
        finally:
            self.closed = True
            for box in list(self._waiting.values()):
                box.put({'error': {'message': 'navigateur_ferme'}})
            self._waiting.clear()

    def send(self, method, params=None, session=None, timeout=30):
        if self.closed:
            raise Stop('navigateur_ferme')
        with self._lock:
            self._next += 1
            ident = self._next
        box = queue.Queue(maxsize=1)
        self._waiting[ident] = box
        msg = {'id': ident, 'method': method, 'params': params or {}}
        if session:
            msg['sessionId'] = session
        self.ws.send_text(json.dumps(msg))
        try:
            reply = box.get(timeout=timeout)
        except queue.Empty:
            self._waiting.pop(ident, None)
            raise Stop('navigateur_delai_depasse') from None
        if 'error' in reply:
            raise Stop('navigateur_erreur:' + str(reply['error'].get('message') or '')[:160])
        return reply.get('result') or {}

    def drain(self, method=None):
        """Événements reçus depuis le dernier appel (filtrés par méthode si demandé)."""
        out = []
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                return out
            if method is None or ev.get('method') == method:
                out.append(ev)

    def close(self):
        self.ws.close()


# ------------------------------------------------------------------------------------------------------- lancement
class Browser:
    """Navigateur visible du poste, profil AxiorHub, port DevTools choisi par le navigateur (fichier DevToolsActivePort)."""

    def __init__(self, profile_dir, executable='', downloads_dir='', headless=False):
        self.headless = bool(headless)   # essais automatiques seulement : la séance de l'avocat est toujours visible
        self.profile = Path(profile_dir)
        self.executable = executable or find_browser()
        self.downloads = Path(downloads_dir) if downloads_dir else self.profile.parent / 'telechargements'
        self.process = None
        self.cdp = None

    def start(self, timeout=30):
        if not self.executable:
            raise Stop('navigateur_absent_du_poste')
        self.profile.mkdir(parents=True, exist_ok=True)
        self.downloads.mkdir(parents=True, exist_ok=True)
        marker = self.profile / 'DevToolsActivePort'
        try:
            marker.unlink()
        except OSError:
            pass
        args = [self.executable, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', '--user-data-dir=' + str(self.profile),
                '--no-first-run', '--no-default-browser-check', '--disable-sync', '--lang=fr-FR', '--new-window', 'about:blank']
        if self.headless:
            args.insert(1, '--headless=new')
        flags = 0x08000000 if os.name == 'nt' else 0   # CREATE_NO_WINDOW : pas de console (la fenêtre du navigateur reste visible)
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if marker.is_file():
                lines = marker.read_text(encoding='utf-8', errors='replace').splitlines()
                if len(lines) >= 2 and lines[0].strip().isdigit():
                    port, path = int(lines[0].strip()), lines[1].strip()
                    self.cdp = CDP(WebSocket('ws://127.0.0.1:%d%s' % (port, path)))
                    self.cdp.send('Browser.setDownloadBehavior', {'behavior': 'allowAndName', 'downloadPath': str(self.downloads),
                                                                  'eventsEnabled': True})
                    return self
            # pas d'arrêt si le processus lancé se termine : sous Windows, msedge.exe peut passer la main au navigateur et se fermer
            time.sleep(0.2)
        self.stop()
        raise Stop('navigateur_demarrage_impossible')

    def alive(self):
        return bool(self.process and self.process.poll() is None and self.cdp and not self.cdp.closed)

    def stop(self):
        if self.cdp is not None:
            try:
                self.cdp.send('Browser.close', timeout=5)
            except Stop:
                pass
            self.cdp.close()
            self.cdp = None
            deadline = time.monotonic() + 10   # le navigateur libère son profil (verrou supprimé à la sortie)
            while (self.profile / 'lockfile').exists() and time.monotonic() < deadline:
                time.sleep(0.2)
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.process.kill()
                except OSError:
                    pass
        self.process = None


def version_info(port):
    """Lecture de /json/version (diagnostic)."""
    with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % int(port), timeout=5) as r:
        return json.loads(r.read().decode('utf-8'))
