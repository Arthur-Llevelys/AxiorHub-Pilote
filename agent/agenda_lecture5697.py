"""5.6.97 (lot proteger, B07) : lecture de l'agenda et des tâches pour un acteur, et export ICS filtré.

Toutes les projections (grille Jour/Semaine/Mois, liste, « Tâches à programmer », vue Tâches, détails par URL, export utilisateur) lisent par
ici. Les droits du dossier sont joints dans la requête SQL **avant** ``ORDER BY`` et ``LIMIT`` (``acces5697.Perimetre.sql``) : un événement
ou une tâche d'un dossier masqué n'occupe jamais une place de la limite et ne laisse ni titre, ni date, ni participant, ni lien, ni identifiant.

- Les deux tables de tâches (``tasks`` locale, ``work_tasks_v211`` synchronisée) sont filtrées de la même façon.
- Les événements importés ou rattachés (``calendar_cache``) sont filtrés sur leur dossier ET sur leurs dossiers candidats : un rapprochement
  ambigu qui touche un dossier masqué n'est pas montré (le titre nomme souvent le client).
- Mode disponibilité : l'autorisation ne porte que sur les créneaux ; chaque créneau est rendu « Occupé », sans aucun autre champ.
- Export utilisateur : ses seuls événements visibles, réponse privée sans cache partagé. L'export technique global (sauvegarde, migration)
  est une commande distincte, réservée à l'administrateur et journalisée.
"""
from datetime import datetime, timedelta, timezone
import json

from .acces5697 import acteur_de, dossiers_autorises
from .common import Stop

OCCUPE = 'Occupé'
ENTETES_PRIVES = [('Cache-Control', 'private, no-store, max-age=0'), ('Pragma', 'no-cache'), ('Vary', 'Cookie, Authorization')]
LIMITE_TACHES = 300


def _instant(valeur, defaut):
    if valeur is None:
        return defaut
    if isinstance(valeur, str):
        valeur = datetime.fromisoformat(valeur)
    if valeur.tzinfo is None:
        valeur = valeur.replace(tzinfo=timezone.utc)
    return valeur.astimezone(timezone.utc).isoformat()


def evenements(desk, acteur, debut=None, fin=None, dossier='', limite=500, annules=False, disponibilite=False):
    """Événements de l'agenda local (``agenda5634``) visibles par l'acteur, droits joints avant LIMIT. ``disponibilite`` : créneaux seulement."""
    from . import agenda5634
    agenda5634.schema(desk)
    maintenant = datetime.now(timezone.utc)
    a, b = _instant(debut, (maintenant - timedelta(days=1)).isoformat()), _instant(fin, (maintenant + timedelta(days=60)).isoformat())
    where, params = ['starts<?', 'ends>?'], [b, a]
    if not annules:
        where.append("status='actif'")
    if dossier:
        where.append('matter=?')
        params.append(str(dossier))
    if not disponibilite:                                    # en disponibilité, tout créneau occupe la journée : aucun détail n'est rendu
        clause, extra = dossiers_autorises(desk, acteur).sql('matter')
        where.append(clause)
        params += extra
    else:
        acteur_de(acteur)
    rows = desk.db.execute('SELECT * FROM agenda5634 WHERE ' + ' AND '.join(where) + ' ORDER BY starts LIMIT ?', (*params, max(1, min(int(limite), 2000)))).fetchall()
    if disponibilite:
        return [{'starts': r['starts'], 'ends': r['ends'], 'all_day': bool(r['all_day']), 'title': OCCUPE} for r in rows]
    return [agenda5634.public(dict(r)) for r in rows]


def evenements_en_ligne(desk, acteur, debut, fin, source=None, dossier=''):
    """Événements du cache des agendas (en ligne, personnels, importés), visibles par l'acteur : dossier ET candidats contrôlés en SQL."""
    from .workplan import ensure_schema
    ensure_schema(desk)
    p = dossiers_autorises(desk, acteur)
    where, params = ['starts<?', 'ends>?'], [_instant(fin, None), _instant(debut, None)]
    if source == 'online':
        where.append("calendar_url<>'local:agenda'")
    elif source == 'personal':
        where.append("calendar_url='local:agenda'")
    if dossier:
        where.append('matter=?')
        params.append(str(dossier))
    for clause, extra in (p.sql('matter'), p.sql_json('matter_candidates')):
        where.append(clause)
        params += extra
    out = []
    for r in desk.db.execute('SELECT * FROM calendar_cache WHERE ' + ' AND '.join(where) + ' ORDER BY starts', params):
        item = dict(r)
        item['matter_candidates'] = json.loads(item['matter_candidates'] or '[]')
        item['busy'] = bool(item['busy'])
        out.append(item)
    return out


def taches(desk, acteur, dossier='', limite=LIMITE_TACHES, a_programmer=False):
    """Tâches ouvertes des deux tables (locale et synchronisée, sans doublon), visibles par l'acteur, droits joints avant LIMIT.
    ``a_programmer`` : la liste « Tâches à programmer » de la vue Semaine (à faire ou en cours)."""
    p = dossiers_autorises(desk, acteur)
    clause, extra = p.sql('matter')
    filtre, fparams = (' AND matter=?', (str(dossier),)) if dossier else ('', ())
    limite = max(1, min(int(limite), 500))
    locales = [dict(x, source='local') for x in desk.db.execute(
        "SELECT id,matter,title,due,status FROM tasks WHERE status='open' AND " + clause + filtre + ' ORDER BY due LIMIT ?', (*extra, *fparams, limite))]
    etats = "('todo','in_progress')" if a_programmer else "('todo','in_progress','blocked')"
    try:
        externes = [dict(x, source='nextcloud') for x in desk.db.execute(
            'SELECT id,matter,title,due,status,external_uid FROM work_tasks_v211 WHERE status IN ' + etats + ' AND ' + clause + filtre + ' ORDER BY due LIMIT ?',
            (*extra, *fparams, limite))]
    except Exception:   # noqa: BLE001 — table des tâches synchronisées pas encore créée : la liste locale seule
        externes = []
    ids = {x['id'] for x in externes}
    return [r for r in locales if r['id'] not in ids] + externes


def detail_evenement(desk, acteur, ident):
    """Détail d'un événement désigné par son identifiant (URL) : réponse neutre si absent ou masqué."""
    from . import agenda5634
    from .acces5697 import exiger
    agenda5634.schema(desk)
    row = desk.db.execute('SELECT * FROM agenda5634 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    exiger(desk, acteur, dossier_id=row['matter'])
    return agenda5634.public(dict(row))


def export_ics(desk, acteur, annules=False):
    """Export iCalendar de l'utilisateur : ses seuls événements visibles (aucun événement d'un dossier masqué, ni titre ni identifiant)."""
    from . import agenda5634
    return agenda5634.export_ics(desk, include_cancelled=annules, masques=dossiers_autorises(desk, acteur).masques)


def export_global(desk, acteur, motif=''):
    """Export technique complet (sauvegarde, migration) : administrateur seulement, motif requis, journalisé avant de rendre le fichier."""
    from . import agenda5634
    a = acteur_de(acteur)
    if a.role != 'administrateur':
        raise Stop('role_insuffisant')
    motif = ' '.join(str(motif or '').split())[:300]
    if len(motif) < 3:
        raise Stop('motif_requis')
    texte = agenda5634.export_ics(desk, include_cancelled=True, masques=frozenset())
    desk.audit('agenda5697_export_global', {'par': a.compte, 'motif': motif, 'evenements': texte.count('BEGIN:VEVENT')})
    return texte


def reponse_ics(texte, nom='agenda.ics'):
    """Réponse HTTP d'un export : fichier iCalendar, privé, jamais conservé par un cache partagé."""
    return {'status': '200 OK', 'kind': 'text/calendar; charset=utf-8', 'body': texte,
            'headers': ENTETES_PRIVES + [('Content-Disposition', 'attachment; filename="%s"' % nom)]}
