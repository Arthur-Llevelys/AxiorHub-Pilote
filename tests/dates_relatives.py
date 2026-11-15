"""5.6.101 (lot 0, T01) : dates des tests calculées à partir du jour du cabinet, jamais figées.

Une date fixe (« 12 novembre 2026 ») finit toujours par passer : le test qui la croyait « à venir » échoue un jour donné, et la
recette GitHub, qui tourne en heure universelle, change de jour une heure avant Paris. Tout test qui compare une date au jour courant
utilise ces fonctions. ``date.today()`` est interdit dans les tests.
"""
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    FUSEAU = ZoneInfo('Europe/Paris')
except Exception:   # noqa: BLE001 — base des fuseaux absente : heure d'hiver de Paris, suffisante pour des écarts en jours
    FUSEAU = timezone(timedelta(hours=1))

MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')


def maintenant_cabinet():
    """Instant courant, dans le fuseau du cabinet."""
    return datetime.now(FUSEAU)


def aujourdhui_cabinet():
    """Jour courant du cabinet (et non celui de la machine qui lance les tests)."""
    return maintenant_cabinet().date()


def jour(n=0):
    """Jour du cabinet décalé de ``n`` jours (négatif : passé)."""
    return aujourdhui_cabinet() + timedelta(days=n)


def annee(k=0):
    """Année du cabinet décalée de ``k`` ans ; ``annee(2)`` : une année toujours à venir."""
    return aujourdhui_cabinet().year + k


def texte_date(d):
    """Date écrite comme dans une pièce : « 12 novembre 2028 »."""
    if isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date):
        raise TypeError('date attendue')
    return '%d %s %d' % (d.day, MOIS[d.month - 1], d.year)
