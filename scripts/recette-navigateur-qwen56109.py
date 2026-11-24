"""5.6.109 (lot O, G24) : recette du navigateur supervisé avec Ollama et le modèle exact ``qwen3.5:9b`` — script prêt à lancer par le cabinet.

Ce script n'a PAS été exécuté avec un vrai modèle pour la livraison 5.6.109 : aucun modèle réel n'était disponible. La compatibilité réelle
reste à démontrer par ce protocole, sur Windows 11 ET sur Ubuntu 24.04, sur un poste décrit (processeur, carte graphique, mémoire).

Protocole (trente exécutions complètes) :
- trois parcours : ``rechercher_societe`` (ouvrir le profil, saisir l'identifiant connu, comparer identité et registre, lire le résultat),
  ``recuperer_document`` (ouvrir la bonne fiche, choisir l'acte, télécharger, vérifier, rattacher au dossier fictif) et
  ``preparer_formulaire`` (lire les champs, rapprocher les données validées, remplir, vérifier valeurs et pièces, SANS transmission finale) ;
- cinq fois chacun sur Windows 11 et cinq fois chacun sur Ubuntu 24.04, démarrages à froid et à chaud, jeux de données fictifs distincts
  (DURAND, MARTIN, ZORVAL, SIREN d'essai 000010017 à 000010090) ;
- d'abord sur un site HTTPS de recette contrôlé par le cabinet (``--site``), puis sur les profils de sites autorisés, publiés séparément.
Une demande de main correcte dans un cas conçu pour manquer d'information est un résultat attendu ; dans un cas complet, une intervention
pour rattraper un clic incorrect compte comme un échec. Une fin annoncée par le modèle n'est pas une preuve : ``recuperer_document`` exige
un fichier reçu (reçu de classement), ``rechercher_societe`` une page enregistrée, ``preparer_formulaire`` la vérification de l'avocat.

Usage :
  python scripts/recette-navigateur-qwen56109.py --protocole
      Affiche le protocole et le réglage actuel (modèle, origine, profil, qualification). Aucun réseau, aucun modèle.
  python scripts/recette-navigateur-qwen56109.py --config CHEMIN --verifier-ollama
      Lit le catalogue LOCAL d'Ollama (``/api/tags``) pour relever le digest de qwen3.5:9b. Aucun téléchargement de poids (pas de « pull »).
  python scripts/recette-navigateur-qwen56109.py --config CHEMIN --executer --site https://recette.example.test --parcours recuperer_document
      [--repetitions 5] [--dossier DOS-001] [--sortie recu.json] [--enregistrer]
      Lance réellement le navigateur supervisé et le modèle choisi (demande explicite, confirmation « oui » au clavier). Mesure la durée
      par parcours, compte réussites et interventions, et écrit un reçu SANS valeur de formulaire ni secret. ``--enregistrer`` inscrit le
      reçu de qualification (``navigateur56109.noter_qualification``) ; un changement de modèle, de digest ou de profil l'invalide.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
PARCOURS = {
    'rechercher_societe': ('Recherche la société fictive d’identifiant {siren} sur le site de recette, compare l’identité et le registre, '
                           'puis enregistre la page du résultat.', ('page',)),
    'recuperer_document': ('Ouvre la fiche de la société fictive d’identifiant {siren} sur le site de recette, choisis le dernier acte '
                           'disponible et télécharge-le.', ('fichier',)),
    'preparer_formulaire': ('Sur le site de recette, remplis le formulaire de modification fictif de la société d’identifiant {siren} avec '
                            'les données du dossier, vérifie les valeurs, et NE transmets RIEN.', ()),
}
SIRENS = ['0000100%02d' % n for n in range(17, 91)]


def _desk(config_path):
    sys.path.insert(0, str(ROOT))
    from agent.common import load_config
    from agent.desk import Desk
    return Desk(load_config(str(config_path)))


def protocole(config_path=None):
    print(__doc__.split('Usage :')[0].strip())
    if config_path:
        desk = _desk(config_path)
        from agent import navigateur56109 as N
        print(json.dumps(N.etat_reglage(desk), ensure_ascii=False, indent=1, default=str))
    return 0


def verifier_ollama(config_path):
    desk = _desk(config_path)
    from agent import navigateur56109 as N
    cfg = N.config_navigateur(desk.c)
    if cfg['provider_type'] != 'ollama' or cfg['model'] != N.MODEL_CIBLE:
        print('Le modèle du navigateur n’est pas Ollama qwen3.5:9b : %s · %s (la recette Qwen ne s’applique pas).' % (cfg['provider_id'], cfg['model']))
        return 2
    digest = N.observer_digest(cfg)
    print(json.dumps({'modele': cfg['model'], 'digest': digest, 'profil': cfg['browser_pin109']['profile']}, ensure_ascii=False))
    return 0


def executer(config_path, site, parcours, repetitions, dossier, sortie, enregistrer):
    if input('Lancer réellement le navigateur et le modèle local pour %d exécution(s) de « %s » ? (oui/non) ' % (repetitions, parcours)).strip().lower() != 'oui':
        print('Rien n’a été lancé.')
        return 1
    desk = _desk(config_path)
    from agent import navigateur5673 as nv, navigateur56109 as N
    cfg = N.config_navigateur(desk.c)
    digest = N.observer_digest(cfg) if cfg['provider_type'] == 'ollama' else ''
    nv.save_sites(desk, 'recette', ' '.join(nv.sites(desk) + [site]))
    mgr = nv.manager(desk.c)
    texte, preuves = PARCOURS[parcours]
    resultats, durees = [], []
    for i in range(repetitions):
        siren = SIRENS[(i * 7 + len(parcours)) % len(SIRENS)]
        debut = time.monotonic()
        s = mgr.start('recette@example.test', texte.format(siren=siren) + ' Site : ' + site, dossier, role='avocat', preuves=preuves)
        while mgr.session.state not in nv.FINALS:
            attente = mgr.session.pending
            if attente is not None:
                print('Intervention demandée : %s' % attente.get('question'))
                resultats.append({'execution': i + 1, 'intervention': attente.get('kind')})   # nature seulement, jamais une valeur saisie
                choix = input('Réponse (autoriser / refuser / reprendre / arreter) : ').strip()
                if choix == 'arreter':
                    mgr.stop(s['id'])
                else:
                    mgr.decide(s['id'], choix if choix in ('autoriser', 'refuser', 'reprendre') else 'reprendre')
            time.sleep(0.5)
        durees.append(time.monotonic() - debut)
        resultats.append({'execution': i + 1, 'etat': mgr.session.state, 'motif': mgr.session.motif, 'pas': len(mgr.session.steps),
                          'fichiers': len(mgr.session.files), 'ecartees': mgr.session.ecartees})
    finis = [r for r in resultats if 'etat' in r]
    recu = {'modele': cfg['model'], 'digest': digest, 'profil': cfg['browser_pin109']['profile'], 'systeme': '%s %s' % (platform.system(), platform.release()),
            'materiel': platform.processor() or platform.machine(), 'executions': len(finis),
            'reussites': sum(1 for r in finis if r['etat'] == 'termine'), 'interventions': sum(1 for r in resultats if 'intervention' in r),
            'latence_mediane_ms': int(statistics.median(durees) * 1000) if durees else None,
            'latence_p95_ms': int(sorted(durees)[max(0, int(len(durees) * 0.95) - 1)] * 1000) if durees else None,
            'parcours': parcours, 'site': site, 'detail': resultats, 'le': time.strftime('%Y-%m-%dT%H:%M:%S')}
    if sortie:
        Path(sortie).write_text(json.dumps(recu, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in recu.items() if k != 'detail'}, ensure_ascii=False, indent=1))
    if enregistrer and cfg['provider_type'] == 'ollama':
        N.noter_qualification(desk, recu)
        print('Reçu de qualification enregistré (il ne vaut qualification qu’avec trente réussites sur les deux systèmes).')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description='Recette du navigateur supervisé avec qwen3.5:9b (lot O, 5.6.109).')
    p.add_argument('--protocole', action='store_true')
    p.add_argument('--config', default=os.environ.get('AXIORHUB_CONFIG', ''))
    p.add_argument('--verifier-ollama', action='store_true')
    p.add_argument('--executer', action='store_true')
    p.add_argument('--site', default='')
    p.add_argument('--parcours', choices=sorted(PARCOURS), default='rechercher_societe')
    p.add_argument('--repetitions', type=int, default=5)
    p.add_argument('--dossier', default='')
    p.add_argument('--sortie', default='')
    p.add_argument('--enregistrer', action='store_true')
    a = p.parse_args(argv)
    if a.executer:
        if not a.config or not a.site.startswith('https://'):
            p.error('--executer exige --config et un --site https de recette contrôlé')
        return executer(a.config, a.site, a.parcours, max(1, min(a.repetitions, 10)), a.dossier, a.sortie, a.enregistrer)
    if a.verifier_ollama:
        if not a.config:
            p.error('--verifier-ollama exige --config')
        return verifier_ollama(a.config)
    return protocole(a.config or None)


if __name__ == '__main__':
    sys.exit(main())
