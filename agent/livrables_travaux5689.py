"""5.6.89 : travaux du registre des livrables. Synchronisation bornée avec le stockage, contrôles, export suivi, proposition assistée. Le travail réel vit dans les modules de service.

La synchronisation est le seul moment où le registre lit le stockage hors d'une action de la personne : c'est elle qui calcule les empreintes, jamais l'affichage.
"""
import time

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
    from . import livrables5689 as liv, pause5629
    from .office440 import dav_client
    liv.synchroniser(desk)
    client = client or dav_client(desk)

    def pause():
        pause5629.checkpoint(desk)
    return {'ok': True, 'reservations': liv.reconcilier(desk, client, pause=pause), 'fichiers': liv.synchroniser_fichiers(desk, client, pause=pause)}


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
