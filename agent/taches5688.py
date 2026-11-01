"""5.6.88 : tâches du cabinet : responsable, échéance, rappel, statut, et création directe depuis un dossier, une audience ou un courriel.

- Les colonnes propres à 5.6.88 vivent dans ``taches5688``, à côté de ``tasks`` : plusieurs modules écrivent ``INSERT OR REPLACE INTO tasks
  VALUES (6 valeurs)`` ; une colonne ajoutée à ``tasks`` casserait ces écritures, et le remplacement d'une ligne effacerait son responsable.
- Une tâche sans ligne ici reste valable : « Sans responsable », à faire. La migration crée les lignes manquantes sans rien modifier d'autre.
- Créer une tâche est un geste de la personne connectée : sans proposition intermédiaire. Ce que propose l'IA reste une proposition, confirmée
  par l'avocat (``intelligence.confirm_deadline``) : ``create`` refuse un auteur qui n'est pas une personne.
- Dossiers réservés : une tâche d'un dossier réservé n'est ni lue ni modifiée par un autre compte, et ne peut pas être confiée à une
  personne sans accès au dossier.
"""
from datetime import date
from html import escape as e
import json
import re
from urllib.parse import quote

from .common import Stop, load_matters, matter_display

STATUTS = {'a_faire': 'À faire', 'en_cours': 'En cours', 'bloquee': 'Bloquée', 'terminee': 'Terminée', 'annulee': 'Annulée'}
ETATS = ('a_faire', 'en_cours', 'bloquee')
ORIGINES = {'': '', 'dossier': 'Dossier', 'audience': 'Audience', 'courriel': 'Courriel'}
SANS = 'sans'
SANS_RESPONSABLE = 'Sans responsable'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS taches5688(task_id TEXT PRIMARY KEY, responsable TEXT NOT NULL DEFAULT '', rappel TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL DEFAULT 'a_faire',
  origine TEXT NOT NULL DEFAULT '', origine_ref TEXT NOT NULL DEFAULT '', createur TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS taches5688_resp ON taches5688(responsable);
CREATE INDEX IF NOT EXISTS taches5688_origine ON taches5688(origine, origine_ref);
'''


def schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def migrate(desk):
    """Une ligne pour chaque tâche existante, sans responsable : idempotent, ``tasks`` n'est pas touchée."""
    schema(desk)
    cur = desk.db.execute("INSERT OR IGNORE INTO taches5688(task_id,updated) SELECT id,? FROM tasks WHERE id NOT IN (SELECT task_id FROM taches5688)", (desk.now(),))
    desk.db.commit()
    return cur.rowcount


def _extra(desk):
    return {r['task_id']: dict(r) for r in desk.db.execute('SELECT * FROM taches5688')}


def reminder_days(task):
    try:
        return [int(x) for x in json.loads(task.get('rappel') or '[]')]
    except (TypeError, ValueError):
        return []


def _with_extra(task, extra):
    side = extra.get(task['id']) or {}
    out = {**task, 'responsable': side.get('responsable', ''), 'rappel': side.get('rappel', ''), 'etat': side.get('etat') or 'a_faire', 'origine': side.get('origine', ''),
           'origine_ref': side.get('origine_ref', '')}
    out['statut'] = {'closed': 'terminee', 'completed': 'terminee', 'cancelled': 'annulee'}.get(task.get('status')) or out['etat']
    out['externe'] = task.get('source') == 'nextcloud'
    return out


def open_tasks(desk):
    """Tâches ouvertes (registre local et tâches Nextcloud, sans doublon) avec leurs champs 5.6.88."""
    from .taches5628 import collect
    schema(desk)
    extra = _extra(desk)
    return [_with_extra(t, extra) for t in collect(desk)]


def open_assigned(desk):
    return [t for t in open_tasks(desk) if t['responsable'] and t['due']]


def of_origin(desk, origine, ref):
    from .membres5650 import viewer_hidden
    schema(desk)
    hidden = viewer_hidden() or frozenset()
    rows = desk.db.execute("SELECT t.id,t.matter,t.title,t.due,t.status FROM tasks t JOIN taches5688 x ON x.task_id=t.id WHERE x.origine=? AND x.origine_ref=? "
                           "AND t.status<>'cancelled' ORDER BY t.due", (origine, str(ref))).fetchall()
    extra = _extra(desk)
    return [_with_extra({**dict(r), 'source': 'local'}, extra) for r in rows if r['matter'] not in hidden]


def mine(desk, owner, scope='moi', hidden=None):
    """``moi`` : mes tâches ; ``sans`` : sans responsable ; ``tout`` : toutes celles que je peux voir."""
    from .membres5650 import viewer_hidden
    hidden = (viewer_hidden() or frozenset()) if hidden is None else hidden
    rows = [t for t in open_tasks(desk) if t['matter'] not in hidden]
    if scope == 'moi':
        return [t for t in rows if t['responsable'] == owner]
    if scope == SANS:
        return [t for t in rows if not t['responsable']]
    return rows


def _row(desk, ident):
    row = desk.db.execute('SELECT * FROM tasks WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('tache_absente')
    return dict(row)


def _days(value):
    if value in (None, ''):
        return ''
    from .rappels5688 import parse_days
    return json.dumps(parse_days(value))


def _due(value):
    value = str(value or '')[:10]
    if value:
        try:
            date.fromisoformat(value)
        except ValueError:
            raise Stop('date_tache_invalide') from None
    return value


def create(desk, data, actor, auth=None):
    """Création directe par la personne connectée."""
    from . import audiences5688 as au
    from .improvements36 import create_task
    schema(desk)
    if actor in au.AGENT_ACTORS or not actor:
        raise Stop('tache_reservee_aux_personnes')
    origine, ref = str(data.get('origine') or ''), str(data.get('origine_ref') or '')
    if origine not in ORIGINES:
        raise Stop('origine_tache_invalide')
    matter = str(data.get('matter') or '')
    if origine == 'audience':
        matter = au.get(desk, ref)['matter'] or matter
    elif origine == 'dossier':
        matter = ref or matter
    au.guard(matter)
    responsable = au.check_person(desk, str(data.get('responsable') or ''), matter, auth, 'responsable_inconnu')
    rappel = _days(data.get('rappel'))
    out = create_task(desk, data.get('title'), matter, _due(data.get('due')))
    desk.db.execute('INSERT OR REPLACE INTO taches5688(task_id,responsable,rappel,etat,origine,origine_ref,createur,updated) VALUES(?,?,?,?,?,?,?,?)',
                    (out['id'], responsable, rappel, 'a_faire', origine, ref, actor, desk.now()))
    desk.db.commit()
    desk.audit('tache5688_creee', {'id': out['id'], 'origine': origine, 'matter': matter, 'responsable': bool(responsable), 'par': actor})
    return {'id': out['id'], 'message': 'Tâche créée.' if responsable else 'Tâche créée. ' + SANS_RESPONSABLE + '.'}


def update(desk, data, actor, auth=None):
    """Responsable, échéance, rappel, statut. Une tâche Nextcloud garde son intitulé et son échéance : ils se modifient dans sa liste."""
    from . import audiences5688 as au
    from .improvements36 import update_local_task
    schema(desk)
    task = _external(desk, data.get('id')) or _row(desk, data.get('id'))
    au.guard(task['matter'])
    side = _extra(desk).get(task['id']) or {}
    responsable = au.check_person(desk, str(data['responsable'] if 'responsable' in data else side.get('responsable', '')), task['matter'], auth, 'responsable_inconnu')
    statut = str(data.get('statut') or 'a_garder')
    if statut != 'a_garder' and statut not in STATUTS:
        raise Stop('statut_tache_invalide')
    etat = statut if statut in ETATS else side.get('etat') or 'a_faire'
    rappel = _days(data.get('rappel')) if 'rappel' in data else side.get('rappel', '')
    if not task.get('external') and ('due' in data or 'title' in data or statut in ('terminee', 'annulee') or statut in ETATS):
        status = {'terminee': 'closed', 'annulee': 'cancelled'}.get(statut, 'open' if statut in ETATS else task['status'])
        update_local_task(desk, task['id'], title=data.get('title'), due=_due(data['due']) if 'due' in data else None, status=status)
    elif task.get('external') and statut in ('terminee', 'annulee'):
        raise Stop('tache_nextcloud_a_clore_dans_sa_liste')
    desk.db.execute('INSERT INTO taches5688(task_id,responsable,rappel,etat,origine,origine_ref,createur,updated) VALUES(?,?,?,?,?,?,?,?) '
                    'ON CONFLICT(task_id) DO UPDATE SET responsable=excluded.responsable,rappel=excluded.rappel,etat=excluded.etat,updated=excluded.updated',
                    (task['id'], responsable, rappel, etat, side.get('origine', ''), side.get('origine_ref', ''), side.get('createur', ''), desk.now()))
    desk.db.commit()
    desk.audit('tache5688_modifiee', {'id': task['id'], 'statut': statut, 'par': actor})
    return {'id': task['id'], 'message': 'Tâche mise à jour.'}


def _external(desk, ident):
    """Tâche d'une liste Nextcloud, absente du registre local ou reflétée par lui."""
    try:
        row = desk.db.execute('SELECT id,matter,title,due,status FROM work_tasks_v211 WHERE id=?', (str(ident or ''),)).fetchone()
    except Exception:   # noqa: BLE001 — table absente : fonction Nextcloud jamais utilisée
        return None
    return {**dict(row), 'external': True} if row else None


def from_mail(desk, data, actor, auth=None):
    """Tâche créée par la personne depuis un courriel : l'intitulé est celui qu'elle valide, la référence du message est conservée."""
    from . import audiences5688 as au
    from .desk import report_for
    key = str(data.get('mail_key') or '')
    if not re.fullmatch(r'[0-9a-f]{64}', key):
        raise Stop('courriel_introuvable')
    try:
        known = str(report_for(desk.c, key).get('matter') or '')   # dossier du courriel lu sur le serveur, jamais d'après le navigateur
    except Stop:
        known = ''
    au.guard(known)
    return create(desk, {**data, 'matter': data.get('matter') or known, 'origine': 'courriel', 'origine_ref': key}, actor, auth)


# ------------------------------------------------------------------------------------------------ présentation
def member_options(known, chosen='', empty=SANS_RESPONSABLE):
    rows = sorted(known.values(), key=lambda m: str(m.get('name') or m.get('owner')).casefold())
    return '<option value="">%s</option>%s' % (e(empty), ''.join(
        '<option value="%s"%s>%s</option>' % (e(m['owner'], quote=True), ' selected' if m['owner'] == chosen else '', e(str(m.get('name') or m['owner']))) for m in rows))


def add_form(prefix, known, origine='', ref='', matter='', title='', mail_key='', matters=None, heading='Ajouter une tâche', owner='', opened=False):
    """Formulaire court réutilisé sur un dossier, une audience, un courriel et « Mes tâches » (aucun script propre)."""
    route = 'courriel' if mail_key else 'creer'
    hidden = ''.join('<input type="hidden" name="%s" value="%s">' % (k, e(v, quote=True)) for k, v in (
        ('origine', origine), ('origine_ref', ref), ('mail_key', mail_key)) if v)
    select = ''
    if matters is not None:
        select = '<label>Dossier<select name="matter"><option value="">Cabinet, sans dossier</option>%s</select></label>' % ''.join(
            '<option value="%s"%s>%s</option>' % (e(m['id'], quote=True), ' selected' if m['id'] == matter else '', e(matter_display(m))) for m in matters)
    elif matter:
        hidden += '<input type="hidden" name="matter" value="%s">' % e(matter, quote=True)
    return ('<details class="t88-add"%s><summary>%s</summary><form class="m5-form t88-form" data-api="m5634/agenda/tache/%s" data-reload="1">%s'
            '<label>Tâche<input name="title" required minlength="3" maxlength="500" value="%s"></label>%s<div class="t88-row">'
            '<label>Responsable<select name="responsable">%s</select></label><label>Échéance<input type="date" name="due"></label>'
            '<label>Rappel<select name="rappel"><option value="">Réglage habituel (J-7, J-1)</option><option value="1">J-1</option><option value="7,1">J-7 et J-1</option>'
            '<option value="0">Le jour même</option></select></label></div><button class="ax-btn" type="submit">Créer la tâche</button>'
            '<p role="status" class="m5-status"></p></form></details>') % (' open' if opened else '', e(heading), route, hidden, e(title, quote=True), select, member_options(known, owner))


def mail_form(desk, auth, prefix, key, report, matter=''):
    """Page d'un courriel : l'intitulé proposé est son objet, la personne le corrige et crée la tâche elle-même."""
    from .audiences5688 import members
    subject = re.sub(r'\s+', ' ', str(report.get('subject') or '')).strip()[:200]
    return '<section class="t88-mail">%s</section>' % add_form(prefix, members(desk, auth), matter=matter, title='Traiter : ' + subject if subject else '', mail_key=key,
                                                                heading='Créer une tâche depuis ce courriel')


def _row_html(prefix, t, known, labels, today):
    from .taches5628 import _bucket, _due_label
    _, day = _bucket(t['due'], today)
    chips = []
    if t['matter']:
        chips.append('<a class="t88-chip" href="%s">%s</a>' % (e(prefix + '/dossier?id=' + quote(t['matter'], safe=''), quote=True), e(labels.get(t['matter'], t['matter']))))
    if day:
        chips.append('<span class="t88-chip%s">%s</span>' % (' t88-late' if day < today else '', e(_due_label(day, today))))
    chips.append('<span class="t88-chip">%s</span>' % e(STATUTS.get(t['statut'], t['statut'])))
    chips.append('<span class="t88-chip">%s</span>' % e(str((known.get(t['responsable']) or {}).get('name') or t['responsable']) if t['responsable'] else SANS_RESPONSABLE))
    if t['origine']:
        chips.append('<span class="t88-chip">%s</span>' % e(ORIGINES.get(t['origine'], '')))
    statuses = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == t['statut'] else '', e(v)) for k, v in STATUTS.items() if not t['externe'] or k in ETATS)
    due_field = '' if t['externe'] else '<label>Échéance<input type="date" name="due" value="%s"></label>' % e(str(t['due'] or '')[:10], quote=True)
    days = reminder_days(t)
    rappel = ', '.join(str(x) for x in days)
    return ('<li class="t88-item" id="tache-%s"><p class="t88-title">%s</p><div class="t88-chips">%s</div><details class="t88-edit"><summary>Modifier</summary>'
            '<form class="m5-form t88-form" data-api="m5634/agenda/tache/modifier" data-reload="1"><input type="hidden" name="id" value="%s"><div class="t88-row">'
            '<label>Responsable<select name="responsable">%s</select></label>%s<label>Statut<select name="statut">%s</select></label>'
            '<label>Rappel (jours avant, ex. 7, 1)<input name="rappel" value="%s" maxlength="20"></label></div>'
            '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></details></li>') % (
        e(t['id'], quote=True), e(t['title']), ''.join(chips), e(t['id'], quote=True), member_options(known, t['responsable']), due_field, statuses, e(rappel, quote=True))


def _top_form(desk, prefix, known, matters, args, owner):
    """Formulaire de la page ; ouvert d'emblée quand on arrive d'un courriel (la référence du message suit, jamais son contenu dans l'adresse)."""
    key = str(args.get('courriel') or '')
    if not re.fullmatch(r'[0-9a-f]{64}', key):
        return add_form(prefix, known, matters=matters, owner=owner)
    from .desk import report_for
    try:
        subject = re.sub(r'\s+', ' ', str(report_for(desk.c, key).get('subject') or '')).strip()[:200]
    except Stop:
        subject = ''
    return add_form(prefix, known, matters=matters, matter=str(args.get('matter') or ''), title='Traiter : ' + subject if subject else '', mail_key=key, owner=owner,
                    heading='Créer une tâche depuis ce courriel', opened=True)


def page_html(desk, auth, prefix, args, owner, role):
    from .audiences5688 import members
    from .membres5650 import viewer_hidden
    from .taches5628 import GROUPS, _bucket
    today = date.today()
    known = members(desk, auth)
    hidden = viewer_hidden() or frozenset()
    scope = str(args.get('vue') or 'moi')
    if scope not in ('moi', SANS, 'tout'):
        scope = 'moi'
    matters = [m for m in load_matters(desk.c) if m['id'] not in hidden]
    labels = {m['id']: matter_display(m) for m in matters}
    rows = mine(desk, owner, scope, hidden)
    grouped = {k: [] for k, _ in GROUPS}
    for t in rows:
        grouped[_bucket(t['due'], today)[0]].append(t)
    body = ''.join('<section class="t88-group"><h2>%s <span class="t88-count">%d</span></h2><ul class="t88-list">%s</ul></section>' % (
        e(label), len(grouped[key]), ''.join(_row_html(prefix, t, known, labels, today) for t in grouped[key])) for key, label in GROUPS if grouped[key])
    tabs = ''.join('<a href="%s"%s>%s</a>' % (e(prefix + '/mes-taches' + ('' if k == 'moi' else '?vue=' + k), quote=True), ' aria-current="page"' if k == scope else '', e(v))
                   for k, v in (('moi', 'Mes tâches'), (SANS, SANS_RESPONSABLE), ('tout', 'Toutes')))
    empty = '<p class="t88-empty">Aucune tâche ouverte ici.</p>'
    return ('<h1>Mes tâches</h1><p class="ax-muted">Les tâches qui vous sont confiées, classées par échéance. Celles d’un dossier réservé n’apparaissent qu’aux comptes autorisés.</p>'
            '<nav class="m34-tabs" aria-label="Tâches">%s</nav>%s%s%s') % (
        tabs, _top_form(desk, prefix, known, matters, args, owner), body or empty, reminders_card(desk, prefix, owner, role, hidden))


def reminders_card(desk, prefix, owner, role, hidden=frozenset()):
    """Mes rappels : liste, canaux de la personne, échéances du cabinet (avocat, administrateur)."""
    from . import rappels5688 as rp
    pref = rp.preferences(desk, owner)
    items = ''.join('<li>%s <form class="m5-form m5-inline" data-api="m5634/agenda/rappel/lu" data-reload="1"><input type="hidden" name="id" value="%s">'
                    '<button class="ax-btn ghost" type="submit">Lu</button></form></li>' % (
                        ('<a href="%s">%s</a>' % (e(prefix + r['link'], quote=True), e(r['message']))) if r['link'] else e(r['message']), e(r['id'], quote=True))
                    for r in rp.unread(desk, owner, hidden))
    cabinet = ''
    if role in ('administrateur', 'avocat'):
        base = rp.cabinet_days(desk)
        cabinet = ('<form class="m5-form t88-form" data-api="m5634/agenda/rappel/cabinet" data-reload="1"><h3>Échéances du cabinet</h3><div class="t88-row">'
                   '<label>Audiences (jours avant)<input name="audience" value="%s" maxlength="20"></label><label>Tâches (jours avant)<input name="tache" value="%s" maxlength="20"></label></div>'
                   '<button class="ax-btn ghost" type="submit">Enregistrer pour le cabinet</button><p role="status" class="m5-status"></p></form>') % (
            e(', '.join(str(x) for x in base['audience']), quote=True), e(', '.join(str(x) for x in base['tache']), quote=True))
    return ('<section class="ax-card t88-rappels" id="mes-rappels"><h2>Mes rappels</h2>%s<form class="m5-form t88-form" data-api="m5634/agenda/rappel/preferences" data-reload="1">'
            '<p class="ax-muted">Un rappel vous parvient dans l’application et sur le téléphone associé. Par courriel, il part seulement à l’adresse de votre compte.</p>'
            '<label class="m50-check"><input type="checkbox" name="push"%s> Signal sur mon téléphone associé (sans contenu)</label>'
            '<label class="m50-check"><input type="checkbox" name="email"%s> Courriel à l’adresse de mon compte</label><div class="t88-row">'
            '<label>Mes audiences (jours avant, vide : réglage du cabinet)<input name="audience" value="%s" maxlength="20"></label>'
            '<label>Mes tâches (jours avant, vide : réglage du cabinet)<input name="tache" value="%s" maxlength="20"></label></div>'
            '<button class="ax-btn ghost" type="submit">Enregistrer mes rappels</button><p role="status" class="m5-status"></p></form>%s</section>') % (
        ('<ul class="t88-list">%s</ul>' % items) if items else '<p class="ax-muted">Aucun rappel non lu.</p>', ' checked' if pref['push'] else '', ' checked' if pref['email'] else '',
        e(', '.join(str(x) for x in pref['audience']) if pref['propres'] else '', quote=True), e(', '.join(str(x) for x in pref['tache']) if pref['propres'] else '', quote=True), cabinet)


def due_within(desk, owner, day, hidden):
    """Tâches de la personne dues ce jour ou en retard (liste du matin)."""
    out = []
    for t in mine(desk, owner, 'moi', hidden):
        raw = str(t['due'] or '')[:10]
        if raw and raw <= day.isoformat():
            out.append({**t, 'retard': raw < day.isoformat()})
    return sorted(out, key=lambda t: str(t['due']))
