"""5.6.108 (lot L) : faux Ollama de bouclage pour les essais de « Configurer l'IA locale » (tests et parcours ``P108l``).

Routes : ``/api/version``, ``/api/tags``, ``/api/ps`` (GET) ; ``/api/show``, ``/api/pull`` (flux NDJSON), ``/api/generate``, ``/api/chat``,
``/api/embed`` (POST). Aucun vrai modèle, aucun téléchargement : un « téléchargement » ajoute le modèle à la liste après quelques lignes de
progression. Toutes les requêtes reçues sont notées (``journal``) pour vérifier qu'aucun ``/api/pull`` ne part sans confirmation.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

GO = 1_000_000_000


def modele(nom, md, taille_go, caps=('completion',), contexte=32768, famille='', distant=False):
    return {'nom': nom, 'md': md, 'taille': int(taille_go * GO), 'caps': list(caps), 'contexte': contexte, 'famille': famille or nom.split(':')[0],
            'distant': distant}


class FauxOllama:
    def __init__(self, modeles=(), version='0.12.99', ps=(), etapes_pull=6, pause_pull=0.05):
        self.modeles = {m['nom']: m for m in modeles}
        self.version, self.ps = version, list(ps)
        self.etapes_pull, self.pause_pull = etapes_pull, pause_pull
        self.erreur_pull_a = None            # numéro de ligne après laquelle le flux rend {"error": ...}
        self.coupure_pull_a = None           # numéro de ligne après laquelle la connexion est fermée sans « success »
        self.journal, self.pulls_interrompus = [], 0
        self.apres_pull = {}                 # nom → modèle « installé » à la fin d'un téléchargement (sinon un modèle de 4 Md)
        self.verrou = threading.Lock()
        faux = self

        class Gestion(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _json(self, valeur, code=200):
                brut = json.dumps(valeur).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(brut)))
                self.end_headers()
                self.wfile.write(brut)

            def do_GET(self):
                with faux.verrou:
                    faux.journal.append(('GET', self.path))
                if self.path == '/api/version':
                    return self._json({'version': faux.version})
                if self.path == '/api/tags':
                    return self._json({'models': [{'name': m['nom'], 'model': m['nom'], 'size': m['taille'],
                                                   'details': {'parameter_size': '%gB' % m['md'], 'family': m['famille']},
                                                   **({'remote_host': 'https://ollama.example.test'} if m['distant'] else {})}
                                                  for m in faux.modeles.values()]})
                if self.path == '/api/ps':
                    return self._json({'models': faux.ps})
                return self._json({'error': 'introuvable'}, 404)

            def do_POST(self):
                corps = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                with faux.verrou:
                    faux.journal.append(('POST', self.path, corps.get('model')))
                nom = str(corps.get('model') or '')
                if self.path == '/api/show':
                    m = faux.modeles.get(nom)
                    if not m:
                        return self._json({'error': 'model not found'}, 404)
                    out = {'capabilities': m['caps'], 'details': {'parameter_size': '%gB' % m['md'], 'family': m['famille']},
                           'model_info': {'%s.context_length' % m['famille']: m['contexte']}}
                    if m['distant']:
                        out['remote_host'] = 'https://ollama.example.test'
                    return self._json(out)
                if self.path == '/api/pull':
                    return self._pull(nom)
                if self.path in ('/api/generate', '/api/chat'):
                    if nom not in faux.modeles:
                        return self._json({'error': 'model not found'}, 404)
                    texte = 'Une mise en demeure est une interpellation fictive du débiteur (essai %s).' % nom
                    if self.path == '/api/chat':
                        return self._json({'message': {'role': 'assistant', 'content': texte}, 'done': True})
                    return self._json({'response': texte, 'done': True})
                if self.path == '/api/embed':
                    if nom not in faux.modeles:
                        return self._json({'error': 'model not found'}, 404)
                    return self._json({'embeddings': [[0.1] * 32 for _ in corps.get('input') or []]})
                return self._json({'error': 'introuvable'}, 404)

            def _pull(self, nom):
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers()
                total = 4 * GO
                lignes = [{'status': 'pulling manifest'}]
                lignes += [{'status': 'pulling 0123456789ab', 'digest': 'sha256:0123456789ab', 'total': total,
                            'completed': total * (i + 1) // faux.etapes_pull} for i in range(faux.etapes_pull)]
                lignes += [{'status': 'verifying sha256 digest'}, {'status': 'writing manifest'}, {'status': 'success'}]
                try:
                    for n, ligne in enumerate(lignes):
                        if faux.erreur_pull_a is not None and n == faux.erreur_pull_a:
                            self.wfile.write(json.dumps({'error': 'max retries exceeded: fictive network error'}).encode() + b'\n')
                            self.wfile.flush()
                            return
                        if faux.coupure_pull_a is not None and n == faux.coupure_pull_a:
                            return
                        self.wfile.write(json.dumps(ligne).encode() + b'\n')
                        self.wfile.flush()
                        time.sleep(faux.pause_pull)
                except OSError:
                    with faux.verrou:
                        faux.pulls_interrompus += 1
                    return
                with faux.verrou:
                    faux.modeles[nom] = faux.apres_pull.get(nom) or modele(nom, 4.0, 2.5, ('completion', 'tools'))

        self.serveur = ThreadingHTTPServer(('127.0.0.1', 0), Gestion)
        self.serveur.daemon_threads = True
        self.url = 'http://127.0.0.1:%d' % self.serveur.server_address[1]
        threading.Thread(target=self.serveur.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()

    def pulls(self):
        with self.verrou:
            return [x for x in self.journal if x[:2] == ('POST', '/api/pull')]

    def arreter(self):
        self.serveur.shutdown()
        self.serveur.server_close()


class FausseSonde:
    """Sonde de machine fictive (aucune lecture du vrai poste)."""

    def __init__(self, plateforme='linux', ram=16.0, coeurs=8, cartes=(), trouve=False, service=False, disque=120.0, ollama_host=''):
        self.plateforme, self.ram, self.nb, self.liste = plateforme, ram, coeurs, list(cartes)
        self.trouve, self.service, self.disque, self.hote = trouve, service, disque, ollama_host

    def memoire_vive_go(self):
        return self.ram

    def coeurs(self):
        return self.nb

    def cartes(self):
        return list(self.liste)

    def executable(self):
        chemin = ('C:/Programmes fictifs/Ollama/ollama.exe' if self.plateforme == 'windows' else '/usr/bin/ollama') if self.trouve else ''
        return {'trouve': self.trouve, 'chemin': chemin, 'service': self.service}

    def disque_libre_go(self):
        return self.disque

    def variable_ollama_host(self):
        return self.hote
