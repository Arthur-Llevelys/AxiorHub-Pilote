#!/usr/bin/env python3
"""5.6.108 (lot E) : banc d'essai avant/après de l'IA, sur des situations fictives (``tests/donnees/banc_ia/situations.json``).

  # mode statique (sans modèle, déterministe) : ce que l'IA reçoit, avant et après
  python scripts/banc-ia.py --avant ../src56107 --sortie <dossier hors des arbres>

  # mode modèle : en plus, ce que l'IA produit (Ollama local seulement, jamais un modèle « :cloud »)
  python scripts/banc-ia.py --avant ../src56107 --modele ollama:ministral-3:3b --sortie <dossier>

  # note de fond par un modèle local plus gros (avis d'un modèle, jamais une vérité)
  python scripts/banc-ia.py --avant ../src56107 --modele ollama:<petit> --juge ollama:<gros> --sortie <dossier>

  # plusieurs modèles dans la même passe (un tableau par modèle), sur l'Ollama du réseau privé du cabinet, pause entre deux appels
  python scripts/banc-ia.py --avant ../src56107 --modele ollama:<a> --modele ollama:<b> --hote-reseau-prive http://<adresse>:11434 --pause-ms 500 --sortie <dossier>

  # mode chaîne (5.6.108, lot R) : pour un arbre qui expose agent.references56108.produire, recherche d'office, besoins de recherche,
  # rédaction, barrière et reprise par le produit, connecteurs factices (champ « corpus » des situations) ; l'arbre 5.6.107 garde le rejeu
  python scripts/banc-ia.py --avant ../src56107 --modele ollama:ministral-3:3b --chaine --hote-reseau-prive http://<adresse>:11434 --sortie <dossier>
  # barrière des références imposée aux grands modèles aussi, dans l'arbre « après » (réglage du produit : petits / locaux (défaut) / tous)
  python scripts/banc-ia.py --avant ../src56107 --modele ollama:qwen3.8:27b --chaine --barriere-tous --hote-reseau-prive http://<adresse>:11434 --sortie <dossier>

  # mode conseil (5.6.108, lot K) : conseil de modèles du produit (réponses indépendantes, revue croisée anonyme, synthèse, barrière) contre
  # chaque membre seul (chaîne du lot R), familles consultation, acte, audience, recherche ; rapport-conseil.json et rapport-conseil.md
  python scripts/banc-ia.py --conseil ollama:<a>,ollama:<b>[,ollama:<c>] [--president ollama:<x>] --hote-reseau-prive http://<adresse>:11434 --sortie <dossier>

Options : ``--ids A,B``, ``--famille consultation``, ``--max 3``, ``--hote http://127.0.0.1:11434``, ``--graine 42``, ``--textes``.
``--hote-reseau-prive`` n'admet qu'une adresse IP littérale privée (10/8, 172.16/12, 192.168/16, fc00::/7), n'est jamais lue dans
``OLLAMA_HOST``, ignore les mandataires ; le rapport n'indique que « hôte : réseau privé », jamais l'adresse.
Les instructions sont construites par le point d'entrée réel du produit (``competences5632.for_job`` puis ``Model._leaf`` sur un faux
transport), dans un sous-processus par arbre, sur une base temporaire. Rien n'est écrit dans un arbre mesuré.
"""
from pathlib import Path
import sys

sys.dont_write_bytecode = True   # rien n'est écrit dans le dépôt pendant une exécution (pas de __pycache__)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from banc_ia56108.banc import principal  # noqa: E402
from banc_ia56108.ollama_local import Refus  # noqa: E402

if __name__ == '__main__':
    try:
        principal(sys.argv[1:])
    except Refus as refus:
        sys.stderr.write('Refusé : %s\n' % refus)
        sys.exit(2)
