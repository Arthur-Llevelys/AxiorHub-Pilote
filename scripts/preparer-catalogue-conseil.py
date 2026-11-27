#!/usr/bin/env python3
"""5.6.111 (lot K, F06) : exporte les trames livrées et leurs questionnaires natifs pour révision par un juriste.

Aucune base, API, donnée client ou installation n'est lue ni modifiée : seules les trames intégrées à la source (``bibliotheque5639.BUILTIN``)
sont exportées, avec le questionnaire que ``questionnaires56108.deriver`` en tire. Ce paquet n'est PAS une bibliothèque juridiquement validée :
il sert à relire puis à améliorer le registre existant, par identifiant et version, sans second moteur ni import automatique. La date et
l'auteur d'une validation ne sont jamais fabriqués ici (``droits56108.qualification`` qualifie toute trame livrée comme brouillon).

  python3 scripts/preparer-catalogue-conseil.py --sortie /chemin/catalogue-conseil.json
  python3 scripts/preparer-catalogue-conseil.py --source /autre/arbre --sortie catalogue.json

Repris de l'outil d'exemple du dossier technique 5.6.111 (``exemples/preparer_catalogue_conseil.py``).
"""
from pathlib import Path
import argparse
import json
import sys

RACINE = Path(__file__).resolve().parents[1]


def construire():
    from agent import bibliotheque5639 as lib, droits56108 as droits, questionnaires56108 as q
    items = []
    for ident in sorted(lib.BUILTIN):
        t = lib._build(ident, None)   # source intégrée uniquement ; pas les personnalisations du cabinet
        meta = droits.qualification(t)
        schema = q.deriver(t, requis=meta['champs_requis'])
        # La date et l'auteur de validation ne sont jamais fabriqués par cet export.
        items.append({'id': ident, 'version_source': t['version'], 'titre': t['titre'],
                      'systeme_juridique': meta['systeme'], 'type_acte': meta['type_acte'], 'langue': meta['langue'],
                      'etat_initial': meta['etat_initial'], 'valide_par': meta['valide_par'], 'valide_le': meta['valide_le'],
                      'sources_de_controle': meta['sources'], 'questionnaire': schema, 'trame': t['texte']})
    return {'format': 'axiorhub-revision-conseil-1', 'origine': 'trames integrees de la source, sans donnees du cabinet',
            'usage': 'Revision puis mise a jour des memes identifiants dans le registre natif ; pas import automatique',
            'modeles': items}


def main(argv=None):
    p = argparse.ArgumentParser(description='Exporte les trames livrées et leurs questionnaires pour révision (aucune validation fabriquée).')
    p.add_argument('--source', default=str(RACINE), help='racine de l’arbre AxiorHub à lire (par défaut : celui de ce script)')
    p.add_argument('--sortie', required=True, help='fichier JSON à écrire')
    a = p.parse_args(argv)
    sys.path.insert(0, str(Path(a.source).resolve()))
    out = construire()
    Path(a.sortie).write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(str(len(out['modeles'])) + ' trames exportees pour revision, aucune validee automatiquement')
    return 0


if __name__ == '__main__':
    sys.exit(main())
