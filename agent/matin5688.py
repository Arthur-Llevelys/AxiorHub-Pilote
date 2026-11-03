"""5.6.88 : liste du matin de chaque personne : ses audiences, ses tâches et les échéances du jour, dans Aujourd'hui et dans le briefing.

Cette liste se lit dans la base, à l'affichage et pour la personne connectée : aucun appel au modèle, et rien de nominatif n'entre dans le
texte commun du briefing (un dossier réservé n'y apparaîtrait pas pour un autre compte). Les échéances de procédure n'ont pas de
responsable : celles du jour s'affichent pour chaque compte qui a accès au dossier.
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
from urllib.parse import quote

from .common import load_matters, matter_display


def _hearings(desk, owner, day, tz, hidden):
    from . import audiences5688 as au
    known = au.members(desk)
    a = datetime.combine(day, datetime.min.time(), tz).astimezone(timezone.utc).isoformat()
    b = datetime.combine(day + timedelta(days=1), datetime.min.time(), tz).astimezone(timezone.utc).isoformat()
    rows = desk.db.execute(au.SELECT + " WHERE h.statut='a_venir' AND COALESCE(a.starts,h.starts)>=? AND COALESCE(a.starts,h.starts)<? AND (h.avocat=? OR h.avocat='') "
                           "ORDER BY COALESCE(a.starts,h.starts)", (a, b, owner)).fetchall()
    return [au.view(desk, r, known) for r in rows if r['matter'] not in hidden]


def _deadlines(desk, day, hidden):
    try:
        rows = desk.db.execute("SELECT id,matter,due,note FROM deadlines450 WHERE status NOT IN ('terminee','annulee') AND substr(due,1,10)=? ORDER BY matter", (day.isoformat(),)).fetchall()
    except Exception:   # noqa: BLE001 — table absente : module jamais utilisé
        rows = []
    out = [dict(r) for r in rows if r['matter'] not in hidden]
    try:
        from .echeances450 import listing
        labels = {}
        for m in {x['matter'] for x in out}:
            labels.update({d['id']: d.get('rule_label', '') for d in listing(desk, m)})
        for x in out:
            x['label'] = ' — '.join(v for v in (labels.get(x['id'], ''), x['note']) if v) or 'Échéance de procédure'
    except Exception:   # noqa: BLE001 — libellé de règle indisponible : l'échéance reste affichée
        for x in out:
            x['label'] = x['note'] or 'Échéance de procédure'
    return out


def build(desk, owner, day=None, hidden=None):
    """Données de la liste du matin ; ``hidden`` : dossiers masqués pour cette personne (par défaut, ceux de la requête en cours)."""
    from . import audiences5688 as au
    from . import taches5688
    from .agenda5634 import _tz
    from .membres5650 import viewer_hidden
    au.schema(desk)
    tz = _tz(desk)
    day = day or datetime.now(tz).date()
    hidden = (viewer_hidden() or frozenset()) if hidden is None else hidden
    return {'date': day.isoformat(), 'audiences': _hearings(desk, owner, day, tz, hidden), 'taches': taches5688.due_within(desk, owner, day, hidden),
            'echeances': _deadlines(desk, day, hidden)}


def _block(title, rows):
    return '<div class="t88-matin-block"><h3>%s</h3><ul>%s</ul></div>' % (e(title), ''.join('<li>%s</li>' % r for r in rows)) if rows else ''


def html(desk, prefix, owner, day=None):
    data = build(desk, owner, day)
    labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}

    def matter(mid):
        return ('<a href="%s">%s</a>' % (e(prefix + '/dossier?id=' + quote(mid, safe=''), quote=True), e(labels.get(mid, mid)))) if mid else ''

    hearings = []
    for h in data['audiences']:
        head = '<a href="%s">%s</a>' % (e(prefix + '/audiences?onglet=suivi&id=' + h['id'], quote=True), e(h['quand'] + ' — ' + h['title']))
        extra = ' · '.join(x for x in (e(h['juridiction']), e(h['chambre']), matter(h['matter']), ('<strong>%s</strong>' % e(h['avocat_nom'])) if h['sans_avocat'] else '') if x)
        hearings.append(head + ((' <small>%s</small>' % extra) if extra else ''))
    tasks = ['<a href="%s">%s</a>%s' % (e(prefix + '/mes-taches#tache-' + t['id'], quote=True), e(t['title'][:200]), ' <small class="t88-late">en retard</small>' if t['retard'] else '') for t in data['taches']]
    deadlines = ['%s <small>%s</small>' % (e(d['label']), matter(d['matter'])) for d in data['echeances']]
    inner = _block('Mes audiences', hearings) + _block('Mes tâches', tasks) + _block('Échéances du jour', deadlines)
    return ('<section class="c530-card t88-matin" id="t88-matin" tabindex="-1" aria-labelledby="t88-matin-titre"><h2 id="t88-matin-titre">Ma liste du jour</h2>%s</section>') % (
        inner or '<p class="c530-empty">Ni audience, ni tâche, ni échéance pour vous aujourd’hui.</p>')
