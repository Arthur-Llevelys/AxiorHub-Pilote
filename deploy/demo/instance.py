#!/usr/bin/env python3
"""Instance de démonstration d'un visiteur (5.6.107, lot H). Lancée par le répartiteur (``agent/demo_hote56107.py``), jamais à la main.

Ordre, avant toute donnée :
1. entrée dans son sous-groupe cgroup (mémoire, processeur, nombre de processus) quand systemd l'a délégué ;
2. limites du processus (``setrlimit``) : temps processeur total, taille d'un fichier, pas de fichier « core » ; mémoire adressable
   quand aucun cgroup ne la borne (conteneur Docker) ; priorité basse. Le travailleur (processus fils) en hérite ;
3. garde réseau dans le processus : toute prise autre qu'Unix est refusée. Ce n'est pas la barrière — elle est posée par le système
   (``PrivateNetwork=yes`` / ``RestrictAddressFamilies=AF_UNIX`` ou réseau Docker ``none``) — mais une ceinture de plus ;
4. création du cabinet fictif par l'API publique du lot M : ``demo56107.creer(<dossier>/donnees, origine=…, prefixe=…)`` rend le
   chemin de ``config.json`` ; la configuration doit être dans le dossier de l'instance et ``demo56107.actif`` doit la reconnaître,
   sinon **l'instance ne sert rien** (code de sortie 3) ;
5. travailleur de la démo (``demo56107.commandes(...)['travailleur']``, jamais la veille IMAP), puis interface (``agent.web.App`` sur
   ``ui-auth.json``, comme ``web.py serve``) servie sur ``<dossier>/instance.sock`` (droits 0600) : aucun port réseau n'est ouvert.

La session du visiteur (compte fictif de la démo) s'ouvre par le jeton de lancement à usage unique que le répartiteur demande à
``demo56107.jeton_de_session`` ; « Quitter » dans le bandeau écrit la demande que le répartiteur lit (``demo56107.quitter_demande``).
"""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys

RACINE = Path(__file__).resolve().parents[2]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))


def entrer_cgroup(groupe):
    if groupe:
        Path(groupe, 'cgroup.procs').write_text(str(os.getpid()))


def limiter(memoire_mo, cpu_secondes, taille_max_mo, avec_cgroup):
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_secondes, cpu_secondes + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (taille_max_mo << 20, taille_max_mo << 20))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if not avec_cgroup:
        # Mémoire adressable (et non résidente) : marge du quadruple, les piles des fils et les zones d'allocation réservent sans occuper.
        resource.setrlimit(resource.RLIMIT_AS, (memoire_mo << 22, memoire_mo << 22))
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass


class _PriseUnixSeulement(socket.socket):
    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        if fileno is None and family not in (getattr(socket, 'AF_UNIX', None),):
            raise OSError(101, 'démonstration : aucun accès réseau')   # 101 = ENETUNREACH
        super().__init__(family, type, proto, fileno)


def couper_reseau():
    """Garde dans le processus : prises IP et résolution de noms refusées (une connexion Unix reste possible)."""
    def _refus(*_a, **_k):
        raise OSError(101, 'démonstration : aucun accès réseau')
    socket.socket = _PriseUnixSeulement
    socket.create_connection = _refus
    socket.getaddrinfo = _refus
    socket.gethostbyname = _refus


def creer(dossier, origine, prefixe):
    """Cabinet fictif (API du lot M) ; rend le chemin de la configuration, contrôlée. Toute anomalie : sortie 3, rien n'est servi."""
    from agent import demo56107
    from agent.common import load_config
    dossier = Path(dossier).resolve()
    config = Path(demo56107.creer(dossier / 'donnees', origine=origine, prefixe=prefixe)).resolve()
    if dossier not in config.parents:
        raise SystemExit(3)                   # une configuration hors du dossier de l'instance n'est jamais servie
    if not demo56107.actif(load_config(str(config))):
        raise SystemExit(3)                   # jamais une instance hors mode Démo derrière la démo publique
    os.environ.update(demo56107.environnement(config))
    return config


def application(config):
    """Interface de l'instance, préparée comme ``agent.web.serve`` (registre du budget et passerelle des appels sortants)."""
    from agent import __version__, budget_ledger5689, passerelle5689
    from agent.common import load_config
    from agent.web import App
    app = App(str(config), str(Path(config).parent / 'ui-auth.json'))
    reglages = load_config(str(config))
    budget_ledger5689.preparer(reglages, 'web', __version__)
    passerelle5689.installer(reglages)
    return app


def travailleur(config):
    """Travailleur de la démo (file des travaux, IA de démonstration) : même groupe de processus, mêmes limites, même fin."""
    from agent import demo56107
    c = demo56107.commandes(config)
    return subprocess.Popen(c['travailleur'], cwd=c['cwd'], env={**os.environ, **c['env']}, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, close_fds=True)


def servir(app, chemin_socket, fils, corps_max_mo):
    from waitress import create_server
    serveur = create_server(app, unix_socket=chemin_socket, unix_socket_perms='600', threads=fils, ident='', send_bytes=1,
                            max_request_body_size=corps_max_mo << 20)
    serveur.run()


def main(argv=None):
    p = argparse.ArgumentParser(description='Instance de démonstration (lancée par le répartiteur).')
    p.add_argument('--dossier', required=True)
    p.add_argument('--socket', required=True)
    p.add_argument('--origine', required=True)
    p.add_argument('--prefixe', default='/agent-courriel')
    p.add_argument('--memoire-mo', type=int, default=512)
    p.add_argument('--cpu-secondes', type=int, default=1800)
    p.add_argument('--taille-max-mo', type=int, default=200)
    p.add_argument('--fils', type=int, default=8)
    p.add_argument('--corps-max-mo', type=int, default=10)
    p.add_argument('--cgroup', default='')
    p.add_argument('--sans-travailleur', action='store_true')
    a = p.parse_args(argv)
    os.umask(0o077)
    entrer_cgroup(a.cgroup)
    limiter(a.memoire_mo, a.cpu_secondes, a.taille_max_mo, bool(a.cgroup))
    couper_reseau()
    os.environ.update({'AXIORHUB_DEMO': '1', 'AXIORHUB_PUBLIC_URL': a.origine})
    config = creer(a.dossier, a.origine, a.prefixe)
    app = application(config)
    if not a.sans_travailleur:
        travailleur(config)
    servir(app, a.socket, a.fils, a.corps_max_mo)


if __name__ == '__main__':
    main()
