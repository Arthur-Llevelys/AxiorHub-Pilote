#!/usr/bin/env python3
"""AxiorHub Pilote 5.6.48 : nouveau mot de passe d'un compte de l'interface (administrateur compris), sur le serveur.

Usage : sudo python3 /opt/axiorhub-mail-agent/current/reinitialiser-acces.py [identifiant]
Les sessions ouvertes de ce compte sont fermées ; un autre compte que l'administrateur devra changer ce mot de passe à sa connexion."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == '__main__':
    if hasattr(os, 'geteuid') and os.geteuid() != 0:
        raise SystemExit('Exécuter avec sudo python3 reinitialiser-acces.py')
    from agent.connexion5648 import cli_reset
    cli_reset(sys.argv[1:])
