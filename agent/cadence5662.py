"""5.6.62 : cadence des travaux périodiques — l'IA ne doit plus tourner en continu ni repousser les demandes de l'avocat.

Constat du cabinet (18 octobre 2026) : file d'attente permanente, l'IA « tourne tout le temps ». Trois causes :
- l'intervalle d'un travail périodique était compté depuis sa mise en file : un travail resté 40 min en attente puis exécuté était
  remis en file aussitôt fini ;
- aucune contre-pression : chaque planificateur ajoutait son travail même quand la file débordait déjà ;
- aucune mesure de la charge réelle (voir ``blocage5660`` : constat « Charge de l'IA sur 24 h »).

Règles : (1) **repos** — un travail périodique ne revient qu'un intervalle complet APRÈS la fin du précédent ; (2) **contre-pression** —
si une demande de l'avocat attend, ou si la file des travaux automatiques est chargée, les travaux périodiques non essentiels attendent
leur tour (le planificateur réessaie au passage suivant, sans rien perdre). Les demandes de l'avocat ne sont jamais concernées.
"""
from datetime import datetime, timezone

ESSENTIAL = frozenset(('orchestrator_mail_sweep', 'autonomy_mail_sweep', 'sync', 'health'))   # nouveaux courriels et santé : jamais différés
BACKLOG = 6                 # travaux automatiques en attente au-delà desquels on n'en ajoute plus
OLDEST_MINUTES = 30         # ou le plus ancien attend depuis plus de 30 min
USER_WAIT_SECONDS = 60      # une demande de l'avocat attend depuis plus d'une minute
WAITING_SECONDS = 120       # un travail automatique « attend » s'il est en file depuis plus de 2 min


def _ts(value):
    try:
        d = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def busy(desk, now_ts=None):
    """Raison de différer les travaux périodiques non essentiels, ou '' si la voie est libre."""
    now_ts = now_ts or datetime.now(timezone.utc).timestamp()
    try:
        from .desk import AUTOMATIC_PRIORITY
        # 5.6.65 (audit 5.6.64, R14) : même prédicat d'éligibilité que le worker (travail échu) ; l'ancienneté part du moment où le
        # travail est devenu exécutable (création ou fin du report), sans être remise à zéro par une attente de verrou.
        now_iso = datetime.fromtimestamp(now_ts, timezone.utc).isoformat()
        ready = "MAX(created,COALESCE(not_before,created))"
        due = "status='pending' AND (not_before IS NULL OR not_before<=?)"
        user = desk.db.execute("SELECT MIN(%s) FROM jobs WHERE %s AND priority<?" % (ready, due), (now_iso, AUTOMATIC_PRIORITY)).fetchone()[0]
        if user and now_ts - (_ts(user) or now_ts) > USER_WAIT_SECONDS:
            return 'demande_avocat_en_attente'
        # seuls comptent les travaux qui ATTENDENT (exécutables depuis plus de 2 min) : un passage du planificateur ne se bloque pas lui-même
        waited = datetime.fromtimestamp(now_ts - WAITING_SECONDS, timezone.utc).isoformat()
        n, oldest = desk.db.execute("SELECT COUNT(*),MIN(%s) FROM jobs WHERE %s AND priority>=? AND %s<=?" % (ready, due, ready),
                                    (now_iso, AUTOMATIC_PRIORITY, waited)).fetchone()
    except Exception:   # noqa: BLE001 — table absente (installation neuve) : aucune contre-pression
        return ''
    if n >= BACKLOG:
        return 'file_chargee'
    if oldest and now_ts - (_ts(oldest) or now_ts) > OLDEST_MINUTES * 60:
        return 'file_en_retard'
    return ''


def rested(desk, kind, minutes, now_ts=None):
    """Vrai si le dernier travail de ce type a fini il y a au moins ``minutes`` (ou n'a jamais tourné)."""
    now_ts = now_ts or datetime.now(timezone.utc).timestamp()
    try:
        last = desk.db.execute("SELECT MAX(finished) FROM jobs WHERE kind=? AND finished IS NOT NULL AND finished<>''", (kind,)).fetchone()[0]
    except Exception:   # noqa: BLE001
        return True
    stamp = _ts(last) if last else None
    return stamp is None or now_ts - stamp >= float(minutes) * 60


def allow(desk, kind, minutes, now_ts=None):
    """Un travail périodique peut-il être mis en file maintenant ? (repos respecté, et file libre sauf travail essentiel)."""
    if not rested(desk, kind, minutes, now_ts):
        return False
    return kind in ESSENTIAL or not busy(desk, now_ts)   # différé : le planificateur réessaie au passage suivant (aucune écriture)
