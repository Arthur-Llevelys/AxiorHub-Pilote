#!/usr/bin/env python3
"""Maintenance explicite du portefeuille et rapprochement des clôtures (AxiorHub 5.6.100, R05 et R10).

  # Aperçu (aucune écriture) : lignes orphelines, décisions humaines conservées, lignes d'un ancien identifiant, jeton du plan
  python scripts/maintenance56100.py --config /chemin/config.json simuler [--json]
  # Suppression des seules lignes de l'aperçu (même jeton), après sauvegarde vérifiée ; inscrite au journal d'audit
  python scripts/maintenance56100.py --config /chemin/config.json nettoyer --jeton JETON --operateur "Me NOM"
  # Rapprochement en LECTURE SEULE d'une sauvegarde antérieure et de l'état courant des cinq tables
  python scripts/maintenance56100.py rapprocher /chemin/sauvegarde.sqlite3 /chemin/desk.sqlite3 [--dossier ID] [--json]
  # Réinsertion, pour UN dossier, des lignes manquantes (simulation sans --appliquer ; jamais d'écrasement)
  python scripts/maintenance56100.py --config /chemin/config.json restaurer --sauvegarde S --dossier ID --operateur "Me NOM" [--appliquer]
  # Dossiers archivés sans décision de clôture ou avec une échéance critique ouverte (lecture seule) ; --reprendre termine les suites
  python scripts/maintenance56100.py --config /chemin/config.json clotures [--json] [--reprendre]

Arrêter le service (interface et worker) avant « nettoyer » et « restaurer » ; travailler sur une copie pour un premier essai.
Rien n'est jamais rouvert, annulé ni supprimé sans commande explicite ; aucune connexion à un service extérieur.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _desk(config):
    from agent.common import load_config
    from agent.desk import Desk
    return Desk(load_config(config))


def _afficher(valeur, en_json):
    if en_json:
        print(json.dumps(valeur, ensure_ascii=False, indent=2, default=str))
        return
    if isinstance(valeur, dict):
        for cle, v in valeur.items():
            if isinstance(v, list):
                print('%s : %d' % (cle, len(v)))
                for x in v[:50]:
                    print('  -', json.dumps(x, ensure_ascii=False, default=str))
            else:
                print('%s : %s' % (cle, json.dumps(v, ensure_ascii=False, default=str)))
    else:
        for x in valeur:
            print('-', json.dumps(x, ensure_ascii=False, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(description='Maintenance du portefeuille et des clôtures (5.6.100).')
    p.add_argument('--config', help='configuration de l’instance (config.json)')
    sous = p.add_subparsers(dest='commande', required=True)
    s = sous.add_parser('simuler')
    s.add_argument('--json', action='store_true')
    s = sous.add_parser('nettoyer')
    s.add_argument('--jeton', required=True)
    s.add_argument('--operateur', required=True)
    s = sous.add_parser('rapprocher')
    s.add_argument('sauvegarde')
    s.add_argument('courant')
    s.add_argument('--dossier', default='')
    s.add_argument('--json', action='store_true')
    s = sous.add_parser('restaurer')
    s.add_argument('--sauvegarde', required=True)
    s.add_argument('--dossier', required=True)
    s.add_argument('--operateur', default='')
    s.add_argument('--appliquer', action='store_true')
    s = sous.add_parser('clotures')
    s.add_argument('--json', action='store_true')
    s.add_argument('--reprendre', action='store_true')
    a = p.parse_args(argv)
    from agent import portefeuille56100 as P
    from agent.common import Stop
    try:
        if a.commande == 'rapprocher':
            _afficher(P.rapprocher(a.sauvegarde, a.courant, a.dossier), a.json)
            return 0
        if not a.config:
            p.error('--config est requis pour cette commande')
        desk = _desk(a.config)
        try:
            if a.commande == 'simuler':
                _afficher(P.simuler(desk), a.json)
            elif a.commande == 'nettoyer':
                _afficher(P.nettoyer(desk, a.jeton, a.operateur), False)
            elif a.commande == 'restaurer':
                _afficher(P.restaurer_dossier(desk, a.sauvegarde, a.dossier, a.operateur, a.appliquer), False)
            elif a.commande == 'clotures':
                from agent import cloture5697
                from agent.acces5697 import AGENT
                if a.reprendre:
                    print('suites reprises : %d dossier(s)' % cloture5697.reprendre_suites(desk))
                _afficher(cloture5697.rapprochement_clotures(desk, AGENT), a.json)
        finally:
            desk.db.close()
    except Stop as ex:
        print('Refusé : %s' % ex, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
