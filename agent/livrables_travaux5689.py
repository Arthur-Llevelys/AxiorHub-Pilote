"""5.6.89 : travaux du registre des livrables. Synchronisation bornée avec le stockage, contrôles, export suivi, proposition assistée. Le travail réel vit dans les modules de service.

La synchronisation est le seul moment où le registre lit le stockage hors d'une action de la personne : c'est elle qui calcule les empreintes, jamais l'affichage.
"""
import time

from .common import Stop

INTERVAL_MINUTES = 10


def schedule(desk, now_ts=None):
    """Boucle des automatismes : un passage toutes les 10 minutes au plus, jamais deux en file."""
    now_ts = time.time() if now_ts is None else now_ts
    if now_ts - float(desk.settings('auto:livrables5689', 0) or 0) < INTERVAL_MINUTES * 60:
        return None
    desk.setting('auto:livrables5689', now_ts)
    if desk.db.execute("SELECT 1 FROM jobs WHERE kind='synchro_livrables5689' AND status IN ('pending','running') LIMIT 1").fetchone():
        return None
    return desk.enqueue('synchro_livrables5689', {}, priority=60)


def synchroniser(desk, client=None):
    from . import livrables5689 as liv, pause5629, registre5693
    from .office440 import dav_client

    def pause():
        pause5629.checkpoint(desk)
    pause()                                  # 5.6.93 (N05 T3) : un travail annulé ou mis en pause ne lit ni ne répare rien
    registre5693.migrer(desk)                # 5.6.93 : réparation répétable, par petits lots, sans effet externe (collecte, contrôles, identités)
    liv.synchroniser(desk, fond=True)        # 5.6.93 (N03) : l'arrière-plan lit la suite des journaux ; l'affichage n'en lit qu'une page
    client = registre5693.compter(client or dav_client(desk))     # 5.6.93 : une seule borne de requêtes au stockage pour tout le passage
    out = {'ok': True, 'reservations': liv.reconcilier(desk, client, pause=pause), 'fichiers': liv.synchroniser_fichiers(desk, client, pause=pause)}
    try:      # 5.6.93 (N02, N10) : petits lots ; un incident ici ne remet pas en cause la synchronisation déjà faite
        from .operations5693 import maintenance
        out['operations'] = maintenance(desk, pause=pause)
    except Stop:                 # arrêt ou pause du travail de synchronisation : comme partout ailleurs
        raise
    except Exception as ex:      # noqa: BLE001
        out['operations'] = {'erreur': type(ex).__name__}
    return out


def perform(desk, kind, args):
    if kind == 'synchro_livrables5689':
        return synchroniser(desk)
    if kind == 'controle_livrable5689':
        from .verification5689 import travail
        return travail(desk, args)
    if kind == 'export_livrable5689':
        from .livrables_export5689 import executer
        return executer(desk, args)
    from .propositions5689 import travail_ia
    return travail_ia(desk, args)
