"""5.6.101 (R05) : reprises uniques faites au démarrage, jamais pendant une lecture.

Avant, le tout premier calcul de la file commune (``file5651.items`` : rang d'Aujourd'hui, « Voir la suite », volet) créait des tables et
appliquait des reprises d'autres modules depuis l'affichage (bouclier 5.6.68, savoir-faire 5.6.65, connexions 5.6.53, colonnes ajoutées des
routines 5.6.53 et des opérations sociétaires 5.6.37, colonnes du modèle et de la relecture du dossier 5.6.52). Elles sont maintenant appelées par ``Desk._prepare`` (une fois par processus et par base,
sous le verrou du schéma) ; chacune est idempotente : au deuxième démarrage, sans changement, elle ne fait que lire (réglage déjà posé,
colonne déjà présente).

Une base occupée par un autre processus remonte à ``Desk._prepare_waiting`` (nouvel essai) ; toute autre erreur est notée au journal de
démarrage et la reprise est retentée au démarrage suivant (son réglage n'a pas été posé) : le service démarre quand même.
"""
import sqlite3


def _etape(desk, nom, faire):
    from .desk import _locked, _startup_log
    try:
        faire()
    except Exception as ex:   # noqa: BLE001 — une reprise en échec ne bloque pas le démarrage ; elle sera retentée
        if _locked(ex):
            raise
        try:
            if desk.db.in_transaction:
                desk.db.rollback()
        except sqlite3.Error:
            pass
        _startup_log('reprise %s non faite (%s) ; nouvel essai au prochain démarrage.' % (nom, type(ex).__name__))


def _routines(desk):
    from . import routines5652
    routines5652._ensure_schema(desk)          # colonnes created_by / request_hash (5.6.53) et leur remplissage, une fois


def _connexions(desk):
    from . import connexions5652
    connexions5652._ensure_schema(desk)
    if not desk.settings('connexions5653:reparation', None):
        connexions5652.repair_5653(desk)       # après les routines : elle suspend des passages de routines désactivées


def _operations(desk):
    from . import corporate5636
    corporate5636.schema(desk)                 # colonne idem (5.6.37) et marquage des opérations 5.6.36, une fois


def _bouclier(desk):
    from . import bouclier5659
    bouclier5659.ensure_schema(desk.db)
    bouclier5659._migrate5668(desk)            # garde son réglage ``bouclier5668:migre``


def _savoir(desk):
    from . import savoir5659
    savoir5659.ensure_schema(desk)
    savoir5659._recheck_5665(desk)             # garde son réglage ``savoir5665:recontrole_anonymisation``


def _file_commune(desk):
    """Colonnes ajoutées (``ALTER TABLE``) que la file commune créait à son premier calcul : modèle et relecture du dossier (5.6.52). Les
    lectures gardent leur ``CREATE … IF NOT EXISTS``, sans effet une fois la base préparée.

    ``taches5614`` n'est pas préparé ici : sa table ``validations5614`` porte le même nom que celle de ``pilote5613`` (définitions
    différentes, la première créée l'emporte). La créer au démarrage la ferait gagner à coup sûr et casserait ``pilote5613.record_validation`` :
    collision antérieure signalée, à corriger à part (renommage avec reprise)."""
    from . import modele5651, relecture5651
    modele5651.ensure_schema(desk)
    relecture5651.ensure_schema(desk)


ETAPES = (('routines5652', _routines), ('connexions5653', _connexions), ('operations5636', _operations),
          ('bouclier5668', _bouclier), ('savoir5665', _savoir), ('schemas_file5651', _file_commune))


def au_demarrage(desk):
    for nom, faire in ETAPES:
        _etape(desk, nom, lambda: faire(desk))
