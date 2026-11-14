"""5.6.100 : interrupteur de la nouvelle interface (lecteur des courriels, Aujourd'hui, Dossiers, Produire).

Choix du cabinet (13 novembre 2026) : activée par défaut, réversible pour tout le cabinet et compte par compte. Les correctifs de
droits, de non-effacement et de clôture (R04, R05, R10) ne dépendent JAMAIS de cet interrupteur : ils s'appliquent aussi à
l'ancienne présentation.
"""

CLE_CABINET = 'interface56100:actif'
PREFIXE_COMPTE = 'interface56100:compte:'


def _cle_compte(owner):
    return PREFIXE_COMPTE + str(owner or 'cabinet')[:200]


def reglage_cabinet(desk):
    """Réglage du cabinet : vrai par défaut."""
    return desk.settings(CLE_CABINET, True) is not False


def reglage_compte(desk, owner):
    """Préférence du compte : None (suit le cabinet), True ou False."""
    valeur = desk.settings(_cle_compte(owner), None)
    return valeur if valeur in (True, False) else None


def active(desk, owner='cabinet'):
    """La nouvelle interface est-elle active pour ce compte ? La préférence du compte prime sur le réglage du cabinet."""
    try:
        compte = reglage_compte(desk, owner)
        return reglage_cabinet(desk) if compte is None else compte
    except Exception:   # noqa: BLE001 — réglage illisible : la présentation par défaut (active) s'affiche
        return True


def regler_cabinet(desk, actif):
    desk.setting(CLE_CABINET, bool(actif))


def regler_compte(desk, owner, actif):
    """``actif`` : True, False, ou None pour revenir au réglage du cabinet."""
    desk.setting(_cle_compte(owner), None if actif is None else bool(actif))
