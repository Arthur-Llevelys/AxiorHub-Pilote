"""5.6.25 : installateur Windows d'AxiorHub Pilote (un seul fichier, pour l'utilisateur courant, sans droit administrateur).

Fenêtre : choix du raccourci sur le Bureau, du lancement des services à l'ouverture de session et du démarrage immédiat.
Ligne de commande (déploiement, essais) :
  --silencieux                 aucune fenêtre
  --racine DOSSIER             dossier du programme (défaut : %LOCALAPPDATA%\\Programs\\AxiorHub Pilote)
  --sans-raccourcis, --sans-registre, --sans-bureau, --demarrage, --lancer
"""
import argparse
from pathlib import Path
import subprocess
import sys

from windows import deploiement


def bundled(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / name


def version():
    try:
        return bundled('version.txt').read_text(encoding='utf-8').strip()
    except OSError:
        return 'inconnue'


def launch(exe):
    subprocess.Popen([str(exe)], cwd=str(Path(exe).parent), close_fds=True)


def run_silent(args):
    exe = deploiement.install(bundled('app.zip'), version(), root=args.racine, desktop=not args.sans_bureau, startup=args.demarrage,
                              shortcuts=not args.sans_raccourcis, registry=not args.sans_registre, progress=print)
    print(str(exe))
    if args.lancer:
        launch(exe)
    return 0


def run_window():
    import tkinter
    from tkinter import messagebox, ttk
    ver = version()
    root = tkinter.Tk()
    root.title('Installation d’AxiorHub Pilote ' + ver)
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=18)
    frame.grid()
    ttk.Label(frame, text='AxiorHub Pilote ' + ver, font=('Segoe UI', 14, 'bold')).grid(sticky='w')
    ttk.Label(frame, wraplength=440, justify='left',
              text='L’agent de cabinet sur ce poste : vos données restent sur votre ordinateur. Installation pour votre compte Windows '
                   'seulement, sans droit administrateur, dans ' + str(deploiement.install_root()) + '.').grid(sticky='w', pady=(6, 10))
    desktop, startup, start_now = tkinter.BooleanVar(value=True), tkinter.BooleanVar(value=False), tkinter.BooleanVar(value=True)
    ttk.Checkbutton(frame, text='Raccourci sur le Bureau', variable=desktop).grid(sticky='w')
    ttk.Checkbutton(frame, text='Lancer les services à l’ouverture de session (veille des courriels, tâches de l’agent)', variable=startup).grid(sticky='w')
    ttk.Checkbutton(frame, text='Ouvrir AxiorHub Pilote à la fin de l’installation', variable=start_now).grid(sticky='w')
    status = ttk.Label(frame, text='Prérequis : Ollama pour l’intelligence artificielle locale (ou une adresse Ollama du cabinet).', wraplength=440)
    status.grid(sticky='w', pady=(10, 6))
    buttons = ttk.Frame(frame)
    buttons.grid(sticky='e')

    def go():
        install_button.state(['disabled'])

        def say(message):
            status.configure(text=message)
            root.update()
        try:
            exe = deploiement.install(bundled('app.zip'), ver, desktop=desktop.get(), startup=startup.get(), progress=say)
        except Exception as error:   # message lisible plutôt qu'une trace
            messagebox.showerror('AxiorHub Pilote', 'Installation interrompue : %s' % error, parent=root)
            install_button.state(['!disabled'])
            return
        if start_now.get():
            launch(exe)
        messagebox.showinfo('AxiorHub Pilote', 'AxiorHub Pilote est installé. Il se trouve dans le menu Démarrer.\n'
                                               'Au premier lancement, Paramètres › Connexions vous guide en trois étapes.', parent=root)
        root.destroy()
    install_button = ttk.Button(buttons, text='Installer', command=go)
    install_button.grid(row=0, column=0, padx=4)
    ttk.Button(buttons, text='Annuler', command=root.destroy).grid(row=0, column=1)
    root.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Installateur AxiorHub Pilote (Windows)')
    parser.add_argument('--silencieux', action='store_true')
    parser.add_argument('--racine', default=None)
    parser.add_argument('--sans-raccourcis', action='store_true')
    parser.add_argument('--sans-registre', action='store_true')
    parser.add_argument('--sans-bureau', action='store_true')
    parser.add_argument('--demarrage', action='store_true')
    parser.add_argument('--lancer', action='store_true')
    args = parser.parse_args(argv)
    return run_silent(args) if args.silencieux else run_window()


if __name__ == '__main__':
    sys.exit(main())
