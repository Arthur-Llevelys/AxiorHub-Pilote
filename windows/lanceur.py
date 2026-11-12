"""5.6.25 : point d'entrée de l'application Windows empaquetée.

Deux exécutables partagent ce lanceur :
- ``AxiorHub Pilote.exe`` (sans console) : fenêtre et superviseur (``poste.py``), ou désinstallation (``--desinstaller``) ;
- ``axiorhub-service.exe`` (console masquée par le superviseur) : services lancés comme scripts de l'application
  (``web.py serve|worker|watch``, ``manage.py run``, ``docker/bootstrap.py``).

Seuls les scripts présents dans le dossier de l'application peuvent être exécutés ainsi.

5.6.97 (A05) : sous-programme de liaison ``--liaison <adresse>`` (protocole ``axiorhub://`` déclaré par l'installateur, la commande
enregistrée passe l'adresse comme UN argument, sans interpréteur de commandes). L'adresse est vérifiée ici : seul l'identifiant opaque
de ``axiorhub://chatgpt/liaison?id=…`` est transmis au poste ; aucune commande, aucun domaine, aucun autre paramètre n'est accepté.
"""
import os
from pathlib import Path
import runpy
import sys


def base():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])).resolve()


def run_script(root, argv):
    script = Path(argv[0])
    if not script.is_absolute():
        script = root / script
    script = script.resolve()
    if root not in script.parents or script.suffix != '.py' or not script.is_file():
        sys.stderr.write('Script hors de l’application refusé.\n')
        return 2
    sys.argv = [str(script)] + list(argv[1:])
    runpy.run_path(str(script), run_name='__main__')
    return 0


def liaison(root, rest):
    """Lien ``axiorhub://chatgpt/liaison?id=…`` : identifiant vérifié, puis fenêtre du poste sur la page de liaison."""
    if len(rest) != 1:
        sys.stderr.write('Lien de liaison refusé.\n')
        return 2
    from agent.chatgpt5697 import lire_lien
    from agent.common import Stop
    try:
        ident = lire_lien(rest[0])
    except Stop:
        sys.stderr.write('Lien de liaison refusé.\n')
        return 2
    return run_script(root, ['poste.py', '--liaison', 'axiorhub://chatgpt/liaison?id=' + ident])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = base()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault('PYTHONUTF8', '1')
    if argv and argv[0].endswith('.py'):
        return run_script(root, argv)
    if argv[:1] == ['--liaison']:
        return liaison(root, argv[1:])
    if argv[:1] == ['--desinstaller']:
        from windows.deploiement import uninstall_dialog
        return uninstall_dialog()
    return run_script(root, ['poste.py'] + argv)


if __name__ == '__main__':
    try:
        code = main()
    except SystemExit as exit_:
        code = exit_.code
    sys.exit(code if isinstance(code, int) else 0)
