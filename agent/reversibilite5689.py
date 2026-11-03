"""5.6.89 (chapitre 46) : preuve de réversibilité : ce qui sort, ce qui est écarté et pourquoi, et ce que l'on vérifie après une restauration.

Une restauration réussie ne se juge pas à un démarrage sans erreur : ``comparer`` relit, table par table, les données métier du cabinet d'origine et de l'instance restaurée, puis contrôle que chaque
décision, version et référence retrouve sa cible. La sauvegarde et l'export restent ceux de la 5.6.88 (``sauvegarde5688``, ``export5688``) ; ce module n'écrit rien, n'efface rien et ne touche à aucun service.

``EXCLUSIONS`` justifie chaque table des versions 5.6.88 et 5.6.89 qui ne va pas dans l'export : une table nouvelle, ni exportée ni justifiée, fait échouer le test. Elles restent toutes dans la sauvegarde chiffrée,
sauf celles que ``sauvegarde5688`` écarte lui-même : l'exclusion ne concerne que l'export lisible hors du logiciel.

Commande, à lancer sur la machine de restauration : ``python -m agent.reversibilite5689 comparer <dossier_etat_source> <dossier_etat_restaure>``.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

from . import export5688 as ex

BASE = 'desk.sqlite3'
_CACHE = 'Cache recalculé à la demande : il ne contient aucune donnée propre.'
_TECHNIQUE = 'Suivi technique du logiciel, sans contenu pour le cabinet.'
_COMPTES = 'Comptes, secrets ou jetons : jamais dans un export, seulement dans la sauvegarde chiffrée.'
EXCLUSIONS = {
    'assertion_index5689': 'Index de recherche recalculé depuis les éléments du dossier, qui sont exportés.', 'dossier_cache5689': _CACHE, 'extraction_cache5689': _CACHE, 'registres_cache5688': _CACHE,
    'budget_meta5689': _TECHNIQUE, 'budget_workers5689': _TECHNIQUE, 'prepared_sources5689': 'État des collecteurs du registre ; les documents et leurs liens aux sources sont exportés.',
    'erreurs_modele5689': 'Diagnostic technique des réponses de modèle.', 'qualification_runs5689': 'Clés d’idempotence ; les preuves elles-mêmes sont exportées.',
    'office_limits5689': 'Découpage temporaire d’un document pendant une génération ; le document est dans le stockage.', 'office_segments5689': 'Découpage temporaire d’un document pendant une génération ; le document est dans le stockage.',
    'agenda_distant5688': 'Identifiants de synchronisation propres à un compte d’agenda ; les événements sont exportés.', 'cloud_migrations5688': 'Journal d’une copie entre stockages, sans valeur hors du logiciel.',
    'cloud_migration_items5688': 'Journal d’une copie entre stockages, sans valeur hors du logiciel.', 'tableau5688': 'Disposition d’écran de chaque personne.',
    'exports5688': 'Journal des exports eux-mêmes.', 'depots5688': 'Dépôts temporaires d’un export à vérifier.', 'sauvegardes5688': 'Journal des sauvegardes.', 'controles5688': 'Journal des contrôles d’archive.',
    'totp5688': _COMPTES, 'secours5688': _COMPTES, 'generations5688': _COMPTES, 'attente5688': _COMPTES, 'reglages5688': _COMPTES, 'jetons5688': _COMPTES,
}
# Références sans entrée dans ``export5688.TABLES`` : (table, colonne, table cible, colonne cible).
LIENS = (('assertion_events5689', 'assertion', 'case_assertions5689', 'id'), ('prepared_representations5689', 'item_id', 'prepared_items5689', 'id'), ('banc_cas5689', 'protocole', 'banc_protocoles5689', 'id'),
         ('banc_essais5689', 'run_id', 'banc_runs5689', 'run_id'), ('banc_avis5689', 'essai', 'banc_essais5689', 'id'), ('banc_runs5689', 'protocole', 'banc_protocoles5689', 'id'))


def tables_declarees(racine=None):
    """Tables des versions 5.6.88 et 5.6.89 déclarées dans le code : {nom: modules}."""
    racine = Path(racine or Path(__file__).parent)
    trouvees = {}
    for chemin in racine.rglob('*.py'):
        if '_vendor' in chemin.parts:
            continue
        for nom in re.findall(r'CREATE (?:VIRTUAL )?TABLE (?:IF NOT EXISTS )?([A-Za-z_0-9]*?568[89][A-Za-z_0-9]*)', chemin.read_text(encoding='utf-8')):
            trouvees.setdefault(nom, set()).add(chemin.name)
    return trouvees


def couverture_export(racine=None):
    """Toute table est exportée ou justifiée. ``orphelines`` doit rester vide."""
    exportees = {t[2] for t in ex.TABLES}
    declarees = tables_declarees(racine)
    return {'exportees': sorted(set(declarees) & exportees), 'exclues': sorted(set(declarees) & set(EXCLUSIONS)), 'orphelines': sorted(set(declarees) - exportees - set(EXCLUSIONS)), 'doublons': sorted(exportees & set(EXCLUSIONS))}


def _ouvrir(etat):
    chemin = Path(etat) / BASE
    db = sqlite3.connect(chemin.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def _colonnes(db, table):
    return [r[1] for r in db.execute('PRAGMA table_info("%s")' % table)]


def empreinte_metier(etat):
    """{table: {lignes, sha256}} pour toutes les tables de l'export, indépendamment de l'ordre physique des lignes. Une table absente vaut zéro ligne : elle n'a pas servi."""
    db = _ouvrir(etat)
    try:
        out = {}
        for table in dict.fromkeys(t[2] for t in ex.TABLES):
            if not _colonnes(db, table):
                out[table] = {'lignes': 0, 'sha256': ''}
                continue
            colonnes = _colonnes(db, table)
            lignes = sorted(hashlib.sha256(json.dumps([r[c] if not isinstance(r[c], bytes) else r[c].hex() for c in colonnes], ensure_ascii=False, default=str).encode('utf-8')).hexdigest()
                            for r in db.execute('SELECT * FROM "%s"' % table))
            out[table] = {'lignes': len(lignes), 'sha256': hashlib.sha256(''.join(lignes).encode()).hexdigest() if lignes else ''}
        return out
    finally:
        db.close()


def references_brisees(etat):
    """Chaque décision, version ou lien doit retrouver sa cible. Rend les lignes orphelines, table par table."""
    db = _ouvrir(etat)
    try:
        liens = list(LIENS) + [(t[2], t[4][0], t[4][1], t[4][2]) for t in ex.TABLES if isinstance(t[4], tuple)]
        out = []
        for table, colonne, cible, cle in dict.fromkeys(liens):
            if not _colonnes(db, table) or not _colonnes(db, cible):
                continue
            n = db.execute('SELECT COUNT(*) FROM "%s" t WHERE COALESCE(t."%s",\'\')<>\'\' AND NOT EXISTS (SELECT 1 FROM "%s" c WHERE c."%s"=t."%s")' % (table, colonne, cible, cle, colonne)).fetchone()[0]
            if n:
                out.append({'table': table, 'colonne': colonne, 'cible': cible, 'orphelines': n})
        return out
    finally:
        db.close()


def integre(etat):
    db = _ouvrir(etat)
    try:
        return [r[0] for r in db.execute('PRAGMA integrity_check(1)')] == ['ok']
    finally:
        db.close()


def comparer(source, restaure):
    """Verdict d'une restauration : base intègre, toutes les lignes métier à l'identique, aucune référence orpheline."""
    avant, apres = empreinte_metier(source), empreinte_metier(restaure)
    differences = [{'table': t, 'avant': avant[t]['lignes'], 'apres': apres[t]['lignes']} for t in avant if avant[t] != apres.get(t)]
    brisees = references_brisees(restaure)
    ok = integre(restaure) and not differences and not brisees
    return {'ok': ok, 'tables': len(avant), 'lignes': sum(v['lignes'] for v in avant.values()), 'differences': differences, 'references_brisees': brisees}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3 or argv[0] != 'comparer':
        print('Usage : python -m agent.reversibilite5689 comparer <dossier_etat_source> <dossier_etat_restaure>')
        return 2
    verdict = comparer(argv[1], argv[2])
    print(json.dumps(verdict, ensure_ascii=False, indent=1))
    return 0 if verdict['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
