"""5.6.25 : point d'entrée de l'application Windows empaquetée.

Deux exécutables partagent ce lanceur :
- ``AxiorHub Pilote.exe`` (sans console) : fenêtre et superviseur (``poste.py``), ou désinstallation (``--desinstaller``) ;
- ``axiorhub-service.exe`` (console masquée par le superviseur) : services lancés comme scripts de l'application
  (``web.py serve|worker|watch``, ``manage.py run``, ``docker/bootstrap.py``).

Seuls les scripts présents dans le dossier de l'application peuvent être exécutés ainsi.
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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = base()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault('PYTHONUTF8', '1')
    if argv and argv[0].endswith('.py'):
        return run_script(root, argv)
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
